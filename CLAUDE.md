# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project status

**Pre-implementation.** The repo currently contains only `eip-library-master-plan.md` — the authoritative spec for `daedalus`, a unified Allen-Bradley / EtherNet-IP Python library. No source, packaging, tests, or git history exist yet. Read the master plan before doing anything; this file summarizes the decisions in it that constrain all future code. When the plan and this file disagree, the plan wins — update this file to match.

`daedalus` ports the protocol core of two existing libraries (`pycomm3`, MIT, unmaintained — the better-engineered base; `pylogix`, Apache-2.0, maintained — specific battle-tested pieces) into a fresh `src/` layout, then adds the value layer (async, Class 1 I/O, write-safety, typed codegen) on top. A sibling package `daedalus-tune` (PID tuning) depends on it.

## The non-negotiable architecture: sans-I/O core

This is the single most important rule and the reason the project exists. The stack is layered L0→L5, and **layers L0–L3 must never touch a socket**:

- **L0 Wire codec** (pure, ported from pycomm3): `DataType` metaclass system, CIP services/objects/status, EtherNet/IP encapsulation, CIP segments/EPATH. No I/O.
- **L1 Transports**: the *only* place sockets live — sync TCP, asyncio TCP, UDP Class 1, CSP (port 2222).
- **L2 Session/connection** (sans-I/O state machine): register session, Forward/Large-Forward Open + fallback, keepalive, reconnect.
- **L3 Drivers**: `LogixDriver`, `SLCDriver`, `PLC5Driver` (new), `GenericCIPDriver`. Produce *bytes to send* + a state object; never move bytes themselves.
- **L4 Runtime** (mostly new): async scheduler, change-of-state/subscriptions, Class 1 manager, **write-safety policy gate**, typed codegen + L5X reconciliation, observability.
- **L5 Integrations**: NEXUS/TALOS adapters, digital-twin bridge, OPC UA/MQTT republish, MicroPython slim build.

The contract: a driver returns bytes + state; an L1 transport moves bytes and feeds replies back into the state machine. The same L0–L3 code is driven by a blocking loop, an asyncio task, or a Class 1 cyclic producer. **Any socket call leaking into L0–L3 breaks the model and forces two parallel codebases — this is the failure mode the whole design exists to prevent.** A CI import/lint check is planned to enforce it.

## Locked technical decisions (do not relitigate)

- **Package name:** `daedalus` (no PyPI dependency, import `from daedalus import ...`).
- **Min Python:** 3.11+ (use `tomllib`, `Self`, exception groups, modern type-hint syntax; strip Python 2 hacks). MicroPython = stripped sync-only extra (L0–L3, no async, no L4/L5).
- **Async:** `anyio` (runs on asyncio + trio) for L1 async transport and L4 runtime. **SUPERSEDED (Phase 3, the plan wins over decision #7):** the original "async-first core + generated sync shim over anyio's blocking portal" is replaced by a **sans-I/O generator core**. The L3 driver orchestration is module-level generator functions (`_*_gen`) that `yield` a CIP message and receive the parsed reply; two thin runners (`_run_sync`, `_run_async`) drive the *same* generators — still never a second protocol stack. Rationale: a portal shim forces every sync call through a running anyio event loop, which **breaks the decision-#2 MicroPython slim build** (sync-only, no anyio, L0–L3); the generator core gives a pure-sync path with zero async dependency. See `eip-library-master-plan.md` §12.
- **Typed models:** `pydantic` v2 behind an optional `[typed]` extra (dataclass fallback). Pydantic stays **out of the L0 hot decode path and out of base dependencies** — it lives at L4 only (validated writes, reconciliation schema, serialization).
- **Port strategy:** clean re-port into fresh `src/`; vendored `pycomm3` is the **parity oracle**, not a runtime dependency.
- **License:** Apache-2.0, with a `NOTICE` preserving pycomm3 (MIT) + pylogix (Apache-2.0) headers.
- **Class 1 v1:** consume-only first (Phase 6a), produce later (6b).
- **Result type:** one unified `Tag` merging pycomm3 `.value/.error/.type` and pylogix `.TagName/.Value/.Status` (both attribute styles aliased for migration). Compat shims `daedalus.compat.pycomm3` / `daedalus.compat.pylogix` let existing scripts port with an import change.

## Safety constraints (hard non-goals — never violate)

These are the differentiated, "you-shaped" doctrine encoded as code. Treat them as invariants:

- **Read-only unless explicitly armed.** Every write path goes through a `WritePolicy` gate (read-only mode, allow/denylist, dry-run, pluggable critic hook that must approve a batch before commit).
- **Never** do online program edits, firmware flashing, program download, or keyswitch changes.
- **GuardLogix safety tags are read-only — never attempt safety writes.**
- **Class 1 is soft real-time only** (Python GIL + OS scheduler). Plan ≥100 ms RPI on stock CPython; document jitter honestly, never oversell determinism.
- Writes use **stage → confirm → commit** with read-back verify, an immutable audit log (who/what/when/old→new), and guaranteed ownership-release on exit (`finally` → release).
- For PID (`daedalus-tune`): operate on `.CVProgEU`, **never `.CVEU`**; the LLM advisor is read-only and never emits a gain to a PLC; never tune safety-interlocked loops.

## License firewall (when borrowing from external tools)

- **Copy-able** (MIT/BSD/Apache): Pymodbus (architecture template), `construct`, Eclipse Tahu (Sparkplug B), python-control, simple-pid.
- **Separate-dependency only** (MPL/LGPL): libplctag, asyncua, SIPPY — never copy source into Apache code.
- **Reference / run-as-tool only** (GPL): cpppo (`enip_server` sim oracle), scapy-cip-enip, OpenPLC, Wireshark dissector. Run as black-box behavioral oracles — capture wire traffic, match bytes, write code from the public ODVA spec. **Do not transcribe their source.**
- **Verify-first:** OpENer (Class 1 blueprint + RT recipe); GEKKO (force local solver — older versions defaulted to a remote server; no client data off-box).

## Planned tooling (once Phase 0 scaffolds the repo)

The plan specifies, but does not yet create, this toolchain. Use these once `pyproject.toml` exists:

- Packaging: `src/` layout, `pyproject.toml` (hatch or uv), `py.typed`, semantic versioning.
- Lint: `ruff`. Type-check: `mypy`/`pyright`. Tests: `pytest` + `hypothesis` (property-based round-trip tests for every codec `encode`/`decode`).
- CI: GitHub Actions — lint, type-check, unit + replay tiers on Python 3.11/3.12/3.13.
- Docs: mkdocs-material.

**Testing strategy:** capture real request/response frame pairs (from chassis + Studio 5000 Logix Emulate) into fixtures and unit-test every codec path offline and deterministically — this proves parity without hardware in CI. A minimal in-process CIP server answers register-session/forward-open/read/write for transport+driver tests (later promoted to a shipped sim-mode feature).

## Phased roadmap (work proceeds in this order)

Each phase is a future prompt batch with a gate that must be green before advancing. Phases 0–2 are the spine; everything after parallelizes once the sans-I/O core is locked.

0. **Scaffold** — repo, pyproject, CI, license/NOTICE → CI green on empty package.
1. **Codec** — port L0 → 100% round-trip tests + replay vectors.
2. **Sync parity** — sync transport + session + LogixDriver r/w/taglist/UDT → **parity vs pycomm3 green** (the key gate).
3. **Async** — asyncio transport + AsyncLogixDriver on the same codec.
4. **Runtime** — scheduler + change-of-state + write-safety gate.
5. **PCCC/PLC-5** — SLCDriver port → PLC5Driver (EIP PCCC object `0x67` + CSP).
6. **Class 1** — implicit I/O manager (UDP, RPI, run/idle header, sequence count).
7. **Typed/L5X** — codegen + online↔offline reconciliation (NEXUS tie-in).
8. **Hardening** — observability, resilience, MicroPython slim build.
9. **Integrations** — twin bridge, NEXUS/TALOS adapters.

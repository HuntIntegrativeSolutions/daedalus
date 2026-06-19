# Unified Allen-Bradley / EtherNet-IP Library — Master Build Plan

**Status:** Draft v0.4 (PID tuning integrated — see §17)
**Name:** `daedalus` (PyPI-free, import `from daedalus import ...`)
**Sources analyzed:** `dmroeder/pylogix` (Apache-2.0), `ottowayi/pycomm3` (MIT, unmaintained)
**Audience:** HIS Controls / NEXUS-TALOS integration

---

## 0. Thesis (read this first)

We are **not** rewriting the EtherNet/IP wire protocol. Both libraries already solve CIP encode/decode correctly and have years of field testing baked in. Reimplementing that is pure downside risk.

The plan rests on three decisions:

1. **Port pycomm3's protocol core, keep pylogix's pragmatic wins, add the missing layer on top.** pycomm3 is the better-engineered base; pylogix contributes specific battle-tested pieces and the maintenance posture we should inherit.
2. **Refactor the protocol into a sans-I/O core.** The single most important architectural move. The protocol logic becomes pure functions / state machines with **zero socket calls**. Transports (sync TCP, asyncio TCP, UDP Class 1, CSP) are pluggable shells around the same codec. This is what makes asyncio, Class 1, and CSP *additions* rather than *forks*.
3. **Parity first, then additive.** Phase the build so we reach feature-parity with pycomm3's LogixDriver (proven against recorded packets) *before* adding async, Class 1, PCCC/PLC-5, scheduler, and safety. No new capability ships until parity regression is green.

---

## 1. Evidence: what's actually in each repo

### pycomm3 (8,642 LOC, layered, MIT, **no longer maintained**)

| File | LOC | What it gives us |
|---|---|---|
| `cip/data_types.py` | 1,226 | **Crown jewel.** Metaclass `DataType` system. Stream-based `encode`/`decode`, `BufferEmptyError` protocol for unknown-length arrays. Elementary + `Array`/`Struct` + CIP segments (`EPATH`, `PortSegment`, `LogicalSegment`). |
| `cip/status_info.py` | 1,732 | Exhaustive CIP general/extended status decoding. Huge time-saver; port wholesale. |
| `logix_driver.py` | 1,532 | Tag-list pipeline (`_get_instance_attribute_list_service` → `_parse_instance_attribute_list` → `_isolate_user_tags`), UDT/template decode (`_get_structure_makeup`, `_read_template`, `_parse_template_data`), MSP multi-request build, fragmented read/write. |
| `cip_driver.py` | 661 | Session register, `_forward_open` / large forward open, `with_forward_open` fallback decorator, discovery, `generic_message`. |
| `slc_driver.py` + `cip/pccc.py` | 967 | **PCCC base.** SLC/MicroLogix addressing (I/O, C/T, L/F/B/N, S, A, ST), PCCC-over-CIP (`0x67` object), file-directory enumeration. The seed for the PLC-5 work. |
| `packets/*` | ~1,150 | Proper packet abstraction (EtherNet/IP encapsulation, CIP, Logix). Clean request/response framing. |

### pylogix (5,127 LOC, monolithic, Apache-2.0, **maintained**)

| File | LOC | What it gives us |
|---|---|---|
| `eip.py` | 1,952 | God-class `PLC`. Worth lifting: masked **bit-of-word writes** (`_add_mod_write_service`, `mod_write_masks`), `Micro800` handling, `ReceiveMessage` unsolicited callback, simple multi-read/write. |
| `lgx_comm.py` | 1,070 | Socket layer with a clean abstraction that also feeds the **MicroPython** build. |
| `lgx_vendors.py` / `lgx_uvendors.py` | 1,643 | Vendor ID tables for discovery. |
| `scripts/build_mpy.py` | — | MicroPython packaging path. Keep this capability — embedded targets are real for you. |

### Shared gaps (confirmed by source inspection)

- **No asyncio** — 0 files in either.
- **No Class 1 implicit I/O** — explicit Class 3 messaging only.
- **No deterministic scheduler / change-of-state** — poll-only, loop-it-yourself.
- **No typed model generation** — dict / `Response` / `Tag` access, no schema.
- **No write safety** — both will write to a live processor with no guardrail.
- **No PLC-5** — pylogix refuses PCCC entirely; pycomm3 covers SLC/MicroLogix but not PLC-5 command set or CSP transport.
- **Legacy packaging** — both `setup.py`, no `pyproject.toml`, no `py.typed`.

---

## 2. License reality

- pylogix = Apache-2.0; pycomm3 = MIT. Both permissive and mutually compatible.
- Ported pycomm3 code → keep its MIT copyright header in a `LICENSES/` / `NOTICE` file.
- Ship the combined work under **Apache-2.0** (patent grant is the safer posture for a library you deliver to clients). Confirm this is your call in §12.

---

## 3. Target architecture (layered, sans-I/O at the core)

```
┌────────────────────────────────────────────────────────────────┐
│ L5  Integrations                                                 │
│     NEXUS/TALOS adapters · digital-twin bridge (Godot/Unity)     │
│     OPC UA / MQTT republish (optional) · MicroPython slim build  │
├────────────────────────────────────────────────────────────────┤
│ L4  Runtime (the value-add, mostly NEW)                          │
│     Async scheduler · change-of-state/subscriptions              │
│     Class 1 I/O manager · write-safety policy gate               │
│     Typed codegen + L5X reconciliation · observability/metrics   │
├────────────────────────────────────────────────────────────────┤
│ L3  Drivers (protocol request building — port + extend)          │
│     LogixDriver · SLCDriver · PLC5Driver(NEW) · GenericCIPDriver  │
├────────────────────────────────────────────────────────────────┤
│ L2  Session & connection mgmt (sans-I/O state machine)           │
│     register session · Forward/Large-Forward Open + fallback     │
│     connection-size negotiation · keepalive · reconnect/backoff  │
├────────────────────────────────────────────────────────────────┤
│ L1  Transports (pluggable shells — the ONLY place sockets live)  │
│     sync TCP · asyncio TCP · UDP Class 1 · CSP (port 2222)        │
├────────────────────────────────────────────────────────────────┤
│ L0  Wire codec (PURE, no I/O — port from pycomm3)                │
│     DataType system · CIP services/objects/status                │
│     EtherNet/IP encapsulation · CIP segments / EPATH             │
└────────────────────────────────────────────────────────────────┘
```

**The sans-I/O contract:** L0–L2 never touch a socket. A driver produces *bytes to send* and a state object; a transport at L1 moves bytes and feeds replies back into the state machine. The same L0–L3 code is driven by a blocking loop, an asyncio task, or a Class 1 cyclic producer. This is non-negotiable — it's what prevents two parallel codebases.

---

## 4. Component plan (port vs. build)

### L0 — Wire codec  *(PORT, ~clean-lift from pycomm3)*
- Lift `cip/data_types.py`, `cip/services.py`, `cip/object_library.py`, `cip/status_info.py`, `packets/*`.
- Action items: add `py.typed`, modernize type hints (3.11+ syntax), strip Python 2 hacks, add property-based tests for every `encode`/`decode` round-trip.

### L1 — Transports  *(BUILD, with sync lifted)*
- **Sync TCP:** behavior from pycomm3 `socket_.py` + pylogix `lgx_comm.py`.
- **Async TCP:** asyncio `StreamReader/StreamWriter`, same codec.
- **UDP Class 1:** new, real-time cyclic (see §5.2).
- **CSP (port 2222):** new, for direct PLC-5/E (see §5.3) — your existing lib lands here.

### L2 — Session & connection  *(PORT + harden)*
- Port `_forward_open` / large forward open + the `with_forward_open` fallback decorator.
- Add: keepalive, auto-reconnect with exponential backoff, session recovery, idle ForwardClose handling. Expose as async-first with a sync wrapper.

### L3 — Drivers  *(PORT + 1 NEW)*
- **LogixDriver** — port pycomm3; fold in pylogix's masked bit-of-word write (cleaner) and `Micro800` handling.
- **SLCDriver** — port pycomm3 PCCC.
- **PLC5Driver (NEW)** — PLC-5 PCCC command set + addressing, over EtherNet/IP PCCC object *and* CSP.
- **GenericCIPDriver** — port `generic_message`; keep pylogix's `ReceiveMessage` unsolicited-callback idea.

### L4–L5 — see §5–§9.

---

## 5. The additive features, in detail

### 5.1 asyncio
- Async-first internals; sync API is a thin `asyncio.run`-style shim over the same drivers so we never maintain two protocol stacks.
- `AsyncLogixDriver.read(*tags)` multiplexes many tags/PLCs on one event loop.
- Connection pool keyed by `(ip, path)`; per-connection request queue with the MSP batcher feeding it.
- Decision in §12: pure `asyncio` vs `anyio` (anyio buys trio compatibility at a small abstraction cost).

### 5.2 Class 1 implicit (cyclic) I/O — the differentiator
This is the hard one and the moat. Scope:
- **Connection establishment:** `Forward_Open` via the Connection Manager with real Class 1 connection parameters — O→T and T→O connection IDs, RPI, connection type/priority, transport class 1, real-time format.
- **Transport:** UDP on 2222; 32-bit real-time **run/idle header**; **sequence count** management; T→O consumption + O→T production at the configured RPI.
- **The twin angle:** lets `daedalus` appear to a controller as *real remote I/O* producing/consuming at scan-rate fidelity, not explicit polling. This is what no Python lib does.
- **Realism guardrails:** Python + GIL + OS scheduling means we target *soft* real-time (single-digit-ms jitter), not hard determinism. Document this honestly; offer a dedicated process / `SCHED_FIFO` hint / busy-wait option for tight RPIs. Validate against Logix Emulate and a real chassis.

### 5.3 PCCC + PLC-5
- **Transport generalization:** PCCC requests must ride over either (a) EtherNet/IP PCCC execute object (`0x67`) — pycomm3's path — or (b) raw **CSP** on TCP 2222 for legacy PLC-5/E. Make transport a parameter, not a fork.
- **PLC5Driver command set:** PLC-5 typed read/write and word-range read/write differ from SLC's protected-typed-logical commands. New PCCC command module distinct from SLC, sharing the PCCC data-type primitives in `cip/pccc.py`.
- **Addressing:** PLC-5 file addressing (`N7:0`, `B3:0/0`, `F8:0`, `T4:0.ACC`, etc.). Reuse the SLC address-parser regex pattern; extend for PLC-5 specifics.
- **Your asset:** the EIP+CSP transport and PCCC command layer you already built drops in here — this phase is largely *integrate + test*, not *invent*.

### 5.4 Deterministic scheduler + change-of-state
- Group tags by update rate; deterministic fixed-interval polling with drift compensation.
- Per-group optimal request packing (MSP up to connection size, fragmented above).
- Subscription API: `subscribe(tag, on_change)` over polling now, over Class 1 later — same callback surface.

### 5.5 Write-safety / policy gate  *(your doctrine as code)*
- A `WritePolicy` object wraps every write path:
  - `read_only` mode (hard block),
  - tag **allowlist / denylist**,
  - **dry-run** (log intended writes, send nothing),
  - **critic hook** — a pluggable callable that must approve a write batch before commit (maps NEXUS "deterministic critics gate, never write to live").
- Default posture: **read-only unless explicitly armed.** This is the differentiated, you-shaped feature.

### 5.6 Typed codegen + L5X reconciliation  *(NEXUS tie-in)*
- Generate `pydantic`/`dataclass` models from either the **online tag list** or an **offline L5X** (NEXUS already parses L5X — 39k tags, address_xref).
- **Reconciliation:** at connect, diff online tag list against the offline project; flag drift (added/removed/retyped tags, program-edit mismatch) before any read/write. Catches "twin and controller disagree about the program."

### 5.7 Observability
- Structured logging (stdlib `logging`, JSON formatter option).
- Prometheus metrics: request-latency histograms, error counts by CIP status, reconnects, batch sizes, scheduler jitter.
- Packet-capture hook (tap raw frames to file for replay-test capture and Wireshark cross-checks).

### 5.8 Resilience
- Reconnect/backoff, session recovery, **partial-batch isolation** (one bad tag in an MSP request doesn't fail the batch — pycomm3 does per-tag errors; make it first-class), circuit breaker per connection.

---

## 6. Unified public API

- One result type: merge pycomm3 `Tag` (`.value/.error/.type`) and pylogix `Response` (`.TagName/.Value/.Status`) into a single `Tag` with both attribute styles aliased for migration.
- Two surfaces over one core: `LogixDriver` (sync) and `AsyncLogixDriver` (async), context managers on both.
- **Compat shims:** `daedalus.compat.pycomm3` and `daedalus.compat.pylogix` expose look-alike APIs so existing scripts (and any NEXUS code on either lib) port with an import change. Lets you migrate incrementally.

---

## 7. Testing strategy

- **Replay vectors:** capture real request/response frame pairs (from your chassis + Emulate) into fixtures; unit-test every codec path offline, deterministically, in CI. This is how we prove parity without hardware in CI.
- **Software CIP server:** a minimal in-process server that answers register-session/forward-open/read/write, for transport + driver tests.
- **Emulator integration:** optional test tier against Studio 5000 Logix Emulate (gated, runs on your workstation).
- **Property tests:** `hypothesis` for type round-trips.
- **CI:** GitHub Actions — lint (ruff), type-check (mypy/pyright), unit + replay tiers on 3.11/3.12/3.13.

---

## 8. Repo & packaging

- `src/` layout, `pyproject.toml` (hatch or uv), `py.typed`, semantic versioning.
- `LICENSE` (Apache-2.0) + `NOTICE` preserving pycomm3 MIT + pylogix Apache notices.
- Slim **MicroPython** extra that strips L4/L5 and async, keeping L0–L3 sync core (preserve pylogix's embedded capability).
- Docs: mkdocs-material; migration guide from both libs.

---

## 9. Phased roadmap (each phase = a future Claude Code prompt batch)

| Phase | Deliverable | Gate to advance |
|---|---|---|
| **0 — Scaffold** | Repo, pyproject, CI, license/NOTICE, sans-I/O design doc, test harness skeleton | CI green on empty package |
| **1 — Codec** | Port L0 (types, services, status, packets, segments) | 100% round-trip unit tests + replay vectors |
| **2 — Sync parity** | Sync transport + session mgmt + LogixDriver read/write/taglist/UDT | **Parity regression vs pycomm3 green** |
| **3 — Async** | asyncio transport + AsyncLogixDriver on same codec | Async tests match sync results |
| **4 — Runtime** | Scheduler + change-of-state + **write-safety gate** | Safety gate blocks live writes in tests |
| **5 — PCCC/PLC-5** | SLCDriver port → PLC5Driver (EIP PCCC + CSP) | Read/write a PLC-5 (your hardware) |
| **6 — Class 1** | Implicit I/O manager (UDP, RPI, run/idle, seq) | Cyclic conn against Emulate + chassis |
| **7 — Typed/L5X** | Codegen + online↔offline reconciliation (NEXUS) | Drift detection on a real project |
| **8 — Hardening** | Observability, resilience, MicroPython slim build | Metrics + reconnect soak test |
| **9 — Integrations** | Twin bridge, NEXUS/TALOS adapters | End-to-end twin loop |

Phases 0–2 are the spine; everything after is parallelizable once the sans-I/O core is locked.

---

## 10. Hard parts / risks (call them out now)

- **Class 1 timing in Python.** GIL + OS scheduler → soft real-time only. Mitigate with dedicated process, optional busy-wait, honest docs. Don't oversell determinism.
- **Template/UDT decode edge cases.** pycomm3's decoder is good but has known gaps on exotic nested structures/strings. Replay vectors from *your* actual programs are the defense.
- **CSP for PLC-5/E.** Sparse public documentation; your existing implementation is the reference. Capture frames early.
- **Maintaining sync+async without forking.** The sans-I/O discipline must hold; any socket call leaking into L0–L3 breaks the model. Enforce with a lint rule / import check in CI.
- **Scope creep.** L4/L5 are tempting to start early. The roadmap gates exist to stop that.

---

## 11. What "best of both" concretely means (cheat sheet)

- **From pycomm3:** type system, packet layering, CIP segments/EPATH, status decoding, tag-list/UDT/template pipeline, MSP + fragmented r/w, PCCC base, `generic_message`, `with_forward_open` fallback.
- **From pylogix:** masked bit-of-word writes, `Micro800` handling, unsolicited `ReceiveMessage` callback, MicroPython build path, maintenance discipline, Apache-2.0 base.
- **Net new:** sans-I/O refactor, asyncio, Class 1 I/O, scheduler/subscriptions, write-safety gate, typed codegen + L5X reconciliation, observability, resilience, modern packaging.

---

## 12. Decisions — LOCKED (2026-06-17)

| # | Decision | Resolution |
|---|---|---|
| 1 | Name / package | **`daedalus`** — LOCKED (PyPI-free, no Rockwell TM exposure) |
| 2 | Min Python | **3.11+** (`tomllib`, `Self`, exception groups, async speedups); MicroPython = stripped sync-only extra |
| 3 | Async lib | **anyio** (runs on asyncio + trio; its blocking portal powers the sync shim) |
| 4 | Typed models | **pydantic v2** behind an optional `[typed]` extra, dataclass fallback. Pydantic stays **out of the L0 hot decode path** and **out of base dependencies**; it sits at L4 for validated writes, reconciliation schema, and serialization |
| 5 | Port strategy | **Clean re-port** into fresh `src/` layout; pycomm3 vendored as the parity oracle |
| 6 | License | **Apache-2.0** (+ NOTICE preserving pycomm3 MIT / pylogix Apache-2.0) |
| 7 | Sync surface | **Async-first core + generated sync shim** on a persistent background loop |
| 8 | Class 1 v1 | **Consume-only first (Phase 6a), produce later (6b)** |

All decisions resolved except the name. **Phase 0 + Phase 1 are ready to become the first Claude Code prompt batch.**

---

## 13. Additional features (added after pressure-test, v0.3)

### Tier 1 — pull into v1 scope
- **Connection broker + backpressure.** Multiplex logical clients over shared physical CIP connections; max-in-flight rate limiting per controller. Logix has finite connection slots + CM capacity — at NEXUS multi-worker scale this is essential, not optional.
- **daedalus-as-CIP-server (sim mode).** Promote the test CIP server to a shipped feature: daedalus impersonates a Logix controller so a digital twin runs with zero hardware. Closes the loop to the original twin goal. (cpppo `enip_server` = interim oracle.)
- **Persistent symbol/template cache** keyed by program edit-time/checksum. Fast reconnect, offline schema, substrate for L5X reconciliation.
- **Read-back verify + immutable write audit log.** Optional confirm-after-write; structured tamper-evident record of every write (who/what/when/old→new). Turns the safety gate into a true guardian layer.
- **First-class routing path builder/parser.** backplane → ENBT → DH+ → PLC-5; 5380 dual-IP. Make deep routing an API, not string-mangling.

### Tier 2 — v1.x
- Generic CIP device I/O via **assembly objects + EDS parsing** (drives, valve manifolds, FLEX/POINT I/O).
- **Sparkplug B / MQTT + OPC UA republish** — scheduler data into the Ignition/IIoT layer.
- **Historian sink** — ring buffer → Parquet/SQLite/Influx.
- **CLI** — `daedalus read|write|taglist|discover|monitor|serve`.
- **CIP Security (TLS/DTLS)** for v21+ / 5380 / 5580 — differentiated, client-grade, big lift (stretch).

## 14. Non-goals (explicit)
- No online program edits, firmware flashing, program download, or keyswitch changes — ever.
- **GuardLogix safety tags = read-only.** Never attempt safety writes.
- **Class 1 is soft real-time only.** Plan ≥100 ms RPI on stock CPython (pylogix maintainer confirmed sub-100 ms unreliable due to socket-timing jitter). Tighter timing = OpENer RT recipe (PREEMPT_RT, SCHED_FIFO, mlock) in a dedicated process.

## 15. External tools & license firewall

For an Apache-2.0 library shipped to clients, classify every borrow by what the license permits:

| Tool | License | Use |
|---|---|---|
| OpENer (EIPStackGroup) | OpENer license (verify) | Class 1 architecture/sequence blueprint + RT recipe. Verify before copying code. |
| cpppo (pjkundert) | GPLv3 + commercial | Test oracle + `enip_server` Logix simulator. **Run as separate process; never copy source.** |
| libplctag | MPL-2.0 / LGPL-2+ | Optional native backend as a **separate dependency** (MPL is file-level; won't infect Apache code). Second oracle; PLC-5/SLC/Micro path. |
| Pymodbus | BSD-3 | Architecture template (sync+async client+server, framer, payload codec). Patterns copy-safe. |
| asyncua (opcua-asyncio) | LGPL-3 | Pattern reference (async-first + sync wrapper + subscriptions); or separate dep for OPC UA republish. |
| scapy-cip-enip (Airbus) | GPLv2 | Fuzzing + dissection cross-check. Separate tool. |
| Wireshark ENIP/CIP dissector | GPLv2 | Decode spec oracle. Reference only. |
| Eclipse Tahu | EPL/Apache | Sparkplug B reference (copy-friendly). |
| OpenPLC | GPLv3 | Closed-loop soft-PLC test target (separate process). |
| construct | MIT | Optional declarative binary parsing (copy-safe). |

**Firewall rule:** copy-able = MIT/BSD/Apache (Pymodbus, construct, Tahu) · separate-dependency-only = MPL/LGPL (libplctag, asyncua) · reference / run-as-tool only = GPL (cpppo, scapy, OpenPLC, Wireshark) · verify-first = OpENer.

**Highest-leverage borrow:** cpppo `enip_server` as simulator/oracle + OpENer as Class 1 blueprint — de-risks the two hardest phases (parity testing, implicit I/O).

---

## 16. License strategy — verdict (v0.3.1)

**Keep Apache-2.0. No feature is license-gated.** The firewall blocks only one path — copying GPL source — not any capability. The CIP/EtherNet-IP protocol is an open ODVA standard (not copyrightable), the bases are permissive, your PCCC/CSP stack is yours, and GPL tools remain fully usable as separate-process oracles/simulators. Everything — Class 1, PCCC, CSP, sim-server, broker, CIP Security — is buildable clean under Apache-2.0.

**Do NOT switch to GPL/AGPL.** The only thing copyleft would unlock is direct copying of cpppo source (modest savings on the sim-server). Copyleft is contagious to distribution; you ship to clients and build commercial tooling on top, so it would force source disclosure to deliverables and poison the consulting/commercial model. Net-negative trade.

**Clean-room discipline (enforced in Phase 5/6 prompts).** Where cpppo (GPLv3) / OpENer are the best references — the Class 1 loop and the CIP sim-server — treat them as black-box behavioral oracles: run them, capture wire traffic, match bytes, and write code from the public ODVA spec. Do not transcribe their source. Keeps Apache provenance clean and audit-defensible.

**Only real acquisition question:** ODVA spec access (membership/purchase) for the CIP Security stretch goal. Not a v1 blocker.

**Reserve option (not now):** if hard-real-time Class 1 becomes mandatory, add a native sidecar for the cyclic UDP (libplctag MPL/LGPL as a separate dep, or a bespoke C helper) — an architecture change, not a license change.

*Not legal advice; recommend a brief IP-attorney pass over the final dependency manifest before first client delivery.*

---

## 17. daedalus-tune (PID tuning) — integration & scope split (v0.4)

**Trigger:** HIS PID research (`PID-Tuning-Tools-Comprehensive-Research.md`, incl. an Opus verification pass) + §13–16 assessment. Independent convergence on: gain form/units conversion is the silent killer; safety is the centerpiece; FOPDT + Lambda/IMC is production-grade (v1 needs no ML).

### Scope split
**Into daedalus CORE** (built once; reused for every write):
- PIDE / P_PIDE-aware **safe-write driver**: mode-handoff state machine (`ProgProgReq` → `ProgManualReq` → confirm `.Manual`/`.ProgOper` → seed `.CVProgEU` = `.CVEU` for bumpless → operate on `.CVProgEU`, **never `.CVEU`**).
- Command-source arbitration (detect being silently outranked by HMI/Operator/Maintenance).
- `.DependIndependent` detection + gain-form/units conversion (`IGain = Kc/Ti` in **1/sec**, `DGain = Kc·Td` in **sec**; textbook rules emit dependent form in minutes).
- Library/firmware-rev detection (`Cfg_PGain` vs `.PIDE.PGain`; PlantPAx 5.0 naming) → branch adapter at runtime.
- Read-back after every non-atomic CIP write; guaranteed ownership-release on exit (`finally` → `ProgOperReq`/`PCmd_Rel`).
- Staged gain write: **stage → confirm → commit** through the write-safety gate + audit log; snapshot prior gains for one-click revert.
- Resolves research Gaps #1, #2.

**daedalus-tune SIBLING package** (depends on daedalus; heavy deps allowed here):
- Step-test orchestration; FOPDT/SOPDT identification (scipy / python-control).
- Multi-method gain calc: ZN, Cohen-Coon, CHR, Lambda/IMC, AMIGO, SIMC, Tyreus-Luyben.
- Closed-loop validation harness: IAE/ITAE, overshoot, settling — **Ms ≤ ~1.4–2.0 baked into the objective** (robustness, not just error).
- Optional Bayesian-optimization layer (Ax/BoTorch/Optuna), GP surrogate + EI/UCB over 3-D log-space gains, 20–50 evals, seeded from Lambda result. **Not deep RL** for v1.
- Read-only **loop-performance monitor**: Harris/minimum-variance index, oscillation + valve-stiction detection, time-in-mode, saturation. Answers Gap #5; safe lead deliverable.
- LLM advisor: **read-only** — diagnose (stiction vs sizing vs tuning), recommend method, write NEXUS report. Never emits a gain to a PLC.

### Shipped deliverables beyond Python
- **PLC-side watchdog AOI/rung** — agent increments a heartbeat; PLC forces Operator mode (+ safe CV) on stale heartbeat. Non-negotiable: Python cannot fail itself safe.
- **sim-server process-model plugin** — FOPDT/SOPDT/state-space + dead time + noise + disturbance, seeded from the real loop's identified model; explicit sim-to-real fidelity check. Resolves Gaps #3, #4. Without this, closed-loop validation and any ML are theater.

### Rockwell autotuner = oracle, not competitor
Read `PIDE_AUTOTUNE` (Gain/τ/θ + Slow/Med/Fast gains); cross-check daedalus-tune's identification and tuning against it. Verification, not a target to beat.

### Differentiation (NOT ML)
Legacy PLC-5/SLC reach (PCCC/CSP), safety-gated/audited/revertible deployment, fleet loop-performance monitoring, custom-objective BO. RL meta-policy in sim = research track only (v2+), gated on sim-to-real fidelity.

### Sequencing
1. **Now (zero-risk, read-only / sim — pylogix or daedalus read path):** logger, FOPDT ID, gain calc, loop-performance monitor, sim validation.
2. **Gated on daedalus safe-write driver:** live mode-handoff + staged gain commit. Build the write path **once**, in daedalus.

### Borrow (license-checked)
| Tool | License | Use |
|---|---|---|
| python-control | BSD-3 | TF, sim, stability margins (Ms). Dependency. |
| sysidentpy | BSD-3 | NARMAX/ARX ID beyond FOPDT. |
| SIPPY | LGPL | MIMO subspace ID — separate dep. |
| pyPIDTune / pytunelogix | MIT | EtherNet/IP tuning-workflow reference; borrowable (verify). |
| simple-pid (m-lundberg) | MIT | Reference PID implementation. |
| GEKKO/APMonitor | **verify** | Opt-based ID. **Force local solver — older versions defaulted to a remote APMonitor server; no client data off-box.** |
| Ax / BoTorch / Optuna | MIT/BSD | Bayesian optimization. |
| Tuning rules (ZN/CC/Lambda/AMIGO/SIMC/T-L) | textbook | Implement clean-room from published refs. |

### Non-goals
LLM never writes gains; no deep-RL on live loops; never tune safety-interlocked loops.

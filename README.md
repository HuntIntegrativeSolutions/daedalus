<p align="center">
  <img src="assets/branding/daedalus-banner.png" alt="daedalus" width="640">
</p>

# daedalus

A unified Allen-Bradley / EtherNet-IP library for Python, built on a **sans-I/O protocol core**.

> **Status:** Phases 0–2 complete. `LogixDriver` reads, writes, enumerates tags, and
> decodes UDTs against a live Logix controller. Phase 3 (asyncio) is next.

## What it is

`daedalus` ports the protocol cores of [`pycomm3`](https://github.com/ottowayi/pycomm3)
(MIT, unmaintained) and [`pylogix`](https://github.com/dmroeder/pylogix) (Apache-2.0)
into a fresh, layered codebase, then adds the capabilities neither library has:
asyncio, Class 1 implicit I/O, a deterministic scheduler, a write-safety policy gate,
typed codegen with L5X reconciliation, and modern packaging.

The defining design rule: **the wire codec, session, and drivers (layers L0–L3) never
touch a socket.** Transports are pluggable shells around one codec, so sync, asyncio,
and Class 1 are *additions* to a single protocol stack rather than parallel forks. A
CI firewall test enforces this at AST level on every commit.

## Safety posture

`daedalus` is **read-only unless explicitly armed.** Every write passes through a
policy gate (read-only mode, allow/denylist, dry-run, critic hook) with read-back
verify and an immutable audit log. It will never perform online program edits,
firmware flashing, program download, or keyswitch changes, and treats GuardLogix
safety tags as read-only.

## Quick start

```python
from daedalus.drivers import LogixDriver
from daedalus.session import Session
from daedalus.transport import SyncTcpTransport
from daedalus.packets.cip import backplane_path
from daedalus import WritePolicy, WriteMode

# --- connect ---
transport = SyncTcpTransport("10.0.0.11", 44818)
transport.connect()
session = Session()
transport.send_frame(session.register_request())
session.register_reply(transport.recv_frame())

conn_path = backplane_path(slot=0)
transport.send_frame(session.forward_open_request(large=False, connection_path=conn_path))
session.forward_open_reply(transport.recv_frame())

def send_recv(req: bytes) -> bytes:
    transport.send_frame(req)
    return transport.recv_frame()

driver = LogixDriver(session, send_recv)

# --- read ---
tag = driver.read_tag("MyDINT")           # Tag(value=42, type='DINT', error=None)
tags = driver.read_tags(["A", "B", "C"]) # multi-service packet (one round-trip)

# --- enumerate tags ---
tag_list = driver.get_tag_list()          # list[TagInfo] -- controller + program scope

# --- write (must arm first) ---
with driver.armed():
    driver.write_tag("MyDINT", 99)

# --- write with policy ---
policy = WritePolicy(mode=WriteMode.ARMED, allowlist={"MyDINT", "MyREAL"})
driver2 = LogixDriver(session, send_recv, policy=policy)
with driver2.armed():
    driver2.write_tag("MyDINT", 99)
```

> Connection identifiers (`connection_serial`, `to_connection_id`) are randomized on
> every `forward_open_request()` call — reconnecting after an unclean disconnect never
> hits CIP status `0x0100` ("connection in use").

## Features (Phases 0–2)

| Capability | Details |
|---|---|
| **Codec (L0)** | `DataType` metaclass, CIP services/objects/status, EtherNet/IP encapsulation, CIP segments/EPATH |
| **Session (L2)** | RegisterSession, Forward/Large Forward Open with fallback, Forward Close; randomized connection IDs per reconnect |
| **Sync transport (L1)** | TCP framing (`SyncTcpTransport`), length-prefix send/recv |
| **LogixDriver (L3)** | `read_tag`, `read_tags` (MSP), `write_tag`, `write_tags`, `get_tag_list` |
| **UDT / template decode** | Flat structs, nested UDTs, array members, BOOL bit-aliasing, STRING; lazy template fetch + cache |
| **Fragmented reads** | Large tags assembled via Read Tag Fragmented (service 0x52) |
| **Write-safety gate (L4)** | `WritePolicy` — READ_ONLY default, ARMED/DRY_RUN modes, allow/denylist, critic hook, read-back verify, audit log |
| **Tag types** | BOOL, SINT, INT, DINT, LINT, USINT, UINT, UDINT, REAL, LREAL, STRING, arrays of any atomic, UDTs |
| **Sans-I/O firewall** | AST-checked CI test: socket/ssl/asyncio/anyio cannot appear in L0–L3 |

## Roadmap

| Phase | Status | Deliverable |
|---|---|---|
| 0 | done | Scaffold — repo, packaging, CI, license/NOTICE |
| 1 | done | Codec — sans-I/O wire layer (types, services, status, packets, segments) |
| 2 | done | Sync parity — transport + session + `LogixDriver` (parity vs pycomm3) |
| 3 | planned | Async — asyncio transport + `AsyncLogixDriver` on the same codec |
| 4 | planned | Runtime — scheduler, change-of-state, write-safety gate (extensions) |
| 5 | planned | PCCC / PLC-5 — `SLCDriver` -> `PLC5Driver` (EtherNet/IP PCCC + CSP) |
| 6 | planned | Class 1 — implicit cyclic I/O (UDP, RPI, run/idle, sequence) |
| 7 | planned | Typed / L5X — codegen + online<->offline reconciliation |
| 8 | planned | Hardening — observability, resilience, MicroPython slim build |
| 9 | planned | Integrations — digital-twin bridge, NEXUS/TALOS adapters |

See [`ARCHITECTURE.md`](ARCHITECTURE.md) for the layer model and
[`eip-library-master-plan.md`](eip-library-master-plan.md) for the full spec.

## Testing

498 unit + offline integration tests; 12 skipped (live hardware tier):

```
tests/
  cip/           -- L0 codec unit tests (data types, segments, templates, services, status)
  packets/       -- encapsulation + CIP message builders
  session/       -- Session state machine, Forward_Open/Close, connection-identity randomization
  transport/     -- SyncTcpTransport + Forward_Open end-to-end (against in-process CIP sim)
  drivers/       -- LogixDriver unit + offline e2e + parity + live tiers
  runtime/       -- WritePolicy gate unit tests
  sim/           -- in-process CIP simulation server (used by offline e2e tests)
  test_parity_oracle.py     -- byte-exact parity vs pycomm3
  test_sans_io_firewall.py  -- AST-level L0-L3 socket import enforcement
  test_write_firewall.py    -- anyio must not leak into driver imports
```

## License

Apache-2.0. See [`LICENSE`](LICENSE) and [`NOTICE`](NOTICE) (preserves the pycomm3 MIT
and pylogix Apache-2.0 attributions).

## Development

**Prerequisites:** [uv](https://docs.astral.sh/uv/)

```bash
# Install all dependencies (base + typed + oracle extras + dev tools)
uv sync --extra typed --extra oracle

# Install pre-commit hooks
uv run pre-commit install

# Run tests
uv run pytest

# Lint
uv run ruff check .

# Format check
uv run ruff format --check .

# Type check
uv run mypy

# Live hardware tests (optional -- requires a Logix controller or Logix Emulate)
DAEDALUS_TEST_PLC=10.0.0.11/0 uv run pytest -m live

# Live write tests (double-gated -- also requires an explicit scratch tag)
DAEDALUS_TEST_PLC=10.0.0.11/0 DAEDALUS_TEST_WRITE_TAG=MyScratchDINT uv run pytest -m live_write
```

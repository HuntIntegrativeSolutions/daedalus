# Architecture

## The sans-I/O principle

`daedalus` is built on a strict layered architecture where **the wire codec, session
state machine, and driver request builders (L0–L3) never touch a socket.** Transports
are pluggable shells around one shared codec, so sync TCP, asyncio TCP, UDP Class 1,
and CSP are *additions* to a single protocol stack rather than parallel forks.

This is the single most important rule and the reason the project exists. Any socket
call leaking into L0–L3 breaks the model and forces two parallel codebases — exactly
the failure mode this design exists to prevent.

## L0–L5 layer model

```
┌────────────────────────────────────────────────────────────────┐
│ L5  Integrations                                                │
│     NEXUS/TALOS adapters · digital-twin bridge                  │
│     OPC UA / MQTT republish · MicroPython slim build            │
├────────────────────────────────────────────────────────────────┤
│ L4  Runtime                                                     │
│     Async scheduler · change-of-state / subscriptions          │
│     Class 1 I/O manager · write-safety policy gate             │
│     Typed codegen + L5X reconciliation · observability         │
├────────────────────────────────────────────────────────────────┤
│ L3  Drivers  (I/O-FORBIDDEN)                                    │
│     LogixDriver · SLCDriver · PLC5Driver · GenericCIPDriver     │
├────────────────────────────────────────────────────────────────┤
│ L2  Session  (I/O-FORBIDDEN)                                    │
│     register session · Forward/Large-Forward Open + fallback   │
│     connection-size negotiation · keepalive · reconnect        │
├────────────────────────────────────────────────────────────────┤
│ L1  Transports  (the ONLY place sockets live)                   │
│     sync TCP · asyncio TCP · UDP Class 1 · CSP (port 2222)     │
├────────────────────────────────────────────────────────────────┤
│ L0  Wire codec  (I/O-FORBIDDEN)                                 │
│     DataType system · CIP services / objects / status          │
│     EtherNet/IP encapsulation · CIP segments / EPATH           │
└────────────────────────────────────────────────────────────────┘
```

## Package → layer → I/O-policy table

| Package | Layer | I/O policy |
|---|---|---|
| `src/daedalus/cip/` | L0 wire codec | **I/O-FORBIDDEN** |
| `src/daedalus/packets/` | L0 encapsulation framing | **I/O-FORBIDDEN** |
| `src/daedalus/session/` | L2 sans-I/O state machine | **I/O-FORBIDDEN** |
| `src/daedalus/drivers/` | L3 request building | **I/O-FORBIDDEN** |
| `src/daedalus/transport/` | L1 transports | I/O allowed (the ONLY place sockets live) |
| `src/daedalus/runtime/` | L4 scheduler / write-safety | anyio allowed |

**I/O-FORBIDDEN** means: no module-level or nested import of `socket`, `ssl`,
`asyncio`, `anyio`, `selectors`, `socketserver`, `http`, `urllib`, or `requests`.

## Transport contract

A driver (L3) produces *bytes to send* and a state object. An L1 transport moves
those bytes over the wire and feeds reply bytes back into the state machine. The
same L0–L3 code is driven by a blocking loop, an asyncio task, or a UDP Class 1
cyclic producer. No driver is ever aware of which transport is beneath it.

## Automated enforcement — `test_sans_io_firewall.py`

`tests/test_sans_io_firewall.py` walks every `*.py` file under the four
I/O-FORBIDDEN subpackages using Python's stdlib `ast` module and asserts that
no `import` or `from ... import` statement references a forbidden module. The test
is parametrized per source file so a CI failure names the exact file and offending
module.

The test passes trivially during Phase 0 (the packages are empty docstring stubs),
but the harness is in place and will catch violations the moment real protocol code
is added in Phase 1.

To run locally:

```bash
uv run pytest tests/test_sans_io_firewall.py -v
```

## Safety constraints

See `CLAUDE.md` for the full set of hard non-goals: read-only-by-default write policy,
no online program edits or firmware flashing, GuardLogix safety tags read-only.

## Branding

`assets/branding/daedalus-icon.png` is the intended mkdocs-material logo and favicon
for the documentation site (Phase 8). It is noted here to avoid re-litigating the
choice when the docs scaffold is added.

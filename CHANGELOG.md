# Changelog

All notable changes to this project will be documented in this file.

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning: [SemVer](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Phase 2c LogixDriver connected tag READ.
  - `src/daedalus/tag.py` — unified `Tag` result type (`tag_name`, `value`,
    `type_code`, `status`, `error`); read-only `.type` property (`"DINT"`,
    `"STRUCT"`, etc.); pycomm3 (`.value/.error/.type`) and pylogix
    (`.TagName/.Value/.Status`) attribute conventions both satisfied.
  - `src/daedalus/drivers/_logix.py` — `LogixDriver` (L3, sans-I/O): module-level
    pure helpers `_extract_connected_cip`, `_decode_read_reply`, `_parse_msp_reply`
    (Phase 3 reuse point); `read_tag()` with automatic fragmented-read loop
    (`READ_TAG_FRAGMENTED 0x52`, `UDINT` byte offset); `read_tags()` scalar batch
    via Multiple Service Request (0x0A); `send_recv: Callable[[bytes], bytes]`
    injection keeps the module I/O-free (sans-I/O firewall passes).
  - `src/daedalus/session/_session.py` — Class 3 sequence counter
    (`next_sequence_count()`; pre-increment, wraps at 0xFFFF; reset on
    Forward_Open and Forward_Close).
  - `src/daedalus/__init__.py` — re-exports `Tag`.
  - `src/daedalus/drivers/__init__.py` — re-exports `LogixDriver`.
  - `tests/sim/server.py` — extended CipSimServer with SendUnitData (0x70)
    handler; per-connection state (`ot_connection_id`); Read Tag (0x4C),
    Read Tag Fragmented (0x52), Multiple Service Packet (0x0A) service handlers;
    `tag_store` / `frag_threshold` constructor params.
  - `tests/conftest.py` — `make_tag_server` factory fixture.
  - `tests/drivers/test_logix_driver.py` — 22 unit tests (no sockets): Tag
    properties, `_decode_read_reply` helpers, `_parse_msp_reply`, `LogixDriver`
    single-tag and batch reads including fragmented accumulation and per-tag
    error capture.
  - `tests/drivers/test_logix_e2e.py` — 8 end-to-end tests through real TCP +
    sim: DINT, REAL, array, struct (0x02A0), multi-read (MSP), fragmented,
    full lifecycle, missing-tag-in-MSP captured not raised.
  - `tests/session/test_session.py` — 5 sequence-counter tests.
  - `tests/test_parity_oracle.py` — full READ_TAG byte parity (service + path +
    element count) and MSP wrapper parity vs. pycomm3 over 5 parametrized cases.
  - 332 tests total (1 skipped).

- Phase 2b Forward_Open / Large_Forward_Open + fallback.
  - `src/daedalus/packets/forward_open.py` — pure builders and parsers for
    Forward_Open (0x54), Large_Forward_Open (0x5B), and Forward_Close (0x4E);
    `ForwardOpenReply` frozen dataclass; `parse_forward_open_reply()` (with
    `was_large` flag that raises `LargeForwardOpenRejected` on CIP status 0x08
    and `ForwardOpenError` for any other non-zero status);
    `parse_forward_close_reply()`.  Default parameters match pycomm3's static
    cfg defaults (RPI = 0x00204001 µs, T→O conn ID = 0x71190427, etc.).
  - `src/daedalus/exceptions.py` — `ForwardOpenError` (ResponseError subclass)
    and `LargeForwardOpenRejected` (ForwardOpenError subclass, typed signal for
    the standard-FO fallback path).
  - `src/daedalus/session/_session.py` — three new states (CONNECTING,
    CONNECTED, CLOSING); `forward_open_request()` / `forward_open_reply()` /
    `forward_close_request()` / `forward_close_reply()` with h11-style
    emit/feed contract; typed fallback: `LargeForwardOpenRejected` resets
    state to REGISTERED so the caller can immediately retry with
    `forward_open_request(large=False)` — fallback decision stays in the
    sans-I/O layer; new properties `connected`, `ot_connection_id`,
    `connection_serial`.
  - `tests/sim/server.py` — extended CipSimServer to handle SendRRData (0x6F):
    dispatches Forward_Open / Large_Forward_Open (success or CIP-status-0x08
    error via `reject_large_fo=True` flag) and Forward_Close; `build_cpf`-based
    reply builder.
  - `tests/conftest.py` — `sim_server_rejecting_large` fixture for fallback tests.
  - `tests/session/test_forward_open.py` — 26 unit tests covering state
    transitions, service-byte selection, error-path resets, fallback cycle,
    full lifecycle.
  - `tests/transport/test_forward_open_e2e.py` — 5 integration tests (large FO
    roundtrip, standard FO roundtrip, non-zero O→T connection ID, fallback to
    standard, FC then unregister).
  - `tests/test_parity_oracle.py` — two FO parity cases: standard (0x54, UINT
    net_params) and large (0x5B, UDINT net_params) byte-identical to pycomm3.

- Phase 2a Sync Transport: RegisterSession / UnregisterSession vertical slice.
  - `src/daedalus/session/_session.py` — `Session` sans-I/O state machine
    (`IDLE → REGISTERING → REGISTERED → IDLE`); h11-style emit/feed contract:
    `register_request()` returns bytes to send, `register_reply(frame)` feeds
    the device's reply back in and advances state, `unregister_request()`
    resets state immediately (no reply expected per ODVA).
  - `src/daedalus/transport/_tcp.py` — `SyncTcpTransport`, the first socket in
    the repo: `send_frame()` / `recv_frame()` byte-mover with looping `recv`
    and OS-error → `CommError` wrapping; context-manager support.
  - `tests/sim/server.py` — `CipSimServer`: in-process TCP server on an
    ephemeral port (daemon thread); assigns cryptographically random session
    handles and closes the connection on `UnregisterSession` per ODVA.
  - End-to-end round-trip test proving the sans-I/O contract: `Session` drives
    `SyncTcpTransport` against `CipSimServer`; 23 new tests covering state
    transitions, error paths, and transport edge cases.

- Phase 1 L0 wire codec: full EtherNet/IP + CIP codec adapted from pycomm3 (MIT).
  - `src/daedalus/exceptions.py` — `DaedalusError` hierarchy (`CommError`,
    `DataError`, `BufferEmptyError`, `ResponseError`, `RequestError`).
  - `src/daedalus/cip/constants.py` — pure wire constants (HEADER_SIZE,
    PRIORITY, TIMEOUT_TICKS, etc.).
  - `src/daedalus/cip/data_types.py` — `DataType[T]` metaclass system; all CIP
    elementary / string / bit-array types; `Array()` and `Struct()` factories;
    `DATA_TYPES_BY_CODE` / `DATA_TYPES_BY_NAME` registries; 5 pycomm3 bug fixes:
    Array-length-as-DataType, code collision 0xCC→LDT / 0xD6→FTIME, STRINGI
    symmetry (via `StringIEntry` dataclass), DATE_AND_TIME 6-byte size, BOOL
    canonical 0xFF encoding.
  - `src/daedalus/cip/segments.py` — `LogicalSegment`, `PortSegment`,
    `DataSegment`, `EPATH` / `PADDED_EPATH` / `PACKED_EPATH` with full
    encode+decode (pycomm3 raised `NotImplementedError` on all decode paths);
    IPv6 rejection in `PortSegment`.
  - `src/daedalus/cip/services.py` — `EncapsulationCommand`, `CIPService`,
    `ConnectionManagerService` as `IntEnum`; `MULTI_PACKET_SERVICES`.
  - `src/daedalus/cip/object_library.py` — `ClassCode` (IntEnum), `Attribute`
    (NamedTuple), standard object attribute dicts.
  - `src/daedalus/cip/status.py` — `VENDORS` / `VENDOR_IDS`, `SERVICE_STATUS`,
    `EXTEND_CODES`, `decode_status()`, `get_vendor()`.
  - `src/daedalus/cip/custom_types.py` — `IPAddress`, `FixedSizeString`,
    `Revision`, `ModuleIdentityObject`, `ListIdentityObject`.
  - `src/daedalus/packets/encap.py` — `EncapsulationHeader` (24-byte
    `<HHII8sI`), `CPFItem`, `CPFTypeCode`, `build_cpf()`, `parse_cpf()`.
  - `src/daedalus/packets/cip.py` — `request_path()`, `tag_request_path()`,
    session/send builders, `parse_cip_response()`, `wrap_unconnected_send()`.
  - `tests/` — 212 tests (210 passing, 1 skipped, 1 xfail): Hypothesis
    round-trip properties, 26 golden vectors, parity oracle vs pycomm3, sans-I/O
    firewall.

- Phase 0 project scaffold: `pyproject.toml` (uv_build backend, PEP 639 license
  metadata), `src/` layout with six-layer package skeleton, GitHub Actions CI
  (lint/type-check, test matrix on Python 3.11/3.12/3.13, build + wheel
  verification), pre-commit config (ruff + mypy), and documentation
  (`ARCHITECTURE.md`, `CONTRIBUTING.md`, `SECURITY.md`).
- `tests/test_sans_io_firewall.py` — AST-based CI enforcement of the sans-I/O
  contract across `cip/`, `packets/`, `session/`, and `drivers/`.
- Project branding assets (banner, mascot, icon) under `assets/branding/`.

# Changelog

All notable changes to this project will be documented in this file.

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning: [SemVer](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

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

# Changelog

All notable changes to this project will be documented in this file.

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning: [SemVer](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Phase 2e: UDT / Template pipeline — `get_tag_list()` + `read_tag()` on struct tags
  now returns `{member: value}` dicts with nested UDT, array-member, and BOOL-bit support.
  - `src/daedalus/cip/templates.py` (new L0): `TemplateAttributes`, `RawMember`,
    `ResolvedMember`, `ResolvedTemplate` models; `parse_template_attr_reply` (parses
    GET_ATTRIBUTE_LIST makeup response); `parse_template_data` (one-level member-info +
    names blob; predefined-type name-pop mirrors pycomm3; `ASCIISTRING82` → `STRING`);
    `decode_struct` (offset-aware; handles BOOL bit-aliasing, arrays, nested UDTs,
    string structs; skips private members).
  - `src/daedalus/tag.py` — `udt_name: str | None` field; `Tag.type` returns resolved
    UDT name for struct reads (falls back to `"STRUCT"` when unresolved).
  - `src/daedalus/drivers/_logix.py` — template fetch pipeline: `_build_template_attr_request`
    (GET_ATTRIBUTE_LIST attrs 4,5,2,1), `_build_template_read_request` (READ_TAG with
    `(obj_def_size * 4) - 21 - offset` formula, DINT offset type); `_get_template` lazily
    fetches + parses + caches; `_maybe_resolve_struct` resolves via name-based lookup
    (`_tag_info_cache → template_instance_id`), caches reply handle, decodes; `get_tag_list`
    populates `_tag_info_cache`; `element_count > 1` on struct raises `DataError`.
  - `tests/sim/server.py` — `TemplateEntry` dataclass; `template_store` + continuation
    support for GET_ATTRIBUTE_LIST and READ_TAG on Template Object (class 0x6C); path
    parsing via `PADDED_EPATH.decode` (not a byte scanner).
  - `tests/cip/test_templates.py` — 21 L0 unit tests + Hypothesis round-trip for
    `parse_template_attr_reply`, `parse_template_data`, `decode_struct`.
  - `tests/drivers/test_udt_e2e.py` — 9 offline e2e tests: flat UDT, array member,
    BOOL bits, nested UDT, string, large-template continuation, no-tag-list fallback,
    MSP batch, array-of-struct guard.
  - `tests/drivers/test_parity_templates.py` — 13 request-bytes parity tests (hand-derived;
    no pycomm3 runtime calls).
  - `tests/drivers/test_udt_live.py` — env-gated live tier (`DAEDALUS_TEST_PLC`);
    CI-skipped; read-only; asserts decoded dict vs pycomm3 and logs empirical
    reply_handle vs makeup structure_handle finding.

- Phase 2d `LogixDriver.get_tag_list` — controller + program scope tag enumeration.
  - `src/daedalus/tag.py` — `TagInfo` dataclass: `tag_name`, `instance_id`,
    `is_struct`, `data_type` (atomic CIP type name or `None` for structs),
    `template_instance_id` (struct template ID for Phase 2e), `dimensions`
    (tuple: `()` scalar, `(n,)` 1D, `(n,m)` 2D, `(n,m,k)` 3D), `scope`
    (`"controller"` or `"Program:Main"`).
  - `src/daedalus/__init__.py` — re-exports `TagInfo`.
  - `src/daedalus/drivers/_logix.py` — Get Instance Attribute List (service 0x55)
    on Symbol Object (class 0x6B); 5 module-level pure helpers (Phase 3 reuse
    point): `_symbol_object_path`, `_build_tag_list_request`, `_is_system_tag`
    (exact port of pycomm3 `_isolate_user_tags` — I/O tags `:I`/`:O`/`:C`/`:S`
    are KEPT, not filtered), `_decode_symbol_type`, `_parse_tag_list_reply`
    (STRING = UINT-length + bytes, NO word-alignment padding); `get_tag_list()`
    orchestrates controller + discovered program scopes; `_get_scope_tag_list()`
    implements the continuation loop (status 0x06 → next at `last_instance+1`,
    status 0x00 → done); attr 10 (external access) deferred (firmware-gated).
  - `tests/sim/server.py` — extended `CipSimServer` with `symbol_store` + 
    `tag_list_frag_size` params; `_serialize_symbol_entry` (STRING.encode, no
    pad), `_extract_tag_list_path`, `_handle_tag_list` service handlers; full
    continuation (0x06 when chunk exceeds frag_size) supported.
  - `tests/conftest.py` — `make_symbol_server` factory fixture.
  - `tests/drivers/test_tag_list.py` — 24 unit tests: `_parse_tag_list_reply`
    (scalar, 1D/2D array, struct, program-scope collection, odd-length name
    parsing); `_is_system_tag` (9 cases including I/O tag kept, colon catch-all,
    system-flag bit); `_build_tag_list_request` (service byte, path shape, attr
    list, continuation instance); `LogixDriver.get_tag_list` stubs (continuation,
    program scope iteration, error raise, truncated payload).
  - `tests/drivers/test_tag_list_e2e.py` — 7 end-to-end tests through sim: DINT
    scalar, 1D array, struct, system tag excluded, program scope, multi-reply
    continuation assembly, full lifecycle (read_tag + get_tag_list together).
  - `tests/drivers/test_tag_list_live.py` — 2 env-gated live tests
    (`DAEDALUS_TEST_PLC=<ip>[/<slot>]`, CI-skipped): basic list shape + replay
    capture; `test_live_tag_set_parity_vs_pycomm3` diffs daedalus vs pycomm3
    tag-name sets on the same controller (definitive filter-parity gate).
  - `tests/fixtures/replay/` — directory for committed replay vectors.
  - `tests/test_parity_oracle.py` — 4 new parity tests: 3-case parametrized
    Get Instance Attribute List request bytes (controller scope, program scope,
    continuation) matched byte-for-byte against pycomm3's base-6 attribute form;
    parse-parity test verifies daedalus/pycomm3 agree on names/instance_ids/dims
    from a pycomm3-encoded synthetic payload, I/O tag present in both, system-
    flagged tag absent from daedalus result.
  - `pyproject.toml` — 3 new mypy overrides for new test modules.
  - 367 tests total (3 skipped).

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

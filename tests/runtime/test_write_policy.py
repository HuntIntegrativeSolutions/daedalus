"""Unit tests for WritePolicy + AuditSink — pure, no I/O, no sim, no driver.

Safety tests are the review priority: read-only-by-default, safety-tag refusal,
denylist/allowlist, dry-run semantics, audit record correctness, and clock
injectability.
"""

from __future__ import annotations

import time

import pytest

from daedalus.runtime.write_policy import (
    InMemorySink,
    WriteMode,
    WritePolicy,
    WriteRecord,
    default_safety_predicate,
)

# ---------------------------------------------------------------------------
# WriteMode defaults
# ---------------------------------------------------------------------------


def test_default_mode_is_read_only() -> None:
    assert WritePolicy().mode == WriteMode.READ_ONLY


# ---------------------------------------------------------------------------
# WritePolicy.evaluate — mode gate
# ---------------------------------------------------------------------------


def test_read_only_refuses_all() -> None:
    p = WritePolicy()
    allowed, reason = p.evaluate("SomeTag")
    assert not allowed
    assert reason is not None
    assert "READ_ONLY" in reason


def test_dry_run_passes_policy_gate() -> None:
    p = WritePolicy(mode=WriteMode.DRY_RUN)
    allowed, reason = p.evaluate("SomeTag")
    assert allowed
    assert reason is None


def test_armed_passes_policy_gate() -> None:
    p = WritePolicy(mode=WriteMode.ARMED)
    allowed, reason = p.evaluate("SomeTag")
    assert allowed
    assert reason is None


# ---------------------------------------------------------------------------
# WritePolicy.evaluate — safety tag
# ---------------------------------------------------------------------------


def test_safety_tag_refused_in_armed_mode() -> None:
    p = WritePolicy(mode=WriteMode.ARMED)
    allowed, reason = p.evaluate("SafetyTask:S.TagName")
    assert not allowed
    assert reason is not None
    assert "Safety" in reason or "safety" in reason.lower()


def test_safety_tag_refused_even_if_in_allowlist() -> None:
    p = WritePolicy(mode=WriteMode.ARMED, allowlist=frozenset({"SafetyTask:S.TagName"}))
    allowed, _ = p.evaluate("SafetyTask:S.TagName")
    assert not allowed


def test_normal_tag_not_flagged_as_safety() -> None:
    p = WritePolicy(mode=WriteMode.ARMED)
    allowed, _ = p.evaluate("NormalDINT")
    assert allowed


def test_io_tag_not_flagged_as_safety() -> None:
    p = WritePolicy(mode=WriteMode.ARMED)
    allowed, _ = p.evaluate("Local:1:I.Data")
    assert allowed


def test_custom_safety_predicate_injectable() -> None:
    always_safe: WritePolicy = WritePolicy(
        mode=WriteMode.ARMED,
        safety_predicate=lambda _: True,
    )
    allowed, reason = always_safe.evaluate("RegularTag")
    assert not allowed
    assert reason is not None
    assert "Safety" in reason or "safety" in reason.lower()


def test_permissive_safety_predicate_allows_all() -> None:
    p = WritePolicy(mode=WriteMode.ARMED, safety_predicate=lambda _: False)
    allowed, _ = p.evaluate("SafetyTask:S.Tag")  # would normally be refused
    assert allowed


# ---------------------------------------------------------------------------
# WritePolicy.evaluate — denylist / allowlist
# ---------------------------------------------------------------------------


def test_denylist_blocks_listed_tag() -> None:
    p = WritePolicy(mode=WriteMode.ARMED, denylist=frozenset({"BadTag"}))
    allowed, reason = p.evaluate("BadTag")
    assert not allowed
    assert reason is not None
    assert "denylist" in reason


def test_denylist_allows_other_tag() -> None:
    p = WritePolicy(mode=WriteMode.ARMED, denylist=frozenset({"BadTag"}))
    allowed, _ = p.evaluate("GoodTag")
    assert allowed


def test_allowlist_blocks_unlisted() -> None:
    p = WritePolicy(mode=WriteMode.ARMED, allowlist=frozenset({"WhiteTag"}))
    allowed, reason = p.evaluate("OtherTag")
    assert not allowed
    assert reason is not None
    assert "allowlist" in reason


def test_allowlist_permits_listed() -> None:
    p = WritePolicy(mode=WriteMode.ARMED, allowlist=frozenset({"WhiteTag"}))
    allowed, _ = p.evaluate("WhiteTag")
    assert allowed


def test_none_allowlist_permits_all() -> None:
    p = WritePolicy(mode=WriteMode.ARMED, allowlist=None)
    allowed, _ = p.evaluate("AnyTag")
    assert allowed


# ---------------------------------------------------------------------------
# default_safety_predicate
# ---------------------------------------------------------------------------


def test_default_predicate_detects_colon_s_scope() -> None:
    assert default_safety_predicate("SafetyTask:S.SomeTag") is True


def test_default_predicate_detects_trailing_colon_s() -> None:
    assert default_safety_predicate("SafetyTask:S") is True


def test_default_predicate_rejects_normal_tag() -> None:
    assert default_safety_predicate("NormalTag") is False


def test_default_predicate_rejects_io_colon_i() -> None:
    assert default_safety_predicate("Local:1:I.Data") is False


def test_default_predicate_rejects_io_colon_o() -> None:
    assert default_safety_predicate("Local:1:O.Data") is False


def test_default_predicate_case_insensitive() -> None:
    assert default_safety_predicate("task:s.tag") is True
    assert default_safety_predicate("TASK:S.TAG") is True


# ---------------------------------------------------------------------------
# WriteRecord
# ---------------------------------------------------------------------------


def test_write_record_is_frozen() -> None:
    rec = WriteRecord("committed", "Tag", "who", 1.0, b"", None, None)
    with pytest.raises((AttributeError, TypeError)):
        rec.outcome = "denied"  # type: ignore[misc]


def test_write_record_old_bytes_none_for_pre_stage_denial() -> None:
    rec = WriteRecord("denied", "Tag", "who", 1.0, b"", None, "READ_ONLY")
    assert rec.old_bytes is None


# ---------------------------------------------------------------------------
# AuditSink / InMemorySink
# ---------------------------------------------------------------------------


def test_in_memory_sink_records_are_ordered() -> None:
    sink = InMemorySink()
    r1 = WriteRecord("committed", "T1", "who", 1.0, b"\x01", b"\x00", None)
    r2 = WriteRecord("denied", "T2", "who", 2.0, b"", None, "READ_ONLY")
    sink.append(r1)
    sink.append(r2)
    records = sink.records()
    assert records == [r1, r2]


def test_in_memory_sink_records_returns_copy() -> None:
    sink = InMemorySink()
    sink.append(WriteRecord("denied", "T", "who", 1.0, b"", None, "x"))
    copy = sink.records()
    copy.clear()
    assert len(sink.records()) == 1


def test_write_policy_audit_and_get_records() -> None:
    p = WritePolicy(mode=WriteMode.ARMED)
    rec = WriteRecord("dry_run", "Tag", "test", 1.0, b"\x2a\x00\x00\x00", None, None)
    p.audit(rec)
    records = p.get_records()
    assert len(records) == 1
    assert records[0] is rec


def test_write_policy_get_records_empty_on_custom_sink() -> None:
    class _CustomSink:
        def append(self, record: WriteRecord) -> None:
            pass

    p = WritePolicy(sink=_CustomSink())
    p.audit(WriteRecord("committed", "T", "who", 1.0, b"", b"", None))
    assert p.get_records() == []


# ---------------------------------------------------------------------------
# Clock
# ---------------------------------------------------------------------------


def test_default_clock_returns_wall_time() -> None:
    p = WritePolicy()
    before = time.time()
    t = p.clock()
    after = time.time()
    assert before <= t <= after


def test_default_clock_is_not_monotonic() -> None:
    # Monotonic clock gives values relative to an arbitrary epoch; wall-clock
    # gives POSIX timestamp. The wall clock value should be >> 1e9 (seconds
    # since 1970); monotonic values are typically small (seconds since boot).
    p = WritePolicy()
    # 2020-01-01 in POSIX seconds
    assert p.clock() > 1_577_836_800.0


def test_injected_clock_used_in_evaluate() -> None:
    fixed_time = 42.0
    p = WritePolicy(clock=lambda: fixed_time)
    assert p.clock() == 42.0


def test_audit_uses_injected_clock() -> None:
    p = WritePolicy(mode=WriteMode.ARMED, clock=lambda: 99.0)
    rec = p._make_record("committed", "Tag", b"\x01", b"\x00", None)
    p.audit(rec)
    assert p.get_records()[0].when == 99.0


# ---------------------------------------------------------------------------
# _make_record / deny helpers
# ---------------------------------------------------------------------------


def test_make_record_stamps_who_and_when() -> None:
    p = WritePolicy(who="operator_1", clock=lambda: 1234.0)
    rec = p._make_record("committed", "Tag", b"\x01", b"\x00", None)
    assert rec.who == "operator_1"
    assert rec.when == 1234.0


def test_deny_appends_denied_record() -> None:
    p = WritePolicy()
    p.deny("Tag", "READ_ONLY")
    records = p.get_records()
    assert len(records) == 1
    assert records[0].outcome == "denied"
    assert records[0].reason == "READ_ONLY"


def test_deny_all_records_every_tag() -> None:
    p = WritePolicy()
    p._deny_all(["Tag1", "Tag2", "Tag3"], "batch refused")
    records = p.get_records()
    assert len(records) == 3
    assert all(r.outcome == "denied" for r in records)
    assert {r.tag_name for r in records} == {"Tag1", "Tag2", "Tag3"}

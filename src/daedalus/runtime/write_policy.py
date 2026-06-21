"""Write-safety policy gate and audit log — Layer 4 (runtime).

Pure Python: no anyio, no socket, no forbidden imports.
Importable by L3 (drivers) without pulling async machinery into the driver
import chain (critical for the MicroPython slim build).

Default mode is READ_ONLY — no writes until explicitly armed.  Use
``driver.armed()`` as a context manager; it guarantees mode reverts on exit
even when the body raises.  The driver's ``write_tag`` never self-disarms —
arming is always batch/context-scoped.
"""

from __future__ import annotations

import enum
import time as _time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

__all__ = [
    "AuditSink",
    "InMemorySink",
    "WriteMode",
    "WritePolicy",
    "WriteRecord",
    "default_safety_predicate",
]


class WriteMode(enum.Enum):
    """Operational mode for a WritePolicy."""

    READ_ONLY = "read_only"
    DRY_RUN = "dry_run"
    ARMED = "armed"


@dataclass(frozen=True)
class WriteRecord:
    """Immutable audit entry for one write attempt (committed, denied, or dry-run)."""

    outcome: str
    """One of: ``"committed"`` | ``"denied"`` | ``"dry_run"`` | ``"verify_failed"``."""
    tag_name: str
    who: str
    when: float
    """Wall-clock timestamp (time.time() or an injectable clock callable)."""
    intended_bytes: bytes
    """Encoded value bytes the driver intended to write (empty for pre-stage denials)."""
    old_bytes: bytes | None
    """Encoded prior value read in the stage step; None when denied before stage."""
    reason: str | None
    """Denial or error message; None on success (committed)."""


class AuditSink(Protocol):
    """Protocol for audit record storage backends."""

    def append(self, record: WriteRecord) -> None: ...


class InMemorySink:
    """Non-durable in-memory audit sink (default).

    Append-only: records() returns a copy; the internal list is never shrunk.

    For production use, inject a durable sink (e.g. a file-based implementation
    that appends JSONL lines) via ``WritePolicy(sink=my_file_sink)``.  The
    ``AuditSink`` Protocol is the durability seam.
    """

    def __init__(self) -> None:
        self._records: list[WriteRecord] = []

    def append(self, record: WriteRecord) -> None:
        self._records.append(record)

    def records(self) -> list[WriteRecord]:
        """Return a copy of all records in insertion order."""
        return list(self._records)


SafetyPredicate = Callable[[str], bool]


def default_safety_predicate(tag_name: str) -> bool:
    """Conservative default: tags with ``:S.`` or ``:S`` at end of name are safety.

    GuardLogix safety tags in Logix 5000 are accessed via a path that contains
    ``:S`` as a path separator (e.g. ``SafetyTask:S.TagName``).  This heuristic
    checks for that pattern case-insensitively.

    Limitations:
    - Does not guarantee detection of all safety-context tags on all controllers.
    - False negatives are possible if the controller uses a non-standard naming
      convention for safety programs.
    - For safety-critical applications, replace this predicate with a
      hardware-verified rule derived from program documentation.
    """
    upper = tag_name.upper()
    return ":S." in upper or upper.endswith(":S")


@dataclass
class WritePolicy:
    """Gate for all write operations through LogixDriver.

    Default mode is READ_ONLY — no writes possible until explicitly armed.
    Use ``driver.armed()`` as a context manager; it guarantees mode reverts on
    exit (the driver's ``write_tag`` never self-disarms).

    Attributes:
        mode:             Current operational mode.
        allowlist:        If set, only these tag names are permitted (None = all).
        denylist:         These tag names are always refused.
        critic:           Optional callable for batch approval.  Receives the
                          full list of tag names before any commit.  Return True
                          to approve; return str (reason) or False to veto.
        who:              Actor identity string recorded in every audit entry.
        clock:            Callable returning wall-clock seconds (float).
                          Defaults to ``time.time``; inject a fixed value for
                          deterministic tests.
        safety_predicate: Callable returning True for safety tags.  Injectable
                          for testing or for hardware-verified rules.
        sink:             AuditSink implementation.  Defaults to InMemorySink
                          (non-durable).  Inject a durable sink for production.
    """

    mode: WriteMode = WriteMode.READ_ONLY
    allowlist: frozenset[str] | None = None
    denylist: frozenset[str] = field(default_factory=frozenset)
    critic: Callable[[list[str]], bool | str] | None = None
    who: str = "unknown"
    clock: Callable[[], float] = field(default_factory=lambda: _time.time)
    safety_predicate: SafetyPredicate = field(default_factory=lambda: default_safety_predicate)
    sink: AuditSink = field(default_factory=InMemorySink)

    def evaluate(self, tag_name: str) -> tuple[bool, str | None]:
        """Cheap pre-I/O gate — run BEFORE any stage read.

        Returns:
            ``(allowed, reason)`` — reason is None when allowed.
        """
        if self.mode == WriteMode.READ_ONLY:
            return False, "WritePolicy is READ_ONLY"
        if self.safety_predicate(tag_name):
            return False, f"Safety tag write refused: {tag_name!r}"
        if tag_name in self.denylist:
            return False, f"Tag {tag_name!r} is in denylist"
        if self.allowlist is not None and tag_name not in self.allowlist:
            return False, f"Tag {tag_name!r} not in allowlist"
        return True, None

    def audit(self, record: WriteRecord) -> None:
        """Append a record to the audit log (append-only; never mutates prior records)."""
        self.sink.append(record)

    def get_records(self) -> list[WriteRecord]:
        """Return all audit records as a list (only for InMemorySink).

        For custom sinks, query the sink directly — this method returns []
        for sinks that are not InMemorySink.
        """
        if isinstance(self.sink, InMemorySink):
            return self.sink.records()
        return []

    def _make_record(
        self,
        outcome: str,
        tag_name: str,
        intended_bytes: bytes,
        old_bytes: bytes | None,
        reason: str | None,
    ) -> WriteRecord:
        """Build a WriteRecord stamped with the current clock and identity."""
        return WriteRecord(
            outcome=outcome,
            tag_name=tag_name,
            who=self.who,
            when=self.clock(),
            intended_bytes=intended_bytes,
            old_bytes=old_bytes,
            reason=reason,
        )

    def deny(
        self,
        tag_name: str,
        reason: str,
        intended_bytes: bytes = b"",
        old_bytes: bytes | None = None,
    ) -> None:
        """Record an audited denial and do nothing else."""
        self.audit(self._make_record("denied", tag_name, intended_bytes, old_bytes, reason))

    def _deny_all(
        self,
        tag_names: list[str],
        reason: str,
    ) -> None:
        """Record an audited denial for every tag in a batch."""
        for name in tag_names:
            self.deny(name, reason)

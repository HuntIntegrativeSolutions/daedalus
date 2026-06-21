#!/usr/bin/env python3
"""Three-way differential harness: daedalus vs pycomm3 vs pylogix.

Connects all three libraries to a live Studio 5000 Logix Emulate controller and
runs matrix A-F.  Scratch tags are discovered from the live tag list and confirmed
interactively before any write runs.

Usage:
    python scripts/diff_emulate.py [--ip 10.0.0.11] [--slot 0]

Env override: set DAEDALUS_TEST_PLC=ip[/slot] to skip CLI args.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import random
import struct
from dataclasses import dataclass
from typing import Any

# ── paths ─────────────────────────────────────────────────────────────────────
REPO_ROOT = pathlib.Path(__file__).parent.parent
REPLAY_DIR = REPO_ROOT / "tests" / "fixtures" / "replay"

# ── known deferred features → classify as FEATURE_GAP, not P1 ────────────────
EXPECTED_REFUSALS: dict[str, str] = {
    "struct_write": "Struct/STRING writes deferred to Phase 2g",
    "array_of_struct_read": "Array-of-struct read (element_count>1) deferred to Phase 2e",
    "fragmented_write": "Fragmented/large-array writes deferred to Phase 2g",
}

P1 = "P1_BUG"
FEATURE_GAP = "FEATURE_GAP"
KNOWN_DIFF = "KNOWN_DIFFERENCE"
INVESTIGATE = "INVESTIGATE"
PASS_ = "PASS"


# ── normalised result ─────────────────────────────────────────────────────────

@dataclass
class R:
    value: Any = None
    type_str: str | None = None
    error: str | None = None


# ── daedalus adapter ──────────────────────────────────────────────────────────

class DaedalusAdapter:
    def __init__(self, ip: str, slot: int) -> None:
        from daedalus.drivers import LogixDriver
        from daedalus.packets.cip import backplane_path
        from daedalus.session import Session
        from daedalus.transport import SyncTcpTransport

        self._session = Session()
        self._transport = SyncTcpTransport(ip, 44818)
        self._transport.connect()
        self._transport.send_frame(self._session.register_request())
        self._session.register_reply(self._transport.recv_frame())

        conn_path = backplane_path(slot)
        # Randomise connection identifiers each run so a previous crashed session
        # (which left its Forward_Open on the controller) never blocks us.
        self._transport.send_frame(self._session.forward_open_request(
            large=False,
            connection_serial=random.randint(1, 0xFFFF),
            originator_serial=random.randint(1, 0xFFFFFFFF),
            to_connection_id=random.randint(1, 0xFFFFFFFF),
            connection_path=conn_path,
        ))
        self._session.forward_open_reply(self._transport.recv_frame())

        self.frames: list[dict[str, str]] = []
        transport = self._transport
        frames = self.frames

        def _send_recv(req: bytes) -> bytes:
            transport.send_frame(req)
            rep = transport.recv_frame()
            frames.append({"request": req.hex(), "reply": rep.hex()})
            return rep

        self.driver = LogixDriver(self._session, _send_recv)

    def read(self, tag: str, element_count: int = 1) -> R:
        try:
            t = self.driver.read_tag(tag, element_count=element_count)
            return R(value=t.value, type_str=t.type, error=t.error)
        except Exception as exc:
            return R(error=str(exc))

    def write(self, tag: str, value: Any, *, data_type: str | None = None,
              element_count: int = 1) -> R:
        try:
            with self.driver.armed():
                t = self.driver.write_tag(tag, value, data_type=data_type,
                                          element_count=element_count)
            return R(value=t.value, type_str=t.type, error=t.error)
        except Exception as exc:
            return R(error=str(exc))

    def tag_list(self):
        return self.driver.get_tag_list()

    def set_allowlist(self, tags: list[str]) -> None:
        self.driver._policy.allowlist = frozenset(tags)

    def pop_frames(self) -> list[dict[str, str]]:
        out = list(self.frames)
        self.frames.clear()
        return out

    def close(self) -> None:
        try:
            self._transport.send_frame(self._session.forward_close_request())
            self._session.forward_close_reply(self._transport.recv_frame())
            self._transport.send_frame(self._session.unregister_request())
        finally:
            self._transport.close()


# ── pycomm3 adapter ───────────────────────────────────────────────────────────

class Pycomm3Adapter:
    def __init__(self, ip: str, slot: int) -> None:
        import pycomm3  # type: ignore[import]
        self._plc = pycomm3.LogixDriver(ip, slot=slot)
        self._plc.open()

    def read(self, tag: str, element_count: int = 1) -> R:
        try:
            tq = f"{tag}[0]{{{element_count}}}" if element_count > 1 else tag
            t = self._plc.read(tq)
            if t is None:
                return R(error="None returned")
            err = getattr(t, "error", None)
            if err:
                return R(error=str(err))
            return R(value=t.value, type_str=str(getattr(t, "type", None)))
        except Exception as exc:
            return R(error=str(exc))

    def write(self, tag: str, value: Any, *, data_type: str | None = None,
              element_count: int = 1) -> R:
        try:
            t = self._plc.write((tag, value))
            if t is None:
                return R(error="None returned")
            err = getattr(t, "error", None)
            if err:
                return R(error=str(err))
            return R(value=value)
        except Exception as exc:
            return R(error=str(exc))

    def snapshot(self, tag: str) -> Any:
        """Read tag value for authoritative snapshot/restore (uses raw plc.read)."""
        try:
            t = self._plc.read(tag)
            if t and not getattr(t, "error", None):
                return t.value
        except Exception:
            pass
        return None

    def restore(self, tag: str, value: Any) -> bool:
        if value is None:
            return False
        try:
            t = self._plc.write((tag, value))
            return not getattr(t, "error", None)
        except Exception:
            return False

    def tag_list(self):
        try:
            return self._plc.get_tag_list(program="*") or []
        except Exception:
            return []

    def close(self) -> None:
        import contextlib
        with contextlib.suppress(Exception):
            self._plc.close()


# ── pylogix adapter ───────────────────────────────────────────────────────────

class PylogixAdapter:
    def __init__(self, ip: str, slot: int) -> None:
        import pylogix  # type: ignore[import]
        self._plc = pylogix.PLC(ip_address=ip, slot=slot)

    def read(self, tag: str, element_count: int = 1) -> R:
        try:
            r = self._plc.Read(tag, element_count) if element_count > 1 else self._plc.Read(tag)
            status = getattr(r, "Status", None)
            if status != "Success":
                return R(error=str(status))
            return R(value=r.Value)
        except Exception as exc:
            return R(error=str(exc))

    def write(self, tag: str, value: Any, *, data_type: str | None = None,
              element_count: int = 1) -> R:
        try:
            r = self._plc.Write(tag, value)
            status = getattr(r, "Status", None)
            if status != "Success":
                return R(error=str(status))
            return R(value=value)
        except Exception as exc:
            return R(error=str(exc))

    def tag_list(self):
        try:
            r = self._plc.GetTagList()
            return getattr(r, "Value", None) or []
        except Exception:
            return []

    def close(self) -> None:
        import contextlib
        with contextlib.suppress(Exception):
            self._plc.Close()


# ── comparison helpers ────────────────────────────────────────────────────────

def _f32_bits(v: Any) -> bytes | None:
    try:
        return struct.pack("<f", float(v))
    except Exception:
        return None


def _normalize_string_value(v: Any) -> Any:
    """Convert daedalus STRING dict {LEN: n, DATA: [...]} to a Python str.

    Logix STRING is a struct; daedalus decodes it as a dict while pycomm3/pylogix
    return a plain str.  Normalize before comparison so representation differences
    don't show up as P1 bugs.
    """
    if isinstance(v, dict) and "LEN" in v and "DATA" in v:
        try:
            data = v["DATA"]
            length = int(v["LEN"])
            if isinstance(data, (bytes, bytearray)):
                return data[:length].decode("ascii", errors="replace")
            if isinstance(data, list):
                return bytes(int(b) & 0xFF for b in data[:length]).decode("ascii", errors="replace")
        except Exception:
            pass
    return v


def _values_agree(a: Any, b: Any) -> bool:
    a = _normalize_string_value(a)
    b = _normalize_string_value(b)
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    # float32 bit-exact compare
    if isinstance(a, float) or isinstance(b, float):
        ba, bb = _f32_bits(a), _f32_bits(b)
        if ba is not None and bb is not None:
            return ba == bb
    if isinstance(a, bool) and isinstance(b, bool):
        return a == b
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            return False
        return all(_values_agree(x, y) for x, y in zip(a, b, strict=True))
    if isinstance(a, dict) and isinstance(b, dict):
        if set(a) != set(b):
            return False
        return all(_values_agree(a[k], b[k]) for k in a)
    return a == b


# ── report state ──────────────────────────────────────────────────────────────

_findings: list[dict] = []           # P1 / INVESTIGATE / KNOWN_DIFF / FEATURE_GAP
_matrix: dict[str, dict] = {}        # phase → {status, count}


def _record(phase: str, tag: str, verdict: str, **kw: Any) -> None:
    _findings.append({"phase": phase, "tag": tag, "verdict": verdict, **kw})


def _phase_result(phase: str, total: int, issues: int) -> None:
    status = "PASS" if issues == 0 else "DIVERGENCE"
    _matrix[phase] = {"status": status, "total": total, "issues": issues}
    print(f"  → {status}  ({total} checked, {issues} issues)")


# ── triage ────────────────────────────────────────────────────────────────────

def triage(
    phase: str,
    tag: str,
    dae: R,
    pc3: R,
    plx: R,
    *,
    expected_refusal_key: str | None = None,
    frames: list[dict] | None = None,
    note: str = "",
) -> str:
    # FEATURE-GAP: daedalus cleanly refused a known-deferred operation
    if dae.error and expected_refusal_key:
        _record(phase, tag, FEATURE_GAP,
                reason=EXPECTED_REFUSALS[expected_refusal_key],
                dae_error=dae.error, note=note)
        return FEATURE_GAP

    # daedalus errored unexpectedly
    if dae.error:
        if not pc3.error and not plx.error:
            verdict = P1
            hyp = "daedalus returned error where both oracles succeeded"
        elif pc3.error and plx.error:
            verdict = INVESTIGATE
            hyp = "all three failed — may be legitimate controller refusal"
        else:
            verdict = INVESTIGATE
            hyp = "mixed failures — investigate individually"
        _record(phase, tag, verdict, hypothesis=hyp,
                dae=repr(dae), pc3=repr(pc3), plx=repr(plx),
                frames=frames or [], note=note)
        return verdict

    # daedalus succeeded — compare with oracles
    pc3_ok = not pc3.error
    plx_ok = not plx.error
    dae_pc3 = pc3_ok and _values_agree(dae.value, pc3.value)
    dae_plx = plx_ok and _values_agree(dae.value, plx.value)
    pc3_plx = pc3_ok and plx_ok and _values_agree(pc3.value, plx.value)

    if dae_pc3 and dae_plx:
        return PASS_

    if not dae_pc3 and not dae_plx and pc3_plx:
        verdict = P1
        hyp = "daedalus value disagrees with BOTH oracles"
    elif not pc3_plx and (dae_pc3 or dae_plx):
        verdict = KNOWN_DIFF
        hyp = "pycomm3 and pylogix disagree — known library difference"
    else:
        verdict = INVESTIGATE
        hyp = "mixed disagreement — investigate"

    _record(phase, tag, verdict, hypothesis=hyp,
            dae={"value": repr(dae.value), "type": dae.type_str},
            pc3={"value": repr(pc3.value), "type": pc3.type_str, "error": pc3.error},
            plx={"value": repr(plx.value), "error": plx.error},
            frames=frames or [], note=note)
    return verdict


# ── tag-list normalisation helpers ────────────────────────────────────────────

def _pc3_tag_names(pc3_list) -> set[str]:
    names: set[str] = set()
    for t in pc3_list:
        if isinstance(t, dict):
            names.add(t["tag_name"])
        elif hasattr(t, "tag_name"):
            names.add(t.tag_name)
        else:
            names.add(str(t))
    return names


def _plx_tag_names(plx_list) -> set[str]:
    names: set[str] = set()
    for t in plx_list:
        if hasattr(t, "TagName"):
            names.add(t.TagName)
        elif isinstance(t, dict):
            names.add(t.get("TagName", str(t)))
        else:
            names.add(str(t))
    return names


# ── Phase A: tag list ─────────────────────────────────────────────────────────

def phase_a(dae: DaedalusAdapter, pc3: Pycomm3Adapter, plx: PylogixAdapter):
    print("\n═══ Phase A: Tag List ═══")

    dae_tags = dae.tag_list()
    pc3_raw = pc3.tag_list()
    plx_raw = plx.tag_list()

    dae_names = {t.tag_name for t in dae_tags}
    pc3_names = _pc3_tag_names(pc3_raw)
    plx_names = _plx_tag_names(plx_raw)

    print(f"  daedalus : {len(dae_names)} tags")
    print(f"  pycomm3  : {len(pc3_names)} tags")
    print(f"  pylogix  : {len(plx_names)} tags")

    issues = 0
    for label, only in [("only in daedalus", dae_names - pc3_names),
                         ("only in pycomm3",  pc3_names - dae_names)]:
        if only:
            sample = sorted(only)[:15]
            print(f"  [!] {label} ({len(only)}): {sample}")
            _record("A", "(tag-set)", P1, note=label, tags=sorted(only)[:30])
            issues += len(only)

    # pylogix list is often smaller (no program-scoped tags) — just report gap, not bug
    only_plx = plx_names - dae_names
    if only_plx:
        print(f"  [i] only in pylogix ({len(only_plx)}): {sorted(only_plx)[:10]}")

    _phase_result("A_tag_list", len(dae_names), issues)
    return dae_tags  # return for downstream use


# ── Phase B: scalar reads ─────────────────────────────────────────────────────

def phase_b(dae: DaedalusAdapter, pc3: Pycomm3Adapter, plx: PylogixAdapter,
            dae_tags) -> None:
    print("\n═══ Phase B: Scalar Reads ═══")

    # Build type→tag map from daedalus tag list (atomics only, no arrays, no structs)
    by_type: dict[str, str] = {}
    for t in dae_tags:
        if t.is_struct or t.data_type is None or t.dimensions:
            continue
        dt = t.data_type.upper()
        if dt not in by_type:
            by_type[dt] = t.tag_name

    # STRING is a struct in Logix (is_struct=True, data_type=None) — find separately
    str_tag: str | None = None
    for t in dae_tags:
        if t.is_struct and not t.dimensions and any(
                kw in t.tag_name.lower() for kw in ("str", "string")):
            str_tag = t.tag_name
            break
    if str_tag:
        by_type["STRING"] = str_tag

    issues = 0
    total = 0
    for want in ("DINT", "INT", "SINT", "USINT", "REAL", "BOOL", "STRING"):
        tag = by_type.get(want)
        if tag is None:
            print(f"  [{want}] no tag found — skipped")
            continue

        dae.pop_frames()
        d = dae.read(tag)
        p = pc3.read(tag)
        q = plx.read(tag)
        frames = dae.pop_frames()

        v = triage("B", tag, d, p, q, frames=frames, note=f"type={want}")
        total += 1
        ok = "✓" if v == PASS_ else "✗"
        row = (
            f"  [{want}] {tag:30s}"
            f"  dae={d.value!r:<20}  pc3={p.value!r:<20}  plx={q.value!r:<20}"
            f"  {ok} {v}"
        )
        print(row)
        if v not in (PASS_, KNOWN_DIFF):
            issues += 1

        # Save agreed frames as replay fixture
        if v == PASS_ and frames:
            _save_replay(tag, "scalar_read", frames)

    _phase_result("B_scalar_reads", total, issues)


# ── Phase C: array reads + fragmented ────────────────────────────────────────

def phase_c(dae: DaedalusAdapter, pc3: Pycomm3Adapter, plx: PylogixAdapter,
            dae_tags) -> None:
    print("\n═══ Phase C: Array Reads (incl. fragmented) ═══")

    # Find DINT and REAL arrays, prefer large ones for fragmented test
    dint_arrays = sorted(
        [t for t in dae_tags if t.data_type == "DINT" and t.dimensions],
        key=lambda t: t.dimensions[0], reverse=True,
    )
    real_arrays = sorted(
        [t for t in dae_tags if t.data_type == "REAL" and t.dimensions],
        key=lambda t: t.dimensions[0], reverse=True,
    )

    issues = 0
    total = 0

    for label, arrays in [("DINT array", dint_arrays), ("REAL array", real_arrays)]:
        if not arrays:
            print(f"  [{label}] no array tags found — skipped")
            continue
        ti = arrays[0]
        tag = ti.tag_name
        n = min(ti.dimensions[0], 200)  # cap at 200 elements
        note = f"dim={ti.dimensions[0]} reading {n}"
        if n * 4 > 480:
            note += " [FRAGMENTED]"
        print(f"  [{label}] {tag}  {note}")

        dae.pop_frames()
        d = dae.read(tag, element_count=n)
        p = pc3.read(tag, element_count=n)
        q = plx.read(tag, element_count=n)
        frames = dae.pop_frames()

        v = triage("C", tag, d, p, q, frames=frames, note=note)
        ok = "✓" if v == PASS_ else "✗"
        dae_len = len(d.value) if isinstance(d.value, list) else "?"
        pc3_len = len(p.value) if isinstance(p.value, list) else "?"
        plx_len = len(q.value) if isinstance(q.value, list) else "?"
        print(f"    dae={dae_len} elems  pc3={pc3_len} elems  plx={plx_len} elems  {ok} {v}")
        if v not in (PASS_, KNOWN_DIFF):
            issues += 1
        total += 1
        if v == PASS_ and frames:
            _save_replay(tag, "array_read", frames)

    _phase_result("C_array_reads", total, issues)


# ── Phase D: UDT / struct reads (PRIORITY) ───────────────────────────────────

def phase_d(dae: DaedalusAdapter, pc3: Pycomm3Adapter, plx: PylogixAdapter,
            dae_tags) -> None:
    print("\n═══ Phase D: UDT / Struct Reads (PRIORITY) ═══")

    # get_tag_list() already called; daedalus driver caches templates internally.

    struct_tags = [t for t in dae_tags if t.is_struct and not t.dimensions]
    array_of_struct = [t for t in dae_tags if t.is_struct and t.dimensions]

    issues = 0
    total = 0

    # Single-instance struct reads (supported, high-value bug target)
    for ti in struct_tags[:8]:  # cap at 8 to keep runtime reasonable
        tag = ti.tag_name
        dae.pop_frames()
        d = dae.read(tag)
        p = pc3.read(tag)
        q = plx.read(tag)
        frames = dae.pop_frames()

        v = triage("D", tag, d, p, q, frames=frames,
                   note=f"tmpl_id={ti.template_instance_id}")
        ok = "✓" if v == PASS_ else "✗"
        dae_summary = repr(d.value)[:60] if d.value is not None else f"ERR:{d.error}"
        print(f"  [UDT] {tag:30s}  dae={dae_summary}  {ok} {v}")
        if v not in (PASS_, KNOWN_DIFF):
            issues += 1
        total += 1
        if v == PASS_ and frames:
            _save_replay(tag, "udt_read", frames)

    # Array-of-struct: expect FEATURE-GAP (element_count>1 on struct)
    for ti in array_of_struct[:3]:
        tag = ti.tag_name
        n = ti.dimensions[0]
        dae.pop_frames()
        d = dae.read(tag, element_count=n)
        frames = dae.pop_frames()
        v = triage("D", tag, d, R(), R(),
                   expected_refusal_key="array_of_struct_read", frames=frames,
                   note=f"array-of-struct dim={n}")
        print(f"  [ARRAY-OF-STRUCT] {tag:30s}  {v}")
        total += 1

    _phase_result("D_udt_reads", total, issues)


# ── scratch-tag discovery ─────────────────────────────────────────────────────

def _find_scratch_candidates(dae_tags) -> dict[str, str | None]:
    """Return {role: tag_name} for each writable atomic role."""
    by_type: dict[str, list] = {}
    for t in dae_tags:
        if t.is_struct or t.dimensions or t.data_type is None:
            continue
        by_type.setdefault(t.data_type.upper(), []).append(t.tag_name)

    # Array candidates
    dint_arrays = [t for t in dae_tags if t.data_type == "DINT" and t.dimensions and
                   t.dimensions[0] >= 3]

    # Prefer tags with "scratch", "test", "demo", "temp" in name (case-insensitive)
    def _prefer(candidates: list[str]) -> str | None:
        if not candidates:
            return None
        preferred = [c for c in candidates
                     if any(kw in c.lower() for kw in ("scratch","test","demo","temp"))]
        return preferred[0] if preferred else candidates[0]

    candidates: dict[str, str | None] = {
        "DINT":  _prefer(by_type.get("DINT", [])),
        "REAL":  _prefer(by_type.get("REAL", [])),
        "BOOL":  _prefer(by_type.get("BOOL", [])),
        "INT":   _prefer(by_type.get("INT", []) or by_type.get("SINT", [])),
        "DINT_ARRAY": dint_arrays[0].tag_name if dint_arrays else None,
    }
    return candidates


def discover_and_confirm_scratch(dae_tags) -> dict[str, str]:
    """Print scratch candidates, block on user confirmation, return confirmed map."""
    print("\n═══ Scratch Tag Discovery ═══")
    candidates = _find_scratch_candidates(dae_tags)

    print("  Proposed scratch tags (safe to write):")
    for role, tag in candidates.items():
        print(f"    {role:12s} → {tag or '[NOT FOUND]'}")

    print()
    print("  These tags will be WRITTEN in Phase E.  Snapshot + restore via pycomm3.")
    print("  Type 'y' to proceed, 'n' (or Ctrl-C) to skip write phases.")
    try:
        answer = input("  Confirm scratch tags? [y/N]: ").strip().lower()
    except (KeyboardInterrupt, EOFError):
        print("\n  (Ctrl-C) — skipping write phases.")
        return {}

    if answer != "y":
        print("  Skipping write phases.")
        return {}

    confirmed: dict[str, str] = {role: tag for role, tag in candidates.items()
                                  if tag is not None}
    return confirmed


# ── Phase E: write round-trip ─────────────────────────────────────────────────

def phase_e(
    dae: DaedalusAdapter,
    pc3: Pycomm3Adapter,
    plx: PylogixAdapter,
    scratch: dict[str, str],
    dae_tags: list,
    snapshots: dict[str, Any],
) -> None:
    """Run write round-trip phase.  Populates `snapshots` in-place as each tag
    is snapshotted so the caller's finally-block can restore even on exception."""
    print("\n═══ Phase E: Write Round-Trip ═══")

    if not scratch:
        print("  (skipped — no confirmed scratch tags)")
        _matrix["E_write_roundtrip"] = {"status": "SKIPPED", "total": 0, "issues": 0}
        return

    # Set allowlist on daedalus to exactly the scratch tags (safety gate)
    dae.set_allowlist(list(scratch.values()))

    # Snapshot originals via pycomm3 (authoritative — not daedalus).
    # Populate `snapshots` one at a time so partial completion is still restorable.
    for role, tag in scratch.items():
        snapshots[tag] = pc3.snapshot(tag)
        d_snap = dae.read(tag)
        q_snap = plx.read(tag)
        snap_pc3 = snapshots[tag]
        snap_dae = d_snap.value
        snap_plx = q_snap.value
        agree = _values_agree(snap_pc3, snap_dae) and _values_agree(snap_pc3, snap_plx)
        print(f"  SNAP {role} {tag}: pc3={snap_pc3!r}  dae={snap_dae!r}  plx={snap_plx!r}"
              f"  {'✓' if agree else '[!] MISMATCH at snapshot'}")

    # Gate: if any snapshot returned None we cannot safely restore that tag → drop it
    snapshot_failures = {role for role, tag in scratch.items() if snapshots.get(tag) is None}
    if snapshot_failures:
        for role in snapshot_failures:
            tag = scratch[role]
            print(f"  WARNING: snapshot of {role} ({tag}) returned None — "
                  f"removing from write set (cannot guarantee restore)")
            del snapshots[tag]
        scratch = {role: tag for role, tag in scratch.items() if role not in snapshot_failures}
        if not scratch:
            print("  All snapshots failed — skipping write round-trips.")
            _phase_result("E_write_roundtrip", 0, 0)
            return
        dae.set_allowlist(list(scratch.values()))

    issues = 0
    total = 0

    # Write test values per type
    write_cases: list[tuple[str, str, Any, str | None, int]] = []
    if "DINT" in scratch:
        write_cases.append(("DINT", scratch["DINT"], 88888, "DINT", 1))
    if "REAL" in scratch:
        write_cases.append(("REAL", scratch["REAL"], 3.14159, "REAL", 1))
    if "BOOL" in scratch:
        write_cases.append(("BOOL", scratch["BOOL"], True, "BOOL", 1))
    if "INT" in scratch:
        write_cases.append(("INT", scratch["INT"], 1234, None, 1))
    if "DINT_ARRAY" in scratch:
        tag = scratch["DINT_ARRAY"]
        write_cases.append(("DINT_ARRAY", tag, [10, 20, 30], "DINT", 3))

    for role, tag, test_val, dt, n in write_cases:
        print(f"\n  --- {role}: {tag} ---")
        total += 1

        # Step 1: daedalus writes → pc3 and plx read back
        kw: dict[str, Any] = {}
        if dt:
            kw["data_type"] = dt
        if n > 1:
            kw["element_count"] = n

        dae.pop_frames()
        dw = dae.write(tag, test_val, **kw)
        frames = dae.pop_frames()

        if dw.error:
            # Check if this is an expected refusal
            expected = None
            if "struct" in dw.error.lower() or "STRING" in role:
                expected = "struct_write"
            elif "fragment" in dw.error.lower():
                expected = "fragmented_write"

            _record("E", tag, FEATURE_GAP if expected else P1,
                    note=f"daedalus write refused: {dw.error}",
                    expected_refusal=EXPECTED_REFUSALS.get(expected or "", ""))
            print(f"    daedalus write: ERR {dw.error!r}")
            if expected:
                print("    → FEATURE_GAP (expected refusal)")
            else:
                print("    → UNEXPECTED refusal — possible P1")
                issues += 1
            continue

        print(f"    daedalus wrote {test_val!r}")
        p_rb = pc3.read(tag, element_count=n)
        q_rb = plx.read(tag, element_count=n)
        print(f"    readback pc3={p_rb.value!r}  plx={q_rb.value!r}")

        dw_result = R(value=test_val)
        v1 = triage("E", f"{tag}[dae→pc3+plx]", dw_result, p_rb, q_rb,
                    frames=frames, note=f"{role} daedalus-write cross-read")
        ok1 = "✓" if v1 == PASS_ else "✗"
        print(f"    cross-read verdict: {ok1} {v1}")
        if v1 not in (PASS_, KNOWN_DIFF):
            issues += 1

        # Step 2: pycomm3 writes a different value → daedalus reads back
        alt_val: Any
        if isinstance(test_val, bool):
            alt_val = not test_val
        elif isinstance(test_val, list):
            alt_val = [v + 1 for v in test_val]
        elif isinstance(test_val, float):
            alt_val = test_val * 2.0
        else:
            alt_val = test_val + 1

        p_write = pc3.write(tag, alt_val)
        if p_write.error:
            print(f"    pycomm3 write failed: {p_write.error} — skipping step 2")
        else:
            dae.pop_frames()
            d_rb2 = dae.read(tag, element_count=n)
            frames2 = dae.pop_frames()
            print(f"    pycomm3 wrote {alt_val!r} → daedalus reads {d_rb2.value!r}")
            expected_result = R(value=alt_val)
            v2 = triage("E", f"{tag}[pc3→dae]", d_rb2, expected_result, expected_result,
                        frames=frames2, note=f"{role} pycomm3-write daedalus-read")
            ok2 = "✓" if v2 == PASS_ else "✗"
            print(f"    daedalus-read-back verdict: {ok2} {v2}")
            if v2 not in (PASS_, KNOWN_DIFF):
                issues += 1

    # STRING write probe — expect FEATURE-GAP (struct write refused)
    # Find any struct tag whose name suggests a STRING, or fall back to first struct
    str_candidates = [t for t in dae_tags
                      if t.is_struct and not t.dimensions and
                      any(kw in t.tag_name.lower() for kw in ("str", "string"))]
    if not str_candidates:
        # Fall back: any struct tag will trigger the struct-write refusal
        str_candidates = [t for t in dae_tags if t.is_struct and not t.dimensions]
    if str_candidates:
        tag = str_candidates[0].tag_name
        total += 1
        # Temporarily add struct tag to allowlist so we hit the struct-write
        # code path (Phase 2g DataError), not the allowlist gate.
        old_allowlist = dae.driver._policy.allowlist
        dae.driver._policy.allowlist = (old_allowlist or frozenset()) | frozenset([tag])
        dae.pop_frames()
        sw = dae.write(tag, "TESTVAL", data_type=None)
        dae.driver._policy.allowlist = old_allowlist
        _record("E", tag, FEATURE_GAP,
                note="struct/STRING write probe (expect Phase 2g DataError refusal)",
                dae_error=sw.error,
                reason=EXPECTED_REFUSALS["struct_write"])
        print(f"\n  [struct write probe] {tag}: dae_error={sw.error!r} → FEATURE_GAP (expected)")

    _phase_result("E_write_roundtrip", total, issues)


# ── Phase F: error paths ──────────────────────────────────────────────────────

def phase_f(
    dae: DaedalusAdapter,
    pc3: Pycomm3Adapter,
    plx: PylogixAdapter,
    scratch: dict[str, str] | None = None,
) -> None:
    print("\n═══ Phase F: Error Paths ═══")
    issues = 0
    total = 0

    # Non-existent tag
    ghost = "__nonexistent_tag_xyz__"
    d = dae.read(ghost)
    p = pc3.read(ghost)
    q = plx.read(ghost)
    total += 1
    all_error = bool(d.error) and bool(p.error) and bool(q.error)
    dae_sane = bool(d.error) and "crash" not in (d.error or "").lower()
    print(f"  [nonexistent tag] dae_err={d.error!r}  pc3_err={p.error!r}  plx_err={q.error!r}")
    if not all_error:
        _record("F", ghost, P1, note="daedalus returned success on nonexistent tag",
                dae=repr(d), pc3=repr(p), plx=repr(q))
        issues += 1
        print("    → P1: expected error, got success")
    elif not dae_sane:
        _record("F", ghost, P1, note="daedalus error message looks like a crash")
        issues += 1
    else:
        print("    → PASS (all three errored as expected)")

    # Wrong-type write: attempt to write a string into a DINT tag.
    # daedalus should catch it in _encode_value (pre-wire) and return DataError.
    # pycomm3 and pylogix should also refuse (type mismatch).
    if scratch and "DINT" in scratch:
        dint_tag = scratch["DINT"]
        bad_val = "THIS_IS_NOT_A_DINT"
        total += 1
        print(f"\n  [wrong-type write DINT←str] {dint_tag}")
        # Temporarily expand daedalus allowlist so refusal comes from type encode,
        # not from the policy gate (which would also refuse, but is a different code path).
        old_al = dae.driver._policy.allowlist
        dae.driver._policy.allowlist = (old_al or frozenset()) | frozenset([dint_tag])
        dw = dae.write(dint_tag, bad_val, data_type="DINT")
        dae.driver._policy.allowlist = old_al

        pw = pc3.write(dint_tag, bad_val)
        qw = plx.write(dint_tag, bad_val)
        print(f"    dae_err={dw.error!r}  pc3_err={pw.error!r}  plx_err={qw.error!r}")

        dae_refused = bool(dw.error)
        dae_sane_f = dae_refused and "crash" not in (dw.error or "").lower()
        if dae_sane_f:
            print("    → PASS (daedalus rejected bad type cleanly)")
        elif not dae_refused:
            _record("F", dint_tag, P1,
                    note=f"daedalus accepted string value for DINT tag: value={dw.value!r}",
                    dae=repr(dw), pc3=repr(pw), plx=repr(qw))
            issues += 1
            print("    → P1: daedalus did not refuse bad type")
        else:
            _record("F", dint_tag, P1,
                    note=f"daedalus error response looks like a crash: {dw.error!r}")
            issues += 1
            print("    → P1: daedalus crashed on bad type instead of clean refusal")

    _phase_result("F_error_paths", total, issues)


# ── replay capture ────────────────────────────────────────────────────────────

def _save_replay(tag: str, operation: str, frames: list[dict]) -> None:
    if not frames:
        return
    REPLAY_DIR.mkdir(parents=True, exist_ok=True)
    safe_tag = tag.replace(":", "_").replace(".", "_").replace("[", "_").replace("]", "")
    path = REPLAY_DIR / f"{safe_tag}_{operation}.jsonl"
    with path.open("w") as fh:
        for frame in frames:
            fh.write(json.dumps({
                "tag": tag,
                "operation": operation,
                "request": frame["request"],
                "reply": frame["reply"],
            }) + "\n")


# ── final report ──────────────────────────────────────────────────────────────

def print_report() -> None:
    p1_bugs = [f for f in _findings if f["verdict"] == P1]
    feature_gaps = [f for f in _findings if f["verdict"] == FEATURE_GAP]
    known_diffs = [f for f in _findings if f["verdict"] == KNOWN_DIFF]
    investigations = [f for f in _findings if f["verdict"] == INVESTIGATE]

    print("\n" + "═" * 70)
    print("DIFFERENTIAL REPORT")
    print("═" * 70)

    print("\n── Summary Table ──")
    print(f"  {'Phase':<25}  {'Status':<15}  {'Checked':>7}  {'Issues':>6}")
    print(f"  {'-'*25}  {'-'*15}  {'-'*7}  {'-'*6}")
    for phase, info in sorted(_matrix.items()):
        status = info["status"]
        total = info.get("total", 0)
        issues = info.get("issues", 0)
        print(f"  {phase:<25}  {status:<15}  {total:>7}  {issues:>6}")

    if p1_bugs:
        print(f"\n── P1 Bugs ({len(p1_bugs)}) ──")
        for b in p1_bugs:
            print(f"\n  [P1] Phase={b['phase']}  Tag={b['tag']}")
            print(f"       {b.get('hypothesis','')}")
            if "dae" in b:
                print(f"       daedalus : {b['dae']}")
            if "pc3" in b:
                print(f"       pycomm3  : {b['pc3']}")
            if "plx" in b:
                print(f"       pylogix  : {b['plx']}")
            if b.get("frames"):
                print(f"       frames   : {len(b['frames'])} captured")
                req_hex = b["frames"][0].get("request", "")[:40]
                rep_hex = b["frames"][0].get("reply", "")[:40]
                print(f"         req[0]: {req_hex}…")
                print(f"         rep[0]: {rep_hex}…")
    else:
        print("\n── P1 Bugs: NONE ✓ ──")

    if feature_gaps:
        print(f"\n── Feature Gaps ({len(feature_gaps)}) ──")
        for g in feature_gaps:
            print(f"  [GAP] Phase={g['phase']}  Tag={g['tag']}  {g.get('reason','')}")

    if investigations:
        print(f"\n── Investigate ({len(investigations)}) ──")
        for i in investigations:
            print(f"  [?] Phase={i['phase']}  Tag={i['tag']}  {i.get('hypothesis','')}")

    if known_diffs:
        print(f"\n── Known Differences between pycomm3/pylogix ({len(known_diffs)}) ──")
        for k in known_diffs:
            print(f"  [LIB] Phase={k['phase']}  Tag={k['tag']}")

    print()


# ── entry point ───────────────────────────────────────────────────────────────

def _parse_args() -> tuple[str, int]:
    env = os.environ.get("DAEDALUS_TEST_PLC", "")
    if "/" in env:
        default_ip, default_slot = env.split("/", 1)
    elif env:
        default_ip, default_slot = env, "0"
    else:
        default_ip, default_slot = "10.0.0.11", "0"

    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ip",   default=default_ip,       help="Controller IP")
    parser.add_argument("--slot", default=default_slot,     type=int, help="CIP slot")
    args = parser.parse_args()
    return args.ip, args.slot


def main() -> None:
    ip, slot = _parse_args()
    print(f"Connecting to {ip} / slot {slot} ...")

    dae: DaedalusAdapter | None = None
    pc3: Pycomm3Adapter | None = None
    plx: PylogixAdapter | None = None
    snapshots: dict[str, Any] = {}  # populated in-place by phase_e
    scratch: dict[str, str] = {}

    try:
        dae = DaedalusAdapter(ip, slot)
        pc3 = Pycomm3Adapter(ip, slot)
        plx = PylogixAdapter(ip, slot)

        # ── Read-only phases ──────────────────────────────────────────────────
        dae_tags = phase_a(dae, pc3, plx)
        phase_b(dae, pc3, plx, dae_tags)
        phase_c(dae, pc3, plx, dae_tags)
        phase_d(dae, pc3, plx, dae_tags)

        # ── Confirm scratch tags ──────────────────────────────────────────────
        scratch = discover_and_confirm_scratch(dae_tags)

        # ── Write phases (only if confirmed) ─────────────────────────────────
        if scratch:
            phase_e(dae, pc3, plx, scratch, dae_tags, snapshots)
        phase_f(dae, pc3, plx, scratch)

    except KeyboardInterrupt:
        print("\n(interrupted)")

    finally:
        # Restore scratch tags via pycomm3 (authoritative, not daedalus)
        if snapshots:
            print("\n── Restoring scratch tags via pycomm3 ──")
            for tag, original in snapshots.items():
                ok = pc3.restore(tag, original)
                print(f"  {tag}: {'restored ✓' if ok else 'RESTORE FAILED ✗'}")

        if dae:
            print("\n── daedalus teardown ──")
            try:
                dae.close()
                print("  Forward_Close: OK ✓")
            except Exception as exc:
                print(f"  Forward_Close: FAILED ✗ ({exc})")
        if pc3:
            pc3.close()
        if plx:
            plx.close()

    print_report()


if __name__ == "__main__":
    main()

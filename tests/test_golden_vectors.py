"""Golden vector tests — decode expected hex for known inputs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "golden"


def _load_vectors() -> list[dict[str, Any]]:
    vectors = []
    for path in sorted(FIXTURES_DIR.glob("*.json")):
        with path.open() as f:
            data = json.load(f)
        if isinstance(data, list):
            for v in data:
                v["_source"] = path.name
            vectors.extend(data)
    return vectors


ALL_VECTORS = _load_vectors()


@pytest.fixture(params=ALL_VECTORS, ids=[f"{v['type']}/{v['description']}" for v in ALL_VECTORS])
def vector(request: pytest.FixtureRequest) -> dict[str, Any]:
    return request.param  # type: ignore[no-any-return]


def _encode_for_type(vector: dict[str, Any]) -> bytes:
    """Call the appropriate encode function for a given vector dict."""
    from daedalus.cip.data_types import (
        BOOL,
        DINT,
        INT,
        LINT,
        LOGIX_STRING,
        LREAL,
        REAL,
        SHORT_STRING,
        SINT,
        STRING,
        TIME,
        UDINT,
        UINT,
        ULINT,
        USINT,
        DataType,
    )
    from daedalus.cip.segments import DataSegment, LogicalSegment, PortSegment

    type_name: str = vector["type"]
    inp = vector.get("input")

    type_map: dict[str, type[DataType[Any]]] = {
        "bool": BOOL,
        "sint": SINT,
        "int": INT,
        "dint": DINT,
        "lint": LINT,
        "usint": USINT,
        "uint": UINT,
        "udint": UDINT,
        "ulint": ULINT,
        "real": REAL,
        "lreal": LREAL,
        "string": STRING,
        "short_string": SHORT_STRING,
        "logix_string": LOGIX_STRING,
        "time": TIME,
    }
    if type_name in type_map:
        return type_map[type_name].encode(inp)
    if type_name == "logical_segment":
        assert isinstance(inp, dict)
        lseg = LogicalSegment(inp["logical_value"], inp["logical_type"])
        padded: bool = bool(vector.get("padded", False))
        return LogicalSegment.encode(lseg, padded=padded)
    if type_name == "data_segment":
        assert isinstance(inp, (str, bytes))
        dseg = DataSegment(inp)
        return DataSegment.encode(dseg)
    if type_name == "port_segment":
        assert isinstance(inp, dict)
        pseg = PortSegment(inp["port"], inp["link_address"])
        return PortSegment.encode(pseg)
    if type_name == "encap_register_session":
        from daedalus.packets.cip import build_register_session

        return build_register_session()
    raise NotImplementedError(f"No encoder for type {type_name!r}")


def test_golden_vector(vector: dict[str, Any]) -> None:
    inp = vector.get("input")
    expected_hex = vector["hex"]

    if inp is None:
        pytest.skip(f"No input in vector {vector['description']!r}")

    actual = _encode_for_type(vector)
    assert actual.hex() == expected_hex, (
        f"Vector {vector['description']!r}: expected 0x{expected_hex}, got 0x{actual.hex()}"
    )

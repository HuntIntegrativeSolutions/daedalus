"""Enforce the sans-I/O contract: no layer outside transport/ may touch a socket.

Walks every *.py under the sans-I/O core (cip/, packets/, session/, drivers/)
plus the pure support modules (tag.py, exceptions.py) with ast and asserts none
reference any forbidden I/O module — raw sockets *or* async frameworks.

runtime/ is L4: it may legitimately use anyio/asyncio (that is where async lives),
but it must still never touch a *raw* socket — that is L1 transport/'s sole job.
So runtime/ is checked against the reduced RAW_IO_MODULES set.

Parametrized per file so a failure names the exact file and offending module.
"""

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
SRC = REPO_ROOT / "src" / "daedalus"

# L0-L3 sans-I/O core directories.
SANS_IO_DIRS = ["cip", "packets", "session", "drivers"]
# Pure support modules used by the core — must stay free of all I/O.
SANS_IO_FILES = ["tag.py", "exceptions.py"]
# L4 runtime — async permitted, raw sockets forbidden.
RUNTIME_DIR = "runtime"

FORBIDDEN_MODULES = frozenset(
    {
        "socket",
        "ssl",
        "asyncio",
        "anyio",
        "selectors",
        "socketserver",
        "http",
        "urllib",
        "requests",
    }
)
# L4 may orchestrate async (anyio/asyncio) but never open a raw socket itself.
RAW_IO_MODULES = FORBIDDEN_MODULES - {"asyncio", "anyio"}


def _collect_py_files() -> list[tuple[Path, str, frozenset[str]]]:
    result: list[tuple[Path, str, frozenset[str]]] = []
    for dirname in SANS_IO_DIRS:
        for py_file in sorted((SRC / dirname).rglob("*.py")):
            result.append((py_file, str(py_file.relative_to(REPO_ROOT)), FORBIDDEN_MODULES))
    for filename in SANS_IO_FILES:
        py_file = SRC / filename
        result.append((py_file, str(py_file.relative_to(REPO_ROOT)), FORBIDDEN_MODULES))
    for py_file in sorted((SRC / RUNTIME_DIR).rglob("*.py")):
        result.append((py_file, str(py_file.relative_to(REPO_ROOT)), RAW_IO_MODULES))
    return result


_PY_FILES = _collect_py_files()


@pytest.mark.parametrize(
    ("file_path", "label", "forbidden"),
    _PY_FILES,
    ids=[label for _, label, _ in _PY_FILES],
)
def test_no_forbidden_imports(file_path: Path, label: str, forbidden: frozenset[str]) -> None:
    source = file_path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=label)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                assert top not in forbidden, f"{label}: forbidden import '{alias.name}'"
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            top = node.module.split(".")[0]
            assert top not in forbidden, f"{label}: forbidden 'from {node.module} import ...'"

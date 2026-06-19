"""Enforce the sans-I/O contract: L0/L2/L3 layers must not import I/O modules.

Walks every *.py under cip/, packets/, session/, and drivers/ with ast and
asserts that none of them reference the forbidden I/O modules. Parametrized
per file so a failure names the exact file and offending module.

The test passes trivially while the packages are empty (Phase 0), but the
harness is in place and correct for all future phases.
"""

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
SANS_IO_DIRS = ["cip", "packets", "session", "drivers"]
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


def _collect_py_files() -> list[tuple[Path, str]]:
    result: list[tuple[Path, str]] = []
    for dirname in SANS_IO_DIRS:
        pkg = REPO_ROOT / "src" / "daedalus" / dirname
        for py_file in sorted(pkg.rglob("*.py")):
            result.append((py_file, str(py_file.relative_to(REPO_ROOT))))
    return result


_PY_FILES = _collect_py_files()


@pytest.mark.parametrize(
    ("file_path", "label"),
    _PY_FILES,
    ids=[label for _, label in _PY_FILES],
)
def test_no_forbidden_imports(file_path: Path, label: str) -> None:
    source = file_path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=label)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                assert top not in FORBIDDEN_MODULES, f"{label}: forbidden import '{alias.name}'"
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            top = node.module.split(".")[0]
            assert top not in FORBIDDEN_MODULES, (
                f"{label}: forbidden 'from {node.module} import ...'"
            )

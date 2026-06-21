"""Sans-I/O firewall test: importing LogixDriver must not pull anyio into sys.modules.

The architecture invariant is that L0-L3 never import socket/asyncio/anyio.
This subprocess test is the enforcement mechanism — it imports the driver in a
clean Python process and asserts that anyio is not a side effect.
"""

from __future__ import annotations

import subprocess
import sys


def test_logix_driver_does_not_pull_anyio() -> None:
    """Import daedalus.drivers._logix; assert anyio not in sys.modules."""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import daedalus.drivers._logix; "
                "import sys; "
                "assert 'anyio' not in sys.modules, "
                "f'anyio pulled in by daedalus.drivers._logix: {list(sys.modules)}'"
            ),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"Firewall violation — anyio pulled into L3 driver import chain.\n"
        f"stderr: {result.stderr}\n"
        f"stdout: {result.stdout}"
    )


def test_write_policy_does_not_pull_anyio() -> None:
    """Import daedalus.runtime.write_policy; assert anyio not in sys.modules."""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import daedalus.runtime.write_policy; "
                "import sys; "
                "assert 'anyio' not in sys.modules, "
                "f'anyio pulled in by runtime.write_policy: {list(sys.modules)}'"
            ),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"WritePolicy pulled anyio into the import chain.\n"
        f"stderr: {result.stderr}\n"
        f"stdout: {result.stdout}"
    )

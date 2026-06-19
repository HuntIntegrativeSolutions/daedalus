"""Verify the package imports and __version__ is a non-empty string."""

import daedalus


def test_version_is_non_empty_string() -> None:
    assert isinstance(daedalus.__version__, str)
    assert daedalus.__version__ != ""

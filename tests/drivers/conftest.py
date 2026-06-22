"""Shared fixtures for driver tests."""

from __future__ import annotations

from typing import cast

import pytest


@pytest.fixture(params=["asyncio", "trio"])
def anyio_backend(request: pytest.FixtureRequest) -> str:
    return cast(str, request.param)

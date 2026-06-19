## Summary

<!-- Describe what this PR does and why. -->

## Checklist

- [ ] `uv run ruff check .` clean
- [ ] `uv run ruff format --check .` clean
- [ ] `uv run mypy` clean (strict)
- [ ] `uv run pytest` green on all supported Python versions (3.11, 3.12, 3.13)
- [ ] Sans-I/O firewall respected: `cip/`, `packets/`, `session/`, `drivers/` import
      none of the forbidden I/O modules (`socket`, `ssl`, `asyncio`, `anyio`, etc.)
- [ ] `CHANGELOG.md` updated under `[Unreleased]`
- [ ] `LICENSE` and `NOTICE` unchanged; no pycomm3/pylogix source copied into `src/`

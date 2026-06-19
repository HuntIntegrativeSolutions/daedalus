# Changelog

All notable changes to this project will be documented in this file.

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning: [SemVer](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Phase 0 project scaffold: `pyproject.toml` (uv_build backend, PEP 639 license
  metadata), `src/` layout with six-layer package skeleton, GitHub Actions CI
  (lint/type-check, test matrix on Python 3.11/3.12/3.13, build + wheel
  verification), pre-commit config (ruff + mypy), and documentation
  (`ARCHITECTURE.md`, `CONTRIBUTING.md`, `SECURITY.md`).
- `tests/test_sans_io_firewall.py` — AST-based CI enforcement of the sans-I/O
  contract across `cip/`, `packets/`, `session/`, and `drivers/`.
- Project branding assets (banner, mascot, icon) under `assets/branding/`.

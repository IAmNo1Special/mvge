# MvgeOS Testing Standards Charter

This document is the single authority for how MvgeOS is tested. It states
requirements, not conventions of convenience. Where any other document,
comment, or tooling configuration conflicts with this charter, this charter
wins; the conflicting artifact is a bug to fix, not license to deviate.

Applies to every package in the uv workspace (`mvgeos-core`, `mvgeos-agent`,
`mvgeos-provider`, `mvgeos-tome`, `mvgeos-runes`, `mvgeos-cli`, `mvgeos-gui`).

## 1. Tier model

The suite has exactly two tiers: **unit** and **integration**.

- **Unit** tests exercise one module (or a small cluster) in isolation. Every
  external dependency is replaced by a mock or fake. Unit tests must be fast
  and fully deterministic.
- **Integration** tests exercise multiple real modules collaborating inside the
  repo. They cross module boundaries but stay hermetic (see section 2): no
  network, no external services, no real credentials.

Requirements:

- Tests live at `<package>/tests/unit/<module>.py` and
  `<package>/tests/integration/<module>.py`, mirroring source structure
  (e.g., `mvgeos-agent/tests/unit/types.py` tests the module importable as
  `mvgeos_agent.types`, which lives at `mvgeos-agent/src/mvgeos_agent/types.py`
  under each package's src layout).
- File names are flat `*.py` without a `test_` prefix (`python_files = "*.py"`
  in the root pyproject).
- Test directories must never contain an `__init__.py`: with
  `--import-mode=importlib`, packaged test dirs collapse to identical dotted
  paths across packages and silently shadow each other.

## 2. Hermeticity

Every test in both tiers must be hermetic:

- No network access of any kind, loopback connections included — in-process
  fakes stand in for real transports instead of local sockets.
- No real secrets, API keys, or credential-bearing environment variables.
- No dependence on wall-clock time: inject or fake clocks and timeouts so
  results never vary with when the suite runs.
- No external services: no LLM APIs, no databases over the network, no MCP
  servers over stdio/HTTP, no subprocesses reaching outside the machine's temp
  space.

External seams must be mocked at dependency boundaries — HTTP clients,
subprocess transports, sleep/clock, environment — so production code accepts
the seam via injection and tests supply the fake there. Mock sync methods with
`MagicMock()` and async methods with `AsyncMock()`; mixing the two produces
"coroutine never awaited" warnings and silent pass-throughs.

Filesystem writes are confined to pytest-managed temp paths
(`tmp_path`/`tmp_path_factory`). When generated artifacts land outside the
project tree, add matching omit patterns to `[tool.coverage.run]` in
pyproject to avoid "couldn't parse" coverage warnings.

## 3. Coverage floors

- The hermetic whole — unit + integration combined — must clear **90%**
  coverage measured with branch analysis enabled
  (`[tool.coverage.run] branch = true`) over first-party source only
  (`[tool.coverage.run] source` lists the seven workspace packages). Test
  files are verification artifacts, not shipped code; counting their
  near-100% lines inflated the denominator and hid gaps in `src/`. The
  floor is a ratchet: it rises as measured gaps close and never falls.
- The floor is enforced through `[tool.coverage.report] fail_under = 90` in
  pyproject — exactly one number and one enforcement point; do not add
  per-tier floors. Locally, a bare full-suite run (`pytest --cov`) applies
  it. In CI, per-package matrix legs measure coverage with the gate disabled
  (`--cov-fail-under=0`: a leg cannot clear the combined floor, so the flag
  neutralizes rather than competes) and the aggregate job applies the floor
  once after merging all legs (`coverage combine`, then `coverage report`).
- Pre-commit runs tests with **no coverage gate at all**: no `--cov`,
  no `--cov-fail-under`. Staged-work feedback measures correctness, not
  coverage.
- Rationale: the floor exists to keep pressure on untested code without
  demanding brittle mocks of external dependencies. The number is the
  honest measured baseline of first-party source, not an aspiration; it
  ratchets up as gaps close.

## 4. Skip policy

- Env-guarded skips (`pytest.mark.skipif`) are permitted only when the guard
  reflects a genuinely environmental condition the CI runner cannot satisfy
  (for example, platform-specific behavior).
- The skip reason must justify the skip: name the environmental condition,
  state why CI cannot satisfy it, and note what would remove the need for the
  skip.
- Skips are expected to be rare. An unconditional skip is prohibited; a
  growing skip count is a defect to fix, not a norm.

## 5. Canonical commands

All invocations use the module form (`uv run python -m <module> ...`). On
Windows, direct executable spawn is blocked by Application Control policy;
only the module invocation form is guaranteed to work everywhere.

All invocations run from the **repository root**. Pytest configuration lives
only in the root pyproject (`--import-mode=importlib`, `python_files`,
`testpaths`); pytest has no config-inheritance mechanism, so running from
inside a package directory bypasses it and silently changes collection
semantics — the cross-package test-shadowing failure the importlib mode
exists to prevent. (Ruff is exempt: subpackages inherit via
`extend = "../pyproject.toml"`.)

```bash
# Full suite with the coverage floor (bare run)
uv run python -m pytest --cov

# One package, both tiers
uv run python -m pytest mvgeos-agent/tests

# One tier, one package
uv run python -m pytest mvgeos-agent/tests/unit
uv run python -m pytest mvgeos-agent/tests/integration

# One file
uv run python -m pytest mvgeos-agent/tests/unit/types.py
```

Run the full output — no tail truncation. Chain commands in PowerShell with
`;`, never `&&`.

## 6. Enforcement map

| Requirement | Enforced by | When |
| --- | --- | --- |
| Hermetic unit + integration run with the combined coverage floor | CI per-package matrix (`pytest --cov` per leg) plus an aggregate job (`coverage combine`, then `coverage report` against pyproject `fail_under`) | every push to `main` and PR |
| Lint, format, types | CI lint job (`ruff check`, `ruff format --check`, `mypy .`) | every push to `main` and PR |
| Fast staged-work checks (lint, format, types, unit tests) | pre-commit hooks | every local commit |
| Release-time full verification (full suite + coverage floor) | release workflow | every `v*` tag |

The CI contract: CI runs the hermetic unit+integration suite with the
first-party-source coverage floor on every push and pull request; the release
workflow performs full verification at tag time; pre-commit provides fast feedback on
staged work without any coverage gating.

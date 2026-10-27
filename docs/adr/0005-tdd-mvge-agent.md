# ADR 0005: TDD for All Code

## Status

Accepted

## Context

Mvge aims for 90%+ test coverage with all tests passing. The project follows Test-Driven Development (TDD) as a binding constraint for all code produced.

## Decision

All code follows the red-green-refactor cycle:
1. Write a failing test first
2. Write minimal implementation to pass the test
3. Refactor with tests still passing

Tests use `pytest` with `pytest-asyncio` for async test support. Test files mirror source file structure under `tests/` directories. Coverage is enforced at 90%+ via `pytest-cov`.

## Consequences

- Every module has a corresponding test file
- No implementation code is committed without a failing test first
- CI enforces both test pass (100%) and coverage threshold (90%+)
- Test structure follows: unit tests (module-level), harness tests (integration), and integration tests (full flow)

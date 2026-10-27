# Contributing

## Setup

```bash
git clone https://github.com/IAmNo1Special/mvge.git
cd mvge
uv sync
pre-commit install
```

## Finding a First Task

Scoped, unassigned work carries the `help wanted` label:

<https://github.com/IAmNo1Special/mvge/issues?q=is%3Aissue+is%3Aopen+label%3A%22help+wanted%22>

Contributing needs no published release — a clone and `uv sync` are enough. The
setup above is all the tooling there is; there is nothing to install first.

Before writing code:

1. Read [`CONTEXT.md`](CONTEXT.md) for the domain vocabulary (Mvge, Spell, Realm,
   Tome, Rune, Sigil). Use those words, not the generic ones.
2. Read `AGENTS.md` for project contracts and testing expectations.
3. Read a recent test in the file you are about to change and copy its shape.
   This project is TDD: failing test first, then implement.
4. Comment on the issue before you start, so two people do not build the same
   thing.

## Code Style

- Follow the Google Python Style Guide
- Ruff for linting and formatting
- mypy strict mode for type checking
- 88-character line length

## Testing

Testing standards are defined in [TESTING.md](TESTING.md). Canonical invocations use the module form (`uv run python -m pytest ...`) — on Windows, direct executable spawn is blocked by Application Control policy.

```bash
# Full suite with the 90% combined coverage floor (fail_under)
uv run python -m pytest --cov

# Unit tests
uv run python -m pytest tests/unit
```

Requirement: all tests passing, 90% combined coverage floor enforced via pyproject.

## Architecture Decision Records

See `docs/adr/` directory for all architectural decisions. To propose a new ADR:

1. Create a new file `docs/adr/NNNN-short-name.md`
2. Follow the ADR template in `docs/adr/0001-monorepo-with-uv.md`
3. Submit as part of a PR with a description of the decision

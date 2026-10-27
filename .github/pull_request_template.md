## Description

Please provide a concise description of the motivation and changes included in this pull request.

Fixes #(issue)

## Type of Change

- [ ] `feat`: New feature or capability
- [ ] `fix`: Bug fix
- [ ] `perf`: Performance improvement
- [ ] `refactor`: Code change that neither fixes a bug nor adds a feature
- [ ] `docs`: Documentation updates
- [ ] `test`: Adding or modifying tests
- [ ] `build` / `ci`: Packaging, dependencies, or workflow changes
- [ ] `chore`: Maintenance tasks

## Packages Affected

- [ ] `mvge.core`
- [ ] `mvge.agent`
- [ ] `mvge.provider`
- [ ] `mvge.tome`
- [ ] `mvge.runes`
- [ ] `mvge.cli`
- [ ] `mvge-desktop`
- [ ] `mvge` (root distribution)

## Quality Gates Checklist

- [ ] Conventional commit messages used (`feat(scope):`, `fix(scope):`, etc.) without emojis
- [ ] Code follows Google Python Style Guide with 88-character line length limit
- [ ] `uv run ruff check` passes with zero errors
- [ ] `uv run ruff format --check` passes with zero diffs
- [ ] `uv run python -m mypy .` passes in strict mode
- [ ] Unit/integration tests added following the [Testing Standards Charter](TESTING.md) (flat naming, no `__init__.py` in test dirs)
- [ ] Tests are hermetic (no network access, no wall-clock dependencies, mocks at seams)
- [ ] `uv run python -m pytest --cov` passes and satisfies the combined 90% coverage floor (`fail_under = 90`)

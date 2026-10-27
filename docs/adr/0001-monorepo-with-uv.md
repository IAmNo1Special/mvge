# ADR 0001: Monorepo with uv

## Status

Accepted

## Context

Mvge needs a Python monorepo structure. The project consists of multiple packages that share types and depend on each other. We need a fast, reliable package manager and workspace tool.

`uv` is a modern Python package manager written in Rust that offers:

- Extremely fast dependency resolution and installation
- Native workspace support via `uv.lock` and `[workspace]` in `pyproject.toml`
- Consistent builds across environments
- Built-in virtual environment management

## Decision

Use `uv` exclusively as the package manager. All workspace configuration lives in the root `pyproject.toml` under `[workspace]`. Each package has its own `pyproject.toml` with its dependencies.

## Consequences

- `uv sync` from root installs all workspace dependencies
- `uv add` adds packages and updates `uv.lock`
- No `package-lock.json` equivalent needed
- `uv.lock` is the source of truth for dependency versions
- All CI and developer workflows must use `uv` commands, not `pip`
- Developers must have `uv` installed (available via `pipx install uv` or `brew install uv`)

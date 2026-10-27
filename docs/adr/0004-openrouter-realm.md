# ADR 0004: OpenRouter as First Provider

## Status

Accepted (amended 2026-10-05: default Realm changed to OpenCode Zen)

> **Amended 2026-10-05.** The decision — build and validate the Realm
> abstraction against one real provider before adding a second — is what made a
> second Realm cheap, and nothing here is in question. What moved is two facts it
> stated as fixed: which provider is the default, and where its implementation
> lives. See [ADR 0015](0015-opencode-zen-default-realm.md).
>
> - **Default Realm.** `nvidia/nemotron-3-ultra-550b-a55b:free` was at capacity on
>   three of three clean-machine runs (SOM-29), so the default is now
>   `opencode/space-bunny-free`. OpenRouter remains available and remains the wider
>   catalog; it is simply no longer what a first run reaches for.
> - **Implementation location.** "MVP includes only the OpenRouter provider
>   (`OpenRouterRealm`)" described an in-package class. `OpenRouterRealm` ships as
>   the `openrouter-realm` Rune in `mvge-marketplace` and registers itself at
>   runtime, so `mvge-provider` contains the Realm *protocol* and no concrete
>   Realm. That is the shape the package's own AGENTS.md now documents.
>
> The claim worth keeping from the original: the decision was made to build the
> abstraction, and the second provider arrived as a consequence rather than a
> goal.

## Context

Mvge needs at least one provider (realm) to be functional. OpenRouter provides unified access to 60+ LLM models through a single API. Starting with OpenRouter allows the provider abstraction to be built and validated before expanding.

## Decision

MVP includes only the OpenRouter provider (`OpenRouterRealm`). The Realm seam is declared in `mvge_provider.realm_seam`, which lists every member a Realm supplies and — for each — whether the engine calls it, whether a Realm may override it, and whether it is engine machinery it should not touch. The `Realm` base (`mvge_provider.base`) is the minimal contract: `stream`, `complete`, `close`, plus `is_router`. `SSEStreamingRealm` is the implementation a RealM speakers of the Chat Completions dialect subclass, and it declares the required pair as `@abstractmethod`. The `Model` type holds provider/model identification and configuration. A `models.json` file in the config directory holds the model catalog.

## Consequences

- Single dependency on OpenRouter API for v0.1
- Provider abstraction (Realm protocol) is implemented first, enabling future providers
- Model configuration is externalized to `models.json`
- Authentication via API key stored in `~/.agents/.mvge/auth/`


## Amendment — 2026-10-11

Amended in the same commit that published `mvge_provider.realm_seam` and moved
the two shipped Realm runes onto a package-root import. The Realm seam is now
declared rather than implicit, and a member the engine never calls
(`_prepare_request_url_and_headers`) is marked as a Rune convention so a Realm
author can find it. `mvge-provider/tests/unit/realm_seam.py` holds the
declaration to the implementation.

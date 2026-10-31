# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.32] - 2026-10-31


### Bug Fixes

- **cli:** suppress recovered-turn note when a turn ends in an unrecovered error (0caab18)


### Chores

- update CHANGELOG.md [skip ci] (b28fc6c)

- **release:** v0.2.32 (a49ba5c)


## [0.2.31] - 2026-10-31


### Bug Fixes

- **cli:** bound and surface mvge setup install silent waits (b76518e)

- **runes:** name the rune in sigil timeout and exception diagnostics (27cbe81)

- **cli:** read seekable stdin when select has no fileno (f7458d4)


### Chores

- update CHANGELOG.md [skip ci] (38f4fcb)

- **release:** v0.2.31 (5cd9bd8)


### Tests

- **cli:** treat Windows WinError 87 as dead process in streaming kill probe (63f7fbe)


## [0.2.30] - 2026-10-31


### Bug Fixes

- **cli:** abort before cancelling on REPL/TUI Ctrl-C so wait:false children stop (0428eeb)

- **agent:** child dispatch set equals declared scope at spawn (49c67d1)


### Chores

- update CHANGELOG.md [skip ci] (9dbdf0e)

- **release:** v0.2.30 (9e30a5d)


## [0.2.29] - 2026-10-31


### Chores

- update CHANGELOG.md [skip ci] (e2c9b8e)

- **release:** v0.2.29 (e90ba21)


### Features

- **cli:** note recovered errors in print mode (4abe040)


## [0.2.28] - 2026-10-31


### Bug Fixes

- **profiles:** include codeact-rune in coding profile (fde6eda)


### Chores

- update CHANGELOG.md [skip ci] (2b4989c)

- **release:** v0.2.28 (8067086)


## [0.2.27] - 2026-10-31


### Bug Fixes

- **agent:** report killed sub-Mvges as terminated instead of canceling (9b83fe3)


### Chores

- **release:** v0.2.27 (9b20070)


## [0.2.26] - 2026-10-31


### Bug Fixes

- **agent:** run rehydrate before prompt assembly during reload (fd001e8)


### Chores

- update CHANGELOG.md [skip ci] (af2728f)

- **release:** v0.2.26 (0ac7df5)


## [0.2.25] - 2026-10-31


### Bug Fixes

- **cli:** finalize narration on renderer error path (15d0da6)


### Chores

- update CHANGELOG.md [skip ci] (f17a596)

- **release:** v0.2.25 (bc9cc45)


## [0.2.24] - 2026-10-30


### Bug Fixes

- **cli:** render unrecovered spell errors deterministically in print mode (2498e1c)


### Chores

- update CHANGELOG.md [skip ci] (b5e3209)

- **release:** v0.2.24 (d875b95)


## [0.2.23] - 2026-10-30


### Bug Fixes

- **agent:** carry persisted title in SESSION_START payload on resume (ab9b99e)


### Chores

- update CHANGELOG.md [skip ci] (bc66603)

- **release:** v0.2.23 (8ea1559)


## [0.2.22] - 2026-10-30


### Chores

- update CHANGELOG.md [skip ci] (9346d4c)

- **deps-dev:** bump pytest-asyncio from 1.4.0 to 1.4.1 (e4c431a)

- **release:** v0.2.22 (2664283)


### Features

- **runes:** sanitize NO_PROXY before rune module execution and factory calls (293c90a)


## [0.2.21] - 2026-10-30


### Chores

- update CHANGELOG.md [skip ci] (955fb4b)

- update CHANGELOG.md [skip ci] (946504c)

- update CHANGELOG.md [skip ci] (236cf8b)

- update CHANGELOG.md [skip ci] (39366f9)

- **release:** v0.2.21 (18032ba)


## [0.2.20] - 2026-10-30


### Chores

- update CHANGELOG.md [skip ci] (2f5e52e)


### Features

- **core,runes:** publish NO_PROXY sanitizer for runes building their own httpx clients (388b58b)


## [0.2.19] - 2026-10-30


### Bug Fixes

- **core:** add per-spell dispatcher timeout override, opt summon out of the 30s wall clock (c19cf35)

- **agent:** dispose retired runes on reload and serve commands from the live runner (828a15d)

- **provider,runes:** sanitize bracketed NO_PROXY at every httpx construction site (e9fbd73)

- **agent:** thread the parent abort signal into child sub-Mvge turns (da46a4f)

- **cli:** render /reload and rune command outcomes in the REPL (df149e4)

- **cli:** render seven silent outcomes in the REPL (cf5f6ac)


### Chores

- update CHANGELOG.md [skip ci] (b0e0ab6)

- **release:** v0.2.19 (a6b5798)


## [0.2.18] - 2026-10-29


### Bug Fixes

- **tome:** persist session titles into the tome header (b34df9f)

- **agent:** stop trust check from hijacking print-mode output (0f13aae)

- **agent:** bare config dirs are not installed agents (48bee9e)

- **cli:** rune list empty message names the resolved extensions dir (28e927f)

- **cli:** warn the user about untrusted workspaces in print mode (36bd655)

- **core:** surface spell error_message in the model-facing result (bd1be25)

- **runes:** ignore hidden dirs when discovering extension manifests (5dc5266)

- **cli:** sanitize bracketed NO_PROXY at runtime startup (613ad0e)

- **agent:** bind the resolved model into the runner context (5a46ec2)

- **tui:** give the input control its own Ctrl-C interrupt binding (5f8eed3)

- **cli:** render terminating spell results in print mode (e134823)

- **provider,core:** stop silently dropping casts with corrupted or unknown spell names (c224dd7)


### Chores

- update CHANGELOG.md [skip ci] (782eb10)

- **release:** v0.2.18 (335015b)


### Tests

- **agent:** update trust tests to the no-steering contract (7edf30e)

- **cli:** lock off-terminal realm self-heal as intended (8577226)

- **core:** make NO_PROXY sanitizer tests Windows-safe (828fa7f)


## [0.2.17] - 2026-10-28


### Bug Fixes

- **agent:** resolve per-user uv for PEP 723 scripts; isolate test env (3dd9f74)


### Chores

- update CHANGELOG.md [skip ci] (7ce6921)

- **release:** v0.2.17 (73398f5)


## [0.2.16] - 2026-10-28


### Bug Fixes

- **agent:** clear agent config file on reset so provenance reports defaults (a4aad5c)


### Chores

- update CHANGELOG.md [skip ci] (71c6b2c)

- **release:** v0.2.16 (8e4a95c)


## [0.2.15] - 2026-10-28


### Chores

- update CHANGELOG.md [skip ci] (20591f2)

- **release:** v0.2.15 (8617f86)


### Features

- **cli:** accept md as an alias for markdown in tome export (6e10bfd)


## [0.2.14] - 2026-10-28


### Bug Fixes

- **scripts:** sync verify_install key table with opencode-go (7f03827)


### Chores

- update CHANGELOG.md [skip ci] (caeca9a)

- **release:** v0.2.14 (266f155)


### Features

- **provider:** register opencode-go realm tables (ea0c261)

- **provider:** move opencode bases to inference endpoints and carry tome session id (00ef789)

- **provider:** mark opencode-go -free models as free (d0c58fb)


### Tests

- **provider:** clarify Go-no-default and harness seam comments (29c26c4)


## [0.2.13] - 2026-10-28


### Chores

- update CHANGELOG.md [skip ci] (fe89d31)

- **release:** v0.2.13 (bba509b)


### Features

- **cli:** auto-bootstrap coding profile on interactive first run (74373d9)


## [0.2.12] - 2026-10-27


### Chores

- update CHANGELOG.md [skip ci] (2c9c4ce)

- **deps:** bump the uv group across 1 directory with 3 updates (b4f1b75)

- **deps:** bump portalocker from 4.4.0 to 4.4.1 (470b05e)

- **release:** v0.2.12 (1d40ea6)


### Features

- **cli:** bind non-interactive approval modes in TUI (92de5f9)


## [0.2.11] - 2026-10-27


### Bug Fixes

- **cli:** carry the callback Realm choice into _run_agent, name the key prompt (a7920f0)

- **agent:** snapshot reports the agent's own model, not its config's (d6399a6)

- **cli:** do not load an OpenRouter key on the deferred credential path (d9caed3)

- **ci:** advance bump version past already-taken tags (69248b2)


### Chores

- update CHANGELOG.md [skip ci] (5a7e098)

- **release:** v0.2.11 (38b357d)


### Features

- **provider:** realm-first default models, remove global default model **BREAKING CHANGE** (9d134bf)

- **provider:** pollinations as the keyless bootstrap fallback (a2c3a02)


## [0.1.5] - 2026-10-27


### Bug Fixes

- **tome:** reject paths escaping the tome directory in tome_file (1933983)

- **tome:** reject paths escaping the tome directory in tome_file (6182b45)


### Chores

- update CHANGELOG.md [skip ci] (c5cc23f)

- **release:** v0.1.5 (9ccbe8a)


## [0.1.4] - 2026-10-27


### Bug Fixes

- **runes:** clear rune-registered skills on reload (v3 port to mvge) (063a37c)

- **runes:** clear rune-registered skills on reload (v3 port to mvge) (61af1b7)


### Chores

- update CHANGELOG.md [skip ci] (8f7a958)

- **deps-dev:** bump ruff from 0.17.0 to 0.17.2 (21c720c)

- **release:** v0.1.4 (bb8171e)


### Documentation

- add PyPI and CI status badges to README (1992998)


## [0.1.3] - 2026-10-25


### Chores

- update CHANGELOG.md [skip ci] (1eea37f)

- **release:** v0.1.3 (3f6218c)


### Documentation

- **marketplace:** update marketplace references to mvge-marketplace (5bac9dc)


### Refactoring

- **engine:** purge all remaining mvgeos references (c0b922c)


## [0.1.2] - 2026-10-25


### Bug Fixes

- **ci:** copy uv and uvx into sandbox bin so verify_install finds uvx in runner (5d72411)


### Chores

- update CHANGELOG.md [skip ci] (75da476)

- **release:** v0.1.2 (83f340c)


## [0.1.1] - 2026-10-25


### Chores

- update CHANGELOG.md [skip ci] (bd38972)

- **release:** v0.1.1 (e4b2726)


### Continuous Integration

- **workflow:** set coverage artifact path and fix mypy exclude patterns (bfc2ac7)


### Documentation

- prune obsolete technical debt plans and redundant specifications (d21623c)


### Features

- **gui:** implement adaptive canvas, in-flight steering, and rune UI contributions (ADR-0026, ADR-0027) (500be01)

- **gui:** integrate nicegui-ai component primitives (ab79826)

- **gui:** adopt Variant B Ambient Glass Studio layout in chat panel (1223f52)

- **gui:** extract mvgeos-gui to standalone mvge-desktop repository (bec59dd)


### Refactoring

- **core:** unify workspace into single mvge package (b080b2b)

- **cli:** remove backward compatibility alias and reset version to 0.1.0 (098fbe4)

- **docs:** update repository name and links to mvge (b8ed3f0)


### Tests

- **agent:** make profile installer tests hermetic and align with default minimal runes (2022307)


## [0.7.8] - 2026-10-21


### Chores

- update CHANGELOG.md [skip ci] (e28149f)

- **release:** v0.7.8 (d5faf7a)


### Features

- **engine:** resolve technical debt across determinism, scale, and resilience (phases 1-3) (0d6598c)


## [0.7.7] - 2026-10-20


### Bug Fixes

- **cli:** default _run_agent bootstrap to False and add unit tests (67ab413)

- **test:** support crossplatform windows fcntl mock and expand branch coverage (74057da)

- **test:** use cross-platform path assertions in sub_mvge and rpc_mode tests (14910f1)


### Chores

- update CHANGELOG.md [skip ci] (337a3e2)

- **deps-dev:** bump pre-commit from 4.6.2 to 4.7.0 (64035be)

- **release:** v0.7.7 (bbee77d)


### Features

- **engine:** implement sub-agents, active discovery, spell context, and rpc mode (d1d580a)


### Refactoring

- **runes:** split SkillCatalog out of RuneRunner (f5ab2a3)


## [0.7.6] - 2026-10-18


### Chores

- update CHANGELOG.md [skip ci] (91b6d9a)

- **deps:** bump typer from 0.27.2 to 0.27.3 (18d884d)

- **deps:** bump pydantic from 2.13.5 to 2.14.1 (94a6274)

- **deps-dev:** update uv-build requirement (72cbf33)

- **deps-dev:** bump ruff from 0.16.10 to 0.17.0 (b280567)

- **deps:** bump nicegui from 3.17.1 to 3.18.0 (56677f1)

- **release:** v0.7.6 (38e2029)


### Features

- **core:** add content block normalization seam (#145) (#236) (8f41398)


## [0.7.5] - 2026-10-17


### Chores

- update CHANGELOG.md [skip ci] (f15ed5c)

- **release:** v0.7.5 (b3ecafb)


### Features

- **harness:** implement Split Capabilities seam (#165) (#234) (23fcbc2)


## [0.7.4] - 2026-10-16


### Chores

- update CHANGELOG.md [skip ci] (2f9f0e2)

- **release:** v0.7.4 (a738f82)


### Documentation

- **adr:** re-derive ADR-0012 section 2 per issue #164 (62005e7)

- **adr:** fix ruff formatting in ADR-0012 code blocks (701c0df)

- **adr:** remove stale with_model and Mvge-owned reload_spells text from ADR-0012 (46d6a6f)


### Features

- **sigil:** one module owns every sigil payload (e05b7a8)


## [0.7.3] - 2026-10-14


### Bug Fixes

- **cli:** propagate --agent-name before a subcommand to that subcommand (b36f545)


### Chores

- update CHANGELOG.md [skip ci] (b9e5288)

- **release:** v0.7.3 (217ab66)


## [0.7.2] - 2026-10-14


### Bug Fixes

- **gui,cli:** one owner for the active turn (60dde8a)


### Chores

- update CHANGELOG.md [skip ci] (3ba6ffe)

- **release:** v0.7.2 (3abb448)


## [0.7.1] - 2026-10-14


### Bug Fixes

- **verify:** wait for the release to reach the PyPI index before probing (2022939)


### Chores

- update CHANGELOG.md [skip ci] (ce4a885)

- **release:** v0.7.1 (785167b)


### Documentation

- **runes:** correct the contract module's own account of mvgeos_provider.sse (3f2b2a8)


## [0.7.0] - 2026-10-12


### Chores

- update CHANGELOG.md [skip ci] (39edefb)

- run the formatter over the files it was never run on (5fa14b9)

- **release:** v0.7.0 (d54370d)


### Documentation

- **provider:** declare the Realm seam (2e6e4d8)

- correct the record to match the code (c56cff4)


### Refactoring

- **agent:** resolve which Mvge runs in one module (149faa5)

- **runes:** own the catalog in one module (b802422)

- **core:** remove the telemetry module **BREAKING CHANGE** (d38c55b)


### Tests

- **runes:** prove the marketplace contract check can fail (079a51f)


## [0.6.30] - 2026-10-11


### Chores

- update CHANGELOG.md [skip ci] (c2b1f3d)

- **release:** v0.6.30 (96f960d)


### Features

- **core:** default to openrouter/free, and require the key it needs (369fdcb)


## [0.6.29] - 2026-10-11


### Bug Fixes

- **core:** fail the suite if the default cannot answer a stranger (7ac251d)


### Chores

- update CHANGELOG.md [skip ci] (6c55c21)

- **deps:** bump multidict from 6.7.1 to 6.9.1 (2ae8195)

- **release:** v0.6.29 (1f2c7dc)


### Documentation

- **adr:** record the measurement that overturned ADR 0018 (4d2bece)


## [0.6.28] - 2026-10-11


### Chores

- update CHANGELOG.md [skip ci] (5d1bf6d)

- **release:** v0.6.28 (e384361)


### Continuous Integration

- **publish:** wait for the index before claiming a release is installable (5a204b3)


### Documentation

- **release:** record the 2026-10-10 token bridge and what still gates releases (98069b6)


### Features

- **core:** default to a free Zen endpoint that is not the one saturating (8d05ada)


### Tests

- **ci:** compile the python embedded in workflow heredocs (32fc2f3)

- **ci:** syntax-check every workflow run block as bash (b823865)

- **ci:** syntax-check every workflow run block as bash (ffb2f8d)


## [0.6.27] - 2026-10-10


### Bug Fixes

- **ci:** give verify the preflight job it reads its VERSION from (8ab2151)


### Chores

- update CHANGELOG.md [skip ci] (ffd11e9)

- **release:** v0.6.27 (a948925)


## [0.6.26] - 2026-10-10


### Bug Fixes

- **publish:** pin internal dependency constraints with ==, not >= (378b16c)

- **ci:** repair the indentation I broke in the bump job's heredoc (380ab5d)


### Chores

- update CHANGELOG.md [skip ci] (6491e0d)

- **release:** v0.6.26 (c373ce6)


## [0.6.25] - 2026-10-10


### Chores

- update CHANGELOG.md [skip ci] (28bc168)

- **bridge:** remove the temporary token upload bridge (806d4fb)

- **release:** v0.6.25 (eb6de22)


### Continuous Integration

- **publish:** let the half-land gate run, and make it check the version (#210) (974a8f1)

- **bridge:** token-auth upload of the two refused v0.6.24 wheels (b33a6d9)


## [0.6.24] - 2026-10-10


### Chores

- update CHANGELOG.md [skip ci] (7bd6449)

- **release:** v0.6.24 (3d4b5e5)


### Features

- **scripts:** measure release automation so no axis is hand-counted (#175) (28a3704)


## [0.6.23] - 2026-10-10


### Chores

- update CHANGELOG.md [skip ci] (23d662e)

- **deps:** bump python-dotenv from 1.2.3 to 1.2.4 (#184) (f37ee3d)

- **deps-dev:** bump mypy from 2.3.1 to 2.4.0 (#185) (4415e08)

- **deps-dev:** bump ruff from 0.16.8 to 0.16.10 (#186) (d5c0424)

- **deps:** bump virtualenv (#189) (ef03776)

- **release:** v0.6.23 (e4d5f87)


### Continuous Integration

- **release:** run the install gate on a Realm that needs no credential (#199) (8f7a550)


### Features

- **cli:** `mvgeos config refresh-models` so a clean machine can escape the stale catalog (#173) (e8de2da)


## [0.6.22] - 2026-10-10


### Chores

- update CHANGELOG.md [skip ci] (a3c1869)

- **release:** v0.6.22 (b1161d5)


### Features

- **core:** name the project skills layer, and anchor it (#209) (65c0940)


## [0.6.21] - 2026-10-10


### Bug Fixes

- **cli,provider:** a Realm the CLI cannot use keyless (#204) (51ef180)


### Chores

- update CHANGELOG.md [skip ci] (12eee4a)

- **release:** v0.6.21 (203318d)


### Documentation

- **readme:** the first run uses the Realm the product ships (#208) (cc8e3fd)


## [0.6.20] - 2026-10-10


### Bug Fixes

- **publish:** restore OIDC trusted publishing now that all eight are registered (#207) (a35d520)


### Chores

- **release:** v0.6.20 (b16c9b4)

- update CHANGELOG.md [skip ci] (77bb770)


## [0.6.19] - 2026-10-10


### Bug Fixes

- **verify_install:** let the gate run on a runner where bwrap is installed and denied (#205) (742ed33)


### Chores

- update CHANGELOG.md [skip ci] (47b8a4c)

- **release:** v0.6.19 (8c240b4)


### Documentation

- **release:** a fix to the tooling cannot rescue the tag it was cut before (#206) (2b5154b)


## [0.6.18] - 2026-10-09


### Bug Fixes

- **publish:** decide what is missing by filename, because uv cannot resume a partial release (#203) (85a65c2)

- **release:** pin uv so a re-dispatch rebuilds the bytes already on PyPI (#202) (655c05b)


### Chores

- update CHANGELOG.md [skip ci] (3e225ab)

- **release:** v0.6.18 (e14fea2)


### Documentation

- **release:** put the trusted publisher on each project page, and name a refused project (#198) (9a1e018)


### Reverts

- **publish:** upload with a stored token again, and make the gate catch a half-land (#201) (689a2cc)


## [0.6.17] - 2026-10-09


### Chores

- update CHANGELOG.md [skip ci] (77387ba)

- **release:** v0.6.17 (71a0810)


### Continuous Integration

- **release:** prove the published mvgeos completes a real task (3fbed01)

- **release:** sandbox the install gate so it cannot see this machine (c802e1e)

- **publish:** authenticate to PyPI with OIDC trusted publishing instead of a stored token (a99270f)


### Documentation

- **readme:** stop the recording caption implying a PyPI install (2835aab)

- **agents:** put the publish dispatch back where it actually lives (416075d)


### Tests

- **verify_install:** skip the Linux sandbox-argv test on Windows (ad6d9b0)


## [0.6.16] - 2026-10-07


### Bug Fixes

- **agent:** a missing credential names the Realm that wanted one (41bbaaf)

- **provider:** stop asking a Realm that serves without a credential for one (2ddce83)


### Chores

- update CHANGELOG.md [skip ci] (4ac6fcb)

- **release:** v0.6.16 (0c4ab5b)


## [0.6.15] - 2026-10-07


### Bug Fixes

- **provider:** never hand one Realm's credential to another Realm's host (870dfa8)


### Chores

- update CHANGELOG.md [skip ci] (23553e0)

- **release:** v0.6.15 (2b926da)


## [0.6.14] - 2026-10-06


### Bug Fixes

- **gui,adr:** derive the provider tier from the model, and record what Zen actually does (#193) (d36f3e0)


### Chores

- update CHANGELOG.md [skip ci] (15362b9)

- **release:** v0.6.14 (46d8d12)


## [0.6.13] - 2026-10-06


### Chores

- update CHANGELOG.md [skip ci] (7c05328)

- **release:** v0.6.13 (c8985d5)


### Documentation

- **adr:** record what the live gateway actually measured (3a84f2a)


### Features

- **core:** land agent_spells_dir and the ADR 0015 sandbox fixes (#190) (04a2fc8)


## [0.6.12] - 2026-10-06


### Chores

- update CHANGELOG.md [skip ci] (da3ffba)

- **release:** v0.6.12 (fcbbe4b)


### Documentation

- **readme:** credit the first outside contributor, and point newcomers at help wanted (83a086d)

- **readme:** link the Contributors discussion from the credit table (d288ab9)


### Features

- **provider:** make OpenCode Zen the default Realm (#188) (799bf67)


## [0.6.11] - 2026-10-06


### Bug Fixes

- **cli:** report already-written work when a one-shot run exits non-zero (#174) (f1ed8e7)

- **provider:** give capacity saturation a minutes-scale retry budget (#178) (b01d754)


### Chores

- update CHANGELOG.md [skip ci] (a3e5486)

- **release:** v0.6.11 (cb5d8a8)


### Continuous Integration

- **scorecard:** name the gate reason in the log, and keep the failure propagating (#180) (ad6e68f)

- **release:** dispatch publish.yml from the bump job (351e581)


### Documentation

- lead the README with the product instead of the package table (#167) (d31cbfc)


### Tests

- **scripts:** test the release helper instead of excluding scripts/ from measure 1 (5365a6a)


## [0.6.10] - 2026-10-05


### Bug Fixes

- **scripts:** re-record the baseline on a commit a reader can resolve (#183) (4c00e96)


### Chores

- update CHANGELOG.md [skip ci] (32ff3be)

- **release:** v0.6.10 (2b93950)


## [0.6.9] - 2026-10-05


### Bug Fixes

- **scripts:** refuse to publish a baseline stamped with an unresolvable commit (#181) (f946644)


### Chores

- update CHANGELOG.md [skip ci] (58fafaa)

- **release:** v0.6.9 (73b99d4)


## [0.6.8] - 2026-10-05


### Bug Fixes

- **release:** read the PyPI quota as a window, and stop retrying a 429 (#179) (4671829)


### Chores

- update CHANGELOG.md [skip ci] (065ba58)

- **release:** v0.6.8 (794e245)


## [0.6.7] - 2026-10-05


### Bug Fixes

- **scripts:** report the commit each scorecard row was measured at (6d655dd)

- **scripts:** count every language in measure 1, and run the scorecard on a schedule (4bffc3c)

- **scripts:** stamp the snapshot with its date and each row's commit, and correct a rank (d8d7b62)

- **scripts:** re-measure the baseline on the merge, not on the branch (f4c65e6)

- **cli:** give `mvge install` the deps flag `rune install` already has (8ce22c0)

- **release:** make bump land its own commit and retry a 429 publish (#177) (89ea5c0)


### Chores

- update CHANGELOG.md [skip ci] (535021d)

- **release:** v0.6.7 (3d055f3)


### Continuous Integration

- **release:** pass built distributions from preflight to the publish job (bc33229)

- **release:** run verify on a dry run only when an upload actually happened (42a2a4e)

- **scorecard:** never let a failed measurement turn main red (bf36d31)


### Features

- **scripts:** make the published scorecard reproducible (c7b14c0)


### Refactoring

- **gui:** read the engine default model instead of re-deriving it (4eca097)


### Style

- **gui:** sort the imports 4eca097 added, and close the test file (f7f21a2)


## [0.6.6] - 2026-10-05


### Chores

- update CHANGELOG.md [skip ci] (2d4340c)

- **release:** v0.6.6 (ef9c063)


### Continuous Integration

- block an ADR from describing code that no longer exists (ea28c64)

- **release:** publish the eight distributions to PyPI (#170) (47c4ebc)


### Documentation

- **adr:** record cache-free format-agnostic Tome persistence (accf23d)

- **adr:** record one resolved layer stack for the .agents protocol (877704b)

- **adr:** mark the superseded storage and layer-scheme ADRs (496245a)

- **adr:** amend Two-Layer Scaffolding and refresh the ADR index (d0e049b)

- **adr:** give every ADR a canonical status section (b4ff3c8)

- **adr:** contract the symbols each live ADR commits to (fe060ee)

- **adr:** narrow ADR-0014 to rune search paths, not persona resolution (8a7ea26)

- record the post-audit issue set and the lesson behind it (fc2ce87)

- **adr:** correct the ADR-0014 implementation note (18203eb)

- **root:** record the cross-repo check rule and retire the audit snapshot (6dbed41)

- **root:** relocate the completed capability-layer decoupling record (afbcc91)


### Refactoring

- **core:** resolve the .agents layer stack from one module (e75d4f5)


## [0.6.5] - 2026-10-04


### Bug Fixes

- **runes:** register entry-point modules in sys.modules before executing (7b013e8)


### Chores

- update CHANGELOG.md [skip ci] (e130a56)

- ignore the scratch marketplace checkout (0aabfe3)

- **release:** v0.6.5 (697bdac)


### Continuous Integration

- block engine API removals that break the marketplace (a6f38e5)


### Tests

- isolate the global .agents layer from the developer's machine (3de39ae)


## [0.6.4] - 2026-10-04


### Bug Fixes

- **agent:** resolve the coding_mvge spell fallback through the resolver (613e3a5)


### Chores

- update CHANGELOG.md [skip ci] (443746f)

- **release:** v0.6.4 (0696d5b)


### Refactoring

- **core:** route every global-layer path through the resolver (58ac1d0)


## [0.6.3] - 2026-10-02


### Bug Fixes

- **core:** relocate tome storage with the global layer (c4743c5)


### Chores

- update CHANGELOG.md [skip ci] (d3549d8)

- **release:** v0.6.3 (fbaf68e)


## [0.6.2] - 2026-10-02


### Bug Fixes

- **gui:** resolve log rotation PermissionError on Windows (096a2e3)

- **typing:** satisfy Windows mypy strict checks and skip unprivileged symlink test (399e509)

- make the strict typecheck and unit suite pass on POSIX hosts (73694e3)

- **core:** resolve the global .agents layer through one canonical function (ca3ca33)

- **gui:** stop the rollover skip guard from breaking Windows collection (6117db7)


### Build System

- **root:** pin the workspace interpreter to Python 3.13 (65efdba)


### Chores

- update CHANGELOG.md [skip ci] (b944d4b)

- **deps:** bump python-dotenv from 1.2.2 to 1.2.3 (5e40829)

- **deps:** bump portalocker from 4.3.2 to 4.4.0 (4798406)

- **release:** v0.6.2 (836b7da)


### Documentation

- **root:** correct shell-chaining guidance for non-PowerShell hosts (68eb9fe)


### Features

- **runes:** install declared deps into a shared extensions environment (5d41f5b)

- **cli:** add rune uninstall (03756b3)


### Refactoring

- **benchmarks:** remove live evaluation benchmarks and related files (c4c8d34)


### Tests

- **agent:** prove content-parts prompts reach the realm untouched (b452852)

- **root:** stop the suite from inheriting real credentials (ff68c3c)


## [0.6.1] - 2026-09-24


### Bug Fixes

- fix(gui): slash-command autocomplete emits exactly one leading slash
Command sources disagree on the slash convention: CLI keys carry it (/help), rune manifests declare bare names (adr). Any slash-prefixed name fed through the old f"/{name}" construction produced //command. Normalize with _single_leading_slash at the registry ingestion boundary, the rune-manifest boundary in state, and the get_insertion_text insertion choke point. (e490ed0)


### Chores

- update CHANGELOG.md [skip ci] (7d62135)

- **release:** v0.6.1 (6d38750)


## [0.6.0] - 2026-09-23


### Chores

- update CHANGELOG.md [skip ci] (810b2a3)

- **deps:** bump click from 8.4.2 to 8.5.0 (#153) (604099c)

- **deps:** bump nicegui from 3.16.0 to 3.17.1 (#151) (d6a3503)

- **deps-dev:** bump git-cliff from 2.13.1 to 2.14.2 (#152) (05bd55e)

- **deps:** bump packaging from 26.2 to 26.3 (#148) (ee3f18b)

- **deps-dev:** bump ruff from 0.16.7 to 0.16.8 (#147) (d662647)

- **release:** v0.6.0 (3e70a24)


## [0.5.8] - 2026-09-23


### Bug Fixes

- **runes:** tolerate vanishing stale bytecode with unlink(missing_ok=True) (adfbce9)

- **runes:** bound RuneWatcher.stop() join; resolve watch target to absolute (819e01e)

- **agent:** robust rune lifecycle shutdown; backoff between retired-shutdown retries (e600251)

- **agent:** catch all audit-append failures in reload audit (af8a7fa)

- **agent:** preserve pending reload when the turn-boundary drain fails (da1d09d)

- **agent:** report queued reload as RELOAD_QUEUED, not RELOADED (6eb648f)

- **runes:** ignore root-level audit files in the rune watcher (41bcdd9)

- **runes:** cancel pending debounce on watcher stop (d4ab9ea)

- **agent:** serialize concurrent reload() executions with a lock (5596add)

- **agent:** replace (not merge) rune-owned providers on successful reload (c390619)

- **gui:** close out GUI reload defects 1-7 and polish notes a-e (9646cd4)

- **runes:** anchor project rune path to project directory (d4d21db)

- **gui:** suggest installed rune slash commands in chat autocomplete (62f1570)

- **gui:** scan lowercase skill.md manifests in skill discovery (f62faa7)

- **windows:** normalize paths and isolate config in CI (4336c1e)

- **windows:** resolve handler extensions dir at construction (ea4c518)

- **runes:** tolerate locked bytecode file during concurrent loads (59085bc)


### Chores

- update CHANGELOG.md [skip ci] (2452915)

- **ci:** re-trigger crossplatform tests (ec6b8ca)

- **ci:** temporary faulthandler probe for macos hang (f52a7ee)

- **release:** v0.5.8 (43be09a)


### Tests

- **runes:** red regression for cross-home rune loading (item 1, UNRESOLVED) (69c0c4b)

- **gui:** isolate OS keyring behind in-memory fake in GUI tests (64078fc)


### Style

- **agent:** ruff format collapse in rune_lifecycle shutdown (6f02ea7)


## [0.5.7] - 2026-09-22


### Bug Fixes

- **gui,agent:** route dynamic slash commands through the GUI dispatcher (5e28b7d)


### Chores

- update CHANGELOG.md [skip ci] (7ba00e9)

- **release:** v0.5.7 (e57a3b4)


### Tests

- **coverage:** cover auth, rune loader, skill installer, entry points, and settings dialog (c342838)


## [0.5.6] - 2026-09-21


### Bug Fixes

- **cli:** harden approval-mode help test against colored output (61c84ca)

- **gui:** center send/stop button and align attach with model picker (1335caa)

- **runes:** align BEFORE_MVGE_START payload fields with approved spec **BREAKING CHANGE** (5b56cfb)

- **runes:** never overwrite an existing audit archive on rotation (2028e17)

- **agent:** declare python-dotenv and httpx dependencies (d7d1d5d)

- **cli:** declare click dependency (5c1998d)

- **runes:** judge watcher ignore rules against the watched subtree (bd8ef07)

- **gui:** hide version-control internals in the file tree (407f2c1)

- **gui:** distinct status bar toggles with tooltips (345aef7)

- **gui:** diff/review empty states say what they track (2767b63)

- **gui:** marketplace empty state explains how to get packages (a8e4945)

- **gui:** skills empty states explain what skills are (8f730c4)

- **gui:** inline validation for marketplace install-from-URL dialogs (f40c6c8)

- **gui:** composer rune-missing badge names the rune (436dddd)

- **gui:** make sidebar a mobile drawer below 768px (0df34d3)

- **gui:** make settings dialog responsive with stronger backdrop (db7e02c)

- **gui:** unify button voice, strengthen hover, equalize stat cards (958dd48)

- **gui:** isolate UI state per client (91cca1c)

- **gui:** harden settings and login dialogs (bf224cc)

- **gui:** restore Ctrl+K palette chord and register keyboard handler once (d34048f)

- **gui:** make sidebar_open the single source of truth for the toggle (55bb034)

- **gui:** autofocus the Quick Switcher input on open (38a9fb4)

- **gui:** refresh sidebar on login via current_user watcher key (f21e104)

- **agent:** record project dir as tome cwd so sessions list correctly (5a0a989)

- **gui:** refresh stale @-mention index; keep one server-shared index (7ec7013)

- **gui:** refuse plan-mode toggle cleanly without API key (f481047)

- **gui:** give New Session visible feedback (navigate + toast) (096ef41)

- **gui:** distinct graceful error state for marketplace catalog failures (89f2a87)

- **gui:** open workspace files in an in-browser preview (major #6) (bf2172e)

- **gui:** apply placeholder token to all inputs and raise light contrast (714d49c)

- **gui:** replace fixed grey-9 badge backgrounds with theme tokens (a34bfa3)

- **gui:** persist settings reset to their default value (3e90211)

- **gui:** drop stale test for removed open_in_editor() (576578b)

- **cli:** skip PTY approval tests where pty is unavailable (d0c118e)

- **ci:** make Windows suite green (5165419)

- **gui:** detect @-mention index renames on Windows (4a96bae)

- **gui,agent:** route dynamic slash commands through the GUI dispatcher (0ac5ab6)

- **gui:** drop stale test for removed open_in_editor() (17e136d)

- **cli:** skip PTY approval tests where pty is unavailable (0d2b49c)

- **ci:** make Windows suite green (46bfe9d)

- **gui:** detect @-mention index renames on Windows (825b1cf)


### Chores

- update CHANGELOG.md [skip ci] (3533b48)

- **release:** v0.5.6 (6502a38)


### Documentation

- **runes:** pin audit log path semantics unambiguously (34a1d14)


### Features

- **attachments:** send files as native OpenRouter content parts (429a245)

- **runes:** extend BeforeMvgeStartData with rehydration fields **BREAKING CHANGE** (6455c29)

- **mvge:** populate BEFORE_MVGE_START payload with rehydration fields **BREAKING CHANGE** (fc00388)

- **runes:** engine-owned rune-op audit trail (bd0f477)

- **runes:** watcher trigger mode, runner refresh + rehydrate (ed56e5c)

- **provider:** add unregister_provider for reload rollback (dda59dd)

- **agent:** transactional engine reload with /reload command (a9e2a6c)

- **gui-logging:** harden crash and shutdown diagnostics (4d4e1bd)

- **gui:** add light-theme token set and theme-aware inject_theme (3dca964)

- **gui:** wire persisted theme through boot, settings, and native chrome (b681b64)


### Refactoring

- **environment:** remove engine self-mod prompt section **BREAKING CHANGE** (dcdc3fd)

- **runes:** audit log lives at .agents/extensions/audit.jsonl (93218de)

- **gui:** convert component colors to theme tokens (2c1d069)

- **gui:** move inject_theme import to module scope in app (6876ae0)


### Tests

- **agent:** cover tolerant and strict broken __init__.py discovery (04b064a)

- **agent:** packaging contract — pyproject deps must cover imports (96b4754)

- **cli:** packaging contract — pyproject deps must cover imports (714e2c8)

- **gui:** patch load_api_key_from_auth via module object, not string path (74bf991)

- **gui:** fix defective theme tests and pin native chrome theming (2cf8944)

- **coverage:** cover auth, rune loader, skill installer, entry points, and settings dialog (28106d7)

- **gui:** fix defective theme tests and pin native chrome theming (b7eb647)


### Merge

- approval gate, CLI and GUI presenters onto main (96b21ac)


### Style

- **agent:** apply ruff formatting to attachment content handling (eff48ef)

- **tests:** apply ruff format to spec-alignment test edits (c99d1d2)


## [0.5.5] - 2026-09-20


### Bug Fixes

- **gui:** bind main module at test collection, not inside tests (71c2f5a)


### Chores

- update CHANGELOG.md [skip ci] (a3c7af0)

- **release:** v0.5.5 (202234f)


## [0.5.4] - 2026-09-19


### Bug Fixes

- **gui:** settings entries open the settings modal instead of the chat view (7755c80)


### Chores

- update CHANGELOG.md [skip ci] (2fcc0e1)

- **release:** v0.5.4 (d4cd61c)


## [0.5.3] - 2026-09-19


### Bug Fixes

- **gui:** format test file for lint (1337635)


### Chores

- update CHANGELOG.md [skip ci] (2e5ac7b)

- **release:** v0.5.3 (acc8bea)


### Features

- **gui:** skills management, rune settings, remove placeholder panels (c9290e5)


## [0.5.2] - 2026-09-19


### Chores

- update CHANGELOG.md [skip ci] (dfcc91f)

- **release:** v0.5.2 (a52518b)


### Features

- **gui:** merge mvges and runes into single marketplace list with Kind filter (409e95a)


### Refactoring

- **gui:** drop Kind filter, make Mvge a rune type in Type filter (f57b87d)


### Tests

- **gui:** increase retries on timing-sensitive marketplace assertions (05cbd0d)

- **gui:** mock mvge fetchers in marketplace panel tests (ded5f47)


## [0.5.1] - 2026-09-19


### Bug Fixes

- **gui:** stop marketplace tab panels wrapping cards into side column (afe1401)


### Chores

- update CHANGELOG.md [skip ci] (b228e50)

- **release:** v0.5.1 (08b1ffa)


## [0.5.0] - 2026-09-19


### Chores

- update CHANGELOG.md [skip ci] (f71e311)

- **deps-dev:** bump ruff from 0.16.6 to 0.16.7 (#143) (511fc3d)

- **deps:** bump filelock from 3.32.5 to 3.32.6 (#144) (aa316e9)

- **release:** v0.5.0 (1e6686f)


### Features

- **cli:** rename agent command to mvge **BREAKING CHANGE** (0611322)


## [0.4.9] - 2026-09-19


### Bug Fixes

- **tome:** tolerate foreign filenames when opening sessions by path (2bfda34)


### Chores

- update CHANGELOG.md [skip ci] (2cc49d6)

- **release:** v0.4.9 (af3412d)


## [0.4.8] - 2026-09-19


### Chores

- update CHANGELOG.md [skip ci] (0f83ecf)

- **release:** v0.4.8 (97677bc)


### Continuous Integration

- delegate to shared reusable workflow (e9fa0d6)

- set COVERAGE_FILE via caller job env (not test-env-json) (6ec3b82)

- re-trigger after COVERAGE_FILE fix (7ee815d)

- set COVERAGE_FILE inline in test command (env+matrix rejected on reusable caller) (c69e0dd)


### Documentation

- close Phase 3 verify-only and record PEP 723 keep-in-core decision (cf3e695)

- record APPEND_SYSTEM.md keep-in-core decision with revisit triggers (c6de4f3)

- close Phase 4 verify-only, mark capability-layer decoupling complete (27f5d14)


### Features

- **tome,runes:** session codec architecture with native Pi v3/v4 support (984b173)


## [0.4.7] - 2026-09-19


### Bug Fixes

- **cli:** sort rune dirs for deterministic duplicate resolution (0892c69)


### Chores

- update CHANGELOG.md [skip ci] (20466f5)

- **release:** v0.4.7 (9b3e67e)


## [0.4.6] - 2026-09-19


### Bug Fixes

- **runes,cli,agent:** install python deps with uv add instead of uv pip (09b4828)


### Chores

- update CHANGELOG.md [skip ci] (f006403)

- **release:** v0.4.6 (d3139a5)


## [0.4.5] - 2026-09-19


### Bug Fixes

- **runes:** PEP 508 aware python_dep preflight (426a5d1)


### Chores

- update CHANGELOG.md [skip ci] (7d6618e)

- **release:** v0.4.5 (1b0c78a)


## [0.4.4] - 2026-09-18


### Bug Fixes

- **runes:** support parent workspace venv discovery and pth processing (d1e809c)


### Chores

- update CHANGELOG.md [skip ci] (f0f321b)

- **release:** v0.4.4 (6c8dc2e)


### Documentation

- add handoff documentation for marketplace rune decoupling (8a7f9de)


### Features

- **skills:** implement agentskills.io and agent plugins v1.0.0 specification (1c69143)

- **agent,cli:** implement PEP 723 inline script metadata and dynamic rune command dispatch (b98c919)

- **telemetry:** add OTel GenAI semantic conventions and ATIF session trajectory serialization (e42938d)


### Refactoring

- **runes,agent,cli:** decouple declarative skills into marketplace skills-bridge rune (6b69c03)

- **agent:** decouple repository steering into marketplace steering-bridge rune (079746d)


## [0.4.3] - 2026-09-17


### Chores

- update CHANGELOG.md [skip ci] (4d4f275)

- **release:** v0.4.3 (f34a820)


### Features

- **agent:** support .agents protocol steering and progressive disclosure (1b6edb1)


## [0.4.2] - 2026-09-16


### Chores

- update CHANGELOG.md [skip ci] (d9d3dfd)

- **release:** v0.4.2 (c59b95d)


### Features

- **runes:** engine-owned spell gateway and harden rune trust boundaries (7e4d7e0)


## [0.4.1] - 2026-09-16


### Chores

- update CHANGELOG.md [skip ci] (6278eee)

- **release:** v0.4.1 (18005b3)


### Features

- **runes:** add global spell allowlist to RuneRunner and RuneAPI (6d4bbe8)

- **agent:** integrate global spell allowlist in Mvge._build_spells (ceeb6c1)

- **gui:** load .env from project directory in AgentService (08ddae0)


## [0.4.0] - 2026-09-15


### Chores

- update CHANGELOG.md [skip ci] (d5924d4)

- **release:** v0.4.0 (a6d85cd)


### Refactoring

- **tome:** replace TomeLedger/FileLock with TomeHandle/TomeHandleFactory **BREAKING CHANGE** (3a1c361)


## [0.3.0] - 2026-09-13


### Chores

- update CHANGELOG.md [skip ci] (b7abc21)

- add test_keys.py to gitignore and mypy exclude for benchmarks (403b56a)

- **release:** v0.3.0 (915aa22)


### Features

- **core:** add SESSION_TITLE_UPDATED event type (997e76e)

- **tome:** add TomeHandle/TomeStore with integrity verification and torn-tail repair (cb1de29)

- **benchmarks:** add hermetic and live Seeker-vs-baseline evaluation harnesses (fcf4f09)


### Refactoring

- **agent:** deepen MvgeHarness as turn-driving module **BREAKING CHANGE** (a27422f)


## [0.2.10] - 2026-09-12


### Bug Fixes

- **gui:** retain marketplace mvges when uninstalled and add resilient catalog fallbacks (ee7432e)

- **ci:** remove stale coding-mvge refs and make protocol test hermetic (5e9d9e8)


### Chores

- update CHANGELOG.md [skip ci] (8dc671a)

- merge origin/main and regenerate uv.lock (14f31a8)

- update versions to v0.2.9 from main (40d634f)

- **release:** v0.2.10 (14adb3f)


## [0.2.9] - 2026-09-12


### Chores

- update CHANGELOG.md [skip ci] (cfa576c)

- **release:** v0.2.9 (f774ea9)


### Features

- **gui:** enable filter by hooks, types, deps, and sort marketplace list (e04c036)

- **mvge:** extract coding_mvge and make mvges installable from marketplace (3a5acf5)


## [0.2.8] - 2026-09-12


### Chores

- update CHANGELOG.md [skip ci] (5d92fff)

- **release:** v0.2.8 (30f305c)


## [0.2.7] - 2026-09-12


### Chores

- update CHANGELOG.md [skip ci] (2e8fe66)

- **release:** v0.2.7 (08b9095)


### Features

- **gui:** merge marketplace and installed tabs into unified view with interactive uninstall button (5c5a3ce)

- **gui:** style installed button, make path clickable, and add type badges (49aa03c)


### Refactoring

- **gui:** remove redundant sidebar toggle buttons from toolbar and sidebar header (edf9ec4)

- **gui:** keep sidebar toggle in header and remove from bottom footer (c9d38f1)

- **gui:** rename packages sidebar item and panel header to marketplace (4f68e2d)

- **gui:** remove separator above settings and integrate into main nav list (b45e0d2)


## [0.2.6] - 2026-09-12


### Bug Fixes

- **gui:** resolve sidebar visual and functional layout bugs (4bdeb5a)


### Chores

- update CHANGELOG.md [skip ci] (e246ddb)

- **release:** v0.2.6 (cf2d308)


## [0.2.5] - 2026-09-12


### Bug Fixes

- **coding-mvge:** prevent timeout race condition in bash cancellation test (1192553)


### Chores

- update CHANGELOG.md [skip ci] (63ea6f3)

- **release:** v0.2.5 (7c02be5)


### Features

- **runes:** add marketplace discovery and management functions (a6f62f3)

- **gui:** integrate marketplace exploration and extension management (fefd245)


### Tests

- **gui,runes:** expand branch coverage for packages panel and installer (3118bdb)


## [0.2.4] - 2026-09-11


### Bug Fixes

- **agent:** use default realm registry and hermetic mocks in tests (67c191d)


### Chores

- update CHANGELOG.md [skip ci] (523f552)

- **release:** v0.2.4 (38bc2b8)


### Features

- **provider:** remove OpenRouterRealm and decouple provider registry (dc0e038)


## [0.2.3] - 2026-09-11


### Chores

- update CHANGELOG.md [skip ci] (1396b0e)

- **release:** v0.2.3 (4e3ffbb)


### Features

- **runes:** support subpaths in marketplace installer and sanitize dependency versions (9324b26)


## [0.2.2] - 2026-09-11


### Chores

- update CHANGELOG.md [skip ci] (058219c)

- **release:** v0.2.2 (48007cd)


### Features

- **agent:** consolidate guidelines into system prompt and add APPEND_SYSTEM support (5812024)

- **provider,runes:** decouple realm implementations and add central marketplace installer (fa58679)

- **gui,cli:** implement 4-tier model selector with dynamic contemplation levels (f507d2d)


### Refactoring

- **paths:** standardize on .agents protocol sessions and extensions directories (35c87d9)


## [0.2.1] - 2026-09-11


### Bug Fixes

- **coding-mvge:** harvest spells from invocation content blocks (1732763)


### Chores

- update CHANGELOG.md [skip ci] (7d98fa5)

- **deps:** bump pydantic from 2.13.4 to 2.13.5 (#128) (59b003b)

- **deps:** bump typer from 0.27.1 to 0.27.2 (#129) (5408e2a)

- **deps:** bump filelock from 3.32.3 to 3.32.5 (#139) (3778b99)

- **deps-dev:** bump ruff from 0.16.4 to 0.16.6 (#140) (523701f)

- **deps-dev:** bump types-pyyaml (#141) (4499d61)

- **release:** v0.2.1 (cfd261f)


### Documentation

- **all:** synchronize documentation with codebase reality and CI ground truth (8539f7a)

- resolve AGENT-15 tech debt and document protocol boundary pattern (da84469)


### Features

- **core:** add spells field to ChannelConfig with tools synchronization (95fb9e6)


### Refactoring

- **agent:** collapse MvgeLoop into MvgeHarness deep module (477b0f7)

- **tome:** reset canonical session schema to version 1 (e14ffa2)

- **cli:** align StreamRenderer methods with spell terminology (41a0510)


## [0.2.0] - 2026-09-11


### Chores

- update CHANGELOG.md [skip ci] (2b2c31e)

- **release:** v0.2.0 (ad9c15b)


### Refactoring

- **provider:** remove deprecated types facade **BREAKING CHANGE** (70a1666)


## [0.1.0] - 2026-09-11


### Chores

- update CHANGELOG.md [skip ci] (1c7ed56)

- **release:** v0.1.0 (05d234a)

- update CHANGELOG.md [skip ci] (74fdef6)


### Continuous Integration

- **release:** ensure changelog job commits to main branch (36886a2)


### Tests

- **core:** add loop and dispatcher integration tests (4c59922)

- **gui:** handle delayed file-handle release in nicegui storage cleanup on windows (2be254c)


## [3.0.0] - 2026-09-11


### Bug Fixes

- **runes:** support UTF-8 BOM in manifest loader (a54349e)


### Chores

- update CHANGELOG.md [skip ci] (c972afd)

- **release:** v3.0.0 (1db6784)


### Documentation

- **coding-mvge:** update spell template import to mvgeos_core (30fd012)


### Features

- **core:** scaffold mvgeos-core workspace package (5f81c61)

- **core:** extract mvgeos-core canonical loop package **BREAKING CHANGE** (8e4baa9)


## [2.2.0] - 2026-09-11


### Bug Fixes

- **agent,provider,gui:** classify mid-stream upstream stalls with friendly message (c1472c9)

- **gui:** drop stale view listeners on chat panel re-render (beff9d1)


### Chores

- update CHANGELOG.md [skip ci] (1fee317)

- **release:** v2.2.0 (32dd62b)


### Features

- **cli:** add skill dedupe command for shadowed skill copies (0704d85)


## [2.1.2] - 2026-09-11


### Bug Fixes

- **agent:** quiet per-skill shadow diagnostics to debug with summary warning (798467e)

- **provider:** log intermediate realm retry attempts at debug level (89704f5)


### Chores

- update CHANGELOG.md [skip ci] (ef289a7)

- **release:** v2.1.2 (ab8c542)


## [2.1.1] - 2026-09-11


### Bug Fixes

- **gui:** suppress textarea focus ring so composer glows once (9f0101b)


### Chores

- update CHANGELOG.md [skip ci] (65c8b27)

- **release:** v2.1.1 (4614000)


## [2.1.0] - 2026-09-10


### Chores

- update CHANGELOG.md [skip ci] (4c656af)

- ignore .nicegui directory (b7f4681)

- **release:** v2.1.0 (708bf38)


### Features

- **gui:** restyle desktop GUI to space-black Void theme (1616f7d)


## [2.0.0] - 2026-09-10


### Bug Fixes

- **coding-mvge:** size byte-limit tests from measured paths (1c13529)


### Chores

- update CHANGELOG.md [skip ci] (743d5ce)

- **release:** v2.0.0 (2283dd3)


### Features

- **agent,coding-mvge,provider:** bound spell outputs with Pi-grade truncation **BREAKING CHANGE** (3bcb6a8)


## [1.15.0] - 2026-09-10


### Bug Fixes

- **test:** make external runes unit tests portable across environments (f2d7455)


### Chores

- update CHANGELOG.md [skip ci] (1e4eff6)

- prepare repository for public release and broaden Python compatibility (043914d)

- **release:** v1.15.0 (3de142e)


### Documentation

- **github:** add issue templates and pull request template (4e87430)

- **env:** provide .env.example configuration template (bdd3227)


### Features

- **core:** implement external runes, tome tree navigation, and web spells (fd4beac)


### Refactoring

- eliminate redundant tests, shims, and legacy migrations (514850f)


### Tests

- **core:** elevate test coverage to 90%+ and ratchet coverage floor to 90 (97a3d83)

- **coding-mvge:** expand unit tests for web spells edge cases (7817213)

- **core:** expand unit tests across agent, provider, runes, and tome for 90% coverage (8ac402b)


## [1.14.1] - 2026-09-08


### Bug Fixes

- **agent:** wire Layer 2 scaffolding and disambiguate spells tools and skills (575edff)


### Chores

- update CHANGELOG.md [skip ci] (64d0212)

- **release:** v1.14.1 (a0491e7)


### Performance

- **gui:** optimize input-to-chat latency and reduce time to first token (44e7c44)


### Refactoring

- **commands:** decouple command execution from cli formatting into mvgeos-agent (dbf3443)

- **coding-mvge:** rename knowledge_skill to skill_evolution and deepen engine seams (5cf1cbf)


## [1.14.0] - 2026-09-08


### Chores

- update CHANGELOG.md [skip ci] (df20cbc)

- **release:** v1.14.0 (e0e5c8c)


### Features

- **gui:** integrate command dispatcher for harness commands and adopt MvgeAgent protocol (33fe0e3)


## [1.13.1] - 2026-09-07


### Bug Fixes

- **gui:** resolve sign-in initialization, dialog dismiss, and shutdown error (1e78ba4)


### Chores

- update CHANGELOG.md [skip ci] (06465dd)

- **release:** v1.13.1 (fb9dc1a)


### Refactoring

- **cli:** deepen command dispatcher and enforce MvgeAgent protocol seam (5b2e3bb)

- **cli:** purge legacy backward-compatibility shims and aliases (989d2b6)

- **gui:** align slash commands with cli dispatcher and purge stale session references (d380ac3)


## [1.13.0] - 2026-09-07


### Bug Fixes

- **agent:** recognize coding_mvge as known agent name (0cbfa25)


### Chores

- update CHANGELOG.md [skip ci] (2fc74f4)

- **release:** v1.13.0 (acccdb5)


### Documentation

- add ADR 0010 and update domain definitions for knowledge skill (8f5e23f)


### Features

- **provider:** add rate limit diagnostics and system prompt support (6b6fa08)

- **agent:** validate spell names and forward rate limit diagnostics (e2954c9)

- **runes:** support clean reload and ignore bytecode in watcher (2c1a12e)

- **cli:** enhance rate limit formatting and handling in repl (bcac243)

- **coding-mvge:** implement knowledge_skill rune and persistent knowledge store (9b59728)

- **gui:** add unit tests and configurable paths for database, logging, and login screen (9204f50)


### Refactoring

- **agent:** remove dead errors, unused Mvge helpers, and dead spell_schema validation (0e8ad82)

- **cli:** remove dead helpers, redundant aliases, and duplicate exception handlers (cfa01d5)

- **gui:** remove dead message part renderers, obsolete settings panel, unused state fields, and redundant autocomplete aliases (f84dab7)

- **gui:** modularize models and services and add login screen (d62dd0e)


### Tests

- **gui:** hermetically mock MvgeEnvironment in agent_service tests (3b04c13)


### Style

- fix import sorting in resume.py tests (02296bb)

- fix ruff formatting in tests (ed474f8)


## [1.12.0] - 2026-09-02


### Bug Fixes

- **coding-mvge:** make system prompt fallback tests hermetic (aca960a)


### Chores

- update CHANGELOG.md [skip ci] (2100ccf)

- **release:** v1.12.0 (99ffa82)


### Documentation

- add Design Philosophy & Standards to AGENTS.md files (614e7b3)


### Features

- **agent:** implement Two-Layer Invariant Scaffolding and modular coding agent (685f807)

- **mvgeos-agent:** validate rune spell schema and signature on assembly (#126) (432b45e)


### Refactoring

- **runes:** make RuneContext immutable and thread-safe (#123) (1156c54)

- **cli:** decouple CLI command runners via MvgeAgent protocol and factory (9d93cc6)

- **runes:** remove dead RuneLoader, legacy load_runes, and unneeded sigil helpers (d78de65)

- **tome:** remove dead Index, obsolete INVOCATION, fix ledger typo (e3097ef)

- **provider:** eliminate duplicate model registry loading, dead get_model export, and null abort signal (a8341bd)


## [1.11.0] - 2026-09-01


### Chores

- update CHANGELOG.md [skip ci] (32a1151)

- **release:** v1.11.0 (614e068)


### Features

- **runes:** add override flag to spell and command registrations (233c5e7)


## [1.10.0] - 2026-09-01


### Chores

- update CHANGELOG.md [skip ci] (9c67c19)

- **release:** v1.10.0 (3df06e2)


### Features

- **provider:** implement pluggable RealmFactory protocol in RealmRegistry (#121) (8526f5b)


### Refactoring

- **provider:** extract generic SSE streaming realm base class (a76059d)

- **agent:** isolate external wire tool formats and align internal events on spell terminology (#120) (9b47f12)


## [1.9.0] - 2026-09-01


### Chores

- update CHANGELOG.md [skip ci] (d466c54)

- **release:** v1.9.0 (d6f072d)


### Features

- **provider:** make model registry cache TTL configurable with force-refresh support (#118) (6f0585a)


## [1.8.0] - 2026-09-01


### Chores

- update CHANGELOG.md [skip ci] (4cce39e)

- **release:** v1.8.0 (5d81101)


### Features

- **agent:** integrate tome session version migration into resume lifecycle (#117) (9e1e08f)


## [1.7.0] - 2026-09-01


### Chores

- update CHANGELOG.md [skip ci] (70f34d1)

- **release:** v1.7.0 (76580f3)


### Features

- **agent:** validate model and spell compatibility on session resume (#116) (41b49ef)


## [1.6.0] - 2026-09-01


### Bug Fixes

- **tome:** resolve cross-platform mypy ctypes and wintypes type resolution (70b2643)


### Chores

- update CHANGELOG.md [skip ci] (302e2e6)

- **release:** v1.6.0 (c856e35)


### Features

- **agent:** optimize spell lookup in dispatch loop with indexed map (#111) (b063308)

- **tome:** add session file and line-level integrity verification (81c552d)

- **tome:** implement line streaming iterator and tail reader (#114) (0286f01)

- **tome:** add automated session schema version migration pipeline (closes #115) (c0ade5c)


### Refactoring

- **gui:** consolidate Git workspace inspection behind git_workspace (5452a7b)

- **coding-mvge:** deepen BuiltinSpells and eliminate per-cast reflection (146f04e)

- **agent:** remove unused spell_map property and redundant constants (d05d7d2)


## [1.5.0] - 2026-08-31


### Chores

- update CHANGELOG.md [skip ci] (72f8ba2)

- **release:** v1.5.0 (060b18f)


### Features

- **provider:** unify model catalog into RealmRegistry and remove GUI duplicate (4ae784b)


### Refactoring

- **runes:** collapse SigilRegistry into RuneRunner (4afc0f7)


## [1.4.0] - 2026-08-31


### Chores

- update CHANGELOG.md [skip ci] (408983b)

- **release:** v1.4.0 (9e98ebb)


### Features

- **gui:** align workspace settings persistence with canonical ConfigManager seam (c87dff8)


### Tests

- **integration:** verify full architecture deepening and ratchet coverage (5e7e41a)


## [1.3.0] - 2026-08-31


### Bug Fixes

- **agent:** update fork target_session_file and refine session terminology (6e3ecd7)


### Chores

- update CHANGELOG.md [skip ci] (462b1d6)

- **release:** v1.3.0 (158606c)


### Features

- **agent:** fold TomeLifecycle session transitions into deep MvgeTome (#107) (46283bd)

- **agent:** consolidate prompt discovery and config coercion into deep MvgeEnvironment (8fd312a)


## [1.2.0] - 2026-08-31


### Chores

- update CHANGELOG.md [skip ci] (3ae757b)

- **deps-dev:** update uv-build requirement (10ecb3b)

- **deps-dev:** bump ruff from 0.16.3 to 0.16.4 (d3deb76)

- **release:** v1.2.0 (02e9c9b)


### Documentation

- **architecture:** define Invocation Transcript in the domain glossary (7d12edb)


### Features

- **provider:** unify model catalog and realm resolution in deep RealmRegistry (#106) (dd232f6)


### Refactoring

- **gui:** assemble Invocation bubbles via InvocationTranscript (3c2e0e9)

- eliminate verified redundancies and dead code across monorepo (7f74a84)


## [1.1.0] - 2026-08-23


### Chores

- update CHANGELOG.md [skip ci] (18c4f74)

- **release:** v1.1.0 (a4edd7c)


### Documentation

- **architecture:** amend ADR-0008 to the shipped GUI pipeline (f9ea325)


### Features

- **gui:** wire settings modals into shell and live Mvge status light (421d766)


### Refactoring

- **gui:** remove dead UI pipeline and port composer tests (6717f66)


### Tests

- add model_registry/sandbox coverage, ratchet floor to 84 (aea8444)

- cover conversation_view/home_screen/sessions_panel/input_dock helpers, ratchet floor to 85 (232692f)

- cover message_parts/diff_review/config_manager, ratchet floor to 86 (cf8fdd2)

- add input_dock full render and state extra coverage (97ef41b)

- cover input_dock icon helpers and chat_panel side panels (f8d617f)


## [1.0.1] - 2026-08-23


### Bug Fixes

- **release:** install git-cliff via taiki-e and fix changelog output (3656042)


### Chores

- **release:** v1.0.1 (3532b80)


### Continuous Integration

- **release:** dispatch Release workflow explicitly after bump (ccd059e)


## [1.0.0] - 2026-08-23


### Bug Fixes

- **root:** fix MD022/MD036/MD047 lint violations in handoff generator (3debd6f)

- **ci:** align pre-commit hooks with CI workflow (4e5bad4)

- **ci:** release workflow only triggers on tags not every push (d790374)

- **ci:** update handoff, distill, git-workflow skills - remove project-specific code (d66f49a)

- **ci:** fix handoff script quality gates and MD013 line length violations (ef7cdd5)

- resolve ruff lint issues and add heal-my-goap availability checks (faa241d)

- add pydantic to agent deps and fix formatting (3fbf33f)

- **agent:** fall back to default when config-dir system prompt is unreadable (428d707)

- **root:** stop test modules shadowing each other across packages (1c5cafb)

- **tome,agent:** repair entry lookup and advance the Leaf on every append (5c15906)

- **agent,provider:** honour custom guidelines and surface Contemplation (f5cba30)

- **prompt:** fix OSError fallback test to use mock (05e3b64)

- **test:** update default name assertion to default-mvge (18d7588)

- **cli:** wire mvgeos info command into the CLI app (0b77674)

- **cli:** unify tome dir to ~/.agents/.mvgeos/tomes and drop dead mvge_config (6bb98c3)

- **provider:** pass absolute request URLs and authorization headers (3ffc4ff)

- **cli:** reset narration buffers on turn start to isolate contemplation styling across multi-turn runs (075802e)

- **gitignore:** added .opencode/ dir (8bd2256)

- **runes:** resolve entrypoint dependencies for default system runes (2bafd15)

- **cli:** handle Windows console CP1252 encoding and sanitize Rich unicode output (f39d860)

- **agent:** shorten long log/raise lines to satisfy ruff E501 (91d7df4)

- **cli:** guard NoConsoleScreenBufferError import for non-Win32 platforms (b18c91a)

- **test:** supply OPENROUTER_API_KEY env in agent name validation tests (9c1221f)

- **agent,cli:** allow unknown agent in BaseMvge and fix mypy unused ignore (94e5a6a)

- **agent:** recognize coding-mvge and test-agent as known agent names (3be6ef7)

- **runes:** make set_active_spells additive and per-rune (d7d35b6)

- **cli:** add ellipsis truncation for table path columns in info and tome (e09d2e9)

- resolve issues #42, #43, #44, #45, #46 (runes, cli, tui, provider, tome) (24aa791)

- **constants:** harmonize system-wide default model to nvidia/nemotron-3-ultra-550b-a55b:free (closes #49) (2142a8b)

- **tome:** record parent_id on tome entries to restore conversation tree branching (closes #47) (24b15c3)

- **gui:** update branding to MvgeOS, apply Windows dark titlebar, and fix bottom layout padding (0f9fc3e)

- **gui:** enforce symmetrical horizontal padding on center viewport and dock (1b37e55)

- **gui:** resolve OpenRouter API key from auth file, env, and cli args (4aabf14)

- **gui:** separate agent contemplation thoughts into distinct collapsible cards (a561ee7)

- **gui:** isolate message updates per turn and prevent duplicate event listeners (4e0e427)

- **provider:** include contemplation in final stream response for persistence (cd6ea3e)

- **gui:** resolve platform-specific mypy and test failures for Windows titlebar (5b02045)

- **cli:** refine stream filter to stop suppressing legitimate markdown headings (9ce388f)

- **cli:** honor every channel tag on a line in stream filter (1312c0f)

- **gui:** resolve ruff lint and format quality gate failures (2b52f06)

- **gui:** report working-tree diff and narrow exception handling in git_diff (bbf4f59)

- **gui:** resolve_git_branch returns None for non-git parent dirs (f897cd9)

- **gui:** render each reasoning segment in its own Thought card (f58c179)

- **gui:** restore cleaned text return in _extract_text_and_contemplation (9da4890)

- **runes:** skip dot-prefixed skill dirs and strip UTF-8 BOM in manifest parsing (e6e13e3)

- **tome:** use tmp_path in locking tests to avoid creating stray dir/ (529e8f5)

- **provider:** handle empty choices chunks in OpenRouter stream (5c5e68d)

- **gui:** P1 bug fixes for input_dock, state, and agent_service (75bbe03)

- **gui:** correct window geometry fallback to target dimensions (1400x900) (c720d8e)

- **gui:** show milliseconds for sub-second durations in _format_duration (67fe5c6)

- **gui:** wire _active_task in AgentService for proper cancellation (065e226)

- **gui:** address P3 reliability issues from technical audit (da81c37)

- **gui:** resolve pre-existing mypy strict-mode errors (83a86da)

- **agent:** validate switch/fork targets before shutting down source (a2cae53)

- **gui:** persist api key to file when keyring unavailable (28dc47d)

- **release:** skip changelog commit on dry-run dispatches (4da24fa)


### Build System

- raise full-suite coverage floor to the documented 90 percent (59a4f1a)

- adopt src layout with uv_build and overhaul workspace packaging (ca1dd30)

- **root:** measure coverage over first-party source only (558195d)


### Chores

- generate CHANGELOG.md for v0.1.0-dev (593e1f0)

- **agent:** update package configuration (3a7c211)

- **root:** update repository URL (db73648)

- **root:** fix test collection for monorepo (ea0823b)

- **ci:** trigger workflow run (88858df)

- **ci:** track uv.lock for CI caching (212822a)

- **ci:** fix test package installation and mypy target (dc56cc9)

- **root:** remove invalid hatch build config (2e60ad1)

- **ci:** fix workspace package installation with --all-packages (4188d35)

- **provider:** track uv.lock for CI caching (cb8e499)

- commit changes (57812bc)

- **docs:** commit changes (87fa48a)

- **root:** update root workspace config and lockfile (c68494d)

- **root:** update AGENTS.md, README.md, and add goap actions (cd78a2d)

- **root:** ignore goap_actions.json (1d2ebce)

- **root:** remove obsolete documentation files (db1260e)

- **config:** ignore agent session files (1976ad6)

- ignore AI-generated and coverage artifacts (d045732)

- **root:** normalize line endings to LF (dfef00f)

- remove one-shot rebuild plan documentation (d8a1487)

- **deps:** bump typer from 0.27.0 to 0.27.1 (04d878c)

- **deps:** bump filelock from 3.32.0 to 3.32.2 (52c6414)

- **deps-dev:** bump ruff from 0.16.0 to 0.16.2 (f2772f6)

- **deps-dev:** bump pre-commit from 4.6.1 to 4.6.2 (d43c3e4)

- **deps-dev:** bump mypy from 2.3.0 to 2.3.1 (80c7d26)

- **deps-dev:** bump types-pyyaml (0062b29)

- **deps-dev:** bump ruff from 0.16.2 to 0.16.3 (2fe989c)

- **root:** update project docs and pytest config (0abad34)

- **root:** update project status and format step_cards (ee6018a)

- **root:** fix pre-commit hooks to use python -m for Windows compatibility (75bd8a1)

- **release:** v1.0.0 (2998a44)


### Continuous Integration

- make heal-my-goap optional dependency for CI (66b4845)

- fix heal-my-goap local path source and sync test extra (e302579)

- remove heal-my-goap from deps, tests skip gracefully when not installed (c5c2eda)

- omit heal_my_goap tests from coverage (require external dep) (d82a002)

- bump checkout and setup-uv actions to node24 releases (7921c54)

- run only unit tests in CI and pre-commit (6b6228c)

- gate main pushes with the full test suite (c119bef)

- run hermetic suites per-package with aggregated 90% floor (47a68b3)

- **release:** gate publish jobs on full-suite verification (0cc5a9d)

- **precommit:** drop coverage gate, adopt charter command forms (90d80c9)

- **precommit:** stop uv run from auto-syncing during hooks (90bbaa7)

- **release:** automate version bumps via bump job in ci.yml (7476a38)


### Documentation

- **root:** update documentation and configuration (a891aae)

- commit changes (c636871)

- commit changes (8755e88)

- **root:** update ADRs for new MvgeOS architecture (a824c74)

- **root:** add architecture, planning, and tech-debt documentation (f7157df)

- **root:** remove mvgeos-spells from package table (ac80d25)

- align terminology with MvgeOS and add agent docs (6fd9d35)

- **root:** document loop architecture, retry, compaction, and known gaps (66c3ac8)

- **root:** add mvgeos-harness to root documentation (f183a41)

- **agent:** update architecture with harness lifecycle (116805f)

- **coding-mvge:** update architecture for harness delegation (8e3f14d)

- **architecture:** add harness package and update invocation flow (60ebbb2)

- **all:** synchronize project documentation with source code implementation (dbadfa7)

- **cli:** add help description to tome subcommand application (c84d098)

- **tech-debt:** mark duplicate _build_spells entry resolved and drop stale line refs (d573406)

- **cli:** document non-interactive setup install (ac6d681)

- **architecture:** update terminology and supersede deprecated mvgeos-spells references (ea69a68)

- **root:** synchronize markdown documentation across monorepo (4a210bd)

- **adr:** record ADR 0009 for NiceGUI desktop application architecture (e7a2819)

- update architecture, spec, and tech-debt plans (f89580b)

- add testing standards charter as single test authority (82745d8)

- **testing:** drop resolved workflow-lag note from charter (e54d6c9)

- **testing:** retire e2e tier from project vocabulary (ee833b4)

- **architecture:** remove seeker protocol architecture docs (2f73ca3)

- **architecture:** retire seeker concept and renumber ADRs (c92805b)


### Features

- **coding-mvge:** add coding agent package with spells and tests (e5a8cc7)

- **agent,runes,coding-mvge:** relocate spell types and load runes from multi-level paths (875ad91)

- **cli:** add setup command for rune dependencies (1b1ee1d)

- **agent,runes,coding-mvge,cli:** rune prompt injection, config module, and gate alignment (621f036)

- **provider:** add two-layer retry policy for Realm calls (78e476a)

- **provider:** add Realm.complete() for non-channelled requests (a177147)

- **agent,tome:** compact the Mana Pool before it overflows (a285b80)

- **harness:** Implement MvgeHarness for Pi parity (327085e)

- **agent:** consolidate canonical name and rune-path resolution (56785c0)

- **runes:** provenance, dedup, and diagnostics for rune loading (b2affd4)

- **config:** ConfigManager with layered merge and agent-scope config (9d025dc)

- **prompts:** implement prompt resolution as a resource with source introspection (adb1aae)

- **runes:** core skills discovery + catalog (e16e750)

- **agent:** BaseMvge/CodingMvge/CLI construction consumes resolved config + resources (7d93c71)

- **agent:** runtime snapshot seam for resolved-state bundle (a1d9517)

- **cli:** add mvgeos build command for runtime manifest serialisation (8d2b027)

- **cli:** mvgeos info command renders runtime snapshot as rich tables (8e90506)

- **runes:** add extension error isolation, history capping, connection pooling, and loader validation (bf789ea)

- **cli:** add TUI stream rendering, queue modes, and slash commands (9b9f073)

- **cli:** add free model filtering, live rate limit countdown, and update default model to openrouter/free (c79394a)

- **cli:** rebuild one-shot mode reusing CodingMvge agent factory and print mode (d71936f)

- **cli:** add interactive setup prompt for API key initialization (#28) (1d17e2e)

- **runes:** generalize rune handling via active-spells set (#30) (0d5cb0b)

- **cli:** validate --agent-name option and environment resolution (0acb24b)

- **cli:** add --format {json,summary} and --pretty to build (32d0786)

- **cli:** surface rune load failures at startup (9188bff)

- **cli:** resolve tome IDs for session resume (4e9db31)

- **cli:** print group help for config/tome and default setup to check (e4c3b38)

- **cli:** add --format option and ISO 8601 timestamps to tome show (d9b5478)

- **cli:** standardize error formatting via format_error helper (#39) (87c4c78)

- **seeker:** widen active spell set on tool search discovery (#41) (8ae8be5)

- **seeker:** widen active spell set on tool search discovery (#41) (075a2a0)

- **gui:** scaffold mvgeos-gui workspace package, desktop runtime, and 3-column obsidian shell (eb4c4dd)

- **gui:** integrate TomeLedger session browser, git branch tags, and top navigation header (08291d6)

- **gui:** implement live agent channeling, Mana tracking, and collapsible execution step cards (a6d029a)

- **agent:** add QueueMode enum and granular queue draining (d8b837a)

- **runes:** add pre-flight python dependency validation for rune loading (closes #55) (0636b6a)

- **agent:** implement end-to-end AbortSignal cancellation architecture (87dfea4)

- **gui:** implement input dock with @-mention and /-slash autocomplete and attachments (981bc06)

- **gui:** track background tasks from MvgeEvents in inspector state (#70) (8b1e514)

- **gui:** wire agent service to populate active_skills from events (#71) (d3d3f92)

- **gui:** render reactive data in inspector Uploads and Background Tasks sections (1b4528d)

- **gui:** add inspector accordion and multi-item reactive integration tests (ef9fc5b)

- **gui:** implement Files Changed tracker and Diff Review modal (812ad55)

- **gui:** implement in-stream Artifact cards and sliding markdown preview drawer (fdc1932)

- **gui:** implement Application and Workspace Settings modals (73fecd3)

- **gui:** add chip-based input for autocomplete selections (8a822e3)

- **gui:** enrich step cards with Spell params and result rendering (e9cd29e)

- **coding-mvge:** improve bash spell Windows support and cleanup (0f8e709)

- **agent:** add environment-aware prompt rendering (e14aa56)

- **gui:** refactor layout and add Antigravity-matching components (ab870a0)

- **gui:** consolidate clipboard/download utilities with improved security (bce4368)

- **gui:** add keyring dependency for secure API key storage (1780014)

- **gui:** store API key in OS keyring instead of plaintext JSON (a0abe1c)


### Performance

- **tome:** optimize ledger index updates and use atomic line appends (5988ae3)

- **cli:** stream tokens instantly without line-buffering delay (ce1e9e1)

- **tome:** implement lazy-loading and bounded LRU indexing for TomeLedger (05cfa99)

- **tome:** migrate synchronous file I/O in Tome ledger and spells to non-blocking async (closes #56) (5be0b53)

- **gui:** cache ModelRegistry instance in model_catalog (39eb05b)


### Refactoring

- **cli:** restructure to flat layout (d3484b0)

- **provider:** update provider implementation (40b4efe)

- **runes:** restructure to flat layout (2618a96)

- **spells:** restructure to flat layout (aa8b75c)

- **tome:** restructure to flat layout (6a95f5e)

- **agent:** restructure agent package with new MvgeOS architecture (2541658)

- **cli:** restructure CLI package with new command structure (05a2ff4)

- **provider:** add model registry and OpenRouter improvements (29cab8b)

- **runes:** add rune API, runner, and watcher (fa2765b)

- **tome:** update ledger for unified session system (bf7b052)

- **spells:** remove deprecated mvgeos-spells package (057373a)

- **agent,cli:** load global runes by default and use ~/.agents/.mvgeos/tomes (d3de0d7)

- **provider:** rename ProviderRegistry to RealmRegistry, derive provider from model id (897615f)

- **tome:** rename session terms to tome (3dda4c8)

- **agent,cli,coding-mvge:** deepen the loop, invert the turn cycle, drop Mana Budget **BREAKING CHANGE** (a994eb7)

- **prompt:** consolidate to one path, one rendering, seeded defaults (c9e8327)

- **prompt:** consolidate to one path, one rendering, seeded defaults (3f83660)

- **harness:** Fix circular import and LSP issues (7374e88)

- **agent:** consolidate session lifecycle harness into mvgeos_agent.harness (ee63a08)

- **agent:** consolidate session lifecycle into MvgeHarness (2bfeae7)

- **agent:** extract SpellDispatcher module for parallel tool execution (b0fd123)

- **agent:** consolidate agent environment into deep MvgeEnvironment module (e15a39b)

- **agent:** remove duplicate realm initialization in BaseMvge (c30d06b)

- **coding-mvge:** consolidate builtin spell map constant (81eee0e)

- **cli:** use canonical BUILTIN_SPELL_MAP from coding-mvge (255da6b)

- **runes:** introduce strongly typed dataclass schemas for Sigil hook payloads (1d073ef)

- **agent:** rename session* to tome* **BREAKING CHANGE** (a9d5cbc)

- **gui:** simplify _track_spell_end status logic to ternary (91f8fb5)

- **agent:** deepen loop core into core_loop + mvge_loop adapter (dae0ba3)

- **agent:** extract ConfigParsing module (25f996b)

- **provider:** extract ModelComposer module (27295aa)

- **agent:** extract TomeLifecycle module (dca56f4)

- **agent:** extract PromptAssembly module (85ed4e0)

- **agent:** extract RuneLifecycle module (79b7f46)

- **agent:** wire BaseMvge.initialize() to the five extracted modules (5c2dfd3)

- **runes:** expose diagnostics retention on RuneRunner (b5ba063)

- **agent:** drop dead fallback in queue_mode getter (38a615c)

- **provider:** own the response and abort vocabulary (978f6bb)

- **runes:** invert sandbox dependency behind a protocol (06a5f2a)

- **gui:** hoist keyring, webview, and subprocess imports to module top (5882b32)

- **coding-mvge:** import AbortError from provider at module top (3632a0a)

- hoist remaining inline imports to top-level (6a34dd8)


### Tests

- **cli:** commit changes (01e8129)

- **all:** restructure tests into tests/unit and tests/integration (f611625)

- **harness:** add integration tests for full harness run with mocked Realm (d4c5520)

- **agent:** enforce no __init__.py in test directories (fddfe3e)

- **gui:** add mode-aware autocomplete tests for skill insertion (4940336)

- **gui:** update config_service tests for keyring-backed storage (3374abd)

- **agent:** strengthen ConfigParsing coverage (3b69fcf)

- **coding-mvge:** remove permanently skipped heal-my-goap demo test (84e2217)

- **coding-mvge:** rename rune integration file to charter naming (d7e9a13)

- **runes:** schedule watcher reloads onto the captured event loop (9bc739a)

- cover previously 0% modules and spell_schema (9cddc0e)


### Fmt

- fix formatting in DESKTOP_APP_PLAN.md (1c68881)


### Prefactor

- **gui:** expand ExecutionStep and ChatMessage schemas for bug fixes (9697a12)


### Sec

- **cli:** enforce restricted file permissions on saved authentication credentials (closes #51) (50906f9)


### Style

- fix ruff formatting in base_mvge.py and main.py (b3fd848)

- **gui:** collapse line continuations and remove redundant lambdas (9508116)


## [0.1.0-dev] - 2026-07-28


### Bug Fixes

- **provider:** handle openrouter rate limits (bde8219)


### Chores

- add pre-commit hooks (bfa2c30)


### Continuous Integration

- add release workflow with git-cliff (3ecf9c4)


### Documentation

- add architecture decision records (ab077de)


### Features

- initial mvgeos monorepo setup (3a9e5a1)

- **agent:** add Mvge core loop with event bus (6417554)


### Performance

- **agent:** optimize mana pool allocation (d3dbbe6)


### Refactoring

- **tome:** improve JSONL locking (5a010ba)


### Tests

- **spells:** add unit tests for bash spell (ee2501c)

---
*Generated by [git-cliff](https://github.com/orhun/git-cliff)*

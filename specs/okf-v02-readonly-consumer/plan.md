# Plan: OKF v0.2 Read-Only Consumer

## Implementation

- Add `OkfMetadata`, `OkfSource`, and related public models.
- Add `llmwiki_serve.okf` parser helpers for OKF v0.2 Markdown front matter.
- Add `OkfV02Adapter` and `source_profile` selection to `load_wiki`.
- Route `source_profile` through service, app factory, and CLI.
- Project OKF metadata into manifest/source-bundle/source-refs/graph surfaces.
- Add profile-aware projection and graph cache identities for non-generic
  interpretation paths.
- Add representative OKF fixture and focused regression tests.
- Update README, architecture docs, OpenAPI, changelog, and ADR.

## Risks

- OKF-like folders without a root marker must remain generic Markdown unless the
  operator opts in.
- Projection stores may contain old generic projections for the same files; the
  profile-scoped cache source id avoids reusing those for OKF.
- OKF source/resource identifiers may be private. Tests and docs must avoid real
  local paths, credentials, or private endpoints.

## Rollout

Ship as `0.2.12` additive support. Existing commands keep `source_profile=auto`.


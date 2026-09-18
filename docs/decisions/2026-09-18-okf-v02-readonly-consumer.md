# ADR: OKF v0.2 Read-Only Consumer Profile

## Status

Accepted.

## Context

OKF v0.2 uses Markdown plus YAML front matter for knowledge objects,
provenance, lifecycle status, and trust metadata. `llmwiki-serve` already reads
Markdown safely, but generic parsing loses the structure of OKF `sources[]` and
does not distinguish OKF concept type/resource facts in graph or source-bundle
contracts.

The same files can be served as generic Markdown or as OKF during migration, so
derived projection caches must not mix those interpretations.

## Decision

Add `okf-v0.2` as a read-only input profile. Auto-detect it only from a root
OKF version marker. Also expose an explicit `source_profile=okf-v0.2` path for
tests and operator-controlled migration.

Treat OKF metadata as source-owned evidence. Preserve and expose the structured
fields, but do not execute computations, dereference resources, validate remote
signatures, or mutate files.

Project OKF concept type, resource, and structured sources into the canonical
page/source/graph model with additive fields and capabilities. Use profile-aware
cache identity for projections that are not generic auto Markdown.

## Consequences

- OKF users get structured source refs, graph facts, and lifecycle/trust
  metadata without changing the authoring workflow.
- Existing Markdown and adapter behavior remains the default for unmarked
  folders.
- Clients can detect OKF support through manifest/source-bundle capabilities and
  `source_profile`.
- OKF support is a consumer compatibility claim, not producer certification or
  attestation verification.

## Follow-Ups

- Mirror the new profile in `llmwiki-docs` release/status pages.
- Revisit deeper OKF schema validation after more real bundles are available.
- Consider source-profile discovery docs for bridge and agent workflows.

## References

- Spec: `../../specs/okf-v02-readonly-consumer/`
- Architecture: `../architecture.md`

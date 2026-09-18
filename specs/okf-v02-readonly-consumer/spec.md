# Spec: OKF v0.2 Read-Only Consumer

## Status

Implementation branch.

## Problem

Open Knowledge Format v0.2 bundles are Markdown files with YAML front matter
that can carry concept type, lifecycle status, provenance, trust, and attested
computation metadata. Before this feature, `llmwiki-serve` could serve many
OKF-like folders as generic Markdown, but it treated OKF `sources` mappings as
ordinary front matter and did not expose OKF provenance or type/resource facts
through source bundles and graph surfaces.

## Goals

- Add a read-only OKF v0.2 input profile.
- Auto-detect OKF only when root `index.md` declares `okf_version: "0.2"` or
  `okfVersion: "0.2"`.
- Preserve existing Markdown, LLMWiki, Obsidian, Logseq, Foam, Dendron, Quartz,
  projection, search, read, graph, and source-bundle behavior.
- Preserve OKF provenance, lifecycle, trust, and attestation metadata without
  executing computations or dereferencing resources.
- Project OKF concept type, resource, and structured source refs into the
  canonical graph and source bundle contracts.
- Allow operators to force the input profile with `--source-profile okf-v0.2`
  for test or migration bundles without a root marker.

## Non-Goals

- Do not become an OKF producer or editor.
- Do not execute attested computations.
- Do not validate remote resources, signatures, or external evidence.
- Do not add auth, hosted RAG, model calls, or raw graph query languages.
- Do not remove or reinterpret existing generic Markdown frontmatter behavior.

## Requirements

- `REQ-OKF-001`: Default `source_profile=auto` detects OKF v0.2 only through the
  root version marker.
- `REQ-OKF-002`: Explicit `source_profile=okf-v0.2` parses Markdown files in the
  configured root as OKF even without auto-detection marker.
- `REQ-OKF-003`: Marked invalid OKF bundles fail closed and do not fall back to
  generic Markdown.
- `REQ-OKF-004`: Root-level `log.md` is not served as a page.
- `REQ-OKF-005`: OKF concept pages require non-empty `type`; index pages do not.
- `REQ-OKF-006`: `status: draft` and other existing non-serving statuses stay
  hidden from default read/search/source-ref/graph surfaces.
- `REQ-OKF-007`: `status: deprecated` remains visible by default because it is
  lifecycle metadata, not a visibility block.
- `REQ-OKF-008`: Manifest and source bundle expose `source_profile` and
  `format_version` for OKF sources.
- `REQ-OKF-009`: OKF capabilities advertise read-only consumer behavior and
  structured provenance.
- `REQ-OKF-010`: OKF `sources[]` mappings become structured `okf_source`
  source refs with opaque locators.
- `REQ-OKF-011`: OKF type/resource/source facts project into graph nodes and
  `typed_as`, `describes`, and `cites` edges.
- `REQ-OKF-012`: Projection and graph cache keys isolate explicit OKF profile
  projections from generic Markdown projections over the same files.

## Compatibility

Existing public fields remain additive. Non-OKF sources keep their previous
manifest/source-bundle projection signature behavior. OKF sources get a
profile-scoped projection identity because the same files can otherwise be
interpreted as generic Markdown or OKF.

## Data Safety

OKF front matter can contain provenance, resource identifiers, attestation
metadata, and internal dependency names. It is source-owned content. The service
continues to read local Markdown only and does not dereference OKF resources.

## Acceptance Criteria

- Focused OKF adapter/service tests pass.
- Existing adapter, service, public API, graph store, OpenAPI, ruff, mypy,
  package build, and release smoke checks pass.
- CLI smoke confirms OKF manifest/query/source-refs behavior.


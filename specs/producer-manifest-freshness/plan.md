# Plan: Producer Manifest Freshness

## Approach

Add a service-level `producer_manifest_path` option. When it points to an
existing file inside the served root, source signature checks use that small
file as the projection freshness marker. If the file is absent or unsafe, the
service uses the existing full source signature path.

The manifest path can be absolute or root-relative. It must resolve inside the
served root and must not be a symlink.

This opt-in marker contract measures the upper bound of producer-owned freshness.
A production hardening pass should consider parsing a schema with
`status: "ready"` and `projection_signature`, then accepting the marker only
after an initial full source scan confirms the producer-declared signature.

## Affected Areas

- Source module: `src/llmwiki_serve/service.py`, `src/llmwiki_serve/api.py`,
  `src/llmwiki_serve/cli.py`
- Tests: service freshness tests and benchmark script
- Docs: README, architecture, release notes, changelog
- Contracts: no OpenAPI response change

## Risks

- Risk: stale projection if producer forgets to update the manifest.
  Mitigation: opt-in only and document the producer/operator contract.

- Risk: manifest path leaks private local paths.
  Mitigation: do not include the path in public network payloads.

- Risk: unsafe symlink manifest points outside the root.
  Mitigation: only trust non-symlink files resolved inside the root.

## Rollout

- Local validation: targeted tests, full tests, release smoke, benchmark with
  strict scan vs manifest marker.
- CI validation: existing Python lint/type/test workflow.
- Docs / LLMWiki ingestion: ingest this spec, ADR, README, architecture, and
  benchmark results after review.

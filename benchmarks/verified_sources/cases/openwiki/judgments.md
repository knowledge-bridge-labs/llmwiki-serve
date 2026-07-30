# OpenWiki judgments

This case was manually judged against `langchain-ai/openwiki` commit
`9c253af17f264ac2589ab6781e79e9bb5b5d1238`, using `openwiki/` as the
`LlmWikiService` source root. The projection produced 13 approved pages with
the `llmwiki-markdown` adapter.

The upstream pages do not author projected `source_refs`. To keep citation IDs
stable and public, each corpus row uses `openwiki:<projected-path>`. Qrels are
page-level, and `support_spans` remain empty so no upstream content is copied
into this repository.

Relevance uses the benchmark scale: 3 is direct/decisive evidence, 2 is useful
supporting evidence, 1 is marginal context, and 0 is a judged non-answer.

## Coverage summary

- Native set: 57 queries, 52 answerable positive-query judgments, 5 negative
  queries, and 164 qrel rows.
- Generic-shadow set: 55 queries, 50 answerable positive-query judgments, 5
  negative queries, and 141 qrel rows.
- Shadow positives intentionally use only substantive non-hub pages:
  `agent/workflow`, `architecture/overview`, `cli/usage`,
  `integrations/connectors`, and `operations/credentials-and-updates`.
- Native-only hub queries are excluded from the shadow set because their
  answer is the authored OpenWiki hub layer itself.

## Query judgments

| Query | Class | Judgment rationale |
| --- | --- | --- |
| `q-openwiki-global-map` | global-map | Root hubs orient the native wiki; the five substantive topic pages cover the requested CLI, architecture, agent, operations, and connector areas. |
| `q-openwiki-global-runtime-modules` | global-map | Architecture is decisive for module relationships; quickstart and topic pages provide source maps for CLI, agent, connectors, and operations. |
| `q-openwiki-global-onboarding-automation` | global-map | Operations covers onboarding and schedules; CLI, connectors, agent, and architecture explain commands and runtime responsibilities. |
| `q-openwiki-global-provider-operations` | global-map | CLI, agent, operations, and architecture each cover a distinct part of provider setup, model creation, and diagnostics. |
| `q-openwiki-global-ingestion-to-wiki` | global-map | Connectors and architecture explain ingestion structure; agent, operations, and CLI connect ingestion to update runs. |
| `q-openwiki-known-binary-env` | known-item | Quickstart and CLI directly identify the binary; operations supplies env-file storage details. |
| `q-openwiki-known-print-noninteractive` | known-item | CLI usage is decisive for `--print`; operations and architecture support non-interactive credential behavior. |
| `q-openwiki-known-auth-subcommands` | known-item | CLI usage lists the auth, ngrok, cron, and ingest commands; connectors and operations explain what those commands manage. |
| `q-openwiki-known-compatible-base-url` | known-item | CLI and agent pages directly cover OpenAI-compatible base URL and model handling. |
| `q-openwiki-known-gemini-enterprise-adc` | known-item | CLI and agent pages are decisive for Gemini Enterprise routing and credentials; architecture and operations support provider gating. |
| `q-openwiki-known-bedrock-region` | known-item | CLI and agent pages identify Bedrock credential and region handling; operations and architecture support the same provider model. |
| `q-openwiki-known-copilot-credential-source` | known-item | CLI and agent pages directly document Copilot credential sourcing; operations supports related env behavior. |
| `q-openwiki-known-provider-retry-attempts` | known-item | CLI usage directly defines retry attempts; operations lists the managed retry setting. |
| `q-openwiki-known-model-id-validation` | known-item | CLI usage is decisive for model-ID validation and URL-shaped rejection. |
| `q-openwiki-known-env-file-diagnostics` | known-item | Operations is decisive for env loading and diagnostics; architecture and CLI support where those concerns are implemented. |
| `q-openwiki-known-onboarding-instructions` | known-item | Operations explains templates and repository instructions; connectors and agent pages support source setup and prompt use. |
| `q-openwiki-known-local-schedule-actions` | known-item | Operations and CLI both directly document cron list, pause, resume, and delete behavior. |
| `q-openwiki-known-diagnostics-redaction` | known-item | Operations and architecture directly cover diagnostics and redaction behavior. |
| `q-openwiki-multihop-update-metadata` | multi-hop | Agent, operations, and architecture each cover snapshot, metadata, and interrupted-run recovery from different implementation angles. |
| `q-openwiki-multihop-provider-gating` | multi-hop | Architecture gives provider order, operations gives setup diagnostics, CLI gives interactive and non-interactive behavior, and agent supplies runtime context. |
| `q-openwiki-multihop-compatible-runtime` | multi-hop | CLI configuration, agent model creation, and architecture startup validation are all needed for the full path. |
| `q-openwiki-multihop-copilot-auth` | multi-hop | Architecture explains non-auto-detection, while CLI and agent pages explain GitHub CLI token resolution; operations supports credential behavior. |
| `q-openwiki-multihop-mcp-ingestion` | multi-hop | Connectors supplies the read-only MCP policy; CLI, architecture, and operations connect it to commands and ingestion. |
| `q-openwiki-multihop-local-brain-profile` | multi-hop | Agent workflow covers local-brain files; operations covers onboarding profile and instructions. Connector setup is nearby context but is intentionally not judged relevant for this query. |
| `q-openwiki-multihop-scheduled-ci` | multi-hop | Operations is decisive for scheduled workflows; architecture and quickstart support code-mode workflow context. |
| `q-openwiki-multihop-okf-translation` | multi-hop | Architecture and agent pages together cover OKF middleware, translation, index sync, and Mermaid validation. |
| `q-openwiki-multihop-telemetry-ci` | multi-hop | Operations and architecture cover telemetry, opt-out, and CI handling; agent workflow supports per-run emission. |
| `q-openwiki-multihop-raw-cache-synthesis` | multi-hop | Connectors is decisive for raw cache and deterministic fetch; operations and architecture support scheduling and orchestration. |
| `q-openwiki-multihop-git-evidence-noop` | multi-hop | Agent, architecture, and operations jointly explain git evidence windows and no-op metadata suppression. |
| `q-openwiki-multihop-code-mode-brief` | multi-hop | Operations explains repository instructions; architecture and agent pages connect that brief to prompt/runtime behavior. |
| `q-openwiki-citation-provider-order` | citation | Operations, architecture, and CLI each state provider selection behavior and are citation-required evidence. |
| `q-openwiki-citation-workflows` | citation | Operations is decisive for scheduled workflow examples; CLI and quickstart support the update command and automation context. |
| `q-openwiki-citation-eight-connectors` | citation | Connectors is decisive for the eight connectors; architecture and quickstart support where connectors fit in the product. |
| `q-openwiki-citation-mcp-readonly` | citation | Connectors is decisive for MCP read-only policy; CLI and architecture support the command and subsystem context. |
| `q-openwiki-citation-env-permissions` | citation | Operations is decisive for env-file location and permissions. |
| `q-openwiki-citation-content-snapshot` | citation | Agent, operations, and architecture directly explain content snapshots and no-op metadata behavior. |
| `q-openwiki-citation-retry-attempts` | citation | CLI and agent pages define retry semantics; operations supports the managed setting. |
| `q-openwiki-citation-diagnostics` | citation | Operations and architecture directly document diagnostics and redaction. |
| `q-openwiki-citation-auto-exit` | citation | CLI and architecture directly explain auto-exit behavior; quickstart supports the one-shot workflow context. |
| `q-openwiki-citation-telemetry` | citation | Operations and architecture are decisive for telemetry and CI handling. |
| `q-openwiki-numeric-eight-connectors` | korean-numeric | Connectors directly enumerates the eight connector IDs. |
| `q-openwiki-numeric-retry-default` | korean-numeric | CLI and agent pages give retry behavior; operations supports the retry setting. |
| `q-openwiki-numeric-git-history-window` | korean-numeric | Architecture and agent workflow directly cover the recent-commit evidence window. |
| `q-openwiki-numeric-backend-limits` | korean-numeric | Architecture is decisive for the 120 second backend timeout and 100000 byte output limit. |
| `q-openwiki-numeric-permission-modes` | korean-numeric | Connectors and operations directly cover restrictive permissions for raw data and env storage. |
| `q-openwiki-korean-connector-raw-cache` | korean-numeric | Connectors is decisive for connector count and raw cache; operations supports scheduling context. |
| `q-openwiki-korean-copilot-autodetect` | korean-numeric | Architecture, CLI, and agent pages support Copilot non-auto-detection and token sourcing. |
| `q-openwiki-korean-scheduled-workflows` | korean-numeric | Operations is decisive for GitHub, GitLab, and Bitbucket scheduled workflow examples. Generic CLI command behavior is intentionally not judged relevant for this workflow-evidence query. |
| `q-openwiki-numeric-five-templates` | korean-numeric | Operations is decisive for onboarding template count. Connector setup is nearby context but is intentionally not judged relevant for the five-template evidence. |
| `q-openwiki-numeric-telemetry-flush` | korean-numeric | Operations is decisive for telemetry timeout and CI sentinel handling; architecture supports telemetry subsystem placement. |
| `q-openwiki-negative-pypi` | negative | The pages describe Node package-manager installation and CLI use, not a PyPI or pip distribution path. This is a hard abstention negative. |
| `q-openwiki-negative-vscode-extension` | negative | The product is documented as a CLI and agent workflow, not a VS Code extension interface. This is a hard abstention negative. |
| `q-openwiki-negative-docker-kubernetes` | negative | The operations pages document local/CI scheduling, not Docker or Kubernetes deployment. This is a hard abstention negative. |
| `q-openwiki-negative-mobile-app` | negative | The CLI pages do not describe an iOS or Android app surface. This is a hard abstention negative. |
| `q-openwiki-negative-postgres-redis` | negative | Runtime persistence is documented through local files and SQLite checkpointing, not PostgreSQL or Redis production storage. This is a hard abstention negative. |
| `q-openwiki-native-root-hubs` | native-llmwiki | Native root hub pages orient readers to the generated wiki and repository-maintenance instructions. Section index pages are not root pages and are intentionally judged only by `q-openwiki-native-section-hubs`. |
| `q-openwiki-native-section-hubs` | native-llmwiki | Section index pages directly route readers to agent, architecture, CLI, connector, and operations topics. |

## Native and generic-shadow qrels

`queries.jsonl` and `qrels.jsonl` measure the native OpenWiki projection,
including the authored root and section hub pages.

`queries-shadow.jsonl` and `qrels-shadow.jsonl` model the same source as a
generic Markdown folder whose authored hub layer is unavailable as a retrieval
advantage. The shadow set removes the two native-only hub queries and strips
all positive qrels to authored hub pages. Every answerable shadow query still
has at least one relevance >= 2 qrel after hub removal.

This separation prevents a generic-shadow run from receiving credit for
product-authored navigation that the generic managed-context layer is intended
to replace. It also avoids penalizing the shadow run with queries whose answer
is the native hub layer itself.

Shadow qrels preserve native grades for shared non-hub documents. In particular,
`q-openwiki-global-map` keeps `architecture/overview` and `cli/usage` at
relevance 2 rather than promoting them to direct evidence only because root hubs
are absent.

Negative queries are strict hard-abstention tests. Any returned row on a
retrieval-evaluated surface counts as a false positive, even when the row is a
nearby page that documents adjacent installation, persistence, CLI, or CI
behavior. Those nearby refusal-evidence pages are intentionally not relevant.

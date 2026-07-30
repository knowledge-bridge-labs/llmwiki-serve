from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load_smoke_module() -> ModuleType:
    module_path = ROOT / "scripts" / "upstream_candidate_smoke.py"
    spec = importlib.util.spec_from_file_location("upstream_candidate_smoke", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


smoke = load_smoke_module()


def write_synthetic_openwiki_setup_contract(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "README.md").write_text(
        """# OpenWiki

## Install

```sh
npm install -g openwiki
pnpm add -g openwiki
```

## Quick Start

openwiki personal --init
openwiki --init
openwiki code --update --print

Secrets are saved to `~/.openwiki/.env`.

```bash
OPENWIKI_PROVIDER=openai-compatible
OPENAI_COMPATIBLE_API_KEY
OPENAI_COMPATIBLE_BASE_URL
OPENWIKI_MODEL_ID
```
""",
        encoding="utf-8",
    )
    (root / "package.json").write_text(
        """{
  "name": "openwiki",
  "license": "MIT",
  "bin": {"openwiki": "./dist/cli.js"},
  "engines": {"node": ">=22"},
  "dependencies": {
    "deepagents": "^1.0.0",
    "langchain": "^1.0.0",
    "@langchain/core": "^1.0.0",
    "@langchain/openai": "^1.0.0"
  }
}
""",
        encoding="utf-8",
    )
    examples = root / "examples"
    examples.mkdir()
    (examples / "openwiki-update.yml").write_text(
        """steps:
  - run: npm install --global openwiki
  - run: openwiki code --update --print
    env:
      PROVIDER_SECRET_REF: ${{ secrets.PUBLIC_PROVIDER_SECRET }}
      OPENWIKI_MODEL_ID: public-model-placeholder
""",
        encoding="utf-8",
    )
    (examples / "openwiki-update.gitlab-ci.yml").write_text(
        """script:
  - npm install --global openwiki
  - openwiki code --update --print
  - echo "uses ${OPENWIKI_GITLAB_TOKEN} from CI secret storage"
variables:
  OPENWIKI_MODEL_ID: public-model-placeholder
""",
        encoding="utf-8",
    )
    src = root / "src"
    (src / "agent").mkdir(parents=True)
    (src / "cli.tsx").write_text("// cli\n", encoding="utf-8")
    (src / "credentials.tsx").write_text("// credentials\n", encoding="utf-8")
    (src / "agent" / "index.ts").write_text("// agent\n", encoding="utf-8")
    (src / "startup.ts").write_text(
        (
            "const key = getProviderApiKeyEnvKey(provider);\n"
            "throw new Error(\n"
            '  "API key is required for non-interactive runs. Run openwiki in an '
            'interactive terminal to save credentials.",\n'
            ");\n"
        ),
        encoding="utf-8",
    )
    (src / "env.ts").write_text(
        """export const OPENAI_COMPATIBLE_API_KEY_ENV_KEY = "OPENAI_COMPATIBLE_API_KEY";
export const OPENAI_COMPATIBLE_BASE_URL_ENV_KEY = "OPENAI_COMPATIBLE_BASE_URL";
export const OPENWIKI_PROVIDER_ENV_KEY = "OPENWIKI_PROVIDER";
export const OPENWIKI_MODEL_ID_ENV_KEY = "OPENWIKI_MODEL_ID";
export const openWikiEnvDir = "~/.openwiki";
""",
        encoding="utf-8",
    )
    openwiki = root / "openwiki"
    openwiki.mkdir()
    (openwiki / "quickstart.md").write_text("# OpenWiki Quickstart\n", encoding="utf-8")


def write_synthetic_microsoft_llmwiki_shape_contract(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "README.md").write_text(
        """# LLM Wiki

https://github.com/microsoft/llmwiki

The generated wiki lives in `.wiki/`.

Commands include `llmwiki.init` and the `@wiki` chat participant. MCP tools are available.
""",
        encoding="utf-8",
    )
    (root / "LICENSE").write_text(
        """MIT License

Copyright (c) Microsoft Corporation
""",
        encoding="utf-8",
    )
    (root / "package.json").write_text(
        """{
  "name": "llmwiki",
  "license": "MIT",
  "repository": {"url": "https://github.com/microsoft/llmwiki.git"},
  "engines": {"node": ">=20"}
}
""",
        encoding="utf-8",
    )
    core = root / "packages" / "core"
    src = core / "src"
    src.mkdir(parents=True)
    (core / "package.json").write_text(
        """{
  "name": "@llmwiki/core",
  "license": "MIT",
  "bin": {"llmwiki-mcp": "./dist/mcp/bin.js"}
}
""",
        encoding="utf-8",
    )
    (src / "constants.ts").write_text(
        """export const WIKI_DIR_NAME = '.wiki';
""",
        encoding="utf-8",
    )
    (src / "init.ts").write_text(
        """const DIRS = ['raw', 'wiki', 'wiki/entities', 'wiki/concepts', 'wiki/sources'];
const INDEX_CONTENT = `# Wiki Index

## Entities

## Concepts

## Sources
`;
const createdFiles = ['wiki/index.md', 'wiki/log.md', 'AGENTS.md'];
""",
        encoding="utf-8",
    )
    (src / "wiki.ts").write_text(
        """const page = { frontmatter: {} };
const entity = `entities/${slug}.md`;
const concept = `concepts/${slug}.md`;
export function createEntityPage() {}
export function createConceptPage() {}
export function getPageLinksDetailed() {
  return target.endsWith('.md');
}
""",
        encoding="utf-8",
    )
    (src / "index-ops.ts").write_text(
        """const heading = '# Wiki Index';
const category = `## ${category}`;
const line = `- [${escapeMarkdownLinkText(entry.title)}](${entry.path})`;
const summary = entry.summary;
const tags = entry.tags;
""",
        encoding="utf-8",
    )
    (src / "query.ts").write_text("// query\n", encoding="utf-8")

    wiki = root / "tests" / "fixtures" / "wiki"
    (wiki / "subdir").mkdir(parents=True)
    (wiki / "empty.md").write_text("", encoding="utf-8")
    (wiki / "minimal-page.md").write_text(
        """---
title: Quick Note
tags:
  - notes
---

This page has minimal frontmatter.
""",
        encoding="utf-8",
    )
    (wiki / "no-frontmatter.md").write_text(
        "This page has no YAML frontmatter at all.\n", encoding="utf-8"
    )
    (wiki / "subdir" / "nested-page.md").write_text(
        """---
title: Nested Page
type: concept
---

A nested page for testing recursive listing.
""",
        encoding="utf-8",
    )
    (wiki / "valid-page.md").write_text(
        """---
type: entity
title: Alan Turing
sources:
  - raw/turing-bio.txt
---

Alan Turing was a British mathematician and computer scientist.

He is widely considered the father of [theoretical computer science](concepts/cs.md)
and [artificial intelligence](concepts/ai.md).

See also: [Claude Shannon](shannon.md)
""",
        encoding="utf-8",
    )


def test_upstream_smoke_cases_are_pinned_to_full_commit_shas() -> None:
    smoke.validate_case_refs(smoke.CASES)


def test_upstream_smoke_cases_have_public_inventory_metadata() -> None:
    smoke.validate_case_refs(smoke.CASES)
    smoke.validate_case_metadata(smoke.CASES)

    rows = smoke.build_smoke_report(smoke.CASES, results=(), mode="dry-run")["cases"]
    openwiki = next(row for row in rows if row["case_id"] == "langchain-openwiki-self-docs")
    microsoft = next(row for row in rows if row["case_id"] == "microsoft-llmwiki-fixtures")
    row_by_id = {row["case_id"]: row for row in rows}
    needs_review_license = (
        "needs-review: no explicit repository content license found at pinned commit"
    )
    needs_review_cases = {
        "foam-template": needs_review_license,
        "karpathy-llm-wiki-vault": needs_review_license,
        "luotwo-llm-wiki": needs_review_license,
        "nishio-llm-wiki-about-delite": (
            needs_review_license
            + "; Quartz/tooling license is not treated as sampled content license"
        ),
        "iblinkq-llm-wiki-obsidian-blink": needs_review_license,
    }

    assert len(smoke.CASES) == 12
    assert {row["source_kind"] for row in rows} == {"actual-pinned"}
    assert all(row["commit"] and len(row["commit"]) == 40 for row in rows)
    assert all(row["product"] for row in rows)
    assert all(row["official_link"].startswith("https://github.com/") for row in rows)
    assert all(row["license_evidence"] for row in rows)
    assert all(row["evidence_type"].startswith("Actual pinned ") for row in rows)
    assert openwiki["product"] == "langchain-ai/openwiki"
    assert openwiki["source_path"] == "openwiki"
    assert openwiki["license_evidence"] == "MIT"
    assert microsoft["product"] == "microsoft/llmwiki"
    assert microsoft["official_link"] == "https://github.com/microsoft/llmwiki"
    assert microsoft["commit"] == "74a8a5bf0011b1092f135e5cbc51bbb44c1e07e7"
    assert microsoft["license_evidence"] == "MIT"
    assert microsoft["source_path"] == "tests/fixtures/wiki"
    assert "generated `.wiki/wiki` setup contract" in microsoft["evidence_type"]
    for case_id, expected_license_evidence in needs_review_cases.items():
        assert row_by_id[case_id]["license_evidence"] == expected_license_evidence
        assert row_by_id[case_id]["license_evidence"].startswith("needs-review: ")
        assert "NOASSERTION" not in row_by_id[case_id]["license_evidence"]


def test_public_safe_report_rejects_private_paths_endpoints_and_credentials() -> None:
    bad_values = [
        "C:" + "\\Users\\example\\scratch",
        "http://" + "127." + "0.0.1:8000/query",
        "api" + "_key=placeholder-value",
        "private" + "-vault",
    ]

    for value in bad_values:
        with pytest.raises(smoke.SmokeFailure, match="public report contains"):
            smoke.assert_public_safe_report({"value": value})


def test_dry_run_main_writes_public_safe_report(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    report_path = tmp_path / "report.json"

    assert smoke.main(["--dry-run", "--case", "openwiki", "--report", str(report_path)]) == 0

    captured = capsys.readouterr()
    report = json.loads(report_path.read_text(encoding="utf-8"))

    assert "report written" in captured.out
    assert report["schema"] == smoke.REPORT_SCHEMA
    assert report["mode"] == "dry-run"
    assert set(report["run_environment"]) == {"architecture", "os"}
    assert "hostname" not in report["run_environment"]
    assert report["certification_scope"] == {
        "answer_quality": "not-certified",
        "compatibility_smoke": "not-run",
        "retrieval_quality": "not-certified",
        "upstream_producer": "not-certified",
    }
    assert report["case_count"] == 1
    assert report["cases"][0]["case_id"] == "langchain-openwiki-self-docs"
    assert report["cases"][0]["page_count"] is None
    assert report["cases"][0]["mutation_check"] == {
        "checkout_status_unchanged": None,
        "source_hash_unchanged": None,
    }
    assert report["cases"][0]["quality_scope"] == {
        "answer_quality": "not-certified",
        "retrieval_quality": "not-certified",
    }
    assert str(tmp_path) not in report_path.read_text(encoding="utf-8")


def test_format_success_line_includes_candidate_repo_and_pinned_ref() -> None:
    result = smoke.UpstreamSmokeResult(
        case_id="local-case",
        repo_url="https://example.invalid/local.git",
        ref="a" * 40,
        source_path="wiki",
        source_file_count=2,
        adapter="fixture-adapter",
        implementation="fixture-implementation",
        page_count=3,
        approved_page_count=2,
        graph_nodes=5,
        graph_edges=8,
    )

    line = smoke.format_success_line(result)

    assert line.startswith("PASS local-case: ")
    assert "repo=https://example.invalid/local.git" in line
    assert f"ref={'a' * 40}" in line
    assert "source=wiki" in line


def test_select_cases_defaults_to_all_cases() -> None:
    assert len(smoke.CASES) >= 10
    assert smoke.select_cases(None) == smoke.CASES
    assert smoke.select_cases(()) == smoke.CASES


def test_select_cases_preserves_requested_order_and_deduplicates() -> None:
    selected = smoke.select_cases(["foam-template", "atomic-compiler-basic", "foam-template"])

    assert [case.id for case in selected] == ["foam-template", "atomic-compiler-basic"]


def test_select_cases_accepts_legacy_shorthand_aliases() -> None:
    selected = smoke.select_cases(
        [
            "atomic",
            "samuraigpt",
            "pratiyush",
            "logseq",
            "foam",
            "dendron",
            "karpathy-vault",
            "openwiki",
            "microsoft",
            "luotwo",
            "quartz-delite",
            "obsidian-blink",
        ]
    )

    assert [case.id for case in selected] == [
        "atomic-compiler-basic",
        "samuraigpt-agent",
        "pratiyush-llm-wiki",
        "logseq-exporter-test-graph",
        "foam-template",
        "dendron-test-workspace",
        "karpathy-llm-wiki-vault",
        "langchain-openwiki-self-docs",
        "microsoft-llmwiki-fixtures",
        "luotwo-llm-wiki",
        "nishio-llm-wiki-about-delite",
        "iblinkq-llm-wiki-obsidian-blink",
    ]


def test_select_cases_rejects_unknown_case() -> None:
    with pytest.raises(smoke.SmokeFailure, match="unknown case"):
        smoke.select_cases(["missing"])


def test_case_source_root_requires_relative_path_inside_checkout(tmp_path: Path) -> None:
    checkout = tmp_path / "checkout"
    source = checkout / "examples" / "basic" / "wiki"
    source.mkdir(parents=True)

    assert smoke.case_source_root(checkout, "examples/basic/wiki") == source.resolve()

    with pytest.raises(smoke.SmokeFailure, match="must be relative"):
        smoke.case_source_root(checkout, str(tmp_path.resolve()))

    with pytest.raises(smoke.SmokeFailure, match="escapes checkout"):
        smoke.case_source_root(checkout, "../outside")


def test_checkout_case_sets_windows_longpaths_in_checkout_local_config_before_sparse_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = smoke.UpstreamSmokeCase(
        id="sparse-case",
        repo_url="https://example.invalid/local.git",
        ref="a" * 40,
        source_path="deep/wiki",
        expected_adapter="fixture-adapter",
        expected_implementation="fixture-implementation",
        query="needle",
        min_pages=1,
        min_approved_pages=1,
    )
    checkout_dir = tmp_path / "checkout"
    calls: list[tuple[tuple[str, ...], Path, int]] = []

    def fake_run_command(args: list[str], *, cwd: Path, timeout: int) -> SimpleNamespace:
        calls.append((tuple(args), cwd, timeout))
        stdout = f"{case.ref}\n" if args == ["git", "rev-parse", "HEAD"] else ""
        return SimpleNamespace(stdout=stdout)

    monkeypatch.setattr(smoke.sys, "platform", "win32")
    monkeypatch.setattr(smoke, "run_command", fake_run_command)

    smoke.checkout_case(case, checkout_dir, timeout=7)

    commands = [args for args, _, _ in calls]
    longpaths_command = ("git", "config", "--local", "core.longpaths", "true")
    sparse_command = ("git", "sparse-checkout", "init", "--cone")

    assert longpaths_command in commands
    assert commands.index(("git", "init", "-q")) < commands.index(longpaths_command)
    assert commands.index(longpaths_command) < commands.index(sparse_command)
    assert all("--global" not in command for command in commands)
    assert all(cwd == checkout_dir for _, cwd, _ in calls)
    assert all(timeout == 7 for _, _, timeout in calls)


def test_checkout_case_does_not_set_windows_longpaths_off_windows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = smoke.UpstreamSmokeCase(
        id="non-windows-case",
        repo_url="https://example.invalid/local.git",
        ref="a" * 40,
        source_path=".",
        expected_adapter="fixture-adapter",
        expected_implementation="fixture-implementation",
        query="needle",
        min_pages=1,
        min_approved_pages=1,
    )
    commands: list[tuple[str, ...]] = []

    def fake_run_command(args: list[str], *, cwd: Path, timeout: int) -> SimpleNamespace:
        commands.append(tuple(args))
        stdout = f"{case.ref}\n" if args == ["git", "rev-parse", "HEAD"] else ""
        return SimpleNamespace(stdout=stdout)

    monkeypatch.setattr(smoke.sys, "platform", "linux")
    monkeypatch.setattr(smoke, "run_command", fake_run_command)

    smoke.checkout_case(case, tmp_path / "checkout", timeout=7)

    assert ("git", "config", "--local", "core.longpaths", "true") not in commands


def test_openwiki_case_uses_full_checkout_and_static_source_path() -> None:
    openwiki = smoke.case_lookup(smoke.CASES)["openwiki"]

    assert openwiki.ref == "9c253af17f264ac2589ab6781e79e9bb5b5d1238"
    assert smoke.case_checkout_path(openwiki) == "."
    assert openwiki.source_path == "openwiki"
    assert openwiki.expected_adapter == "llmwiki-markdown"
    assert openwiki.setup_validator == "langchain-openwiki-setup"
    assert openwiki.source_kind == "actual-pinned"
    assert openwiki.evidence_type.startswith("Actual pinned OpenWiki 0.2.4 self-docs")
    assert "synthetic" not in openwiki.evidence_type.lower()


def test_microsoft_case_uses_pinned_static_fixture_and_shape_validator() -> None:
    microsoft = smoke.case_lookup(smoke.CASES)["microsoft"]

    assert microsoft.ref == "74a8a5bf0011b1092f135e5cbc51bbb44c1e07e7"
    assert smoke.case_checkout_path(microsoft) == "."
    assert microsoft.source_path == "tests/fixtures/wiki"
    assert microsoft.expected_adapter == "generic-markdown"
    assert microsoft.expected_implementation == "generic-markdown"
    assert microsoft.setup_validator == "microsoft-llmwiki-shape"
    assert microsoft.product == "microsoft/llmwiki"
    assert microsoft.official_link == "https://github.com/microsoft/llmwiki"
    assert microsoft.license_evidence == "MIT"
    assert "synthetic" not in microsoft.evidence_type.lower()


@pytest.mark.parametrize("engine", [">=20", ">=20.0.0", ">=22", ">=22 <23", ">=20 || >=22"])
def test_node_engine_minimum_accepts_supported_lower_bounds(engine: str) -> None:
    assert smoke.node_engine_minimum_at_least(engine, major=20)


@pytest.mark.parametrize("engine", ["", ">=18", ">=18 || >=22", ">=22 || *", "<23"])
def test_node_engine_minimum_rejects_too_low_or_missing_lower_bounds(engine: str) -> None:
    assert not smoke.node_engine_minimum_at_least(engine, major=20)


def test_openwiki_setup_contract_accepts_minimal_public_setup(tmp_path: Path) -> None:
    checkout = tmp_path / "checkout"
    write_synthetic_openwiki_setup_contract(checkout)
    openwiki = smoke.case_lookup(smoke.CASES)["openwiki"]

    smoke.validate_case_setup(openwiki, checkout)


def test_openwiki_setup_contract_rejects_node_engine_below_minimum(tmp_path: Path) -> None:
    checkout = tmp_path / "checkout"
    write_synthetic_openwiki_setup_contract(checkout)
    package_path = checkout / "package.json"
    package = json.loads(package_path.read_text(encoding="utf-8"))
    package["engines"]["node"] = ">=18"
    package_path.write_text(json.dumps(package), encoding="utf-8")
    openwiki = smoke.case_lookup(smoke.CASES)["openwiki"]

    with pytest.raises(smoke.SmokeFailure, match="Node engine minimum must be >=20"):
        smoke.validate_case_setup(openwiki, checkout)


def test_openwiki_setup_contract_rejects_missing_readme_markers(tmp_path: Path) -> None:
    checkout = tmp_path / "checkout"
    write_synthetic_openwiki_setup_contract(checkout)
    (checkout / "README.md").write_text("# OpenWiki\n", encoding="utf-8")
    openwiki = smoke.case_lookup(smoke.CASES)["openwiki"]

    with pytest.raises(smoke.SmokeFailure, match="OpenWiki README markers missing"):
        smoke.validate_case_setup(openwiki, checkout)


def test_openwiki_setup_contract_rejects_missing_package_contract(tmp_path: Path) -> None:
    checkout = tmp_path / "checkout"
    write_synthetic_openwiki_setup_contract(checkout)
    (checkout / "package.json").write_text('{"name":"openwiki","license":"MIT"}', encoding="utf-8")
    openwiki = smoke.case_lookup(smoke.CASES)["openwiki"]

    with pytest.raises(smoke.SmokeFailure, match="missing openwiki bin entry"):
        smoke.validate_case_setup(openwiki, checkout)


def test_microsoft_llmwiki_shape_contract_accepts_static_fixture_and_setup_contract(
    tmp_path: Path,
) -> None:
    checkout = tmp_path / "checkout"
    write_synthetic_microsoft_llmwiki_shape_contract(checkout)
    microsoft = smoke.case_lookup(smoke.CASES)["microsoft"]

    smoke.validate_case_setup(microsoft, checkout)


def test_microsoft_llmwiki_shape_contract_rejects_missing_generated_contract(
    tmp_path: Path,
) -> None:
    checkout = tmp_path / "checkout"
    write_synthetic_microsoft_llmwiki_shape_contract(checkout)
    (checkout / "packages" / "core" / "src" / "init.ts").write_text("// missing\n")
    microsoft = smoke.case_lookup(smoke.CASES)["microsoft"]

    with pytest.raises(smoke.SmokeFailure, match="Microsoft LLMWiki init contract"):
        smoke.validate_case_setup(microsoft, checkout)


def test_microsoft_llmwiki_shape_contract_rejects_fixture_shape_changes(
    tmp_path: Path,
) -> None:
    checkout = tmp_path / "checkout"
    write_synthetic_microsoft_llmwiki_shape_contract(checkout)
    (checkout / "tests" / "fixtures" / "wiki" / "unexpected.md").write_text(
        "# Unexpected\n",
        encoding="utf-8",
    )
    microsoft = smoke.case_lookup(smoke.CASES)["microsoft"]

    with pytest.raises(smoke.SmokeFailure, match="fixture Markdown shape changed"):
        smoke.validate_case_setup(microsoft, checkout)


def test_tree_hash_ignores_git_metadata_and_detects_source_changes(tmp_path: Path) -> None:
    root = tmp_path / "wiki"
    git_dir = root / ".git"
    root.mkdir()
    git_dir.mkdir()
    source = root / "index.md"
    source.write_text("# Index\n", encoding="utf-8")
    (git_dir / "index").write_text("first metadata\n", encoding="utf-8")

    original = smoke.tree_hash(root)
    (git_dir / "index").write_text("changed metadata\n", encoding="utf-8")

    assert smoke.tree_hash(root) == original

    source.write_text("# Index\n\nChanged source.\n", encoding="utf-8")

    assert smoke.tree_hash(root) != original


def test_count_source_files_ignores_runtime_metadata(tmp_path: Path) -> None:
    root = tmp_path / "wiki"
    root.mkdir()
    (root / "index.md").write_text("# Index\n", encoding="utf-8")
    (root / "nested").mkdir()
    (root / "nested" / "topic.md").write_text("# Topic\n", encoding="utf-8")
    (root / ".git").mkdir()
    (root / ".git" / "index").write_text("metadata\n", encoding="utf-8")
    (root / "__pycache__").mkdir()
    (root / "__pycache__" / "cache.pyc").write_bytes(b"cache")

    assert smoke.count_source_files(root) == 2


def test_run_case_exercises_service_surfaces_and_reports_source_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = smoke.UpstreamSmokeCase(
        id="local-case",
        repo_url="https://example.invalid/local.git",
        ref="a" * 40,
        source_path="wiki",
        expected_adapter="fixture-adapter",
        expected_implementation="fixture-implementation",
        query="needle",
        min_pages=1,
        min_approved_pages=1,
        min_graph_nodes=1,
        min_graph_edges=1,
    )
    calls: list[tuple[object, ...]] = []

    def fake_checkout_case(checkout_case: object, checkout_dir: Path, *, timeout: int) -> None:
        assert checkout_case == case
        calls.append(("checkout", checkout_dir.name, timeout))
        source = checkout_dir / "wiki"
        source.mkdir(parents=True)
        (source / "index.md").write_text("# Index\n\n[[Topic]]\n", encoding="utf-8")
        (source / "__pycache__").mkdir()
        (source / "__pycache__" / "ignored.pyc").write_bytes(b"ignored")

    def fake_require_clean_checkout(
        clean_case: object, checkout_dir: Path, *, timeout: int
    ) -> None:
        assert clean_case == case
        calls.append(("clean", checkout_dir.name, timeout))

    def fake_validate_case_setup(setup_case: object, checkout_dir: Path) -> None:
        assert setup_case == case
        calls.append(("setup", checkout_dir.name))

    def fake_checkout_status(checkout_dir: Path, *, timeout: int) -> str:
        calls.append(("status", checkout_dir.name, timeout))
        return ""

    def fake_require_checkout_status_unchanged(
        clean_case: object,
        checkout_dir: Path,
        *,
        initial_checkout_status: str,
        timeout: int,
    ) -> None:
        assert clean_case == case
        calls.append(("unchanged", checkout_dir.name, initial_checkout_status, timeout))

    class FakeService:
        def __init__(self, root: Path) -> None:
            calls.append(("service", root.name))

        def index(self) -> SimpleNamespace:
            calls.append(("index",))
            return SimpleNamespace(
                pages=[
                    SimpleNamespace(
                        id="index",
                        path="index.md",
                        approved_for_serving=True,
                    )
                ]
            )

        def manifest(self) -> SimpleNamespace:
            calls.append(("manifest",))
            return SimpleNamespace(
                adapter="fixture-adapter",
                implementation="fixture-implementation",
                page_count=1,
                approved_page_count=1,
            )

        def graph(self, *, limit: int) -> dict[str, list[dict[str, object]]]:
            calls.append(("graph", limit))
            return {
                "nodes": [{"id": "page:index"}],
                "edges": [
                    {
                        "source": "page:index",
                        "target": "page:topic",
                        "relation": "links_to",
                    }
                ],
            }

        def context(self, query: str, *, limit: int) -> SimpleNamespace:
            calls.append(("context", query, limit))
            return SimpleNamespace(answerable=True, evidence=[object()])

        def search(self, query: str, *, limit: int) -> list[dict[str, object]]:
            calls.append(("search", query, limit))
            return [{"page_id": "index"}]

        def read(self, page_id: str) -> dict[str, object]:
            calls.append(("read", page_id))
            return {"path": "index.md"}

    monkeypatch.setattr(smoke, "checkout_case", fake_checkout_case)
    monkeypatch.setattr(smoke, "require_clean_checkout", fake_require_clean_checkout)
    monkeypatch.setattr(smoke, "validate_case_setup", fake_validate_case_setup)
    monkeypatch.setattr(smoke, "checkout_status", fake_checkout_status)
    monkeypatch.setattr(
        smoke,
        "require_checkout_status_unchanged",
        fake_require_checkout_status_unchanged,
    )
    monkeypatch.setattr(smoke, "LlmWikiService", FakeService)

    result = smoke.run_case(case, tmp_path, timeout=9)

    assert result.repo_url == "https://example.invalid/local.git"
    assert result.ref == "a" * 40
    assert result.source_path == "wiki"
    assert result.source_file_count == 1
    assert result.adapter == "fixture-adapter"
    assert result.implementation == "fixture-implementation"
    assert result.page_count == 1
    assert result.approved_page_count == 1
    assert result.graph_nodes == 1
    assert result.graph_edges == 1
    assert calls == [
        ("checkout", "local-case", 9),
        ("clean", "local-case", 9),
        ("setup", "local-case"),
        ("status", "local-case", 9),
        ("service", "wiki"),
        ("index",),
        ("manifest",),
        ("graph", 2000),
        ("context", "needle", 5),
        ("search", "needle", 5),
        ("read", "index"),
        ("unchanged", "local-case", "", 9),
    ]


def test_run_case_rejects_source_mutation_during_service_smoke(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = smoke.UpstreamSmokeCase(
        id="mutating-case",
        repo_url="https://example.invalid/local.git",
        ref="a" * 40,
        source_path="wiki",
        expected_adapter="fixture-adapter",
        expected_implementation="fixture-implementation",
        query="needle",
        min_pages=1,
        min_approved_pages=1,
        min_graph_nodes=1,
        min_graph_edges=1,
    )

    def fake_checkout_case(checkout_case: object, checkout_dir: Path, *, timeout: int) -> None:
        assert checkout_case == case
        source = checkout_dir / "wiki"
        source.mkdir(parents=True)
        (source / "index.md").write_text("# Index\n", encoding="utf-8")

    class MutatingService:
        def __init__(self, root: Path) -> None:
            (root / "generated.md").write_text("# Generated\n", encoding="utf-8")

        def index(self) -> SimpleNamespace:
            return SimpleNamespace(
                pages=[
                    SimpleNamespace(
                        id="index",
                        path="index.md",
                        approved_for_serving=True,
                    )
                ]
            )

        def manifest(self) -> SimpleNamespace:
            return SimpleNamespace(
                adapter="fixture-adapter",
                implementation="fixture-implementation",
                page_count=1,
                approved_page_count=1,
            )

        def graph(self, *, limit: int) -> dict[str, list[dict[str, object]]]:
            return {"nodes": [{"id": "page:index"}], "edges": [{"source": "a", "target": "b"}]}

        def context(self, query: str, *, limit: int) -> SimpleNamespace:
            return SimpleNamespace(answerable=True, evidence=[object()])

        def search(self, query: str, *, limit: int) -> list[dict[str, object]]:
            return [{"page_id": "index"}]

        def read(self, page_id: str) -> dict[str, object]:
            return {"path": "index.md"}

    monkeypatch.setattr(smoke, "checkout_case", fake_checkout_case)
    monkeypatch.setattr(
        smoke, "require_clean_checkout", lambda case, checkout_dir, *, timeout: None
    )
    monkeypatch.setattr(smoke, "validate_case_setup", lambda case, checkout_dir: None)
    monkeypatch.setattr(smoke, "checkout_status", lambda checkout_dir, *, timeout: "")
    monkeypatch.setattr(smoke, "LlmWikiService", MutatingService)

    with pytest.raises(smoke.SmokeFailure, match="source tree changed during smoke"):
        smoke.run_case(case, tmp_path, timeout=9)


def test_require_checkout_status_unchanged_rejects_status_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = smoke.UpstreamSmokeCase(
        id="changed-status",
        repo_url="https://example.invalid/local.git",
        ref="a" * 40,
        source_path="wiki",
        expected_adapter="fixture-adapter",
        expected_implementation="fixture-implementation",
        query="needle",
        min_pages=1,
        min_approved_pages=1,
    )

    monkeypatch.setattr(
        smoke, "checkout_status", lambda checkout_dir, *, timeout: "?? generated.md"
    )

    with pytest.raises(smoke.SmokeFailure, match="checkout status changed during smoke"):
        smoke.require_checkout_status_unchanged(
            case,
            tmp_path,
            initial_checkout_status="",
            timeout=1,
        )

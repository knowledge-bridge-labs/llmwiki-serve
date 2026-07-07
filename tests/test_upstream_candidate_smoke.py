from __future__ import annotations

import importlib.util
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


def test_upstream_smoke_cases_are_pinned_to_full_commit_shas() -> None:
    smoke.validate_case_refs(smoke.CASES)


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
        ("service", "wiki"),
        ("index",),
        ("manifest",),
        ("graph", 2000),
        ("context", "needle", 5),
        ("search", "needle", 5),
        ("read", "index"),
        ("clean", "local-case", 9),
    ]

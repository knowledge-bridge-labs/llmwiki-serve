from __future__ import annotations

import argparse
import hashlib
import json
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from llmwiki_serve.service import LlmWikiService  # noqa: E402

FULL_COMMIT_SHA = re.compile(r"\A[0-9a-f]{40}\Z")
IGNORED_TREE_HASH_PARTS = frozenset({".git", "__pycache__", ".venv", "node_modules"})
NODE_ENGINE_COMPARATOR = re.compile(
    r"(?<![A-Za-z0-9_.-])(?P<operator>>=|>|<=|<|=|\^|~)?\s*"
    r"(?P<major>\d+)(?:\.(?:x|X|\*|\d+)){0,2}"
)
REPORT_SCHEMA = "llmwiki-serve-upstream-candidate-smoke-v1"
ALLOWED_SOURCE_KINDS = frozenset({"actual-pinned"})
QUALITY_SCOPE = {
    "retrieval_quality": "not-certified",
    "answer_quality": "not-certified",
}
LICENSE_EVIDENCE = re.compile(r"\A(?:[A-Za-z0-9][A-Za-z0-9.-]*|needs-review: .+)\Z")
WINDOWS_ABSOLUTE_PATH = re.compile(r"\b[A-Za-z]:[\\/][^\s\"']+")
POSIX_PRIVATE_PATH = re.compile(r"(?<![\w])/(?:Users|home|tmp|var/folders)/[^\s\"']+")
PRIVATE_ENDPOINT = re.compile(
    r"https?://(?:localhost|127\.0\.0\.1|0\.0\.0\.0|\[::1\]|10\.|192\.168\.|"
    r"172\.(?:1[6-9]|2[0-9]|3[0-1])\.)",
    re.IGNORECASE,
)
SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(?:api[_-]?key|token|secret|password|credential)s?\b\s*[:=]\s*[^,\s\]}]+"
)
BEARER_TOKEN = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._-]+")
PRIVATE_VAULT_LABEL = re.compile(r"(?i)\bprivate[-_ ]vault\b")
PUBLIC_SAFETY_PATTERNS = (
    WINDOWS_ABSOLUTE_PATH,
    POSIX_PRIVATE_PATH,
    PRIVATE_ENDPOINT,
    SECRET_ASSIGNMENT,
    BEARER_TOKEN,
    PRIVATE_VAULT_LABEL,
)


class SmokeFailure(RuntimeError):
    pass


@dataclass(frozen=True)
class UpstreamSmokeCase:
    id: str
    repo_url: str
    ref: str
    source_path: str
    expected_adapter: str
    expected_implementation: str
    query: str
    min_pages: int
    min_approved_pages: int
    min_graph_nodes: int = 1
    min_graph_edges: int = 1
    aliases: tuple[str, ...] = field(default_factory=tuple)
    forbidden_paths: tuple[str, ...] = field(default_factory=tuple)
    notes: str = ""
    product: str = ""
    official_link: str = ""
    source_kind: str = "actual-pinned"
    license_evidence: str = ""
    evidence_type: str = ""
    checkout_path: str | None = None
    setup_validator: str | None = None


@dataclass(frozen=True)
class UpstreamSmokeResult:
    case_id: str
    repo_url: str
    ref: str
    source_path: str
    source_file_count: int
    adapter: str
    implementation: str
    page_count: int
    approved_page_count: int
    graph_nodes: int
    graph_edges: int


CASES: tuple[UpstreamSmokeCase, ...] = (
    UpstreamSmokeCase(
        id="atomic-compiler-basic",
        aliases=("atomic",),
        repo_url="https://github.com/atomicstrata/llm-wiki-compiler.git",
        ref="69701f609ae166e9da194c2d340699eb43abf77e",
        source_path="examples/basic/wiki",
        expected_adapter="llmwiki-markdown",
        expected_implementation="llmwiki-markdown",
        query="knowledge compilation wikilinks",
        min_pages=8,
        min_approved_pages=8,
        product="atomicstrata/llm-wiki-compiler",
        official_link="https://github.com/atomicstrata/llm-wiki-compiler",
        license_evidence="MIT",
        evidence_type=(
            "Actual pinned static LLMWiki Markdown sample; projection compatibility only."
        ),
        notes="Static generated LLMWiki Markdown example; no provider calls or build step.",
    ),
    UpstreamSmokeCase(
        id="samuraigpt-agent",
        aliases=("samuraigpt",),
        repo_url="https://github.com/SamurAIGPT/llm-wiki-agent.git",
        ref="11f66f1166994b35de2d7d3d0b246cb28847bbf2",
        source_path=".",
        expected_adapter="llmwiki-markdown",
        expected_implementation="llmwiki-markdown",
        query="agent memory knowledge wiki",
        min_pages=3,
        min_approved_pages=3,
        product="SamurAIGPT/llm-wiki-agent",
        official_link="https://github.com/SamurAIGPT/llm-wiki-agent",
        license_evidence="MIT",
        evidence_type=(
            "Actual pinned static agent-maintained Markdown snapshot; "
            "projection compatibility only."
        ),
        notes="Static agent-maintained Markdown wiki snapshot; no provider calls.",
    ),
    UpstreamSmokeCase(
        id="pratiyush-llm-wiki",
        aliases=("pratiyush",),
        repo_url="https://github.com/Pratiyush/llm-wiki.git",
        ref="b1088890ee0743810a92577aecad946c6b3eb2d2",
        source_path=".",
        expected_adapter="llmwiki-markdown",
        expected_implementation="llmwiki-markdown",
        query="knowledge base wiki",
        min_pages=10,
        min_approved_pages=10,
        product="Pratiyush/llm-wiki",
        official_link="https://github.com/Pratiyush/llm-wiki",
        license_evidence="MIT",
        evidence_type=(
            "Actual pinned static Markdown knowledge-base snapshot; projection compatibility only."
        ),
        notes="Static Markdown knowledge-base snapshot; no provider calls.",
    ),
    UpstreamSmokeCase(
        id="logseq-exporter-test-graph",
        aliases=("logseq",),
        repo_url="https://github.com/logseq/logseq.git",
        ref="a9a67f61ab29972d2e2b6c7a5864e6e3306c0d9a",
        source_path="deps/graph-parser/test/resources/exporter-test-graph",
        expected_adapter="logseq",
        expected_implementation="logseq/logseq",
        query="logseq graph exporter pages journals",
        min_pages=50,
        min_approved_pages=50,
        min_graph_nodes=80,
        min_graph_edges=50,
        forbidden_paths=("ignored/",),
        product="logseq/logseq",
        official_link="https://github.com/logseq/logseq",
        license_evidence="AGPL-3.0",
        evidence_type=(
            "Actual pinned static Logseq graph-parser fixture; projection compatibility only."
        ),
        notes=(
            "Static Logseq graph-parser fixture with pages/, journals/, and "
            "logseq/config.edn; no desktop runtime or build step."
        ),
    ),
    UpstreamSmokeCase(
        id="foam-template",
        aliases=("foam",),
        repo_url="https://github.com/foambubble/foam-template.git",
        ref="84fa1844270d214520aca32c01d4e27c6728d12e",
        source_path=".",
        expected_adapter="foam",
        expected_implementation="foambubble/foam",
        query="Foam wikilinks getting started",
        min_pages=10,
        min_approved_pages=10,
        forbidden_paths=(".foam/",),
        product="foambubble/foam-template",
        official_link="https://github.com/foambubble/foam-template",
        license_evidence=(
            "needs-review: no explicit repository content license found at pinned commit"
        ),
        evidence_type=(
            "Actual pinned static Foam template workspace; projection compatibility only."
        ),
        notes="Static Foam template workspace; no VS Code or desktop runtime launched.",
    ),
    UpstreamSmokeCase(
        id="dendron-test-workspace",
        aliases=("dendron",),
        repo_url="https://github.com/dendronhq/dendron.git",
        ref="4420715a421756518863c47005c8c49a38e37621",
        source_path="test-workspace",
        expected_adapter="dendron",
        expected_implementation="dendronhq/dendron",
        query="dendron notes workspace",
        min_pages=100,
        min_approved_pages=100,
        forbidden_paths=("other-files/",),
        product="dendronhq/dendron",
        official_link="https://github.com/dendronhq/dendron",
        license_evidence="Apache-2.0",
        evidence_type="Actual pinned static Dendron test workspace; projection compatibility only.",
        notes="Static Dendron test workspace; no editor runtime or build step.",
    ),
    UpstreamSmokeCase(
        id="karpathy-llm-wiki-vault",
        aliases=("karpathy-vault", "jason-effi"),
        repo_url="https://github.com/jason-effi-lab/karpathy-llm-wiki-vault.git",
        ref="18f4e71518af7d0c51a2fc65f5e3ec3043668e54",
        source_path="wiki",
        expected_adapter="llmwiki-markdown",
        expected_implementation="llmwiki-markdown",
        query="karpathy llm wiki knowledge vault",
        min_pages=15,
        min_approved_pages=15,
        min_graph_nodes=100,
        min_graph_edges=150,
        product="jason-effi-lab/karpathy-llm-wiki-vault",
        official_link="https://github.com/jason-effi-lab/karpathy-llm-wiki-vault",
        license_evidence=(
            "needs-review: no explicit repository content license found at pinned commit"
        ),
        evidence_type="Actual pinned static LLMWiki Markdown vault; projection compatibility only.",
        notes="Static LLMWiki Markdown vault with concepts, entities, sources, and syntheses.",
    ),
    UpstreamSmokeCase(
        id="langchain-openwiki-self-docs",
        aliases=("openwiki",),
        repo_url="https://github.com/langchain-ai/openwiki.git",
        ref="9c253af17f264ac2589ab6781e79e9bb5b5d1238",
        checkout_path=".",
        source_path="openwiki",
        expected_adapter="llmwiki-markdown",
        expected_implementation="llmwiki-markdown",
        query="provider credentials update metadata",
        min_pages=6,
        min_approved_pages=6,
        min_graph_nodes=50,
        min_graph_edges=50,
        product="langchain-ai/openwiki",
        official_link="https://github.com/langchain-ai/openwiki",
        license_evidence="MIT",
        evidence_type=(
            "Actual pinned OpenWiki 0.2.4 self-docs; proves static generated docs and "
            "setup-contract compatibility, not provider-backed generation."
        ),
        setup_validator="langchain-openwiki-setup",
        notes=(
            "Static OpenWiki generated Markdown docs from the official 0.2.4 tag plus "
            "setup contract checks; no provider calls."
        ),
    ),
    UpstreamSmokeCase(
        id="microsoft-llmwiki-fixtures",
        aliases=("microsoft", "microsoft-llmwiki", "ms-llmwiki"),
        repo_url="https://github.com/microsoft/llmwiki.git",
        ref="74a8a5bf0011b1092f135e5cbc51bbb44c1e07e7",
        checkout_path=".",
        source_path="tests/fixtures/wiki",
        expected_adapter="generic-markdown",
        expected_implementation="generic-markdown",
        query="Alan Turing computer science",
        min_pages=5,
        min_approved_pages=5,
        min_graph_nodes=10,
        min_graph_edges=5,
        product="microsoft/llmwiki",
        official_link="https://github.com/microsoft/llmwiki",
        license_evidence="MIT",
        evidence_type=(
            "Actual pinned Microsoft LLMWiki committed Markdown page fixture plus "
            "generated `.wiki/wiki` setup contract; projection compatibility only."
        ),
        setup_validator="microsoft-llmwiki-shape",
        notes=(
            "Static committed Microsoft LLMWiki page fixture; setup validator checks "
            "the generated `.wiki/wiki` contract; no VS Code, MCP server, or model calls."
        ),
    ),
    UpstreamSmokeCase(
        id="luotwo-llm-wiki",
        aliases=("luotwo",),
        repo_url="https://github.com/luotwo/llm-wiki.git",
        ref="9ab20ee0e9db3ca0bc7998b1b4a97ba7c821279f",
        source_path=".",
        expected_adapter="llmwiki-markdown",
        expected_implementation="llmwiki-markdown",
        query="llm wiki concepts sources",
        min_pages=10,
        min_approved_pages=10,
        min_graph_nodes=40,
        min_graph_edges=50,
        product="luotwo/llm-wiki",
        official_link="https://github.com/luotwo/llm-wiki",
        license_evidence=(
            "needs-review: no explicit repository content license found at pinned commit"
        ),
        evidence_type=(
            "Actual pinned static nested LLMWiki source root; projection compatibility only."
        ),
        notes="Repository root contains a nested static `wiki/` folder served by the adapter.",
    ),
    UpstreamSmokeCase(
        id="nishio-llm-wiki-about-delite",
        aliases=("nishio-delite", "quartz-delite"),
        repo_url="https://github.com/nishio/llm-wiki-about-delite.git",
        ref="4181dd42ff78d72a5e5a05512a59dc37d7ef97a2",
        source_path=".",
        expected_adapter="quartz",
        expected_implementation="jackyzha0/quartz",
        query="delite wiki concepts",
        min_pages=100,
        min_approved_pages=100,
        min_graph_nodes=200,
        min_graph_edges=300,
        product="nishio/llm-wiki-about-delite",
        official_link="https://github.com/nishio/llm-wiki-about-delite",
        license_evidence=(
            "needs-review: no explicit repository content license found at pinned commit; "
            "Quartz/tooling license is not treated as sampled content license"
        ),
        evidence_type="Actual pinned static Quartz source tree; projection compatibility only.",
        notes="Static Quartz source tree with config and Markdown pages; no Quartz build step.",
    ),
    UpstreamSmokeCase(
        id="iblinkq-llm-wiki-obsidian-blink",
        aliases=("iblinkq", "obsidian-blink"),
        repo_url="https://github.com/iBlinkQ/llm-wiki-obsidian-blink.git",
        ref="a9e8399cc29dbcce75fb47f61f1f2034a9dfc199",
        source_path=".",
        expected_adapter="obsidian",
        expected_implementation="Obsidian vault",
        query="llm wiki obsidian blink",
        min_pages=4,
        min_approved_pages=4,
        min_graph_nodes=20,
        min_graph_edges=15,
        forbidden_paths=(".obsidian/",),
        product="iBlinkQ/llm-wiki-obsidian-blink",
        official_link="https://github.com/iBlinkQ/llm-wiki-obsidian-blink",
        license_evidence=(
            "needs-review: no explicit repository content license found at pinned commit"
        ),
        evidence_type="Actual pinned static Obsidian vault; projection compatibility only.",
        notes="Static LLMWiki Obsidian vault with `.obsidian` marker and Markdown pages.",
    ),
)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.list_cases:
        print_case_list(CASES)
        return 0

    try:
        cases = select_cases(args.case_ids)
        validate_case_refs(cases)
        validate_case_metadata(cases)
        if args.dry_run:
            report = build_smoke_report(cases, results=(), mode="dry-run")
            emit_report(report, args.report)
            return 0

        results: list[UpstreamSmokeResult] = []
        with temporary_checkout_root(keep=args.keep_temp) as temp_root:
            print(
                "upstream candidate smoke: "
                f"{len(cases)} case(s), temporary checkout outside repository"
            )
            for case in cases:
                result = run_case(case, temp_root, timeout=args.timeout)
                results.append(result)
                print(format_success_line(result))
        if args.report is not None:
            emit_report(build_smoke_report(cases, results=results, mode="smoke"), args.report)
    except SmokeFailure as error:
        print(f"upstream candidate smoke failed: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("upstream candidate smoke interrupted", file=sys.stderr)
        return 130
    print("upstream candidate smoke passed")
    return 0


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Opt-in network smoke for pinned public upstream sample/template wiki snapshots. "
            "This is not an upstream certification gate."
        )
    )
    parser.add_argument(
        "--case",
        action="append",
        dest="case_ids",
        metavar="ID",
        help="Case id or alias to run. Repeat to select multiple cases. Defaults to all cases.",
    )
    parser.add_argument(
        "--list-cases",
        action="store_true",
        help="Print available case ids and exit.",
    )
    parser.add_argument(
        "--keep-temp",
        action="store_true",
        help="Keep the temporary checkout directory for local debugging.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate case metadata and emit a public-safe inventory report without cloning.",
    )
    parser.add_argument(
        "--report",
        type=Path,
        metavar="PATH",
        help=(
            "Write a public-safe JSON compatibility report. With --dry-run, "
            "the report contains metadata only."
        ),
    )
    parser.add_argument(
        "--timeout",
        type=positive_int,
        default=120,
        help="Timeout in seconds for each git command. Defaults to 120.",
    )
    return parser.parse_args(argv)


def print_case_list(cases: Sequence[UpstreamSmokeCase]) -> None:
    for case in cases:
        alias_text = f" aliases={','.join(case.aliases)}" if case.aliases else ""
        print(
            f"{case.id}{alias_text}\t{case.product}\t{case.official_link}@{case.ref}\t"
            f"{case.source_kind}\t{case.license_evidence}\t"
            f"{case.source_path}\t{case.expected_adapter}\t{case.notes}"
        )


def format_success_line(result: UpstreamSmokeResult) -> str:
    return (
        "PASS "
        f"{result.case_id}: "
        f"repo={result.repo_url}, "
        f"ref={result.ref}, "
        f"adapter={result.adapter}, "
        f"implementation={result.implementation}, "
        f"source={result.source_path}, "
        f"files={result.source_file_count}, "
        f"pages={result.page_count}, "
        f"approved={result.approved_page_count}, "
        f"graph={result.graph_nodes} nodes/{result.graph_edges} edges"
    )


def select_cases(case_ids: Sequence[str] | None) -> tuple[UpstreamSmokeCase, ...]:
    if not case_ids:
        return CASES

    case_by_id = case_lookup(CASES)
    selected: list[UpstreamSmokeCase] = []
    seen: set[str] = set()
    for case_id in case_ids:
        case = case_by_id.get(case_id)
        if case is None:
            known = ", ".join(sorted(case_by_id))
            raise SmokeFailure(f"unknown case {case_id!r}; known cases: {known}")
        if case.id in seen:
            continue
        selected.append(case)
        seen.add(case.id)
    return tuple(selected)


def case_lookup(cases: Sequence[UpstreamSmokeCase]) -> dict[str, UpstreamSmokeCase]:
    lookup: dict[str, UpstreamSmokeCase] = {}
    for case in cases:
        for case_id in (case.id, *case.aliases):
            if case_id in lookup:
                raise SmokeFailure(f"duplicate case id or alias: {case_id}")
            lookup[case_id] = case
    return lookup


def validate_case_refs(cases: Sequence[UpstreamSmokeCase]) -> None:
    invalid = [case.id for case in cases if not FULL_COMMIT_SHA.fullmatch(case.ref)]
    if invalid:
        raise SmokeFailure(f"case refs must be pinned 40-character commit SHAs: {invalid}")


def validate_case_metadata(cases: Sequence[UpstreamSmokeCase]) -> None:
    for case in cases:
        missing = [
            field_name
            for field_name in (
                "product",
                "official_link",
                "source_kind",
                "license_evidence",
                "evidence_type",
            )
            if not str(getattr(case, field_name)).strip()
        ]
        require(not missing, f"{case.id}: missing compatibility metadata: {', '.join(missing)}")
        require(
            case.official_link.startswith("https://github.com/")
            and not case.official_link.endswith(".git"),
            f"{case.id}: official_link must be a public GitHub repository URL",
        )
        require(
            case.source_kind in ALLOWED_SOURCE_KINDS,
            f"{case.id}: unsupported source_kind {case.source_kind!r}",
        )
        require(
            bool(LICENSE_EVIDENCE.fullmatch(case.license_evidence)),
            f"{case.id}: license_evidence must be an SPDX id or needs-review reason",
        )
        require(
            case.evidence_type.startswith("Actual pinned "),
            f"{case.id}: evidence_type must clearly identify actual pinned smoke evidence",
        )
        validate_public_relative_path(case.id, case.source_path, field_name="source_path")
        validate_public_relative_path(
            case.id,
            case_checkout_path(case),
            field_name="checkout_path",
        )


def validate_public_relative_path(case_id: str, value: str, *, field_name: str) -> None:
    raw_path = Path(value)
    require(not raw_path.is_absolute(), f"{case_id}: {field_name} must be relative")
    parts = value.replace("\\", "/").split("/")
    require(".." not in parts, f"{case_id}: {field_name} must not contain parent traversal")
    require(
        not any(pattern.search(value) for pattern in PUBLIC_SAFETY_PATTERNS),
        f"{case_id}: {field_name} is not public-safe",
    )


def build_smoke_report(
    cases: Sequence[UpstreamSmokeCase],
    *,
    results: Sequence[UpstreamSmokeResult],
    mode: str,
) -> dict[str, Any]:
    result_by_case_id = {result.case_id: result for result in results}
    report = {
        "schema": REPORT_SCHEMA,
        "mode": mode,
        "evidence_track": "compatibility-smoke",
        "run_environment": safe_run_environment(),
        "certification_scope": {
            "compatibility_smoke": compatibility_smoke_scope(cases, results, mode=mode),
            **QUALITY_SCOPE,
            "upstream_producer": "not-certified",
        },
        "case_count": len(cases),
        "cases": [case_report_row(case, result_by_case_id.get(case.id)) for case in cases],
    }
    assert_public_safe_report(report)
    return report


def safe_run_environment() -> dict[str, str]:
    return {
        "os": platform.system() or "unknown",
        "architecture": platform.machine() or "unknown",
    }


def compatibility_smoke_scope(
    cases: Sequence[UpstreamSmokeCase],
    results: Sequence[UpstreamSmokeResult],
    *,
    mode: str,
) -> str:
    if mode == "dry-run":
        return "not-run"
    if len(results) == len(cases):
        return "passed"
    return "incomplete"


def case_report_row(
    case: UpstreamSmokeCase,
    result: UpstreamSmokeResult | None,
) -> dict[str, Any]:
    return {
        "case_id": case.id,
        "product": case.product,
        "official_link": case.official_link,
        "source_kind": case.source_kind,
        "commit": case.ref,
        "license_evidence": case.license_evidence,
        "source_path": case.source_path,
        "adapter": result.adapter if result is not None else case.expected_adapter,
        "implementation": (
            result.implementation if result is not None else case.expected_implementation
        ),
        "source_file_count": result.source_file_count if result is not None else None,
        "page_count": result.page_count if result is not None else None,
        "approved_page_count": result.approved_page_count if result is not None else None,
        "graph_nodes": result.graph_nodes if result is not None else None,
        "graph_edges": result.graph_edges if result is not None else None,
        "minimums": {
            "pages": case.min_pages,
            "approved_pages": case.min_approved_pages,
            "graph_nodes": case.min_graph_nodes,
            "graph_edges": case.min_graph_edges,
        },
        "mutation_check": {
            "source_hash_unchanged": True if result is not None else None,
            "checkout_status_unchanged": True if result is not None else None,
        },
        "quality_scope": QUALITY_SCOPE,
        "evidence_type": case.evidence_type,
    }


def emit_report(report: dict[str, Any], report_path: Path | None) -> None:
    text = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if report_path is None:
        print(text, end="")
        return
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(text, encoding="utf-8")
    print("upstream candidate smoke report written")


def assert_public_safe_report(report: dict[str, Any]) -> None:
    for value in iter_report_strings(report):
        for pattern in PUBLIC_SAFETY_PATTERNS:
            require(
                pattern.search(value) is None,
                "public report contains private path, endpoint, credential, or vault marker",
            )


def iter_report_strings(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, nested_value in value.items():
            yield str(key)
            yield from iter_report_strings(nested_value)
    elif isinstance(value, list | tuple):
        for nested_value in value:
            yield from iter_report_strings(nested_value)


@contextmanager
def temporary_checkout_root(*, keep: bool) -> Iterator[Path]:
    temp_root = Path(tempfile.mkdtemp(prefix="llmwiki-upstream-smoke-")).resolve()
    try:
        ensure_outside_repo(temp_root)
        yield temp_root
    finally:
        if keep:
            print(f"kept temp root: {temp_root}")
        else:
            shutil.rmtree(temp_root, ignore_errors=True)


def ensure_outside_repo(path: Path) -> None:
    resolved = path.resolve()
    try:
        resolved.relative_to(ROOT)
    except ValueError:
        return
    raise SmokeFailure(f"temporary checkout root must be outside this repository: {resolved}")


def run_case(case: UpstreamSmokeCase, temp_root: Path, *, timeout: int) -> UpstreamSmokeResult:
    checkout_dir = temp_root / case.id
    checkout_case(case, checkout_dir, timeout=timeout)
    require_clean_checkout(case, checkout_dir, timeout=timeout)
    validate_case_setup(case, checkout_dir)
    initial_checkout_status = checkout_status(checkout_dir, timeout=timeout)
    source_root = case_source_root(checkout_dir, case.source_path)
    require(source_root.is_dir(), f"{case.id}: source path is not a directory: {case.source_path}")

    source_file_count = count_source_files(source_root)
    before_hash = tree_hash(source_root)
    service = LlmWikiService(source_root)
    index = service.index()
    manifest = service.manifest()
    graph = service.graph(limit=2_000)
    context = service.context(case.query, limit=5)
    search_results = service.search(case.query, limit=5)
    first_page = next((page for page in index.pages if page.approved_for_serving), None)

    require(
        manifest.adapter == case.expected_adapter,
        f"{case.id}: expected adapter {case.expected_adapter}, got {manifest.adapter}",
    )
    require(
        manifest.implementation == case.expected_implementation,
        f"{case.id}: expected implementation {case.expected_implementation}, "
        f"got {manifest.implementation}",
    )
    require(
        manifest.page_count >= case.min_pages,
        f"{case.id}: expected at least {case.min_pages} pages, got {manifest.page_count}",
    )
    require(
        manifest.approved_page_count >= case.min_approved_pages,
        f"{case.id}: expected at least {case.min_approved_pages} approved pages, "
        f"got {manifest.approved_page_count}",
    )
    require(
        len(graph["nodes"]) >= case.min_graph_nodes,
        f"{case.id}: expected at least {case.min_graph_nodes} projected graph nodes, "
        f"got {len(graph['nodes'])}",
    )
    require(
        len(graph["edges"]) >= case.min_graph_edges,
        f"{case.id}: expected at least {case.min_graph_edges} projected graph edges, "
        f"got {len(graph['edges'])}",
    )
    require(context.answerable, f"{case.id}: service context was not answerable")
    require(bool(context.evidence), f"{case.id}: service context returned no evidence")
    require(bool(search_results), f"{case.id}: service search returned no results")
    require(first_page is not None, f"{case.id}: no approved page available for read check")
    read_result = service.read(first_page.id)
    require(read_result.get("path") == first_page.path, f"{case.id}: read check mismatch")

    page_paths = {page.path for page in index.pages}
    for forbidden in case.forbidden_paths:
        require(
            all(not path.startswith(forbidden) for path in page_paths),
            f"{case.id}: internal path was served: {forbidden}",
        )

    require(tree_hash(source_root) == before_hash, f"{case.id}: source tree changed during smoke")
    require_checkout_status_unchanged(
        case,
        checkout_dir,
        initial_checkout_status=initial_checkout_status,
        timeout=timeout,
    )

    return UpstreamSmokeResult(
        case_id=case.id,
        repo_url=case.repo_url,
        ref=case.ref,
        source_path=case.source_path,
        source_file_count=source_file_count,
        adapter=manifest.adapter,
        implementation=manifest.implementation,
        page_count=manifest.page_count,
        approved_page_count=manifest.approved_page_count,
        graph_nodes=len(graph["nodes"]),
        graph_edges=len(graph["edges"]),
    )


def checkout_case(case: UpstreamSmokeCase, checkout_dir: Path, *, timeout: int) -> None:
    checkout_dir.mkdir(parents=True)
    run_command(["git", "init", "-q"], cwd=checkout_dir, timeout=timeout)
    configure_windows_checkout(checkout_dir, timeout=timeout)
    run_command(
        ["git", "remote", "add", "origin", case.repo_url],
        cwd=checkout_dir,
        timeout=timeout,
    )
    run_command(
        ["git", "fetch", "--depth=1", "--filter=blob:none", "--no-tags", "origin", case.ref],
        cwd=checkout_dir,
        timeout=timeout,
    )
    sparse_path = case_checkout_path(case)
    if sparse_path != ".":
        run_command(["git", "sparse-checkout", "init", "--cone"], cwd=checkout_dir, timeout=timeout)
        run_command(
            ["git", "sparse-checkout", "set", sparse_path],
            cwd=checkout_dir,
            timeout=timeout,
        )
    run_command(
        ["git", "-c", "advice.detachedHead=false", "checkout", "--detach", case.ref],
        cwd=checkout_dir,
        timeout=timeout,
    )
    head = run_command(["git", "rev-parse", "HEAD"], cwd=checkout_dir, timeout=timeout)
    require(head.stdout.strip() == case.ref, f"{case.id}: checked out unexpected revision")


def configure_windows_checkout(checkout_dir: Path, *, timeout: int) -> None:
    if sys.platform != "win32":
        return
    run_command(
        ["git", "config", "--local", "core.longpaths", "true"],
        cwd=checkout_dir,
        timeout=timeout,
    )


def case_checkout_path(case: UpstreamSmokeCase) -> str:
    return case.checkout_path or case.source_path


def validate_case_setup(case: UpstreamSmokeCase, checkout_dir: Path) -> None:
    if case.setup_validator is None:
        return
    if case.setup_validator == "langchain-openwiki-setup":
        require_openwiki_setup_contract(case, checkout_dir)
        return
    if case.setup_validator == "microsoft-llmwiki-shape":
        require_microsoft_llmwiki_shape_contract(case, checkout_dir)
        return
    raise SmokeFailure(f"{case.id}: unknown setup validator {case.setup_validator!r}")


def require_openwiki_setup_contract(case: UpstreamSmokeCase, checkout_dir: Path) -> None:
    required_files = (
        "README.md",
        "package.json",
        "examples/openwiki-update.yml",
        "examples/openwiki-update.gitlab-ci.yml",
        "src/cli.tsx",
        "src/env.ts",
        "src/startup.ts",
        "src/agent/index.ts",
        "src/credentials.tsx",
    )
    missing = [path for path in required_files if not (checkout_dir / path).is_file()]
    require(not missing, f"{case.id}: OpenWiki setup files missing: {', '.join(missing)}")

    readme = (checkout_dir / "README.md").read_text(encoding="utf-8")
    require_text_markers(
        case,
        "OpenWiki README",
        readme,
        (
            "npm install -g openwiki",
            "pnpm add -g openwiki",
            "openwiki personal --init",
            "openwiki code --update --print",
            "~/.openwiki/.env",
            "OPENWIKI_PROVIDER=openai-compatible",
            "OPENAI_COMPATIBLE_API_KEY",
            "OPENAI_COMPATIBLE_BASE_URL",
            "OPENWIKI_MODEL_ID",
        ),
    )
    require_any_text_marker(
        case,
        "OpenWiki README",
        readme,
        ("openwiki --init", "openwiki code --init"),
    )

    package = json.loads((checkout_dir / "package.json").read_text(encoding="utf-8"))
    dependencies = package.get("dependencies", {})
    require(package.get("name") == "openwiki", f"{case.id}: package name is not openwiki")
    require(package.get("license") == "MIT", f"{case.id}: package license is not MIT")
    require(package.get("bin", {}).get("openwiki"), f"{case.id}: missing openwiki bin entry")
    require(
        node_engine_minimum_at_least(package.get("engines", {}).get("node", ""), major=20),
        f"{case.id}: Node engine minimum must be >=20",
    )
    for dependency in ("deepagents", "langchain", "@langchain/core", "@langchain/openai"):
        require(dependency in dependencies, f"{case.id}: missing dependency {dependency}")

    github_workflow = (checkout_dir / "examples" / "openwiki-update.yml").read_text(
        encoding="utf-8"
    )
    require_text_markers(
        case,
        "OpenWiki GitHub workflow",
        github_workflow,
        (
            "npm install --global openwiki",
            "openwiki code --update --print",
            "${{ secrets.",
            "OPENWIKI_MODEL_ID",
        ),
    )
    gitlab_workflow = (checkout_dir / "examples" / "openwiki-update.gitlab-ci.yml").read_text(
        encoding="utf-8"
    )
    require_text_markers(
        case,
        "OpenWiki GitLab workflow",
        gitlab_workflow,
        (
            "npm install --global openwiki",
            "openwiki code --update --print",
            "${OPENWIKI_GITLAB_TOKEN}",
            "OPENWIKI_MODEL_ID",
        ),
    )

    startup = (checkout_dir / "src" / "startup.ts").read_text(encoding="utf-8")
    require_text_markers(
        case,
        "OpenWiki startup",
        startup,
        (
            "getProviderApiKeyEnvKey",
            "non-interactive runs",
            "Run openwiki in an interactive terminal to save credentials",
        ),
    )
    env_source = (checkout_dir / "src" / "env.ts").read_text(encoding="utf-8")
    require_text_markers(
        case,
        "OpenWiki env",
        env_source,
        (
            "OPENAI_COMPATIBLE_API_KEY_ENV_KEY",
            "OPENAI_COMPATIBLE_BASE_URL_ENV_KEY",
            "OPENWIKI_PROVIDER_ENV_KEY",
            "OPENWIKI_MODEL_ID_ENV_KEY",
            "openWikiEnvDir",
        ),
    )


def require_microsoft_llmwiki_shape_contract(
    case: UpstreamSmokeCase,
    checkout_dir: Path,
) -> None:
    required_files = (
        "README.md",
        "LICENSE",
        "package.json",
        "packages/core/package.json",
        "packages/core/src/constants.ts",
        "packages/core/src/init.ts",
        "packages/core/src/wiki.ts",
        "packages/core/src/index-ops.ts",
        "packages/core/src/query.ts",
        "tests/fixtures/wiki/valid-page.md",
        "tests/fixtures/wiki/minimal-page.md",
        "tests/fixtures/wiki/no-frontmatter.md",
        "tests/fixtures/wiki/empty.md",
        "tests/fixtures/wiki/subdir/nested-page.md",
    )
    missing = [path for path in required_files if not (checkout_dir / path).is_file()]
    require(not missing, f"{case.id}: Microsoft LLMWiki shape files missing: {', '.join(missing)}")

    package = json.loads((checkout_dir / "package.json").read_text(encoding="utf-8"))
    core_package = json.loads(
        (checkout_dir / "packages" / "core" / "package.json").read_text(encoding="utf-8")
    )
    require(package.get("name") == "llmwiki", f"{case.id}: package name is not llmwiki")
    require(package.get("license") == "MIT", f"{case.id}: package license is not MIT")
    require(
        package.get("repository", {}).get("url") == "https://github.com/microsoft/llmwiki.git",
        f"{case.id}: package repository URL is not microsoft/llmwiki",
    )
    require(
        node_engine_minimum_at_least(package.get("engines", {}).get("node", ""), major=20),
        f"{case.id}: Node engine minimum must be >=20",
    )
    require(core_package.get("name") == "@llmwiki/core", f"{case.id}: core package name changed")
    require(core_package.get("license") == "MIT", f"{case.id}: core package license is not MIT")
    require(
        core_package.get("bin", {}).get("llmwiki-mcp"),
        f"{case.id}: missing llmwiki-mcp bin entry",
    )

    license_text = (checkout_dir / "LICENSE").read_text(encoding="utf-8")
    require_text_markers(
        case,
        "Microsoft LLMWiki license",
        license_text,
        ("MIT License", "Copyright (c) Microsoft Corporation"),
    )

    readme = (checkout_dir / "README.md").read_text(encoding="utf-8")
    require_text_markers(
        case,
        "Microsoft LLMWiki README",
        readme,
        (
            "# LLM Wiki",
            ".wiki/",
            "llmwiki.init",
            "@wiki",
            "MCP",
        ),
    )

    constants = (checkout_dir / "packages" / "core" / "src" / "constants.ts").read_text(
        encoding="utf-8"
    )
    require_text_markers(
        case,
        "Microsoft LLMWiki constants",
        constants,
        ("WIKI_DIR_NAME", "'.wiki'"),
    )

    init_source = (checkout_dir / "packages" / "core" / "src" / "init.ts").read_text(
        encoding="utf-8"
    )
    require_text_markers(
        case,
        "Microsoft LLMWiki init contract",
        init_source,
        (
            "'raw'",
            "'wiki'",
            "'wiki/entities'",
            "'wiki/concepts'",
            "'wiki/sources'",
            "wiki/index.md",
            "wiki/log.md",
            "AGENTS.md",
            "# Wiki Index",
            "## Entities",
            "## Concepts",
            "## Sources",
        ),
    )

    wiki_source = (checkout_dir / "packages" / "core" / "src" / "wiki.ts").read_text(
        encoding="utf-8"
    )
    require_text_markers(
        case,
        "Microsoft LLMWiki page contract",
        wiki_source,
        (
            "frontmatter",
            "createEntityPage",
            "entities/${slug}.md",
            "createConceptPage",
            "concepts/${slug}.md",
            "getPageLinksDetailed",
            "target.endsWith('.md')",
        ),
    )

    index_source = (checkout_dir / "packages" / "core" / "src" / "index-ops.ts").read_text(
        encoding="utf-8"
    )
    require_text_markers(
        case,
        "Microsoft LLMWiki index contract",
        index_source,
        (
            "# Wiki Index",
            "## ${category}",
            "- [${escapeMarkdownLinkText(entry.title)}](${entry.path})",
            "summary",
            "tags",
        ),
    )

    source_root = case_source_root(checkout_dir, case.source_path)
    markdown_files = sorted(
        path.relative_to(source_root).as_posix()
        for path in source_root.rglob("*.md")
        if path.is_file()
    )
    require(
        markdown_files
        == [
            "empty.md",
            "minimal-page.md",
            "no-frontmatter.md",
            "subdir/nested-page.md",
            "valid-page.md",
        ],
        f"{case.id}: Microsoft LLMWiki fixture Markdown shape changed: {markdown_files}",
    )
    valid_page = (source_root / "valid-page.md").read_text(encoding="utf-8")
    require_text_markers(
        case,
        "Microsoft LLMWiki page fixture",
        valid_page,
        (
            "type: entity",
            "title: Alan Turing",
            "sources:",
            "concepts/cs.md",
            "concepts/ai.md",
            "See also: [Claude Shannon](shannon.md)",
        ),
    )


def require_text_markers(
    case: UpstreamSmokeCase,
    label: str,
    text: str,
    markers: Sequence[str],
) -> None:
    missing = [marker for marker in markers if marker not in text]
    require(not missing, f"{case.id}: {label} markers missing: {', '.join(missing)}")


def require_any_text_marker(
    case: UpstreamSmokeCase,
    label: str,
    text: str,
    markers: Sequence[str],
) -> None:
    require(
        any(marker in text for marker in markers),
        f"{case.id}: {label} markers missing one of: {', '.join(markers)}",
    )


def node_engine_minimum_at_least(value: object, *, major: int) -> bool:
    if not isinstance(value, str):
        return False

    alternatives = [alternative.strip() for alternative in value.split("||") if alternative.strip()]
    if not alternatives:
        return False

    for alternative in alternatives:
        lower_bound = node_engine_alternative_minimum_major(alternative)
        if lower_bound is None or lower_bound < major:
            return False
    return True


def node_engine_alternative_minimum_major(alternative: str) -> int | None:
    lower_bounds = [
        int(match.group("major"))
        for match in NODE_ENGINE_COMPARATOR.finditer(alternative)
        if match.group("operator") not in ("<", "<=")
    ]
    if not lower_bounds:
        return None
    return min(lower_bounds)


def checkout_status(checkout_dir: Path, *, timeout: int) -> str:
    return run_command(
        ["git", "status", "--porcelain=v1"],
        cwd=checkout_dir,
        timeout=timeout,
    ).stdout.rstrip("\r\n")


def require_clean_checkout(case: UpstreamSmokeCase, checkout_dir: Path, *, timeout: int) -> None:
    status = checkout_status(checkout_dir, timeout=timeout)
    require(not status, f"{case.id}: checkout has uncommitted changes before smoke: {status}")


def require_checkout_status_unchanged(
    case: UpstreamSmokeCase,
    checkout_dir: Path,
    *,
    initial_checkout_status: str,
    timeout: int,
) -> None:
    status = checkout_status(checkout_dir, timeout=timeout)
    require(
        status == initial_checkout_status,
        (
            f"{case.id}: checkout status changed during smoke; "
            f"before={initial_checkout_status!r}, after={status!r}"
        ),
    )


def run_command(
    args: Sequence[str], *, cwd: Path, timeout: int
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            args,
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError as error:
        raise SmokeFailure(f"missing command: {args[0]}") from error
    except subprocess.TimeoutExpired as error:
        raise SmokeFailure(f"command timed out after {timeout}s: {format_command(args)}") from error
    except subprocess.CalledProcessError as error:
        details = "\n".join(part for part in (error.stdout, error.stderr) if part)
        raise SmokeFailure(f"command failed: {format_command(args)}\n{details}") from error


def case_source_root(checkout_dir: Path, source_path: str) -> Path:
    raw_path = Path(source_path)
    if raw_path.is_absolute():
        raise SmokeFailure(f"source path must be relative: {source_path}")
    source_root = (checkout_dir / raw_path).resolve()
    try:
        source_root.relative_to(checkout_dir.resolve())
    except ValueError as error:
        raise SmokeFailure(f"source path escapes checkout: {source_path}") from error
    return source_root


def tree_hash(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root)
        if path.is_symlink() or set(relative.parts) & IGNORED_TREE_HASH_PARTS:
            continue
        digest.update(relative.as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def count_source_files(root: Path) -> int:
    count = 0
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if path.is_symlink() or set(relative.parts) & IGNORED_TREE_HASH_PARTS:
            continue
        count += 1
    return count


def positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be a positive integer") from error
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def format_command(args: Sequence[str]) -> str:
    return " ".join(args)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SmokeFailure(message)


if __name__ == "__main__":
    raise SystemExit(main())

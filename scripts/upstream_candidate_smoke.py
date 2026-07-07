from __future__ import annotations

import argparse
import hashlib
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from llmwiki_serve.service import LlmWikiService  # noqa: E402

FULL_COMMIT_SHA = re.compile(r"\A[0-9a-f]{40}\Z")
IGNORED_TREE_HASH_PARTS = frozenset({".git", "__pycache__", ".venv", "node_modules"})


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
        notes="Static LLMWiki Markdown vault with concepts, entities, sources, and syntheses.",
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
        with temporary_checkout_root(keep=args.keep_temp) as temp_root:
            print(f"upstream candidate smoke: {len(cases)} case(s), temp root {temp_root}")
            for case in cases:
                result = run_case(case, temp_root, timeout=args.timeout)
                print(format_success_line(result))
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
            f"{case.id}{alias_text}\t{case.repo_url}@{case.ref}\t"
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
    require_clean_checkout(case, checkout_dir, timeout=timeout)

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
    if case.source_path != ".":
        run_command(["git", "sparse-checkout", "init", "--cone"], cwd=checkout_dir, timeout=timeout)
        run_command(
            ["git", "sparse-checkout", "set", case.source_path],
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


def require_clean_checkout(case: UpstreamSmokeCase, checkout_dir: Path, *, timeout: int) -> None:
    status = run_command(
        ["git", "status", "--porcelain=v1"],
        cwd=checkout_dir,
        timeout=timeout,
    ).stdout.strip()
    require(not status, f"{case.id}: checkout has uncommitted changes after smoke: {status}")


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

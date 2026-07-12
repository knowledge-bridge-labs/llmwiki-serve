from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass

from .models import SearchResult, WikiIndex, WikiPage

TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*|[가-힣]{2,}")


@dataclass(frozen=True)
class SearchDocument:
    page: WikiPage
    tokens: tuple[str, ...]
    counts: Counter[str]


@dataclass(frozen=True)
class SearchCorpus:
    pages: list[WikiPage]
    documents: list[SearchDocument]
    doc_freq: Counter[str]

    @property
    def total(self) -> int:
        return max(1, len(self.documents))


def search(
    index: WikiIndex, query: str, *, limit: int = 8, include_drafts: bool = False
) -> list[SearchResult]:
    pages = visible_pages(index.pages, include_drafts)
    return search_corpus(build_search_corpus(pages), query, limit=limit)


def build_search_corpus(pages: list[WikiPage]) -> SearchCorpus:
    documents: list[SearchDocument] = []
    doc_freq: Counter[str] = Counter()
    for page in pages:
        tokens = tuple(tokenize(page_text(page)))
        counts = Counter(tokens)
        doc_freq.update(set(tokens))
        documents.append(SearchDocument(page=page, tokens=tokens, counts=counts))
    return SearchCorpus(pages=pages, documents=documents, doc_freq=doc_freq)


def search_corpus(corpus: SearchCorpus, query: str, *, limit: int = 8) -> list[SearchResult]:
    tokens = tokenize(query)
    if not tokens:
        return overview_results(corpus.pages, limit)

    results: list[SearchResult] = []
    for document in corpus.documents:
        text_score = 0.0
        for token in tokens:
            if not document.counts[token]:
                continue
            idf = math.log(
                1 + (corpus.total - corpus.doc_freq[token] + 0.5) / (corpus.doc_freq[token] + 0.5)
            )
            text_score += document.counts[token] * idf
        if text_score <= 0:
            continue
        page = document.page
        score = text_score + role_boost(page)
        results.append(to_result(page, score=score, query_tokens=tokens, route="search"))
    results.sort(key=lambda item: (-item.score, role_rank(item.role), item.path))
    return results[:limit]


def context_orientation(index: WikiIndex, *, include_drafts: bool = False) -> list[SearchResult]:
    pages = visible_pages(index.pages, include_drafts)
    ordered = orientation_pages(pages)
    return [
        to_result(page, score=1.0 - rank * 0.01, query_tokens=[], route="orientation")
        for rank, page in enumerate(ordered[:3])
    ]


def overview_results(pages: list[WikiPage], limit: int) -> list[SearchResult]:
    ordered = orientation_pages(pages)
    return [
        to_result(page, score=1.0 - rank * 0.01, query_tokens=[], route="overview")
        for rank, page in enumerate(ordered[:limit])
    ]


def orientation_pages(pages: list[WikiPage]) -> list[WikiPage]:
    ordered = sorted(pages, key=lambda page: (role_rank(page.role), page.path))
    selected: list[WikiPage] = []
    selected_paths: set[str] = set()
    for role in ("hot", "index", "overview"):
        page = next(
            (
                candidate
                for candidate in ordered
                if candidate.role == role and candidate.path not in selected_paths
            ),
            None,
        )
        if page is None:
            continue
        selected.append(page)
        selected_paths.add(page.path)
    selected.extend(page for page in ordered if page.path not in selected_paths)
    return selected


def visible_pages(pages: list[WikiPage], include_drafts: bool) -> list[WikiPage]:
    if include_drafts:
        return pages
    return [page for page in pages if page.approved_for_serving]


def page_text(page: WikiPage) -> str:
    return " ".join(
        [page.title, page.summary, page.text, " ".join(page.tags), " ".join(page.source_refs)]
    )


def tokenize(text: str) -> list[str]:
    return [match.group(0).lower() for match in TOKEN_RE.finditer(text)]


def role_boost(page: WikiPage) -> float:
    return {"hot": 2.5, "index": 2.0, "overview": 1.4}.get(page.role, 0.0)


def role_rank(role: str) -> int:
    return {"hot": 0, "index": 1, "overview": 2}.get(role, 3)


def to_result(page: WikiPage, *, score: float, query_tokens: list[str], route: str) -> SearchResult:
    return SearchResult(
        page_id=page.id,
        title=page.title,
        path=page.path,
        score=round(score, 4),
        snippet=snippet_for(page, query_tokens),
        role=page.role,
        source_refs=page.source_refs,
        route=route,
    )


def snippet_for(page: WikiPage, query_tokens: list[str], limit: int = 420) -> str:
    haystack = page.text or page.summary
    if query_tokens:
        lowered = haystack.lower()
        positions = [lowered.find(token) for token in query_tokens if lowered.find(token) >= 0]
        if positions:
            start = max(0, min(positions) - 100)
            haystack = haystack[start : start + limit]
    clean = " ".join(haystack.split())
    if len(clean) <= limit:
        return clean
    return clean[: limit - 1].rstrip() + "..."

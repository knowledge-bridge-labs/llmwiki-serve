from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass

from .models import SearchResult, WikiIndex, WikiPage

TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*(?:[가-힣]+)?|[가-힣]+")
ASCII_HANGUL_RE = re.compile(r"^([A-Za-z0-9]+)([가-힣]+)$")
HANGUL_RE = re.compile(r"^[가-힣]+$")
BM25_K1 = 1.2
BM25_B = 0.75
SEARCH_SNIPPET_LIMIT = 280


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

    @property
    def average_doc_length(self) -> float:
        if not self.documents:
            return 1.0
        return max(1.0, sum(len(document.tokens) for document in self.documents) / self.total)


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
    tokens = unique_tokens(tokenize(query))
    if not tokens:
        return overview_results(corpus.pages, limit)

    results: list[SearchResult] = []
    average_doc_length = corpus.average_doc_length
    for document in corpus.documents:
        text_score = 0.0
        for token in tokens:
            frequency = document.counts[token]
            if not frequency:
                continue
            idf = math.log(
                1 + (corpus.total - corpus.doc_freq[token] + 0.5) / (corpus.doc_freq[token] + 0.5)
            )
            text_score += (
                idf
                * bm25_term_frequency(
                    frequency,
                    doc_length=len(document.tokens),
                    average_doc_length=average_doc_length,
                )
                * query_token_weight(token)
            )
        if text_score <= 0:
            continue
        page = document.page
        score = text_score * role_multiplier(page)
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
    tokens: list[str] = []
    for match in TOKEN_RE.finditer(text):
        token = match.group(0).lower()
        tokens.append(token)
        compound = ASCII_HANGUL_RE.match(token)
        if compound:
            tokens.extend(part for part in compound.groups() if part)
            continue
        if HANGUL_RE.match(token) and len(token) > 2:
            tokens.extend(token[index : index + 2] for index in range(len(token) - 1))
    return tokens


def unique_tokens(tokens: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for token in tokens:
        if token in seen:
            continue
        seen.add(token)
        result.append(token)
    return result


def bm25_term_frequency(frequency: int, *, doc_length: int, average_doc_length: float) -> float:
    denominator = frequency + BM25_K1 * (
        1 - BM25_B + BM25_B * (max(1, doc_length) / average_doc_length)
    )
    return (frequency * (BM25_K1 + 1)) / denominator


def query_token_weight(token: str) -> float:
    if token.isdigit():
        return 0.2
    if HANGUL_RE.match(token) and len(token) == 1:
        return 0.4
    return 1.0


def role_multiplier(page: WikiPage) -> float:
    return {"hot": 1.15, "index": 1.06, "overview": 1.03}.get(page.role, 1.0)


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


def snippet_for(page: WikiPage, query_tokens: list[str], limit: int = SEARCH_SNIPPET_LIMIT) -> str:
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

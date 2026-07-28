from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

from .models import SearchMode, SearchResult, SearchResultProjection, WikiIndex, WikiPage

TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*(?:[가-힣]+)?|[가-힣]+")
ASCII_HANGUL_RE = re.compile(r"^([A-Za-z0-9]+)([가-힣]+)$")
HANGUL_RE = re.compile(r"^[가-힣]+$")
BM25_K1 = 1.2
BM25_B = 0.75
SEARCH_SNIPPET_LIMIT = 280
SEARCH_SNIPPET_MAX = 2_000
SEARCH_RESULT_FIELD_ORDER = (
    "page_id",
    "title",
    "path",
    "score",
    "snippet",
    "role",
    "source_refs",
    "route",
)
SEARCH_RESULT_FIELDS = set(SEARCH_RESULT_FIELD_ORDER)


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
    index: WikiIndex,
    query: str,
    *,
    limit: int = 8,
    include_drafts: bool = False,
    mode: SearchMode = "lexical",
    snippet_chars: int | None = None,
    min_score: float | None = None,
    exclude_page_ids: Sequence[str] | None = None,
) -> list[SearchResult]:
    pages = visible_pages(index.pages, include_drafts)
    return search_corpus(
        build_search_corpus(pages),
        query,
        limit=limit,
        mode=mode,
        snippet_chars=snippet_chars,
        min_score=min_score,
        exclude_page_ids=exclude_page_ids,
    )


def build_search_corpus(pages: list[WikiPage]) -> SearchCorpus:
    documents: list[SearchDocument] = []
    doc_freq: Counter[str] = Counter()
    for page in pages:
        tokens = tuple(tokenize(page_text(page)))
        counts = Counter(tokens)
        doc_freq.update(set(tokens))
        documents.append(SearchDocument(page=page, tokens=tokens, counts=counts))
    return SearchCorpus(pages=pages, documents=documents, doc_freq=doc_freq)


def search_corpus(
    corpus: SearchCorpus,
    query: str,
    *,
    limit: int = 8,
    mode: SearchMode = "lexical",
    snippet_chars: int | None = None,
    min_score: float | None = None,
    exclude_page_ids: Sequence[str] | None = None,
) -> list[SearchResult]:
    if mode == "literal":
        return literal_search_corpus(
            corpus,
            query,
            limit=limit,
            snippet_chars=snippet_chars,
            min_score=min_score,
            exclude_page_ids=exclude_page_ids,
        )
    tokens = unique_tokens(tokenize(query))
    excluded = page_exclusion_set(exclude_page_ids)
    if not tokens:
        return overview_results(
            corpus.pages,
            limit,
            snippet_chars=snippet_chars,
            exclude_page_ids=exclude_page_ids,
        )

    results: list[SearchResult] = []
    average_doc_length = corpus.average_doc_length
    for document in corpus.documents:
        if page_is_excluded(document.page, excluded):
            continue
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
        if min_score is not None and score < min_score:
            continue
        results.append(
            to_result(
                page,
                score=score,
                query_tokens=tokens,
                route="search",
                snippet_chars=snippet_chars,
            )
        )
    results.sort(key=lambda item: (-item.score, role_rank(item.role), item.path))
    return results[:limit]


def literal_search_corpus(
    corpus: SearchCorpus,
    query: str,
    *,
    limit: int,
    snippet_chars: int | None = None,
    min_score: float | None = None,
    exclude_page_ids: Sequence[str] | None = None,
) -> list[SearchResult]:
    literal_query = normalized_literal(query)
    excluded = page_exclusion_set(exclude_page_ids)
    if not literal_query:
        return overview_results(
            corpus.pages,
            limit,
            snippet_chars=snippet_chars,
            exclude_page_ids=exclude_page_ids,
        )

    folded_query = literal_query.casefold()
    results: list[SearchResult] = []
    for document in corpus.documents:
        page = document.page
        if page_is_excluded(page, excluded):
            continue
        searchable = literal_page_text(page).casefold()
        occurrences = substring_count(searchable, folded_query)
        if not occurrences:
            continue
        score = literal_score(page, folded_query, occurrences) * role_multiplier(page)
        if min_score is not None and score < min_score:
            continue
        results.append(
            to_result(
                page,
                score=score,
                query_tokens=[],
                route="literal",
                snippet_chars=snippet_chars,
                literal_query=literal_query,
            )
        )
    results.sort(key=lambda item: (-item.score, role_rank(item.role), item.path))
    return results[:limit]


def context_orientation(
    index: WikiIndex,
    *,
    include_drafts: bool = False,
    snippet_chars: int | None = None,
) -> list[SearchResult]:
    pages = visible_pages(index.pages, include_drafts)
    ordered = orientation_pages(pages)
    return [
        to_result(
            page,
            score=1.0 - rank * 0.01,
            query_tokens=[],
            route="orientation",
            snippet_chars=snippet_chars,
        )
        for rank, page in enumerate(ordered[:3])
    ]


def overview_results(
    pages: list[WikiPage],
    limit: int,
    *,
    snippet_chars: int | None = None,
    exclude_page_ids: Sequence[str] | None = None,
) -> list[SearchResult]:
    excluded = page_exclusion_set(exclude_page_ids)
    ordered = orientation_pages(pages)
    results = [
        to_result(
            page,
            score=1.0 - rank * 0.01,
            query_tokens=[],
            route="overview",
            snippet_chars=snippet_chars,
        )
        for rank, page in enumerate(
            page for page in ordered if not page_is_excluded(page, excluded)
        )
    ]
    return results[:limit]


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


def literal_page_text(page: WikiPage) -> str:
    return " ".join(
        [
            page.title,
            page.path,
            page.summary,
            page.text,
            " ".join(page.tags),
            " ".join(page.source_refs),
        ]
    )


def normalized_literal(query: str) -> str:
    return " ".join(query.split())


def substring_count(haystack: str, needle: str) -> int:
    count = start = 0
    while True:
        position = haystack.find(needle, start)
        if position < 0:
            return count
        count += 1
        start = position + max(1, len(needle))


def literal_score(page: WikiPage, folded_query: str, occurrences: int) -> float:
    score = 1.0 + min(3.0, math.log1p(occurrences))
    if folded_query in page.title.casefold():
        score += 1.5
    if folded_query in page.summary.casefold():
        score += 0.7
    return score


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


def to_result(
    page: WikiPage,
    *,
    score: float,
    query_tokens: list[str],
    route: str,
    snippet_chars: int | None = None,
    literal_query: str = "",
) -> SearchResult:
    return SearchResult(
        page_id=page.id,
        title=page.title,
        path=page.path,
        score=round(score, 4),
        snippet=snippet_for(
            page,
            query_tokens,
            limit=snippet_limit(snippet_chars),
            literal_query=literal_query,
        ),
        role=page.role,
        source_refs=page.source_refs,
        route=route,
    )


def snippet_limit(value: int | None) -> int:
    if value is None:
        return SEARCH_SNIPPET_LIMIT
    return max(0, min(int(value), SEARCH_SNIPPET_MAX))


def snippet_for(
    page: WikiPage,
    query_tokens: list[str],
    limit: int = SEARCH_SNIPPET_LIMIT,
    *,
    literal_query: str = "",
) -> str:
    if limit <= 0:
        return ""
    haystack = page.text or page.summary
    folded_haystack = haystack.casefold()
    if literal_query:
        position = folded_haystack.find(literal_query.casefold())
        if position >= 0:
            start = max(0, position - 100)
            haystack = haystack[start : start + limit]
    elif query_tokens:
        lowered = haystack.lower()
        positions = [lowered.find(token) for token in query_tokens if lowered.find(token) >= 0]
        if positions:
            start = max(0, min(positions) - 100)
            haystack = haystack[start : start + limit]
    clean = " ".join(haystack.split())
    if len(clean) <= limit:
        return clean
    return clean[: limit - 1].rstrip() + "..."


def page_exclusion_set(page_ids: Sequence[str] | None) -> set[str]:
    return {item.strip() for item in page_ids or [] if item.strip()}


def page_is_excluded(page: WikiPage, excluded: set[str]) -> bool:
    return page.id in excluded or page.path in excluded


def normalize_search_result_fields(fields: Sequence[str] | None) -> tuple[str, ...] | None:
    if fields is None:
        return None
    normalized: list[str] = []
    for field in fields:
        name = field.strip().lower()
        if name in SEARCH_RESULT_FIELDS and name not in normalized:
            normalized.append(name)
    if "page_id" not in normalized:
        normalized.insert(0, "page_id")
    return tuple(field for field in SEARCH_RESULT_FIELD_ORDER if field in normalized)


def project_search_result(
    result: SearchResult,
    fields: Sequence[str] | None,
) -> SearchResult | SearchResultProjection:
    normalized = normalize_search_result_fields(fields)
    if normalized is None:
        return result
    return SearchResultProjection.model_construct(
        _fields_set=set(normalized),
        **{field: getattr(result, field) for field in normalized},
    )

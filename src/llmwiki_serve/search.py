from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final, Literal, TypeAlias

import snowballstemmer  # type: ignore[import-untyped]

from .errors import LlmWikiUserError
from .models import SearchMode, SearchResult, SearchResultProjection, WikiIndex, WikiPage

TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*(?:[가-힣]+)?|[가-힣]+")
ASCII_HANGUL_RE = re.compile(r"^([A-Za-z0-9]+)([가-힣]+)$")
HANGUL_RE = re.compile(r"^[가-힣]+$")
ENGLISH_APOSTROPHES = frozenset("'’")
EXACT_COMPOUND_SEPARATORS = frozenset("._-")
SINGLE_COMPOUND_REMAINDER_CHARS = "\"'`“”‘’()[]{}.,;:!?"
BM25_K1 = 1.2
BM25_B = 0.75
EXACT_COMPOUND_SCORE_WEIGHT = 0.85
EXACT_METADATA_SCORE_WEIGHT = 0.35
SEARCH_SNIPPET_LIMIT = 280
SEARCH_SNIPPET_MAX = 2_000
PublicAnalyzerProfile: TypeAlias = Literal["legacy", "english"]
AnalyzerProfile: TypeAlias = Literal["legacy", "english"]
PUBLIC_ANALYZER_PROFILES: tuple[PublicAnalyzerProfile, ...] = ("legacy", "english")
ANALYZER_PROFILES: tuple[AnalyzerProfile, ...] = PUBLIC_ANALYZER_PROFILES
DEFAULT_ANALYZER_PROFILE: Final[AnalyzerProfile] = "legacy"
DEFAULT_PUBLIC_ANALYZER_PROFILE: Final[PublicAnalyzerProfile] = "legacy"
LUCENE_ENGLISH_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "but",
        "by",
        "for",
        "if",
        "in",
        "into",
        "is",
        "it",
        "no",
        "not",
        "of",
        "on",
        "or",
        "such",
        "that",
        "the",
        "their",
        "then",
        "there",
        "these",
        "they",
        "this",
        "to",
        "was",
        "will",
        "with",
    }
)
ENGLISH_STEMMER = snowballstemmer.stemmer("english")
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
SEARCH_MODES: tuple[SearchMode, ...] = ("lexical", "literal", "vector", "hybrid")
ManagedPrior = Callable[[SearchResult], float]
TokenPostings = Mapping[str, tuple[tuple[int, int], ...]]


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
    doc_lengths: tuple[int, ...]
    average_doc_length_value: float
    postings: TokenPostings
    exact_compound_postings: TokenPostings
    exact_metadata_postings: TokenPostings
    analyzer_profile: AnalyzerProfile = DEFAULT_ANALYZER_PROFILE

    @property
    def total(self) -> int:
        return max(1, len(self.documents))

    @property
    def average_doc_length(self) -> float:
        return self.average_doc_length_value


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
    managed_prior: ManagedPrior | None = None,
    managed_tie_band: float = 0.0,
    analyzer_profile: AnalyzerProfile = DEFAULT_ANALYZER_PROFILE,
) -> list[SearchResult]:
    pages = visible_pages(index.pages, include_drafts)
    return search_corpus(
        build_search_corpus(pages, analyzer_profile=analyzer_profile),
        query,
        limit=limit,
        mode=mode,
        snippet_chars=snippet_chars,
        min_score=min_score,
        exclude_page_ids=exclude_page_ids,
        managed_prior=managed_prior,
        managed_tie_band=managed_tie_band,
    )


def build_search_corpus(
    pages: list[WikiPage],
    *,
    analyzer_profile: AnalyzerProfile = DEFAULT_ANALYZER_PROFILE,
) -> SearchCorpus:
    profile = normalize_analyzer_profile(analyzer_profile)
    documents: list[SearchDocument] = []
    doc_freq: Counter[str] = Counter()
    doc_lengths: list[int] = []
    mutable_postings: dict[str, list[tuple[int, int]]] = {}
    mutable_exact_compound_postings: dict[str, list[tuple[int, int]]] = {}
    mutable_exact_metadata_postings: dict[str, list[tuple[int, int]]] = {}
    for doc_index, page in enumerate(pages):
        tokens = tuple(tokenize(page_text_for_profile(page, profile), analyzer_profile=profile))
        counts = Counter(tokens)
        doc_freq.update(set(tokens))
        doc_lengths.append(len(tokens))
        for token, frequency in counts.items():
            mutable_postings.setdefault(token, []).append((doc_index, frequency))
        if profile == "english":
            index_exact_channel(
                mutable_exact_compound_postings,
                doc_index,
                authored_page_text(page),
            )
            index_exact_channel(
                mutable_exact_metadata_postings,
                doc_index,
                exact_metadata_text(page),
            )
        documents.append(SearchDocument(page=page, tokens=tokens, counts=counts))
    total = max(1, len(documents))
    average_doc_length = 1.0
    if documents:
        average_doc_length = max(1.0, sum(doc_lengths) / total)
    postings = {token: tuple(entries) for token, entries in mutable_postings.items()}
    exact_compound_postings = {
        token: tuple(entries) for token, entries in mutable_exact_compound_postings.items()
    }
    exact_metadata_postings = {
        token: tuple(entries) for token, entries in mutable_exact_metadata_postings.items()
    }
    return SearchCorpus(
        pages=pages,
        documents=documents,
        doc_freq=doc_freq,
        doc_lengths=tuple(doc_lengths),
        average_doc_length_value=average_doc_length,
        postings=MappingProxyType(postings),
        exact_compound_postings=MappingProxyType(exact_compound_postings),
        exact_metadata_postings=MappingProxyType(exact_metadata_postings),
        analyzer_profile=profile,
    )


def search_corpus(
    corpus: SearchCorpus,
    query: str,
    *,
    limit: int = 8,
    mode: SearchMode = "lexical",
    snippet_chars: int | None = None,
    min_score: float | None = None,
    exclude_page_ids: Sequence[str] | None = None,
    managed_prior: ManagedPrior | None = None,
    managed_tie_band: float = 0.0,
) -> list[SearchResult]:
    mode = normalize_search_mode(mode)
    if mode == "literal":
        return literal_search_corpus(
            corpus,
            query,
            limit=limit,
            snippet_chars=snippet_chars,
            min_score=min_score,
            exclude_page_ids=exclude_page_ids,
            managed_prior=managed_prior,
            managed_tie_band=managed_tie_band,
        )
    if mode in {"vector", "hybrid"}:
        raise LlmWikiUserError(
            f"Search mode {mode!r} requires a configured vector provider through "
            "LlmWikiService, create_app, or the llmwiki-serve CLI."
        )
    tokens = unique_tokens(tokenize(query, analyzer_profile=corpus.analyzer_profile))
    exact_query_token = single_exact_compound_query_token(query, corpus.analyzer_profile)
    exact_query_tokens = (
        [exact_query_token]
        if exact_query_token
        else exact_compound_tokens(query, corpus.analyzer_profile)
    )
    required_exact_docs = exact_required_doc_indexes(
        corpus,
        exact_query_token,
    )
    excluded = page_exclusion_set(exclude_page_ids)
    if not tokens:
        if not lexical_empty_query_uses_overview(query, corpus.analyzer_profile):
            return []
        return overview_results(
            corpus.pages,
            limit,
            snippet_chars=snippet_chars,
            exclude_page_ids=exclude_page_ids,
        )
    if required_exact_docs == set():
        return []

    candidate_scores: dict[int, float] = {}
    average_doc_length = corpus.average_doc_length
    for token in tokens:
        doc_frequency = corpus.doc_freq[token]
        if not doc_frequency:
            continue
        idf = math.log(1 + (corpus.total - doc_frequency + 0.5) / (doc_frequency + 0.5))
        token_weight = query_token_weight(token)
        for doc_index, frequency in corpus.postings.get(token, ()):
            if required_exact_docs is not None and doc_index not in required_exact_docs:
                continue
            candidate_scores[doc_index] = candidate_scores.get(doc_index, 0.0) + (
                idf
                * bm25_term_frequency(
                    frequency,
                    doc_length=corpus.doc_lengths[doc_index],
                    average_doc_length=average_doc_length,
                )
                * token_weight
            )
    if exact_query_token:
        add_exact_channel_scores(
            candidate_scores,
            corpus=corpus,
            postings=corpus.exact_compound_postings,
            query_tokens=[exact_query_token],
            weight=EXACT_COMPOUND_SCORE_WEIGHT,
            required_docs=required_exact_docs,
        )
    add_exact_channel_scores(
        candidate_scores,
        corpus=corpus,
        postings=corpus.exact_metadata_postings,
        query_tokens=exact_query_tokens,
        weight=EXACT_METADATA_SCORE_WEIGHT,
        required_docs=required_exact_docs,
    )

    results: list[SearchResult] = []
    for doc_index in sorted(candidate_scores):
        document = corpus.documents[doc_index]
        if page_is_excluded(document.page, excluded):
            continue
        text_score = candidate_scores[doc_index]
        if text_score <= 0:
            continue
        page = document.page
        score = text_score * analyzer_role_multiplier(page, corpus.analyzer_profile)
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
    rank_search_results(
        results,
        managed_prior=managed_prior,
        managed_tie_band=managed_tie_band,
    )
    return results[:limit]


def literal_search_corpus(
    corpus: SearchCorpus,
    query: str,
    *,
    limit: int,
    snippet_chars: int | None = None,
    min_score: float | None = None,
    exclude_page_ids: Sequence[str] | None = None,
    managed_prior: ManagedPrior | None = None,
    managed_tie_band: float = 0.0,
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
    rank_search_results(
        results,
        managed_prior=managed_prior,
        managed_tie_band=managed_tie_band,
    )
    return results[:limit]


def rank_search_results(
    results: list[SearchResult],
    *,
    managed_prior: ManagedPrior | None = None,
    managed_tie_band: float = 0.0,
) -> None:
    results.sort(key=lexical_sort_key)
    if (
        managed_prior is None
        or managed_tie_band <= 0
        or not math.isfinite(managed_tie_band)
        or len(results) < 2
    ):
        return

    ranked: list[SearchResult] = []
    index = 0
    while index < len(results):
        band_top_score = results[index].score
        band: list[SearchResult] = []
        while index < len(results) and band_top_score - results[index].score <= managed_tie_band:
            band.append(results[index])
            index += 1
        band.sort(key=lambda item: managed_sort_key(item, managed_prior, managed_tie_band))
        ranked.extend(band)
    results[:] = ranked


def lexical_sort_key(item: SearchResult) -> tuple[float, int, str]:
    return (-item.score, role_rank(item.role), item.path)


def managed_sort_key(
    item: SearchResult,
    managed_prior: ManagedPrior,
    managed_tie_band: float,
) -> tuple[float, float, int, str]:
    try:
        boost = float(managed_prior(item))
    except (TypeError, ValueError):
        boost = 0.0
    boost = 0.0 if not math.isfinite(boost) or boost <= 0 else min(boost, managed_tie_band)
    return (-(item.score + boost), -item.score, role_rank(item.role), item.path)


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
        [
            page.title,
            page.summary,
            page.text,
            " ".join(page.tags),
            " ".join(page.source_refs),
        ]
    )


def authored_page_text(page: WikiPage) -> str:
    return " ".join([page.title, page.summary, page.text, " ".join(page.tags)])


def exact_metadata_text(page: WikiPage) -> str:
    return " ".join([page.path, " ".join(page.source_refs)])


def page_text_for_profile(page: WikiPage, analyzer_profile: AnalyzerProfile) -> str:
    if analyzer_profile == "english":
        return authored_page_text(page)
    return page_text(page)


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


def tokenize(
    text: str, *, analyzer_profile: AnalyzerProfile = DEFAULT_ANALYZER_PROFILE
) -> list[str]:
    profile = normalize_analyzer_profile(analyzer_profile)
    if profile == "english":
        return tokenize_english(text)
    return tokenize_legacy(text)


def tokenize_legacy(text: str) -> list[str]:
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


def tokenize_english(text: str) -> list[str]:
    tokens: list[str] = []
    for raw_token in scan_english_raw_tokens(text):
        token = raw_token.lower()
        compound = ASCII_HANGUL_RE.match(token)
        if compound:
            tokens.append(token)
            tokens.extend(part for part in compound.groups() if part)
            continue
        if HANGUL_RE.match(token):
            tokens.append(token)
            if len(token) > 2:
                tokens.extend(token[index : index + 2] for index in range(len(token) - 1))
            continue
        analyzed = english_analyzed_token(token)
        if analyzed:
            tokens.append(analyzed)
    return tokens


def english_analyzed_token(token: str) -> str:
    normalized = normalize_english_possessive(token)
    if not normalized or normalized in LUCENE_ENGLISH_STOPWORDS:
        return ""
    if normalized.isdigit():
        return normalized
    return str(ENGLISH_STEMMER.stemWord(normalized))


def normalize_english_possessive(token: str) -> str:
    normalized = token.casefold()
    if normalized.endswith("'s") or normalized.endswith("’s"):
        return normalized[:-2]
    if normalized.endswith("'") or normalized.endswith("’"):
        return normalized[:-1]
    return normalized


def index_exact_channel(
    postings: dict[str, list[tuple[int, int]]],
    doc_index: int,
    text: str,
) -> None:
    counts = Counter(exact_compound_tokens(text, "english"))
    for token, frequency in counts.items():
        postings.setdefault(token, []).append((doc_index, frequency))


def is_ascii_alnum(char: str) -> bool:
    return "0" <= char <= "9" or "A" <= char <= "Z" or "a" <= char <= "z"


def is_hangul_syllable(char: str) -> bool:
    return "\uac00" <= char <= "\ud7a3"


def is_exact_boundary_word_char(char: str) -> bool:
    return is_ascii_alnum(char) or is_hangul_syllable(char)


def consume_ascii_alnum(text: str, start: int) -> int:
    cursor = start
    length = len(text)
    while cursor < length and is_ascii_alnum(text[cursor]):
        cursor += 1
    return cursor


def consume_hangul_syllables(text: str, start: int) -> int:
    cursor = start
    length = len(text)
    while cursor < length and is_hangul_syllable(text[cursor]):
        cursor += 1
    return cursor


def scan_english_raw_tokens(text: str) -> list[str]:
    tokens: list[str] = []
    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        if is_ascii_alnum(char):
            start = index
            index = consume_ascii_alnum(text, index)
            if index < length and is_hangul_syllable(text[index]):
                index = consume_hangul_syllables(text, index)
            elif index < length and text[index] in ENGLISH_APOSTROPHES:
                index += 1
                # Preserve authored-case semantics before the later lowercase step.
                if index < length and text[index] == "s":
                    index += 1
            tokens.append(text[start:index])
            continue
        if is_hangul_syllable(char):
            start = index
            index = consume_hangul_syllables(text, index)
            tokens.append(text[start:index])
            continue
        index += 1
    return tokens


def consume_exact_boundary_word_chars(text: str, start: int) -> int:
    cursor = start
    length = len(text)
    while cursor < length and is_exact_boundary_word_char(text[cursor]):
        cursor += 1
    return cursor


def exact_compound_token_spans(text: str) -> list[tuple[int, int, str]]:
    spans: list[tuple[int, int, str]] = []
    index = 0
    length = len(text)
    while index < length:
        if not is_ascii_alnum(text[index]):
            index += 1
            continue
        if index > 0 and is_exact_boundary_word_char(text[index - 1]):
            index += 1
            continue

        start = index
        cursor = consume_ascii_alnum(text, index)
        separator_groups = 0
        last_valid_end: int | None = None
        reject_cursor = cursor
        while cursor < length and text[cursor] in EXACT_COMPOUND_SEPARATORS:
            next_index = cursor + 1
            if next_index >= length or not is_ascii_alnum(text[next_index]):
                break
            cursor = consume_ascii_alnum(text, next_index)
            separator_groups += 1
            hangul_end = consume_hangul_syllables(text, cursor)
            reject_cursor = hangul_end
            if hangul_end >= length or not is_exact_boundary_word_char(text[hangul_end]):
                last_valid_end = hangul_end

        if not separator_groups:
            index = cursor
            continue

        if last_valid_end is None:
            if reject_cursor < length and is_exact_boundary_word_char(text[reject_cursor]):
                index = consume_exact_boundary_word_chars(text, reject_cursor)
            else:
                index = max(reject_cursor, index + 1)
            continue

        spans.append((start, last_valid_end, text[start:last_valid_end].casefold()))
        index = last_valid_end
    return spans


def exact_compound_tokens(
    text: str,
    analyzer_profile: AnalyzerProfile = DEFAULT_ANALYZER_PROFILE,
) -> list[str]:
    if analyzer_profile != "english":
        return []
    return unique_tokens([token for _start, _end, token in exact_compound_token_spans(text)])


def single_exact_compound_query_token(
    query: str,
    analyzer_profile: AnalyzerProfile,
) -> str:
    if analyzer_profile != "english":
        return ""
    spans = exact_compound_token_spans(query)
    if len(spans) != 1:
        return ""
    start, end, token = spans[0]
    remainder = f"{query[:start]}{query[end:]}".strip()
    if remainder.strip(SINGLE_COMPOUND_REMAINDER_CHARS).strip():
        return ""
    return token


def exact_required_doc_indexes(corpus: SearchCorpus, exact_query_token: str) -> set[int] | None:
    if not exact_query_token:
        return None
    doc_indexes = {
        doc_index
        for doc_index, _frequency in corpus.exact_compound_postings.get(exact_query_token, ())
    }
    doc_indexes.update(
        doc_index
        for doc_index, _frequency in corpus.exact_metadata_postings.get(exact_query_token, ())
    )
    return doc_indexes


def exact_required_page_ids_for_query(corpus: SearchCorpus, query: str) -> set[str] | None:
    exact_query_token = single_exact_compound_query_token(query, corpus.analyzer_profile)
    required_doc_indexes = exact_required_doc_indexes(corpus, exact_query_token)
    if required_doc_indexes is None:
        return None
    return {corpus.documents[index].page.id for index in required_doc_indexes}


def add_exact_channel_scores(
    candidate_scores: dict[int, float],
    *,
    corpus: SearchCorpus,
    postings: TokenPostings,
    query_tokens: Sequence[str],
    weight: float,
    required_docs: set[int] | None,
) -> None:
    for token in query_tokens:
        entries = postings.get(token, ())
        doc_frequency = len(entries)
        if not doc_frequency:
            continue
        idf = math.log(1 + (corpus.total - doc_frequency + 0.5) / (doc_frequency + 0.5))
        for doc_index, frequency in entries:
            if required_docs is not None and doc_index not in required_docs:
                continue
            candidate_scores[doc_index] = candidate_scores.get(doc_index, 0.0) + (
                idf * (1.0 + math.log1p(frequency)) * weight
            )


def lexical_empty_query_uses_overview(query: str, analyzer_profile: AnalyzerProfile) -> bool:
    if not query.strip():
        return True
    return analyzer_profile == "legacy"


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


def analyzer_role_multiplier(page: WikiPage, analyzer_profile: AnalyzerProfile) -> float:
    _ = analyzer_profile
    return role_multiplier(page)


def role_rank(role: str) -> int:
    return {"hot": 0, "index": 1, "overview": 2}.get(role, 3)


def normalize_analyzer_profile(value: str) -> AnalyzerProfile:
    if value in ANALYZER_PROFILES:
        return value
    raise ValueError(f"unknown analyzer profile: {value!r}")


def normalize_search_mode(value: str) -> SearchMode:
    normalized = str(value or "lexical").strip().lower()
    if normalized in SEARCH_MODES:
        return normalized
    raise LlmWikiUserError("unknown search mode: expected lexical, literal, vector, or hybrid")


def normalize_public_analyzer_profile(value: str) -> PublicAnalyzerProfile:
    if value in PUBLIC_ANALYZER_PROFILES:
        return value
    raise ValueError(f"unknown public analyzer profile: {value!r}")


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

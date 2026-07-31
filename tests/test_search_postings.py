from __future__ import annotations

import json
import math
import re
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, Literal

import pytest

from llmwiki_serve.models import SearchMode, SearchResult, WikiPage
from llmwiki_serve.search import (
    DEFAULT_ANALYZER_PROFILE,
    AnalyzerProfile,
    SearchCorpus,
    analyzer_role_multiplier,
    bm25_term_frequency,
    build_search_corpus,
    literal_search_corpus,
    overview_results,
    page_exclusion_set,
    page_is_excluded,
    query_token_weight,
    rank_search_results,
    search_corpus,
    to_result,
    tokenize,
    unique_tokens,
)
from llmwiki_serve.service import LlmWikiService

ManagedPrior = Callable[[SearchResult], float]
REFERENCE_EXACT_COMPOUND_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9])" r"[A-Za-z0-9]+(?:[_.-][A-Za-z0-9]+)+" r"(?![A-Za-z0-9])"
)
REFERENCE_SINGLE_COMPOUND_REMAINDER_CHARS = "\"'`()[]{}.,;:!?"
REFERENCE_ENGLISH_ANALYZER_PROFILES: frozenset[AnalyzerProfile] = frozenset({"english"})
REFERENCE_EXACT_COMPOUND_SCORE_WEIGHT = 0.85
REFERENCE_EXACT_METADATA_SCORE_WEIGHT = 0.35


def test_postings_doc_lengths_and_statistics_are_global() -> None:
    pages = representative_pages()
    corpus = build_search_corpus(pages, analyzer_profile="legacy")

    assert corpus.doc_lengths == tuple(len(document.tokens) for document in corpus.documents)
    assert DEFAULT_ANALYZER_PROFILE == "legacy"
    assert build_search_corpus(pages).analyzer_profile == "legacy"
    assert corpus.average_doc_length == max(1.0, sum(corpus.doc_lengths) / corpus.total)
    assert corpus.doc_freq["shared"] == sum(
        1 for document in corpus.documents if document.counts["shared"]
    )
    assert corpus.postings["release.v1-beta"] == tuple(
        (doc_index, document.counts["release.v1-beta"])
        for doc_index, document in enumerate(corpus.documents)
        if document.counts["release.v1-beta"]
    )
    assert tokenize("release.v1-beta", analyzer_profile="legacy") == ["release.v1-beta"]

    with pytest.raises(TypeError):
        corpus.postings["new-token"] = ((0, 1),)  # type: ignore[index]


def test_lexical_postings_match_full_scan_reference_for_representative_options() -> None:
    corpus = build_search_corpus(representative_pages(), analyzer_profile="legacy")
    shared_scores = reference_search_corpus(corpus, "shared alpha", limit=10)
    min_score = (shared_scores[1].score + shared_scores[-1].score) / 2

    cases: list[dict[str, Any]] = [
        {"query": "alpha release.v1-beta", "limit": 10, "snippet_chars": 96},
        {"query": "", "limit": 5, "snippet_chars": 80},
        {"query": "zzunmatched", "limit": 10},
        {
            "query": "shared alpha",
            "limit": 10,
            "exclude_page_ids": ["hot", "b-beta.md"],
        },
        {"query": "shared alpha", "limit": 10, "min_score": min_score},
        {"query": "zztieonly", "limit": 10},
        {"query": "Exact Phrase", "limit": 10, "mode": "literal"},
    ]

    for options in cases:
        query = str(options.pop("query"))
        assert_serialized_results_match_reference(corpus, query, **options)


def test_explicit_english_postings_match_contract_scan_reference() -> None:
    corpus = build_search_corpus(english_representative_pages(), analyzer_profile="english")
    role_scores = reference_search_corpus(corpus, "roleboost parity", limit=10)
    min_score = (role_scores[0].score + role_scores[1].score) / 2

    assert corpus.analyzer_profile == "english"
    assert [item.page_id for item in role_scores] == ["hot-role", "topic-role"]

    cases: list[dict[str, Any]] = [
        {"query": "running treatments of the", "limit": 10, "snippet_chars": 96},
        {"query": "the and of", "limit": 10},
        {"query": "http_response", "limit": 10},
        {"query": "release.v1-beta.md", "limit": 10},
        {"query": "SRC-CARDIOLOGY-STUDIES", "limit": 10},
        {"query": "cardiology studies", "limit": 10},
        {"query": "roleboost parity", "limit": 10, "exclude_page_ids": ["hot-role"]},
        {"query": "roleboost parity", "limit": 10, "min_score": min_score},
        {"query": "pathorder equality", "limit": 10},
    ]

    for options in cases:
        query = str(options.pop("query"))
        assert_serialized_results_match_reference(corpus, query, **options)


def test_explicit_english_postings_keep_exact_channels_outside_bm25() -> None:
    corpus = build_search_corpus(english_representative_pages(), analyzer_profile="english")

    assert "studi" in corpus.postings
    assert "treatment" in corpus.postings
    assert "of" not in corpus.postings
    assert "the" not in corpus.postings
    assert "http_response" not in corpus.postings
    assert "src-cardiology-studies" not in corpus.postings
    assert "source-ref-only" not in postings_page_ids(corpus, corpus.postings, "cardiolog")
    assert "source-ref-only" not in postings_page_ids(corpus, corpus.postings, "studi")

    assert postings_page_ids(corpus, corpus.exact_compound_postings, "http_response") == [
        "compound-authored"
    ]
    assert postings_page_ids(corpus, corpus.exact_compound_postings, "release.v1-beta.md") == []
    assert postings_page_ids(corpus, corpus.exact_metadata_postings, "release.v1-beta.md") == [
        "path-metadata"
    ]
    assert postings_page_ids(corpus, corpus.exact_metadata_postings, "src-cardiology-studies") == [
        "source-ref-only"
    ]


def test_explicit_english_exact_and_broad_queries_preserve_contracts() -> None:
    corpus = build_search_corpus(english_representative_pages(), analyzer_profile="english")

    assert result_ids(search_corpus(corpus, "http_response", limit=10)) == ["compound-authored"]
    assert result_ids(search_corpus(corpus, "release.v1-beta.md", limit=10)) == ["path-metadata"]
    assert result_ids(search_corpus(corpus, "SRC-CARDIOLOGY-STUDIES", limit=10)) == [
        "source-ref-only"
    ]
    assert result_ids(search_corpus(corpus, "cardiology studies", limit=10)) == ["natural-source"]
    assert search_corpus(corpus, "the and of", limit=10) == []


def test_explicit_english_role_filter_min_score_and_path_order() -> None:
    corpus = build_search_corpus(english_representative_pages(), analyzer_profile="english")

    role_results = search_corpus(corpus, "roleboost parity", limit=10)
    min_score = (role_results[0].score + role_results[1].score) / 2

    assert result_ids(role_results) == ["hot-role", "topic-role"]
    assert role_results[0].score > role_results[1].score
    assert result_ids(search_corpus(corpus, "roleboost parity", limit=10, min_score=min_score)) == [
        "hot-role"
    ]
    assert result_ids(
        search_corpus(corpus, "roleboost parity", limit=10, exclude_page_ids=["hot-role"])
    ) == ["topic-role"]
    assert result_ids(search_corpus(corpus, "pathorder equality", limit=10)) == [
        "order-alpha",
        "order-beta",
    ]


def test_role_path_and_managed_prior_ties_match_full_scan_reference() -> None:
    corpus = build_search_corpus(representative_pages(), analyzer_profile="legacy")

    tied = search_corpus(corpus, "zztieonly", limit=10)
    tied_ids = [item.page_id for item in tied]
    assert tied_ids.index("m-alpha") < tied_ids.index("m-beta")
    assert_serialized_results_match_reference(corpus, "zztieonly", limit=10)

    def managed_prior(result: SearchResult) -> float:
        return 0.05 if result.page_id == "m-beta" else 0.0

    boosted = search_corpus(
        corpus,
        "managedtie",
        limit=4,
        managed_prior=managed_prior,
        managed_tie_band=0.1,
    )

    assert [item.page_id for item in boosted[:2]] == ["m-beta", "m-alpha"]
    assert_serialized_results_match_reference(
        corpus,
        "managedtie",
        limit=4,
        managed_prior=managed_prior,
        managed_tie_band=0.1,
    )


def test_rank_search_results_preserves_role_and_path_ties() -> None:
    results = [
        search_result("topic-b", "b.md", "topic"),
        search_result("hot", "z-hot.md", "hot"),
        search_result("topic-a", "a.md", "topic"),
        search_result("index", "y-index.md", "index"),
    ]

    rank_search_results(results)

    assert [item.page_id for item in results] == ["hot", "index", "topic-a", "topic-b"]


def test_excluded_pages_remain_in_bm25_statistics() -> None:
    corpus = build_search_corpus(
        [
            page("included", "included.md", "Needle appears in the visible page."),
            page("excluded", "excluded.md", "Needle appears in the excluded page."),
        ],
        analyzer_profile="legacy",
    )

    assert corpus.doc_freq["needle"] == 2
    assert_serialized_results_match_reference(
        corpus,
        "needle",
        limit=5,
        exclude_page_ids=["excluded"],
    )
    actual = search_corpus(corpus, "needle", limit=5, exclude_page_ids=["excluded"])
    assert [item.page_id for item in actual] == ["included"]


def test_service_views_keep_approved_and_draft_corpora_independent(tmp_path: Path) -> None:
    root = tmp_path / "wiki"
    write_markdown(
        root / "index.md",
        """
---
wiki_title: Draft View Fixture
review_state: approved
---
# Draft View Fixture

shared public context.
""",
    )
    write_markdown(
        root / "topic.md",
        """
---
title: Topic
review_state: approved
---
# Topic

shared public evidence.
""",
    )
    write_markdown(
        root / "draft.md",
        """
---
title: Draft
review_state: draft
---
# Draft

draftonly shared hidden evidence.
""",
    )
    service = LlmWikiService(root)
    index = service.index()
    views = service._index_views(index)

    approved = views.search_corpus(include_drafts=False)
    all_pages = views.search_corpus(include_drafts=True)
    draft_token = tokenize("draftonly")[0]

    assert approved is not all_pages
    assert draft_token not in approved.postings
    assert all_pages.postings[draft_token]
    assert service.search("draftonly") == []
    assert service.search("draftonly", include_drafts=True)[0]["page_id"] == "draft"
    assert_serialized_results_match_reference(approved, "shared", limit=5)
    assert_serialized_results_match_reference(all_pages, "shared", limit=5)


def test_public_service_payloads_do_not_expose_postings(tmp_path: Path) -> None:
    root = tmp_path / "wiki"
    write_markdown(
        root / "index.md",
        """
---
wiki_title: Public Payload Fixture
review_state: approved
---
# Public Payload Fixture

visible search content.
""",
    )
    service = LlmWikiService(root)

    payloads = [
        service.manifest().model_dump(mode="json"),
        service.source_bundle().model_dump(mode="json"),
        service.context("visible").model_dump(mode="json"),
        {"results": service.search("visible")},
    ]

    for payload in payloads:
        assert "postings" not in json.dumps(payload, ensure_ascii=False).lower()


def assert_serialized_results_match_reference(
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
) -> None:
    expected = serialize_results(
        reference_search_corpus(
            corpus,
            query,
            limit=limit,
            mode=mode,
            snippet_chars=snippet_chars,
            min_score=min_score,
            exclude_page_ids=exclude_page_ids,
            managed_prior=managed_prior,
            managed_tie_band=managed_tie_band,
        )
    )
    actual = serialize_results(
        search_corpus(
            corpus,
            query,
            limit=limit,
            mode=mode,
            snippet_chars=snippet_chars,
            min_score=min_score,
            exclude_page_ids=exclude_page_ids,
            managed_prior=managed_prior,
            managed_tie_band=managed_tie_band,
        )
    )

    assert actual == expected


def reference_search_corpus(
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

    tokens = unique_tokens(tokenize(query, analyzer_profile=corpus.analyzer_profile))
    exact_query_token = reference_single_exact_compound_query_token(
        query,
        corpus.analyzer_profile,
    )
    exact_query_tokens = (
        [exact_query_token]
        if exact_query_token
        else reference_exact_compound_tokens(query, corpus.analyzer_profile)
    )
    required_exact_docs = reference_required_exact_doc_indexes(corpus, exact_query_token)
    excluded = page_exclusion_set(exclude_page_ids)
    if not tokens:
        if query.strip() and reference_is_english_profile(corpus.analyzer_profile):
            return []
        return overview_results(
            corpus.pages,
            limit,
            snippet_chars=snippet_chars,
            exclude_page_ids=exclude_page_ids,
        )
    if required_exact_docs == set():
        return []

    results: list[SearchResult] = []
    average_doc_length = corpus.average_doc_length
    for doc_index, document in enumerate(corpus.documents):
        if required_exact_docs is not None and doc_index not in required_exact_docs:
            continue
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
        if exact_query_token:
            text_score += reference_exact_channel_score(
                corpus,
                doc_index,
                corpus.exact_compound_postings,
                [exact_query_token],
                REFERENCE_EXACT_COMPOUND_SCORE_WEIGHT,
            )
        text_score += reference_exact_channel_score(
            corpus,
            doc_index,
            corpus.exact_metadata_postings,
            exact_query_tokens,
            REFERENCE_EXACT_METADATA_SCORE_WEIGHT,
        )
        if text_score <= 0:
            continue
        page_record = document.page
        score = text_score * analyzer_role_multiplier(page_record, corpus.analyzer_profile)
        if min_score is not None and score < min_score:
            continue
        results.append(
            to_result(
                page_record,
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


def reference_is_english_profile(analyzer_profile: AnalyzerProfile) -> bool:
    return analyzer_profile in REFERENCE_ENGLISH_ANALYZER_PROFILES


def reference_exact_compound_tokens(
    text: str,
    analyzer_profile: AnalyzerProfile,
) -> list[str]:
    if not reference_is_english_profile(analyzer_profile):
        return []
    tokens: list[str] = []
    for match in REFERENCE_EXACT_COMPOUND_TOKEN_RE.finditer(text):
        token = match.group(0).casefold()
        if token not in tokens:
            tokens.append(token)
    return tokens


def reference_single_exact_compound_query_token(
    query: str,
    analyzer_profile: AnalyzerProfile,
) -> str:
    tokens = reference_exact_compound_tokens(query, analyzer_profile)
    if len(tokens) != 1:
        return ""
    remainder = REFERENCE_EXACT_COMPOUND_TOKEN_RE.sub("", query, count=1).strip()
    if remainder.strip(REFERENCE_SINGLE_COMPOUND_REMAINDER_CHARS).strip():
        return ""
    return tokens[0]


def reference_required_exact_doc_indexes(
    corpus: SearchCorpus,
    exact_query_token: str,
) -> set[int] | None:
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


def reference_exact_channel_score(
    corpus: SearchCorpus,
    doc_index: int,
    postings: Mapping[str, tuple[tuple[int, int], ...]],
    query_tokens: Sequence[str],
    weight: float,
) -> float:
    score = 0.0
    for token in query_tokens:
        entries = postings.get(token, ())
        if not entries:
            continue
        frequency = next(
            (
                entry_frequency
                for entry_doc_index, entry_frequency in entries
                if entry_doc_index == doc_index
            ),
            0,
        )
        if not frequency:
            continue
        idf = math.log(1 + (corpus.total - len(entries) + 0.5) / (len(entries) + 0.5))
        score += idf * (1.0 + math.log1p(frequency)) * weight
    return score


def serialize_results(results: list[SearchResult]) -> list[dict[str, Any]]:
    return [item.model_dump(mode="json") for item in results]


def result_ids(results: list[SearchResult]) -> list[str]:
    return [item.page_id for item in results]


def postings_page_ids(
    corpus: SearchCorpus,
    postings: Mapping[str, tuple[tuple[int, int], ...]],
    token: str,
) -> list[str]:
    return [
        corpus.documents[doc_index].page.id for doc_index, _frequency in postings.get(token, ())
    ]


def representative_pages() -> list[WikiPage]:
    return [
        page(
            "hot",
            "hot.md",
            "zztieonly shared alpha release.v1-beta hot context.",
            role="hot",
            summary="hot alpha summary",
            source_refs=["SRC-HOT"],
            tags=["ops"],
        ),
        page(
            "index",
            "index.md",
            "zztieonly shared alpha beta index context.",
            role="index",
            summary="index alpha summary",
        ),
        page(
            "a-alpha",
            "a-alpha.md",
            "alpha shared punct release.v1-beta source reference repeated repeated.",
        ),
        page(
            "b-beta",
            "b-beta.md",
            "alpha shared punct release.v1-beta source reference.",
        ),
        page("m-alpha", "m-alpha.md", "zztieonly managedtie commonfill."),
        page("m-beta", "m-beta.md", "zztieonly managedtie commonfill."),
        page("literal", "literal.md", "The Exact Phrase appears here for literal mode."),
        page(
            "draft",
            "draft.md",
            "draftonly shared alpha hidden.",
            review_state="draft",
        ),
    ]


def english_representative_pages() -> list[WikiPage]:
    return [
        page(
            "hot-role",
            "roles/hot.md",
            "roleboost parity evidence marker.",
            role="hot",
            summary="roleboost parity summary",
        ),
        page(
            "topic-role",
            "roles/topic.md",
            "roleboost parity evidence marker.",
            summary="roleboost parity summary",
        ),
        page("order-alpha", "order/a.md", "pathorder equality token."),
        page("order-beta", "order/b.md", "pathorder equality token."),
        page("stemmed", "clinical/stemmed.md", "Run treatment evidence."),
        page(
            "compound-authored",
            "compound/authored.md",
            "Exact authored identifier is available.",
            summary="Calls http_response exactly.",
        ),
        page(
            "compound-split-only",
            "compound/split-only.md",
            "The http response terms appear only as split English components.",
        ),
        page(
            "path-metadata",
            "metadata/release.v1-beta.md",
            "Plain unrelated metadata carrier.",
        ),
        page(
            "path-split-only",
            "metadata/split-release.md",
            "Release v1 beta md appears only as split English components.",
        ),
        page(
            "source-ref-only",
            "metadata/source-ref-only.md",
            "Plain unrelated metadata carrier.",
            source_refs=["SRC-CARDIOLOGY-STUDIES"],
        ),
        page(
            "natural-source",
            "natural/source.md",
            "Cardiology studies appear in authored body evidence.",
        ),
    ]


def page(
    page_id: str,
    path: str,
    text: str,
    *,
    role: Literal["hot", "index", "overview", "topic"] = "topic",
    summary: str = "",
    review_state: Literal[
        "approved",
        "reviewed",
        "verified",
        "draft",
        "proposed",
        "needs_review",
        "unknown",
    ] = "approved",
    source_refs: list[str] | None = None,
    tags: list[str] | None = None,
) -> WikiPage:
    return WikiPage(
        id=page_id,
        title=page_id.replace("-", " ").title(),
        path=path,
        role=role,
        text=text,
        summary=summary,
        review_state=review_state,
        source_refs=source_refs or [],
        tags=tags or [],
    )


def write_markdown(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content.strip() + "\n", encoding="utf-8")


def search_result(
    page_id: str, path: str, role: Literal["hot", "index", "overview", "topic"]
) -> SearchResult:
    return SearchResult(
        page_id=page_id,
        title=page_id,
        path=path,
        score=1.0,
        snippet="",
        role=role,
    )

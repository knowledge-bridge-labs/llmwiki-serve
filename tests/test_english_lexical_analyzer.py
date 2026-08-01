from __future__ import annotations

import time
from pathlib import Path
from typing import Literal

import pytest

from llmwiki_serve.models import WikiPage
from llmwiki_serve.search import (
    DEFAULT_ANALYZER_PROFILE,
    build_search_corpus,
    exact_compound_tokens,
    search_corpus,
    single_exact_compound_query_token,
    tokenize,
)
from llmwiki_serve.service import LlmWikiService


def test_english_profile_splits_punctuation_possessives_stopwords_and_stems() -> None:
    tokens = tokenize("Patient's studies of the treatments.", analyzer_profile="english")

    assert tokens == ["patient", "studi", "treatment"]
    assert "patient's" not in tokens
    assert "studies" not in tokens
    assert "of" not in tokens
    assert "the" not in tokens


def test_default_analyzer_profile_is_legacy() -> None:
    tokens = tokenize("Patient's studies of the treatments.")

    assert DEFAULT_ANALYZER_PROFILE == "legacy"
    assert tokens == ["patient", "s", "studies", "of", "the", "treatments."]


def test_stopword_only_english_query_returns_no_match_without_overview(tmp_path: Path) -> None:
    root = tmp_path / "wiki"
    write_markdown(
        root / "index.md",
        """
---
review_state: approved
---
# Index

visible overview page.
""",
    )
    service = LlmWikiService(root, analyzer_profile="english")

    assert service.search("the and of", limit=3) == []
    assert service.search("   ", limit=3)[0]["route"] == "overview"


def test_hangul_numeric_mixed_terms_keep_legacy_compatibility() -> None:
    tokens = tokenize(
        "release.v1-beta http_response 3차 계약서 codex한국", analyzer_profile="english"
    )

    assert "release.v1-beta" not in tokens
    assert "releas" in tokens
    assert "v1" in tokens
    assert "beta" in tokens
    assert "http" in tokens
    assert "respons" in tokens
    assert "3차" in tokens
    assert "3" in tokens
    assert "차" in tokens
    assert "계약서" in tokens
    assert "계약" in tokens
    assert "약서" in tokens
    assert "codex한국" in tokens
    assert "codex" in tokens
    assert "한국" in tokens


def test_single_compound_query_requires_authored_exact_match() -> None:
    corpus = build_search_corpus(
        [
            page(
                "split-only",
                "split-only.md",
                "Release notes mention v1 beta readiness but not the exact version token.",
            ),
            page(
                "authored-exact",
                "authored-exact.md",
                "release.v1-beta is the shipped compatibility target.",
            ),
        ],
        analyzer_profile="english",
    )

    assert [item.page_id for item in search_corpus(corpus, "release.v1-beta", limit=5)] == [
        "authored-exact"
    ]

    no_exact = build_search_corpus(
        [
            page(
                "split-only",
                "split-only.md",
                "Release notes mention v1 beta readiness but not the exact version token.",
            )
        ],
        analyzer_profile="english",
    )

    assert search_corpus(no_exact, "release.v1-beta", limit=5) == []


def test_exact_compound_channel_covers_identifiers_versions_and_trailing_punctuation() -> None:
    corpus = build_search_corpus(
        [
            page("identifier", "identifier.md", "http_response confirms exact identifier lookup."),
            page("version", "version.md", "The stable release is v1.2.3."),
            page("split", "split.md", "http response and v1 2 3 only appear as split words."),
        ],
        analyzer_profile="english",
    )

    assert exact_compound_tokens("release.v1-beta.", analyzer_profile="english") == [
        "release.v1-beta"
    ]
    assert exact_compound_tokens("stable.", analyzer_profile="english") == []
    assert [item.page_id for item in search_corpus(corpus, "http_response", limit=5)] == [
        "identifier"
    ]
    assert [item.page_id for item in search_corpus(corpus, "v1.2.3.", limit=5)] == ["version"]


def test_exact_compound_tokens_keep_authored_boundary_semantics() -> None:
    tokens = exact_compound_tokens(
        "release.v1-beta HTTP_RESPONSE v1.2.3. x.y한국 x_y's foo..bar a.b한c d.e",
        analyzer_profile="english",
    )

    assert tokens == [
        "release.v1-beta",
        "http_response",
        "v1.2.3",
        "x.y한국",
        "x_y",
        "d.e",
    ]
    assert exact_compound_tokens("release.v1-beta", analyzer_profile="legacy") == []


def test_single_compound_query_rejects_duplicate_or_nonpunctuation_remainder() -> None:
    assert single_exact_compound_query_token("(release.v1-beta).", "english") == "release.v1-beta"
    assert single_exact_compound_query_token("release.v1-beta release.v1-beta", "english") == ""
    assert single_exact_compound_query_token("release.v1-beta notes", "english") == ""


def test_exact_compound_scanner_handles_long_adversarial_input_with_bounded_output() -> None:
    adversarial = "a.b" + ("한" * 50_000) + "c " + ("x.y- " * 2_000)

    started = time.perf_counter()
    tokens = exact_compound_tokens(adversarial, analyzer_profile="english")
    elapsed_seconds = time.perf_counter() - started

    assert tokens == ["x.y"]
    assert elapsed_seconds < 3.0


def test_source_refs_are_exact_metadata_not_stemmed_english_content() -> None:
    corpus = build_search_corpus(
        [
            page(
                "source-ref-only",
                "notes/source-ref-only.md",
                "Plain unrelated body.",
                source_refs=["CARDIOLOGY-STUDIES"],
            ),
            page("natural", "natural.md", "Cardiology studies are described in the body."),
        ],
        analyzer_profile="english",
    )

    assert "cardiolog" in corpus.postings
    assert "studi" in corpus.postings
    assert corpus.doc_freq["cardiolog"] == 1
    assert corpus.doc_freq["studi"] == 1
    assert "cardiology-studies" in corpus.exact_metadata_postings
    assert [item.page_id for item in search_corpus(corpus, "CARDIOLOGY-STUDIES", limit=5)] == [
        "source-ref-only"
    ]


def test_path_metadata_matches_only_exact_original_tokens() -> None:
    corpus = build_search_corpus(
        [page("metadata", "guides/release.v1-beta.md", "Plain unrelated body.")],
        analyzer_profile="english",
    )

    assert search_corpus(corpus, "release beta", limit=5) == []
    assert [item.page_id for item in search_corpus(corpus, "release.v1-beta.md", limit=5)] == [
        "metadata"
    ]


def test_literal_mode_is_unchanged_by_analyzer_profile(tmp_path: Path) -> None:
    root = tmp_path / "wiki"
    write_markdown(
        root / "index.md",
        """
---
review_state: approved
---
# Index

The Exact Phrase appears here.
""",
    )

    legacy = LlmWikiService(root, analyzer_profile="legacy")
    english = LlmWikiService(root, analyzer_profile="english")

    assert english.search("Exact Phrase", mode="literal", limit=3) == legacy.search(
        "Exact Phrase",
        mode="literal",
        limit=3,
    )


def test_service_default_uses_legacy_and_explicit_english_opts_in(tmp_path: Path) -> None:
    root = tmp_path / "wiki"
    write_markdown(
        root / "index.md",
        """
---
review_state: approved
---
# Index

release.v1-beta compatibility.
""",
    )

    default_service = LlmWikiService(root)
    english_service = LlmWikiService(root, analyzer_profile="english")

    default_views = default_service._index_views(default_service.index())
    english_views = english_service._index_views(english_service.index())
    default_corpus = default_views.search_corpus(include_drafts=False)
    english_corpus = english_views.search_corpus(include_drafts=False)

    assert default_service.analyzer_profile == "legacy"
    assert default_views.analyzer_profile == "legacy"
    assert english_views.analyzer_profile == "english"
    assert default_corpus.analyzer_profile == "legacy"
    assert english_corpus.analyzer_profile == "english"
    assert default_corpus is default_views.search_corpus(include_drafts=False)
    assert english_corpus is english_views.search_corpus(include_drafts=False)
    assert "release.v1-beta" in default_corpus.postings
    assert "release.v1-beta" not in english_corpus.postings
    assert "release.v1-beta" in english_corpus.exact_compound_postings


def test_explicit_legacy_profile_keeps_source_refs_in_lexical_content() -> None:
    corpus = build_search_corpus(
        [
            page(
                "legacy-ref",
                "legacy-ref.md",
                "Plain unrelated body.",
                source_refs=["CARDIOLOGY-STUDIES"],
            )
        ],
        analyzer_profile="legacy",
    )

    assert "cardiology-studies" in corpus.postings
    assert not corpus.exact_compound_postings
    assert not corpus.exact_metadata_postings
    assert [item.page_id for item in search_corpus(corpus, "CARDIOLOGY-STUDIES", limit=5)] == [
        "legacy-ref"
    ]


def test_invalid_analyzer_profile_is_rejected(tmp_path: Path) -> None:
    for value in ("english_additive", "english_flatlike", "unknown"):
        with pytest.raises(ValueError, match="unknown analyzer profile"):
            LlmWikiService(tmp_path, analyzer_profile=value)  # type: ignore[arg-type]


def page(
    page_id: str,
    path: str,
    text: str,
    *,
    role: Literal["hot", "index", "overview", "topic"] = "topic",
    summary: str = "",
    source_refs: list[str] | None = None,
) -> WikiPage:
    return WikiPage(
        id=page_id,
        title="Shared Title",
        path=path,
        role=role,
        text=text,
        summary=summary,
        review_state="approved",
        source_refs=source_refs or [],
    )


def write_markdown(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content.strip() + "\n", encoding="utf-8")

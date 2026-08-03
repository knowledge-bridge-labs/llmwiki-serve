from __future__ import annotations

import re
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Sequence
from typing import Any, Literal

from .errors import LlmWikiUserError
from .models import (
    FolderCard,
    PageCard,
    RetrievalGuidance,
    RetrievalGuidanceFallbackMode,
    RetrievalGuidanceOrientationSource,
    SearchMode,
    WikiIndex,
    WikiPage,
)

AGENT_GUIDED_LEXICAL_CAPABILITY = "llmwiki_agent_guided_lexical_v1"
RETRIEVAL_GUIDANCE_SCHEMA_VERSION: Literal["llmwiki.retrieval_guidance.v1"] = (
    "llmwiki.retrieval_guidance.v1"
)
MAX_QUERY_VARIANTS: Literal[2] = 2
MAX_LEXICAL_CHANNELS = 1 + MAX_QUERY_VARIANTS
GUIDANCE_CHARACTER_BUDGET = 4000
AUTHORED_ORIENTATION_ROLES = {"hot", "index", "overview"}
FALLBACK_MODE_ORDER: tuple[RetrievalGuidanceFallbackMode, ...] = (
    "literal",
    "hybrid",
    "vector",
)

_TERM_RE = re.compile(r"(?u)[^\W\d_][\w-]{1,119}")
_EXACT_IDENTIFIER_RE = re.compile(
    r"(?u)(?:"
    r"[A-Za-z_][\w]*(?:[./:@#-][\w][\w-]*)+"
    r"|[A-Za-z_][\w]*[A-Z][\w]*"
    r"|[A-Z]{2,}-\d+"
    r"|v?\d+(?:\.\d+){1,}(?:[-+][\w.-]+)?"
    r"|[\w가-힣]+(?:[-_/.:][\w가-힣]+)+"
    r")"
)
_MARKDOWN_LINK_RE = re.compile(r"!?\[([^\]]+)\]\([^)]+\)")
_WIKILINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]")
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_SENSITIVE_TEXT_RE = re.compile(
    r"(?ix)"
    r"(?:\b(?:authorization|password|passwd|secret|api[_-]?key|access[_-]?token|"
    r"bearer|credential|cookie|session[_-]?id|client[_-]?secret|private[_-]?key)s?\b\s*[:=])"
    r"|(?:\bBearer\s+[A-Za-z0-9._-]{12,})"
    r"|(?:\b(?:sk|sk-proj|ghp|gho|github_pat|xox[baprs])[-_][A-Za-z0-9_-]{12,})"
    r"|(?:\bAKIA[0-9A-Z]{16}\b)"
    r"|(?:\b(?:redis|file)://)"
    r"|(?:https?://[^\s/]*@)"
    r"|(?:https?://(?:localhost|127\.0\.0\.1|0\.0\.0\.0|10\.|192\.168\.|"
    r"169\.254\.|100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.|"
    r"172\.(?:1[6-9]|2\d|3[01])\.|\[::1\]|\[fc[0-9a-f:]+\]|\[fd[0-9a-f:]+\]))"
    r"|(?:\b(?:localhost|(?:[a-z0-9-]+\.)+(?:local|internal|corp|lan)|"
    r"(?:internal|intranet|private|vpn)[.-][a-z0-9.-]+)\b)"
    r"|(?:[A-Za-z]:[\\/][^\s)]+)"
    r"|(?:/(?:Users|home|root|var|tmp)/[^\s)]+)"
)
_SENSITIVE_STANDALONE_TERMS = {
    "authorization",
    "bearer",
    "client_secret",
    "credential",
    "credentials",
    "cookie",
    "password",
    "passwd",
    "private",
    "private_key",
    "secret",
    "session",
    "session_id",
    "token",
}
_BOILERPLATE_PREFIXES = (
    "table of contents",
    "toc",
    "navigation",
    "breadcrumbs",
    "copyright",
    "license",
)
_COMMON_TERMS = {
    "about",
    "after",
    "also",
    "and",
    "are",
    "before",
    "between",
    "from",
    "for",
    "into",
    "markdown",
    "note",
    "notes",
    "page",
    "pages",
    "that",
    "the",
    "this",
    "wiki",
    "with",
}


def authored_orientation_pages(index: WikiIndex, *, include_drafts: bool) -> list[WikiPage]:
    pages = visible_index_pages(index, include_drafts=include_drafts)
    return [
        page
        for page in sorted(pages, key=lambda item: (orientation_role_rank(item.role), item.path))
        if is_authored_orientation_page(page)
    ]


def is_authored_orientation_page(page: WikiPage) -> bool:
    return page.role in AUTHORED_ORIENTATION_ROLES


def build_retrieval_guidance(
    index: WikiIndex,
    *,
    query: str,
    include_drafts: bool,
    fallback_modes: Sequence[RetrievalGuidanceFallbackMode],
) -> RetrievalGuidance:
    visible = visible_index_pages(index, include_drafts=include_drafts)
    authored = authored_orientation_pages(index, include_drafts=include_drafts)
    safe_fallbacks = available_fallback_modes(fallback_modes)

    if authored:
        guidance = guidance_from_pages(
            authored,
            query=query,
            orientation_source="authored",
            fallback_modes=safe_fallbacks,
            folder_source_pages=authored,
        )
        return trim_guidance_to_budget(guidance)

    if index.adapter == "generic-markdown" and visible:
        selected = projection_extractive_pages(visible, query=query)
        guidance = guidance_from_pages(
            selected,
            query=query,
            orientation_source="projection_extractive",
            fallback_modes=safe_fallbacks,
            folder_source_pages=visible,
        )
        if guidance.page_cards or guidance.folder_cards or guidance.suggested_terms:
            return trim_guidance_to_budget(guidance)

    return RetrievalGuidance(
        schema_version=RETRIEVAL_GUIDANCE_SCHEMA_VERSION,
        orientation_source="none",
        content_trust="untrusted_source_evidence",
        max_query_variants=MAX_QUERY_VARIANTS,
        character_budget=GUIDANCE_CHARACTER_BUDGET,
        folder_cards=[],
        page_cards=[],
        suggested_terms=[],
        exact_identifiers=[],
        fallback_modes=safe_fallbacks,
    )


def visible_index_pages(index: WikiIndex, *, include_drafts: bool) -> list[WikiPage]:
    if include_drafts:
        return list(index.pages)
    return [page for page in index.pages if page.approved_for_serving]


def projection_extractive_pages(pages: Sequence[WikiPage], *, query: str) -> list[WikiPage]:
    query_terms = set(terms_from_text(query))
    ranked = sorted(
        pages,
        key=lambda page: (
            -page_query_overlap(page, query_terms),
            orientation_role_rank(page.role),
            page.path,
        ),
    )
    return ranked[:24]


def page_query_overlap(page: WikiPage, query_terms: set[str]) -> int:
    if not query_terms:
        return 0
    page_terms = set(page_terms_for_guidance(page))
    return len(page_terms & query_terms)


def guidance_from_pages(
    pages: Sequence[WikiPage],
    *,
    query: str,
    orientation_source: RetrievalGuidanceOrientationSource,
    fallback_modes: Sequence[RetrievalGuidanceFallbackMode],
    folder_source_pages: Sequence[WikiPage],
) -> RetrievalGuidance:
    page_cards = [card for page in pages if (card := page_card(page)) is not None][:12]
    folder_cards = folder_cards_from_pages(folder_source_pages, query=query)[:8]
    suggested_terms = unique_preserving_order(
        [
            *terms_from_text(query),
            *[term for card in page_cards for term in card.terms],
            *[term for card in folder_cards for term in card.terms],
        ],
        max_items=16,
        max_chars=120,
    )
    exact_identifiers = unique_preserving_order(
        [
            *[identifier for card in page_cards for identifier in card.exact_identifiers],
            *[card.path for card in page_cards],
        ],
        max_items=16,
        max_chars=240,
    )
    return RetrievalGuidance(
        schema_version=RETRIEVAL_GUIDANCE_SCHEMA_VERSION,
        orientation_source=orientation_source,
        content_trust="untrusted_source_evidence",
        max_query_variants=MAX_QUERY_VARIANTS,
        character_budget=GUIDANCE_CHARACTER_BUDGET,
        folder_cards=folder_cards,
        page_cards=page_cards,
        suggested_terms=suggested_terms,
        exact_identifiers=exact_identifiers,
        fallback_modes=available_fallback_modes(fallback_modes),
    )


def page_card(page: WikiPage) -> PageCard | None:
    path = safe_projected_path(page.path)
    if path is None:
        return None
    excerpt = safe_excerpt(page)
    if not excerpt:
        return None
    page_id = clean_inline(page.id, max_chars=512)
    if not page_id or sensitive_text(page_id):
        return None
    title = clean_inline(page.title or page.id, max_chars=240)
    if not title or sensitive_text(title):
        return None
    return PageCard(
        page_id=page_id,
        title=title,
        path=path,
        headings=unique_preserving_order(page.headings, max_items=8, max_chars=160),
        terms=page_terms_for_guidance(page)[:12],
        exact_identifiers=page_exact_identifiers(page, excerpt)[:8],
        excerpt=excerpt,
    )


def folder_cards_from_pages(pages: Sequence[WikiPage], *, query: str) -> list[FolderCard]:
    grouped: dict[str, list[WikiPage]] = defaultdict(list)
    for page in pages:
        folder = parent_folder(page.path)
        if folder is not None:
            grouped[folder].append(page)

    query_terms = set(terms_from_text(query))
    cards: list[FolderCard] = []
    for folder, folder_pages in grouped.items():
        terms = folder_terms(folder_pages)[:8]
        cards.append(FolderCard(path=folder, page_count=len(folder_pages), terms=terms))
    return sorted(
        cards,
        key=lambda card: (
            -len(set(card.terms) & query_terms),
            -card.page_count,
            card.path,
        ),
    )


def folder_terms(pages: Sequence[WikiPage]) -> list[str]:
    counts: Counter[str] = Counter()
    first_seen: dict[str, str] = {}
    for page in pages:
        for term in page_terms_for_guidance(page):
            key = term.casefold()
            counts[key] += 1
            first_seen.setdefault(key, term)
    return [
        first_seen[key]
        for key, _count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    ]


def page_terms_for_guidance(page: WikiPage) -> list[str]:
    return unique_preserving_order(
        [
            *terms_from_text(page.title),
            *[term for heading in page.headings for term in terms_from_text(heading)],
            *[term for tag in page.tags for term in terms_from_text(tag)],
        ],
        max_items=12,
        max_chars=120,
    )


def page_exact_identifiers(page: WikiPage, excerpt: str) -> list[str]:
    candidates: list[str] = []
    path = safe_projected_path(page.path)
    if path:
        candidates.append(path)
    candidates.extend(page.source_refs)
    candidates.extend(page.tags)
    candidates.extend(exact_identifiers_from_text(" ".join([page.title, *page.headings, excerpt])))
    return unique_preserving_order(candidates, max_items=8, max_chars=240)


def terms_from_text(text: str) -> list[str]:
    if sensitive_text(text):
        return []
    terms: list[str] = []
    for match in _TERM_RE.finditer(text):
        term = clean_inline(match.group(0), max_chars=120)
        key = term.casefold()
        if len(term) < 2 or key in _COMMON_TERMS or sensitive_text(term):
            continue
        terms.append(term)
    return unique_preserving_order(terms, max_items=32, max_chars=120)


def exact_identifiers_from_text(text: str) -> list[str]:
    return unique_preserving_order(
        [
            match.group(0)
            for match in _EXACT_IDENTIFIER_RE.finditer(text)
            if not sensitive_text(match.group(0))
        ],
        max_items=32,
        max_chars=240,
    )


def safe_excerpt(page: WikiPage) -> str:
    candidates = [page.summary, *paragraphs(page.text)]
    for candidate in candidates:
        text = clean_markdown_inline(candidate, max_chars=240)
        if not text or boilerplate_text(text) or sensitive_text(text):
            continue
        return text
    return ""


def paragraphs(text: str) -> list[str]:
    return [part for part in re.split(r"\n\s*\n", text) if part.strip()]


def clean_markdown_inline(text: str, *, max_chars: int) -> str:
    text = _MARKDOWN_LINK_RE.sub(r"\1", text)
    text = _WIKILINK_RE.sub(lambda match: match.group(2) or match.group(1), text)
    text = _HTML_TAG_RE.sub(" ", text)
    lines = []
    in_fence = False
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith("```") or line.startswith("~~~"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        line = line.lstrip("#").strip()
        line = line.lstrip("-*0123456789. ").strip()
        if line and not line.startswith("|"):
            lines.append(line)
    return clean_inline(" ".join(lines), max_chars=max_chars)


def clean_inline(text: str, *, max_chars: int) -> str:
    cleaned = " ".join(str(text).replace("\x00", " ").split())
    if len(cleaned) <= max_chars:
        return cleaned
    if max_chars <= 3:
        return cleaned[:max_chars]
    return cleaned[: max_chars - 3].rstrip() + "..."


def boilerplate_text(text: str) -> bool:
    lowered = text.casefold()
    if len(text) < 16:
        return True
    return any(lowered.startswith(prefix) for prefix in _BOILERPLATE_PREFIXES)


def sensitive_text(text: str) -> bool:
    candidate = str(text).strip()
    if not candidate:
        return False
    if candidate.casefold().replace("-", "_") in _SENSITIVE_STANDALONE_TERMS:
        return True
    return _SENSITIVE_TEXT_RE.search(candidate) is not None


def safe_projected_path(path: str) -> str | None:
    text = path.strip().replace("\\", "/")
    if not text:
        return None
    if len(text) > 1024 or sensitive_text(text):
        return None
    if any(ord(char) < 32 for char in text):
        return None
    if text.startswith("/") or "://" in text:
        return None
    parts = text.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        return None
    if len(parts[0]) == 2 and parts[0][1] == ":":
        return None
    for part in parts:
        stem = part.rsplit(".", 1)[0].casefold().replace("-", "_")
        if stem in _SENSITIVE_STANDALONE_TERMS:
            return None
    return text


def parent_folder(path: str) -> str | None:
    safe = safe_projected_path(path)
    if safe is None:
        return None
    if "/" not in safe:
        return "."
    folder = safe.rsplit("/", 1)[0]
    return folder or "."


def unique_preserving_order(
    values: Sequence[str],
    *,
    max_items: int,
    max_chars: int,
) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = clean_inline(value, max_chars=max_chars)
        if not text or sensitive_text(text):
            continue
        key = unicodedata.normalize("NFC", text).casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(text)
        if len(result) >= max_items:
            break
    return result


def orientation_role_rank(role: str) -> int:
    return {"hot": 0, "index": 1, "overview": 2}.get(role, 3)


def available_fallback_modes(
    fallback_modes: Sequence[RetrievalGuidanceFallbackMode],
) -> list[RetrievalGuidanceFallbackMode]:
    available = set(fallback_modes)
    return [mode for mode in FALLBACK_MODE_ORDER if mode in available]


def trim_guidance_to_budget(guidance: RetrievalGuidance) -> RetrievalGuidance:
    current = guidance
    while len(current.model_dump_json()) > current.character_budget:
        if current.page_cards:
            current = current.model_copy(update={"page_cards": current.page_cards[:-1]})
            continue
        if current.folder_cards:
            current = current.model_copy(update={"folder_cards": current.folder_cards[:-1]})
            continue
        if current.suggested_terms:
            current = current.model_copy(update={"suggested_terms": current.suggested_terms[:-1]})
            continue
        if current.exact_identifiers:
            current = current.model_copy(
                update={"exact_identifiers": current.exact_identifiers[:-1]}
            )
            continue
        break
    return current


def validate_public_query_variants(value: Any) -> list[str]:
    if value is None:
        raise LlmWikiUserError(
            "query_variants must be an array of strings; omit it instead of null"
        )
    return validate_query_variants(value)


def validate_query_variants(value: Sequence[Any] | None) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise LlmWikiUserError("query_variants must be an array of up to 2 strings")
    if len(value) > MAX_QUERY_VARIANTS:
        raise LlmWikiUserError("query_variants accepts at most 2 supplied variants")
    variants: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise LlmWikiUserError("query_variants entries must be strings")
        text = item.strip()
        if not text:
            raise LlmWikiUserError("query_variants entries must be non-empty strings")
        variants.append(text)
    return variants


def effective_lexical_query_channels(
    query: str,
    query_variants: Sequence[Any] | None,
    *,
    mode: SearchMode,
) -> list[str]:
    variants = validate_query_variants(query_variants)
    if not variants:
        return [query]
    if mode != "lexical":
        raise LlmWikiUserError("query_variants is supported only with mode=lexical")
    primary = query.strip()
    if not primary:
        raise LlmWikiUserError("query is required when query_variants are supplied")
    channels: list[str] = []
    seen: set[str] = set()
    for candidate in [primary, *variants]:
        key = unicodedata.normalize("NFC", candidate).casefold()
        if key in seen:
            continue
        seen.add(key)
        channels.append(candidate)
        if len(channels) >= MAX_LEXICAL_CHANNELS:
            break
    return channels

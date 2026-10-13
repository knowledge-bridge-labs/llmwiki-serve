"""Context-efficient page read shaping.

Large wiki pages are expensive for chat agents that resend tool results on every
turn. This module keeps ``llmwiki_read`` backwards compatible (no shaping unless
asked) while offering:

* focused reads: ``query`` and/or ``sections`` return title, summary, a compact
  outline (heading hierarchy plus per-section length) and only the selected
  section bodies within a character budget;
* plain budgets: ``max_chars`` clips the full text at a section/line boundary and
  reports the sections that did not fit;
* ``include_links=False`` drops the (often large) outbound link list.

Relevance scoring is intentionally simple and deterministic: Unicode word tokens
with light Korean particle stripping, Hangul character bigrams for partial
matches, and a heading weight. No model or external dependency is involved.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

READ_MAX_CHARS_MIN = 200
READ_MAX_CHARS_MAX = 1_000_000
FOCUSED_READ_DEFAULT_MAX_CHARS = 8_000
FOCUSED_QUERY_MAX_SECTIONS = 6
FOCUSED_QUERY_RELATIVE_THRESHOLD = 0.35
FOCUSED_QUERY_MIN_SCORE = 1.0
MIN_UNIT_WEIGHT = 0.1
HEADING_WEIGHT = 3.0
BODY_REPEAT_BONUS = 0.25
BODY_REPEAT_CAP = 4
PREAMBLE_INDEX = "0"
PREAMBLE_HEADING = "(preamble)"
LINE_BREAK_CLIP_RATIO = 0.6
TRUNCATION_FIELDS = ("truncated", "total_chars", "returned_chars", "remaining_sections")

_HEADING_RE = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$")
_FENCE_RE = re.compile(r"^[ \t]*(```|~~~)")
_TOKEN_RE = re.compile(r"\w+", re.UNICODE)
_HANGUL_RE = re.compile(r"[가-힣]")
_SECTION_NUMBER_RE = re.compile(r"\d+(?:\.\d+)*")
# Longest first so "에서는" wins over "는". Only stripped when >= 2 chars remain.
KOREAN_PARTICLES = tuple(
    sorted(
        (
            "에서는",
            "으로는",
            "에서",
            "으로",
            "에게",
            "까지",
            "부터",
            "처럼",
            "보다",
            "이란",
            "라는",
            "이다",
            "입니다",
            "은",
            "는",
            "이",
            "가",
            "을",
            "를",
            "의",
            "에",
            "로",
            "와",
            "과",
            "도",
            "만",
            "란",
        ),
        key=len,
        reverse=True,
    )
)


@dataclass(frozen=True)
class MarkdownSection:
    index: str
    level: int
    heading: str
    path: tuple[str, ...]
    text: str

    def is_descendant_of(self, other: MarkdownSection) -> bool:
        return other.index != PREAMBLE_INDEX and self.index.startswith(other.index + ".")

    def outline_label(self) -> str:
        return f"{self.index} {self.heading} ({len(self.text)})"


def is_focused_read(query: str | None, sections: Sequence[str] | None) -> bool:
    return bool((query or "").strip()) or bool(sections)


def split_markdown_sections(text: str) -> list[MarkdownSection]:
    """Split Markdown into heading-delimited sections with hierarchical numbers."""
    heads: list[tuple[int, int, str]] = []
    offset = 0
    in_fence = False
    for line in text.splitlines(keepends=True):
        if _FENCE_RE.match(line):
            in_fence = not in_fence
        elif not in_fence:
            match = _HEADING_RE.match(line.rstrip("\r\n"))
            if match:
                heads.append((offset, len(match.group(1)), match.group(2).strip()))
        offset += len(line)

    sections: list[MarkdownSection] = []
    first_start = heads[0][0] if heads else len(text)
    if text[:first_start].strip():
        sections.append(
            MarkdownSection(PREAMBLE_INDEX, 0, PREAMBLE_HEADING, (), text[:first_start])
        )
    stack: list[tuple[int, str, str]] = []
    child_counts: dict[str, int] = {}
    for position, (start, level, heading) in enumerate(heads):
        end = heads[position + 1][0] if position + 1 < len(heads) else len(text)
        while stack and stack[-1][0] >= level:
            stack.pop()
        parent_index = stack[-1][1] if stack else ""
        child_counts[parent_index] = child_counts.get(parent_index, 0) + 1
        index = (
            f"{parent_index}.{child_counts[parent_index]}"
            if parent_index
            else str(child_counts[parent_index])
        )
        path = (*(item[2] for item in stack), heading)
        stack.append((level, index, heading))
        sections.append(MarkdownSection(index, level, heading, path, text[start:end]))
    return sections


def normalize_text(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold()


def strip_korean_particle(token: str) -> str:
    if not _HANGUL_RE.search(token):
        return token
    for particle in KOREAN_PARTICLES:
        if token.endswith(particle) and len(token) - len(particle) >= 2:
            return token[: -len(particle)]
    return token


def query_terms(query: str) -> list[str]:
    terms: list[str] = []
    for raw in _TOKEN_RE.findall(normalize_text(query)):
        term = strip_korean_particle(raw)
        if len(term) >= 2 and term not in terms:
            terms.append(term)
    return terms


def term_bigrams(terms: Sequence[str]) -> set[str]:
    grams: set[str] = set()
    for term in terms:
        if _HANGUL_RE.search(term) and len(term) > 2:
            grams.update(term[i : i + 2] for i in range(len(term) - 1))
        else:
            grams.add(term)
    return grams


def text_bigrams(text: str) -> set[str]:
    return term_bigrams(_TOKEN_RE.findall(text))


def unit_weights(
    sections: Sequence[MarkdownSection], terms: Sequence[str], bigrams: set[str]
) -> dict[str, float]:
    """Inverse section frequency within the page so ubiquitous words barely count."""
    total = len(sections)
    texts = [normalize_text(" ".join(s.path) + "\n" + s.text) for s in sections]
    grams = [text_bigrams(text) for text in texts]
    weights: dict[str, float] = {}
    for term in terms:
        df = sum(1 for text in texts if term in text)
        weights[term] = max(math.log((total + 1) / (df + 1)), MIN_UNIT_WEIGHT)
    for gram in bigrams:
        df = sum(1 for gram_set in grams if gram in gram_set)
        weights.setdefault(gram, max(math.log((total + 1) / (df + 1)), MIN_UNIT_WEIGHT))
    return weights


def score_section(
    section: MarkdownSection,
    terms: Sequence[str],
    bigrams: set[str],
    weights: dict[str, float] | None = None,
) -> float:
    weights = weights or {}
    heading = normalize_text(" ".join(section.path))
    body = normalize_text(section.text)
    score = 0.0
    for term in terms:
        weight = weights.get(term, 1.0)
        if term in heading:
            score += HEADING_WEIGHT * weight
        count = body.count(term)
        if count:
            score += weight * (1.0 + BODY_REPEAT_BONUS * min(count - 1, BODY_REPEAT_CAP))
    if bigrams:
        heading_grams = text_bigrams(heading)
        body_grams = text_bigrams(body)
        for gram in bigrams:
            weight = weights.get(gram, 1.0) / len(bigrams)
            score += weight * (
                (HEADING_WEIGHT / 2) * (gram in heading_grams) + (gram in body_grams)
            )
    return round(score, 3)


def match_requested_sections(
    sections: Sequence[MarkdownSection], requested: Sequence[str]
) -> tuple[list[MarkdownSection], list[str]]:
    """Resolve section numbers or heading names (exact, then substring) incl. descendants."""
    chosen: list[MarkdownSection] = []
    unmatched: list[str] = []
    for spec in requested:
        wanted = normalize_text(spec.strip().lstrip("#").strip())
        if not wanted:
            continue
        if _SECTION_NUMBER_RE.fullmatch(wanted):
            roots = [section for section in sections if section.index == wanted]
        else:
            roots = [s for s in sections if normalize_text(s.heading) == wanted] or [
                s for s in sections if wanted in normalize_text(s.heading)
            ]
        if not roots:
            unmatched.append(spec)
            continue
        for root in roots:
            for section in sections:
                if (section is root or section.is_descendant_of(root)) and section not in chosen:
                    chosen.append(section)
    return chosen, unmatched


def clip_text(text: str, budget: int) -> str:
    if len(text) <= budget:
        return text
    clipped = text[:budget]
    line_break = clipped.rfind("\n")
    if line_break >= budget * LINE_BREAK_CLIP_RATIO:
        clipped = clipped[: line_break + 1]
    return clipped


def fill_budget(
    ranked: Sequence[MarkdownSection], budget: int
) -> tuple[dict[str, str], list[MarkdownSection]]:
    """Pick sections in rank order until the budget is used; clip the first if needed."""
    included: dict[str, str] = {}
    remaining: list[MarkdownSection] = []
    used = 0
    for section in ranked:
        if used + len(section.text) <= budget:
            included[section.index] = section.text
            used += len(section.text)
        elif not included:
            included[section.index] = clip_text(section.text, budget)
            used += len(included[section.index])
        else:
            remaining.append(section)
    return included, remaining


def focused_read_payload(
    page: dict[str, Any],
    *,
    query: str | None,
    sections: Sequence[str] | None,
    max_chars: int | None,
    include_links: bool | None,
) -> dict[str, Any]:
    text = str(page.get("text") or "")
    budget = max_chars if max_chars is not None else FOCUSED_READ_DEFAULT_MAX_CHARS
    all_sections = split_markdown_sections(text)
    chosen: list[MarkdownSection] = []
    unmatched: list[str] = []
    scores: dict[str, float] = {}
    if sections:
        chosen, unmatched = match_requested_sections(all_sections, sections)
    query_text = (query or "").strip()
    if query_text:
        terms = query_terms(query_text)
        bigrams = term_bigrams(terms)
        weights = unit_weights(all_sections, terms, bigrams)
        scores = {s.index: score_section(s, terms, bigrams, weights) for s in all_sections}
        top = max(scores.values(), default=0.0)
        threshold = max(top * FOCUSED_QUERY_RELATIVE_THRESHOLD, FOCUSED_QUERY_MIN_SCORE)
        candidates = sorted(
            (s for s in all_sections if scores[s.index] >= threshold and s not in chosen),
            key=lambda s: -scores[s.index],
        )
        chosen.extend(candidates[:FOCUSED_QUERY_MAX_SECTIONS])
    matched = bool(chosen)
    if not chosen:
        chosen = list(all_sections[:1])
    included, remaining = fill_budget(chosen, budget)
    clipped = any(len(included[s.index]) < len(s.text) for s in chosen if s.index in included)
    ordered = [s for s in all_sections if s.index in included]
    body = "".join(included[s.index] for s in ordered)

    payload: dict[str, Any] = {
        key: page[key]
        for key in ("id", "title", "path", "role", "summary", "review_state", "status")
        if key in page
    }
    payload["updated_at"] = page.get("updated_at", "")
    payload["tags"] = page.get("tags", [])
    payload["read_mode"] = "focused"
    if query_text:
        payload["query"] = query_text
    if sections:
        payload["requested_sections"] = list(sections)
    payload["matched"] = matched
    payload["outline"] = [s.outline_label() for s in all_sections]
    payload["selected_sections"] = [
        {"index": s.index, "heading": s.heading, "chars": len(included[s.index])}
        | ({"score": scores[s.index]} if s.index in scores else {})
        for s in ordered
    ]
    payload["text"] = body
    payload["total_chars"] = len(text)
    payload["returned_chars"] = len(body)
    payload["max_chars"] = budget
    payload["truncated"] = bool(remaining) or clipped
    payload["remaining_sections"] = [s.outline_label() for s in remaining]
    if unmatched:
        payload["unmatched_sections"] = unmatched
    if include_links is True:
        payload["links"] = page.get("links", [])
    return payload


def plain_read_payload(
    page: dict[str, Any],
    *,
    max_chars: int | None,
    include_links: bool | None,
) -> dict[str, Any]:
    payload = dict(page)
    if include_links is False:
        payload.pop("links", None)
    text = str(payload.get("text") or "")
    if max_chars is None or len(text) <= max_chars:
        return payload
    clipped = clip_text(text, max_chars)
    payload["text"] = clipped
    payload["truncated"] = True
    payload["total_chars"] = len(text)
    payload["returned_chars"] = len(clipped)
    remaining: list[str] = []
    end = len(text)
    for section in reversed(split_markdown_sections(text)):
        if end > len(clipped):
            remaining.append(section.outline_label())
        end -= len(section.text)
    payload["remaining_sections"] = list(reversed(remaining))
    return payload

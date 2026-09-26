from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Literal, TypeAlias

from .io_logging import local_root_strings, redact_string
from .models import (
    ContextPack,
    ContextSearchResult,
    RetrievalActionDecision,
    RetrievalActionGuidance,
)

DEFAULT_QUERY_ACTION_JUDGE_ENDPOINT = "https://api.typesafe.ai/v1/systemone"
DEFAULT_QUERY_ACTION_JUDGE_MODEL = "jev-latest"
DEFAULT_QUERY_ACTION_JUDGE_TIMEOUT_MS = 3_000
MAX_PROVIDER_RESPONSE_BYTES = 1_000_000
MAX_ACTION_TARGETS = 3
QUERY_ACTION_JUDGE_ENV = "LLMWIKI_QUERY_ACTION_JUDGE"
QUERY_ACTION_JUDGE_API_KEY_ENV = "LLMWIKI_QUERY_ACTION_JUDGE_API_KEY"
QUERY_ACTION_JUDGE_ENDPOINT_ENV = "LLMWIKI_QUERY_ACTION_JUDGE_ENDPOINT"
QUERY_ACTION_JUDGE_BASE_URL_ENV = "LLMWIKI_QUERY_ACTION_JUDGE_BASE_URL"
QUERY_ACTION_JUDGE_MODEL_ENV = "LLMWIKI_QUERY_ACTION_JUDGE_MODEL"
QUERY_ACTION_JUDGE_TIMEOUT_MS_ENV = "LLMWIKI_QUERY_ACTION_JUDGE_TIMEOUT_MS"
LEGACY_JEV_API_KEY_ENV = "JEV_API_KEY"
LEGACY_TYPESAFE_API_KEY_ENV = "TYPESAFE_API_KEY"
QueryActionJudgeMode: TypeAlias = Literal["off", "system-one"]

_OFF_VALUES = {"", "0", "false", "no", "off", "none", "disabled", "disable"}
_SYSTEM_ONE_VALUES = {"system-one", "system_one", "systemone", "jev", "typesafe"}
_TOKEN_RE = re.compile(r"[\w가-힣]+", re.UNICODE)
_EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)


@dataclass(frozen=True)
class QueryActionJudgeConfig:
    mode: QueryActionJudgeMode = "off"
    endpoint: str = DEFAULT_QUERY_ACTION_JUDGE_ENDPOINT
    model: str = DEFAULT_QUERY_ACTION_JUDGE_MODEL
    api_key: str | None = None
    timeout_ms: int = DEFAULT_QUERY_ACTION_JUDGE_TIMEOUT_MS

    @property
    def enabled(self) -> bool:
        return self.mode == "system-one"


class QueryActionJudgmentError(RuntimeError):
    pass


def query_action_judge_config_from_env() -> QueryActionJudgeConfig:
    return validate_query_action_judge_config(
        QueryActionJudgeConfig(
            mode=normalize_query_action_judge_mode(os.getenv(QUERY_ACTION_JUDGE_ENV)),
            endpoint=endpoint_from_env(),
            model=string_env(QUERY_ACTION_JUDGE_MODEL_ENV) or DEFAULT_QUERY_ACTION_JUDGE_MODEL,
            api_key=first_string_env(
                QUERY_ACTION_JUDGE_API_KEY_ENV,
                LEGACY_TYPESAFE_API_KEY_ENV,
                LEGACY_JEV_API_KEY_ENV,
            ),
            timeout_ms=timeout_ms_from_env(),
        )
    )


def validate_query_action_judge_config(
    value: QueryActionJudgeConfig | str | None,
) -> QueryActionJudgeConfig:
    if value is None:
        return QueryActionJudgeConfig()
    if isinstance(value, str):
        return validate_query_action_judge_config(
            QueryActionJudgeConfig(
                mode=normalize_query_action_judge_mode(value),
                endpoint=endpoint_from_env(),
                model=string_env(QUERY_ACTION_JUDGE_MODEL_ENV) or DEFAULT_QUERY_ACTION_JUDGE_MODEL,
                api_key=first_string_env(
                    QUERY_ACTION_JUDGE_API_KEY_ENV,
                    LEGACY_TYPESAFE_API_KEY_ENV,
                    LEGACY_JEV_API_KEY_ENV,
                ),
                timeout_ms=timeout_ms_from_env(),
            )
        )
    mode = normalize_query_action_judge_mode(value.mode)
    endpoint = value.endpoint.strip() or DEFAULT_QUERY_ACTION_JUDGE_ENDPOINT
    model = value.model.strip() or DEFAULT_QUERY_ACTION_JUDGE_MODEL
    if value.timeout_ms <= 0:
        raise ValueError("query_action_judge timeout_ms must be positive")
    return replace(value, mode=mode, endpoint=endpoint, model=model)


def normalize_query_action_judge_mode(value: str | None) -> QueryActionJudgeMode:
    normalized = (value or "").strip().lower()
    if normalized in _OFF_VALUES:
        return "off"
    if normalized in _SYSTEM_ONE_VALUES:
        return "system-one"
    raise ValueError(f"{QUERY_ACTION_JUDGE_ENV} must be 'off' or 'system-one'")


def endpoint_from_env() -> str:
    endpoint = string_env(QUERY_ACTION_JUDGE_ENDPOINT_ENV)
    if endpoint:
        return endpoint
    base_url = string_env(QUERY_ACTION_JUDGE_BASE_URL_ENV)
    if base_url:
        trimmed = base_url.rstrip("/")
        return trimmed if trimmed.endswith("/systemone") else f"{trimmed}/systemone"
    return DEFAULT_QUERY_ACTION_JUDGE_ENDPOINT


def timeout_ms_from_env() -> int:
    value = string_env(QUERY_ACTION_JUDGE_TIMEOUT_MS_ENV)
    if not value:
        return DEFAULT_QUERY_ACTION_JUDGE_TIMEOUT_MS
    try:
        timeout_ms = int(value)
    except ValueError as exc:
        raise ValueError(f"{QUERY_ACTION_JUDGE_TIMEOUT_MS_ENV} must be an integer") from exc
    if timeout_ms <= 0:
        raise ValueError(f"{QUERY_ACTION_JUDGE_TIMEOUT_MS_ENV} must be positive")
    return timeout_ms


def first_string_env(*names: str) -> str | None:
    for name in names:
        value = string_env(name)
        if value:
            return value
    return None


def string_env(name: str) -> str | None:
    value = os.getenv(name)
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def apply_query_action_judgment(
    context: ContextPack,
    *,
    config: QueryActionJudgeConfig | str | None,
    root: Path | str,
) -> ContextPack:
    resolved = validate_query_action_judge_config(config)
    if not resolved.enabled:
        return context
    if not resolved.api_key:
        return context.model_copy(
            update={
                "retrieval_action_guidance": RetrievalActionGuidance(
                    status="unconfigured",
                    diagnostics=["missing_api_key"],
                )
            }
        )

    try:
        payload = build_system_one_payload(context, config=resolved, root=root)
        response = call_system_one_provider(payload, resolved)
        guidance = guidance_from_provider_response(context, response)
    except Exception:
        guidance = RetrievalActionGuidance(
            status="failed",
            diagnostics=["provider_failed"],
        )
    return context.model_copy(update={"retrieval_action_guidance": guidance})


def build_system_one_payload(
    context: ContextPack,
    *,
    config: QueryActionJudgeConfig,
    root: Path | str,
) -> dict[str, Any]:
    local_roots = local_root_strings([root])
    query_redacted = redact_provider_string(context.query, local_roots)
    state = {
        "schema_version": "llmwiki.query_action_judgment.state.v1",
        "gate": "query_action_guidance",
        "data_minimized": True,
        "query": {
            "redacted_text": query_redacted,
            "character_count": len(context.query),
            "token_count": len(tokenize_for_overlap(context.query)),
        },
        "result": {
            "answerable": context.answerable,
            "evidence_count": len(context.evidence),
            "orientation_count": len(context.orientation),
            "limitation_count": len(context.limitations),
            "graph_node_count": len(context.graph.get("nodes", [])),
            "graph_edge_count": len(context.graph.get("edges", [])),
            "retrieval_guidance_present": context.retrieval_guidance is not None,
        },
        "evidence": [
            evidence_structural_summary(item, index=index, query=context.query)
            for index, item in enumerate(context.evidence[:MAX_ACTION_TARGETS])
        ],
        "orientation": [
            evidence_structural_summary(item, index=index, query=context.query)
            for index, item in enumerate(context.orientation[:MAX_ACTION_TARGETS])
        ],
        "allowed_actions": ["stop", "read", "search", "graph", "ask_clarification"],
        "omitted_fields": [
            "raw_page_text",
            "raw_snippet_text",
            "page_id",
            "source_ref_labels",
            "raw_paths",
            "local_roots",
        ],
    }
    return {
        "model": config.model,
        "state": state,
        "questions": query_action_judgment_questions(),
    }


def evidence_structural_summary(
    item: ContextSearchResult,
    *,
    index: int,
    query: str,
) -> dict[str, Any]:
    return {
        "ordinal": index,
        "role": item.role,
        "route": item.route,
        "score_bucket": score_bucket(item.score),
        "snippet_character_count": len(item.snippet),
        "source_ref_count": len(item.source_refs),
        "path_depth": path_depth(item.path),
        "query_overlap_ratio": query_overlap_ratio(query, item.title, item.snippet),
    }


def query_action_judgment_questions() -> dict[str, Any]:
    return {
        "next_action": {
            "type": "choice",
            "instructions": (
                "Choose the next read-only LLMWiki retrieval action after this masked query result."
            ),
            "criteria": {
                "stop": (
                    "The current structural evidence appears sufficient; no immediate "
                    "follow-up retrieval is needed."
                ),
                "read": (
                    "One or more returned evidence pages should be read for deeper "
                    "support before answering."
                ),
                "search": (
                    "A refined lexical, vector, or hybrid search is likely more useful "
                    "than reading the current evidence."
                ),
                "graph": (
                    "Relationship structure, dependencies, source refs, tags, or links "
                    "are likely important enough to inspect graph data."
                ),
                "ask_clarification": (
                    "The query or structural evidence is too ambiguous for a useful "
                    "next retrieval action."
                ),
            },
        },
        "evidence_sufficiency_score": {
            "type": "score",
            "instructions": (
                "Score whether the masked LLMWiki query result appears sufficient "
                "without another source-tool action."
            ),
            "criteria": [
                "0 means the result is absent, weak, contradictory, or needs more retrieval.",
                "1 means the result is likely sufficient to stop retrieval for now.",
            ],
        },
        "graph_context_useful": {
            "type": "bool",
            "instructions": (
                "Does the structural state indicate graph context may improve the next step?"
            ),
            "criteria": {
                "true": (
                    "Graph counts, relation distribution, or source spread suggest graph "
                    "inspection may help."
                ),
                "false": "Graph context appears unnecessary or uninformative for this query.",
            },
        },
    }


def call_system_one_provider(
    payload: dict[str, Any],
    config: QueryActionJudgeConfig,
) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        config.endpoint,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {config.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=config.timeout_ms / 1000) as response:
            status = response.getcode()
            response_body = response.read(MAX_PROVIDER_RESPONSE_BYTES + 1)
    except (OSError, urllib.error.HTTPError) as exc:
        raise QueryActionJudgmentError("provider request failed") from exc
    if status < 200 or status >= 300:
        raise QueryActionJudgmentError("provider returned non-success status")
    if len(response_body) > MAX_PROVIDER_RESPONSE_BYTES:
        raise QueryActionJudgmentError("provider response exceeded size limit")
    try:
        decoded = response_body.decode("utf-8")
        parsed = json.loads(decoded)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise QueryActionJudgmentError("provider returned invalid json") from exc
    if not isinstance(parsed, dict):
        raise QueryActionJudgmentError("provider returned non-object json")
    return parsed


def guidance_from_provider_response(
    context: ContextPack,
    response: dict[str, Any],
) -> RetrievalActionGuidance:
    answers = dict_value(response.get("answers"))
    next_action_answer = dict_value(answers.get("next_action"))
    action = normalize_action(answer_choice(next_action_answer))
    if action is None:
        raise QueryActionJudgmentError("provider returned invalid action")
    sufficiency = numeric_answer(dict_value(answers.get("evidence_sufficiency_score")))
    confidence = numeric_answer(next_action_answer, key="confidence")
    targets = top_page_ids(context.evidence)
    return RetrievalActionGuidance(
        status="ok",
        recommended_action=action,
        confidence=confidence,
        evidence_sufficiency_score=sufficiency,
        read_page_ids=targets if action == "read" else [],
        search_queries=[context.query] if action == "search" else [],
        graph_seeds=targets if action == "graph" else [],
        reasons=[f"System-One selected {action} from masked query-action judgment."],
    )


def dict_value(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def answer_choice(answer: dict[str, Any]) -> str | None:
    for key in ("choice", "answer", "value"):
        value = answer.get(key)
        if isinstance(value, str):
            return value
    return None


def normalize_action(value: str | None) -> RetrievalActionDecision | None:
    normalized = (value or "").strip().lower().replace("-", "_")
    aliases: dict[str, RetrievalActionDecision] = {
        "none": "stop",
        "stop": "stop",
        "done": "stop",
        "read": "read",
        "read_page": "read",
        "search": "search",
        "query": "search",
        "refine_search": "search",
        "graph": "graph",
        "inspect_graph": "graph",
        "expand_neighbors": "graph",
        "graph_neighbors": "graph",
        "ask_clarification": "ask_clarification",
        "clarify": "ask_clarification",
    }
    return aliases.get(normalized)


def numeric_answer(answer: dict[str, Any], *, key: str = "score") -> float | None:
    value = answer.get(key)
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, int | float):
        return clamp_score(float(value))
    if isinstance(value, str):
        try:
            return clamp_score(float(value))
        except ValueError:
            return None
    return None


def clamp_score(value: float) -> float:
    return max(0.0, min(1.0, value))


def top_page_ids(items: Sequence[ContextSearchResult]) -> list[str]:
    page_ids: list[str] = []
    for item in items:
        if item.page_id and item.page_id not in page_ids:
            page_ids.append(item.page_id)
        if len(page_ids) >= MAX_ACTION_TARGETS:
            break
    return page_ids


def score_bucket(score: float) -> str:
    if score <= 0:
        return "zero"
    if score < 1:
        return "low"
    if score < 3:
        return "medium"
    if score < 8:
        return "high"
    return "very_high"


def path_depth(path: str) -> int:
    normalized = path.replace("\\", "/").strip("/")
    if not normalized:
        return 0
    return len([part for part in normalized.split("/") if part])


def query_overlap_ratio(query: str, *texts: str) -> float:
    query_tokens = set(tokenize_for_overlap(query))
    if not query_tokens:
        return 0.0
    text_tokens: set[str] = set()
    for text in texts:
        text_tokens.update(tokenize_for_overlap(text))
    return round(len(query_tokens & text_tokens) / len(query_tokens), 3)


def tokenize_for_overlap(value: str) -> list[str]:
    return [match.group(0).lower() for match in _TOKEN_RE.finditer(value)]


def redact_provider_string(value: str, local_roots: Sequence[str]) -> str:
    return _EMAIL_RE.sub("[REDACTED_EMAIL]", redact_string(value, local_roots))

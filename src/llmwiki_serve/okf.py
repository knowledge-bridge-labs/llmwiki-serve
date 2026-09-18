from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime
from pathlib import Path
from typing import Any

import yaml

from .models import (
    OkfActorEvent,
    OkfDocumentRole,
    OkfMetadata,
    OkfSource,
    OkfTrustTier,
    PageRole,
    WikiPage,
)
from .parser import (
    HEADING_RE,
    clean_id,
    clean_text,
    extract_inline_tags,
    extract_links,
    first_heading,
    string_list,
    summary_text,
    title_from_path,
    unique,
)

OKF_V02_VERSION = "0.2"
OKF_V02_ADAPTER_NAME = "okf-v0.2"
OKF_V02_IMPLEMENTATION = "Open Knowledge Format v0.2"
OKF_ROOT_VERSION_KEYS = ("okf_version", "okfVersion")
OKF_RESERVED_FILENAMES = {"index.md", "log.md"}
OKF_SOURCE_FIELDS = {
    "id",
    "resource",
    "title",
    "author",
    "usage_count",
    "last_modified",
    "usage_window",
}
OKF_METADATA_FIELDS = {
    "type",
    "title",
    "description",
    "resource",
    "tags",
    "generated",
    "verified",
    "status",
    "stale_after",
    "sources",
    "usage_window",
    "runtime",
    "parameters",
    "computation",
    "executor",
    "attester",
}


class OkfV02ValidationError(ValueError):
    pass


def is_okf_v02_root(root: Path) -> bool:
    index = root / "index.md"
    if not index.is_file() or index.is_symlink():
        return False
    try:
        frontmatter, _body = split_okf_frontmatter(index.read_text(encoding="utf-8"), "index.md")
    except OkfV02ValidationError:
        return False
    return root_okf_version(frontmatter) == OKF_V02_VERSION


def parse_okf_v02_pages(root: Path, paths: Iterable[Path]) -> list[WikiPage]:
    pages: list[WikiPage] = []
    for path in sorted(paths):
        role = okf_document_role(path)
        if role == "log":
            continue
        pages.append(parse_okf_v02_page(root, path, role=role))
    if not pages:
        raise OkfV02ValidationError("OKF bundle has no serveable markdown pages")
    return pages


def parse_okf_v02_page(root: Path, path: Path, *, role: OkfDocumentRole) -> WikiPage:
    rel = path.relative_to(root).as_posix()
    raw = path.read_text(encoding="utf-8")
    frontmatter, body = split_okf_frontmatter(raw, rel)
    if rel == "index.md":
        declared_version = root_okf_version(frontmatter)
        if declared_version and declared_version != OKF_V02_VERSION:
            raise OkfV02ValidationError("root index.md declares an unsupported OKF version")
    if role == "concept":
        concept_type = scalar_text(frontmatter.get("type"))
        if not concept_type:
            raise OkfV02ValidationError(f"OKF concept {rel} is missing required type")
    else:
        concept_type = ""
    okf = normalize_okf_metadata(frontmatter, role=role, concept_type=concept_type)
    title = clean_text(
        scalar_text(frontmatter.get("title"))
        or first_heading(body)
        or title_from_path(path, decode_namespace_title=False)
    )
    page_id = clean_id(scalar_text(frontmatter.get("id")) or rel)
    tags = unique([*string_list(frontmatter.get("tags")), *extract_inline_tags(body)])
    source_refs = unique(
        [
            *[okf_source_ref_key(source) for source in okf.sources],
            *string_list(frontmatter.get("source_refs")),
        ]
    )
    return WikiPage(
        id=page_id,
        title=title,
        path=rel,
        role=okf_page_role(role),
        text=body.strip(),
        summary=scalar_text(frontmatter.get("description")) or summary_text(body),
        frontmatter=frontmatter,
        review_state="unknown",
        status=okf.status,
        source_refs=source_refs,
        tags=tags,
        links=extract_links(body),
        headings=[clean_text(match.group(2)) for match in HEADING_RE.finditer(body)][:40],
        updated_at=okf.generated.at if okf.generated else "",
        okf=okf,
    )


def split_okf_frontmatter(raw: str, rel: str) -> tuple[dict[str, Any], str]:
    if not raw.startswith("---\n"):
        return {}, raw
    end = raw.find("\n---", 4)
    if end < 0:
        raise OkfV02ValidationError(f"OKF frontmatter is not closed in {rel}")
    block = raw[4:end]
    body = raw[end + 4 :]
    try:
        data = yaml.safe_load(block) or {}
    except yaml.YAMLError as exc:
        raise OkfV02ValidationError(f"OKF frontmatter is invalid in {rel}") from exc
    if not isinstance(data, dict):
        raise OkfV02ValidationError(f"OKF frontmatter must be a mapping in {rel}")
    return data, body


def normalize_okf_metadata(
    frontmatter: dict[str, Any],
    *,
    role: OkfDocumentRole,
    concept_type: str,
) -> OkfMetadata:
    shared_usage_window = mapping_value(frontmatter.get("usage_window"))
    sources = normalize_okf_sources(frontmatter.get("sources"), shared_usage_window)
    generated = normalize_okf_actor_event(frontmatter.get("generated"), field_name="generated")
    verified = normalize_okf_verified(frontmatter.get("verified"))
    runtime = scalar_text(frontmatter.get("runtime"))
    if concept_type.casefold() == "attested computation" and not runtime:
        raise OkfV02ValidationError("OKF Attested Computation is missing required runtime")
    return OkfMetadata(
        version=OKF_V02_VERSION,
        role=role,
        concept_type=concept_type,
        description=scalar_text(frontmatter.get("description")),
        resource=scalar_text(frontmatter.get("resource")),
        generated=generated,
        verified=verified,
        trust_tier=okf_trust_tier(verified),
        status=scalar_text(frontmatter.get("status")) or "stable",
        stale_after=scalar_text(frontmatter.get("stale_after")),
        usage_window=shared_usage_window,
        runtime=runtime,
        parameters=list_of_mappings(frontmatter.get("parameters"), field_name="parameters"),
        computation=scalar_text(frontmatter.get("computation")),
        executor=mapping_value(frontmatter.get("executor")),
        attester=mapping_value(frontmatter.get("attester")),
        sources=sources,
        extra={
            str(key): value
            for key, value in frontmatter.items()
            if str(key) not in OKF_METADATA_FIELDS and str(key) not in OKF_ROOT_VERSION_KEYS
        },
    )


def normalize_okf_sources(value: Any, shared_usage_window: dict[str, Any]) -> list[OkfSource]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise OkfV02ValidationError("OKF sources must be a list")
    sources: list[OkfSource] = []
    for item in value:
        if not isinstance(item, dict):
            raise OkfV02ValidationError("OKF sources entries must be mappings")
        resource = scalar_text(item.get("resource"))
        if not resource:
            raise OkfV02ValidationError("OKF sources entries require resource")
        usage_window = mapping_value(item.get("usage_window")) or shared_usage_window
        sources.append(
            OkfSource(
                id=scalar_text(item.get("id")),
                resource=resource,
                title=scalar_text(item.get("title")),
                author=scalar_text(item.get("author")),
                usage_count=usage_count_value(item.get("usage_count")),
                last_modified=scalar_text(item.get("last_modified")),
                usage_window=usage_window,
                extra={
                    str(key): value
                    for key, value in item.items()
                    if str(key) not in OKF_SOURCE_FIELDS
                },
            )
        )
    return sources


def normalize_okf_verified(value: Any) -> list[OkfActorEvent]:
    if value is None:
        return []
    if isinstance(value, dict):
        event = normalize_okf_actor_event(value, field_name="verified")
        return [event] if event else []
    if not isinstance(value, list):
        raise OkfV02ValidationError("OKF verified must be a mapping or list")
    events: list[OkfActorEvent] = []
    for item in value:
        event = normalize_okf_actor_event(item, field_name="verified")
        if event:
            events.append(event)
    return events


def normalize_okf_actor_event(value: Any, *, field_name: str) -> OkfActorEvent | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise OkfV02ValidationError(f"OKF {field_name} must be a mapping")
    by = scalar_text(value.get("by"))
    if not by:
        raise OkfV02ValidationError(f"OKF {field_name}.by is required")
    return OkfActorEvent(by=by, at=scalar_text(value.get("at")))


def okf_trust_tier(verified: list[OkfActorEvent]) -> OkfTrustTier:
    if not verified:
        return "unverified"
    if any(event.by.startswith("human:") for event in verified):
        return "human-reviewed"
    return "machine-confirmed"


def okf_source_ref_key(source: OkfSource) -> str:
    return source.id or source.resource


def okf_source_ref_label(source: OkfSource) -> str:
    return source.title or source.id or source.resource


def okf_document_role(path: Path) -> OkfDocumentRole:
    name = path.name.casefold()
    if name == "index.md":
        return "index"
    if name == "log.md":
        return "log"
    return "concept"


def okf_page_role(role: OkfDocumentRole) -> PageRole:
    if role == "index":
        return "index"
    return "topic"


def root_okf_version(frontmatter: dict[str, Any]) -> str:
    for key in OKF_ROOT_VERSION_KEYS:
        value = scalar_text(frontmatter.get(key))
        if value:
            return value
    return ""


def mapping_value(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def list_of_mappings(value: Any, *, field_name: str) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise OkfV02ValidationError(f"OKF {field_name} must be a list")
    if not all(isinstance(item, dict) for item in value):
        raise OkfV02ValidationError(f"OKF {field_name} entries must be mappings")
    return [dict(item) for item in value]


def usage_count_value(value: Any) -> int | float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    return None


def scalar_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        text = value.isoformat()
        return text.replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    return clean_text(str(value))

from __future__ import annotations

import json
import os
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from pathlib import Path

import pytest

import llmwiki_serve.managed_context as managed_context_module
from llmwiki_serve.api import create_app
from llmwiki_serve.cli import cli_service, resolve_managed_context_cli_config
from llmwiki_serve.instances import parse_serve_args
from llmwiki_serve.managed_context import (
    SIDECAR_RECORD_READ_MAX_BYTES,
    SIDECAR_SALT_READ_MAX_BYTES,
    ManagedContextConfig,
    ManagedContextRuntime,
    record_from_payload,
    source_signature_digest,
)
from llmwiki_serve.service import (
    LlmWikiService,
    _PathState,
    managed_projection_signature_digest,
    source_signature,
)


def test_managed_context_is_disabled_by_default_and_schema_unchanged(tmp_path: Path) -> None:
    root = tmp_path / "docs"
    write_markdown(root / "alpha.md", "# Alpha\n\nzzsharedneedle alpha text\n")
    state_dir = tmp_path / "state"

    default_service = LlmWikiService(root)
    disabled_service = LlmWikiService(
        root,
        managed_context=ManagedContextConfig(enabled=False, state_dir=state_dir),
    )

    assert disabled_service.manifest().adapter == "generic-markdown"
    assert disabled_service.search("zzsharedneedle") == default_service.search("zzsharedneedle")
    assert disabled_service.context("zzsharedneedle") == default_service.context("zzsharedneedle")
    assert not state_dir.exists()
    assert (
        create_app(root).openapi()
        == create_app(
            root,
            managed_context=ManagedContextConfig(enabled=True, state_dir=state_dir),
        ).openapi()
    )


def test_generic_managed_context_writes_private_sidecar_outside_source_root(
    tmp_path: Path,
) -> None:
    root = tmp_path / "docs"
    write_markdown(root / "alpha.md", "# Alpha\n\nzzsharedneedle alpha-private-body\n")
    write_markdown(root / "beta.md", "# Beta\n\nzzsharedneedle beta-private-body\n")
    state_dir = tmp_path / "managed-state"
    before = source_tree_snapshot(root)

    service = LlmWikiService(
        root,
        source_id="generic-source",
        managed_context=ManagedContextConfig(
            enabled=True,
            state_dir=state_dir,
            namespace_secret="test-secret",
        ),
        _managed_context_clock=lambda: 1000.0,
    )
    query_sentinel = "QUERY_SENTINEL_PRIVATE"
    locator_sentinel = "LOCATOR_SENTINEL_PRIVATE"
    results = service.search(f"zzsharedneedle {query_sentinel} {locator_sentinel}", limit=2)

    assert [item["page_id"] for item in results] == ["alpha", "beta"]
    assert source_tree_snapshot(root) == before
    assert not any(root in path.parents for path in state_dir.rglob("*"))

    encoded = "\n".join(path.read_text(encoding="utf-8") for path in state_dir.rglob("*.json"))
    forbidden = [
        query_sentinel,
        locator_sentinel,
        str(root),
        root.as_posix(),
        "alpha.md",
        "beta.md",
        "alpha-private-body",
        "beta-private-body",
        '"alpha"',
        '"beta"',
    ]
    for value in forbidden:
        assert value not in encoded
    assert "pk_" in encoded
    assert "projection_signature_digest" in encoded
    assert "source_signature_digest" in encoded


def test_nested_generic_quickstart_does_not_disable_managed_context(
    tmp_path: Path,
) -> None:
    root = tmp_path / "docs"
    write_markdown(root / "guide" / "quickstart.md", "# Quickstart\n\nzzguideneedle\n")
    write_markdown(root / "alpha.md", "# Alpha\n\nzzguideneedle\n")
    state_dir = tmp_path / "state"
    before = source_tree_snapshot(root)

    service = LlmWikiService(
        root,
        managed_context=ManagedContextConfig(enabled=True, state_dir=state_dir),
        _managed_context_clock=lambda: 1000.0,
    )

    assert service.manifest().adapter == "generic-markdown"
    assert service.read("guide/quickstart")["role"] == "topic"
    assert service.search("zzguideneedle", limit=2)
    assert managed_record_payload(state_dir)["page_hit_prior"]
    assert source_tree_snapshot(root) == before


def test_root_generic_quickstart_disables_managed_context_as_authored_orientation(
    tmp_path: Path,
) -> None:
    root = tmp_path / "docs"
    write_markdown(root / "quickstart.md", "# Quickstart\n\nzzquickroot\n")
    write_markdown(root / "alpha.md", "# Alpha\n\nzzquickroot\n")
    state_dir = tmp_path / "state"

    service = LlmWikiService(
        root,
        managed_context=ManagedContextConfig(enabled=True, state_dir=state_dir),
    )

    assert service.manifest().adapter == "generic-markdown"
    assert service.read("quickstart")["role"] == "index"
    assert service.search("zzquickroot", limit=2)
    assert not state_dir.exists()


def test_managed_context_cli_env_and_explicit_overrides(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "docs"
    write_markdown(root / "alpha.md", "# Alpha\n\nzzclineedle\n")
    env_state_dir = tmp_path / "env-state"
    cli_state_dir = tmp_path / "cli-state"
    monkeypatch.setenv("LLMWIKI_MANAGED_CONTEXT", "true")
    monkeypatch.setenv("LLMWIKI_MANAGED_CONTEXT_STATE_DIR", str(env_state_dir))
    monkeypatch.setenv("LLMWIKI_MANAGED_CONTEXT_NAMESPACE", "env-namespace")

    assert cli_service(root).search("zzclineedle")[0]["page_id"] == "alpha"
    assert managed_record_payload(env_state_dir)["page_hit_prior"]

    disabled_override = resolve_managed_context_cli_config(
        enabled=False,
        state_dir=cli_state_dir,
        namespace="cli-namespace",
    )
    disabled_service = LlmWikiService(root, managed_context=disabled_override)

    assert disabled_service.search("zzclineedle")[0]["page_id"] == "alpha"
    assert not cli_state_dir.exists()
    assert disabled_override.enabled is False
    assert disabled_override.state_dir == cli_state_dir
    assert disabled_override.namespace == "cli-namespace"


def test_instance_parser_skips_managed_context_serve_options(tmp_path: Path) -> None:
    root = str(tmp_path / "docs")

    assert parse_serve_args(
        [
            "--managed-context",
            "--managed-context-state-dir",
            str(tmp_path / "state"),
            "--managed-context-namespace=team-a",
            "--host",
            "127.0.0.2",
            "--port=8766",
            root,
        ]
    ) == ("127.0.0.2", 8766, root)
    assert parse_serve_args(
        [
            "--no-managed-context",
            "--managed-context-state-dir",
            str(tmp_path / "state"),
            "--managed-context-namespace",
            "team-a",
            root,
        ]
    ) == ("127.0.0.1", 8765, root)


def test_service_managed_context_uses_wall_clock_separate_from_refresh_clock(
    tmp_path: Path,
) -> None:
    root = tmp_path / "docs"
    write_markdown(root / "alpha.md", "# Alpha\n\nzzclockneedle alpha\n")
    state_dir = tmp_path / "state"
    refresh_now = 0.0
    managed_now = 1_700_000_000.0

    def refresh_clock() -> float:
        return refresh_now

    def managed_clock() -> float:
        return managed_now

    service = LlmWikiService(
        root,
        source_id="generic-source",
        refresh_interval_seconds=10.0,
        managed_context=ManagedContextConfig(
            enabled=True,
            state_dir=state_dir,
            namespace_secret="test-secret",
            hit_half_life_seconds=100.0,
            max_hit_counter=10.0,
        ),
        clock=refresh_clock,
        _managed_context_clock=managed_clock,
    )

    assert service.read("alpha")["id"] == "alpha"
    assert service._last_refresh_check == 0.0
    payload = managed_record_payload(state_dir)
    assert payload["updated_at"] == 1_700_000_000.0
    assert payload["page_hit_prior"][0]["last_hit_at"] == 1_700_000_000.0
    assert payload["page_hit_prior"][0]["counter"] == 1.0

    managed_now = 1_700_000_100.0
    refresh_now = 9.0

    assert service.read("alpha")["id"] == "alpha"
    assert service._last_refresh_check == 0.0
    payload = managed_record_payload(state_dir)
    assert payload["updated_at"] == 1_700_000_100.0
    assert payload["page_hit_prior"][0]["last_hit_at"] == 1_700_000_100.0
    assert payload["page_hit_prior"][0]["counter"] == pytest.approx(1.5)


def test_managed_context_is_noop_for_llmwiki_and_authored_generic_hubs(
    tmp_path: Path,
) -> None:
    native_root = tmp_path / "native"
    write_markdown(native_root / "hot.md", "# Hot\n\nzzsharedneedle\n")
    write_markdown(native_root / "index.md", "# Index\n\nzzsharedneedle\n")
    write_markdown(native_root / "topic.md", "# Topic\n\nzzsharedneedle\n")
    authored_generic_root = tmp_path / "authored-generic"
    write_markdown(authored_generic_root / "index.md", "# Index\n\nzzsharedneedle\n")
    write_markdown(authored_generic_root / "notes" / "topic.md", "# Topic\n\nzzsharedneedle\n")
    state_dir = tmp_path / "state"

    native_service = LlmWikiService(
        native_root,
        managed_context=ManagedContextConfig(enabled=True, state_dir=state_dir),
    )
    authored_service = LlmWikiService(
        authored_generic_root,
        managed_context=ManagedContextConfig(enabled=True, state_dir=state_dir),
    )

    assert native_service.manifest().adapter == "llmwiki-markdown"
    assert authored_service.manifest().adapter == "generic-markdown"
    assert [item.path for item in native_service.context("").orientation[:2]] == [
        "hot.md",
        "index.md",
    ]
    assert [item.path for item in authored_service.context("").orientation[:2]] == [
        "index.md",
        "notes/topic.md",
    ]
    native_service.search("zzsharedneedle")
    authored_service.read("index")
    assert not state_dir.exists()


def test_enabled_managed_context_rejects_source_root_sidecar(tmp_path: Path) -> None:
    root = tmp_path / "docs"
    write_markdown(root / "alpha.md", "# Alpha\n\nBody\n")

    with pytest.raises(ValueError, match="outside the served source root"):
        LlmWikiService(
            root,
            managed_context=ManagedContextConfig(enabled=True, state_dir=root / ".managed"),
        )

    service = LlmWikiService(
        root,
        managed_context=ManagedContextConfig(enabled=False, state_dir=root / ".managed"),
    )
    assert service.manifest().adapter == "generic-markdown"
    assert not (root / ".managed").exists()


def test_enabled_managed_context_rejects_source_root_symlink_sidecar(
    tmp_path: Path,
) -> None:
    root = tmp_path / "docs"
    outside = tmp_path / "outside-state"
    write_markdown(root / "alpha.md", "# Alpha\n\nBody\n")
    outside.mkdir()
    link = root / "state-link"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"directory symlink unavailable: {exc}")

    with pytest.raises(ValueError, match="outside the served source root"):
        LlmWikiService(
            root,
            managed_context=ManagedContextConfig(enabled=True, state_dir=link),
        )


def test_managed_orientation_and_overview_do_not_record_feedback_loop(
    tmp_path: Path,
) -> None:
    root = tmp_path / "docs"
    write_markdown(root / "alpha.md", "# Alpha\n\nzzfeedbackneedle\n")
    write_markdown(root / "beta.md", "# Beta\n\nzzfeedbackneedle\n")
    state_dir = tmp_path / "state"
    service = LlmWikiService(
        root,
        managed_context=ManagedContextConfig(enabled=True, state_dir=state_dir),
        _managed_context_clock=step_clock(1000.0),
    )

    context = service.context("")

    assert context.orientation
    assert context.evidence
    assert not state_dir.exists()

    assert service.context("zzfeedbackneedle", limit=1).evidence[0].page_id == "alpha"
    payload = managed_record_payload(state_dir)
    assert payload["generation"] == 1

    for _ in range(3):
        assert service.context("").orientation

    assert managed_record_payload(state_dir)["generation"] == 1


def test_managed_orientation_abstains_for_unrelated_query(
    tmp_path: Path,
) -> None:
    root = tmp_path / "docs"
    write_markdown(root / "alpha.md", "# Alpha\n\nzzknownneedle alpha body\n")
    write_markdown(root / "beta.md", "# Beta\n\nzzknownneedle beta body\n")
    state_dir = tmp_path / "state"
    before = source_tree_snapshot(root)
    service = LlmWikiService(
        root,
        managed_context=ManagedContextConfig(enabled=True, state_dir=state_dir),
        _managed_context_clock=lambda: 1000.0,
    )

    context = service.context("zzmissingmanagedorientation", limit=2)

    assert context.answerable is False
    assert context.evidence == []
    assert context.orientation == []
    assert not state_dir.exists()
    assert source_tree_snapshot(root) == before


def test_managed_orientation_abstention_does_not_suppress_evidence_search(
    tmp_path: Path,
) -> None:
    root = tmp_path / "docs"
    write_markdown(root / "alpha.md", "# Alpha\n\nThe alpha page has ordinary prose.\n")
    write_markdown(root / "beta.md", "# Beta\n\nThe beta page has ordinary prose.\n")
    state_dir = tmp_path / "state"
    service = LlmWikiService(
        root,
        managed_context=ManagedContextConfig(enabled=True, state_dir=state_dir),
        _managed_context_clock=lambda: 1000.0,
    )

    context = service.context("the zzmissingmanagedorientation", limit=2)

    assert context.evidence
    assert context.orientation == []
    assert not state_dir.exists()


def test_managed_orientation_gate_uses_prior_free_evidence_search(
    tmp_path: Path,
) -> None:
    root = tmp_path / "docs"
    write_markdown(root / "z-alpha.md", "# Alpha\n\nthe\n")
    write_markdown(root / "a-beta.md", "# Beta\n\nzzrare\n")
    state_dir = tmp_path / "state"
    service = LlmWikiService(
        root,
        source_id="generic-source",
        managed_context=ManagedContextConfig(
            enabled=True,
            state_dir=state_dir,
            namespace_secret="test-secret",
            max_boost=5.0,
            lexical_tie_band=0.05,
        ),
        _managed_context_clock=lambda: 1000.0,
    )
    for _ in range(4):
        assert service.read("z-alpha")["id"] == "z-alpha"

    assert service.search("the zzrare", limit=1)[0]["page_id"] == "z-alpha"
    context = service.context("the zzrare", limit=1)

    assert context.evidence[0].page_id == "z-alpha"
    assert [item.page_id for item in context.orientation] == ["a-beta"]


def test_managed_orientation_is_query_related_and_uses_compact_default_snippets(
    tmp_path: Path,
) -> None:
    root = tmp_path / "docs"
    write_markdown(
        root / "alpha.md",
        "# Alpha\n\nzzalphaneedle " + "alpha details " * 80,
    )
    write_markdown(
        root / "beta.md",
        "# Beta\n\n" + "beta unrelated details " * 80,
    )
    graph_path = root / "graph" / "graph.json"
    write_graph(graph_path, target="beta")
    service = LlmWikiService(
        root,
        managed_context=ManagedContextConfig(
            enabled=True,
            state_dir=tmp_path / "state",
            orientation_snippet_chars=48,
        ),
        _managed_context_clock=lambda: 1000.0,
    )

    context = service.context("zzalphaneedle", limit=2)

    assert [item.page_id for item in context.evidence] == ["alpha"]
    assert [item.page_id for item in context.orientation] == ["alpha"]
    assert len(context.orientation[0].snippet) <= 51


def test_managed_context_draft_hits_require_explicit_draft_access(tmp_path: Path) -> None:
    root = tmp_path / "docs"
    write_markdown(root / "alpha.md", "# Alpha\n\nzzapprovedneedle\n")
    write_markdown(
        root / "draft.md",
        "---\nreview_state: draft\n---\n# Draft\n\nzzdraftneedle private text.\n",
    )
    state_dir = tmp_path / "state"
    service = LlmWikiService(
        root,
        managed_context=ManagedContextConfig(enabled=True, state_dir=state_dir),
        _managed_context_clock=step_clock(1000.0),
    )

    assert service.search("zzdraftneedle") == []
    assert service.read("draft") == {"found": False, "reason": "not approved for serving"}
    assert not state_dir.exists()

    assert service.search("zzdraftneedle", include_drafts=True)[0]["page_id"] == "draft"
    encoded = json.dumps(managed_record_payload(state_dir), sort_keys=True)
    assert "draft" not in encoded
    assert "zzdraftneedle" not in encoded


def test_managed_prior_reorders_only_inside_lexical_tie_band(tmp_path: Path) -> None:
    root = tmp_path / "docs"
    write_markdown(root / "alpha.md", "# Alpha\n\nzztieonly commonfill\n")
    write_markdown(root / "beta.md", "# Beta\n\nzztieonly commonfill\n")
    write_markdown(root / "strong.md", "# Strong\n\nzznoninvert zzdominant zzdominant\n")
    write_markdown(root / "weak.md", "# Weak\n\nzznoninvert\n")
    state_dir = tmp_path / "state"
    service = LlmWikiService(
        root,
        managed_context=ManagedContextConfig(
            enabled=True,
            state_dir=state_dir,
            namespace_secret="test-secret",
            max_boost=5.0,
            lexical_tie_band=0.05,
            max_hit_counter=3.0,
        ),
        _managed_context_clock=step_clock(1000.0),
    )

    for _ in range(6):
        assert service.read("beta")["id"] == "beta"
        assert service.read("weak")["id"] == "weak"

    tied = service.search("zztieonly", limit=2)
    assert tied[0]["page_id"] == "beta"

    stronger_lexical = service.search("zznoninvert zzdominant", limit=2)
    assert stronger_lexical[0]["page_id"] == "strong"
    assert stronger_lexical[0]["score"] > stronger_lexical[1]["score"]


def test_signature_change_ignores_prior_from_previous_projection(tmp_path: Path) -> None:
    root = tmp_path / "docs"
    write_markdown(root / "alpha.md", "# Alpha\n\nzzsigneedle commonfill\n")
    write_markdown(root / "beta.md", "# Beta\n\nzzsigneedle commonfill\n")
    state_dir = tmp_path / "state"
    service = LlmWikiService(
        root,
        managed_context=ManagedContextConfig(
            enabled=True,
            state_dir=state_dir,
            namespace_secret="test-secret",
            max_boost=5.0,
            lexical_tie_band=0.05,
        ),
        _managed_context_clock=step_clock(1000.0),
    )

    for _ in range(4):
        service.read("beta")
    assert service.search("zzsigneedle", limit=2)[0]["page_id"] == "beta"

    write_markdown(root / "beta.md", "# Beta\n\nzzsigneedle after signature change\n")

    assert service.search("zzsigneedle", limit=2)[0]["page_id"] == "alpha"


def test_explicit_refresh_ignores_prior_from_cached_previous_projection(
    tmp_path: Path,
) -> None:
    root = tmp_path / "docs"
    write_markdown(root / "alpha.md", "# Alpha\n\nzzrefreshneedle commonfill\n")
    write_markdown(root / "beta.md", "# Beta\n\nzzrefreshneedle commonfill\n")
    state_dir = tmp_path / "state"
    service = LlmWikiService(
        root,
        refresh_interval_seconds=999.0,
        managed_context=ManagedContextConfig(
            enabled=True,
            state_dir=state_dir,
            namespace_secret="test-secret",
            max_boost=5.0,
            lexical_tie_band=0.05,
        ),
        clock=lambda: 0.0,
        _managed_context_clock=step_clock(1000.0),
    )

    for _ in range(4):
        service.read("beta")
    assert service.search("zzrefreshneedle", limit=2)[0]["page_id"] == "beta"

    write_markdown(root / "beta.md", "# Beta\n\nzzrefreshneedle changed projection state\n")
    service.index(refresh=True)

    assert service.search("zzrefreshneedle", limit=2)[0]["page_id"] == "alpha"


def test_sidecar_graph_change_rebuilds_managed_orientation(
    tmp_path: Path,
) -> None:
    root = tmp_path / "docs"
    write_markdown(root / "alpha.md", "# Alpha\n\nzzgraphneedle\n")
    write_markdown(root / "beta.md", "# Beta\n\nzzgraphneedle\n")
    graph_path = root / "graph" / "graph.json"
    write_graph(graph_path, target="beta")
    service = LlmWikiService(
        root,
        refresh_interval_seconds=999.0,
        managed_context=ManagedContextConfig(
            enabled=True,
            state_dir=tmp_path / "state",
            namespace_secret="test-secret",
        ),
        clock=lambda: 0.0,
        _managed_context_clock=step_clock(1000.0),
    )

    assert service.context("").orientation[0].page_id == "beta"

    write_graph(graph_path, target="alpha")
    service.index(refresh=True)

    assert service.context("").orientation[0].page_id == "alpha"


def test_sidecar_corrupt_records_are_ignored_and_concurrent_writes_stay_bounded(
    tmp_path: Path,
) -> None:
    root = tmp_path / "docs"
    write_markdown(root / "alpha.md", "# Alpha\n\nzzconcurrent\n")
    service = LlmWikiService(root, source_id="generic-source")
    index = service.index()
    config = ManagedContextConfig(
        enabled=True,
        state_dir=tmp_path / "state",
        namespace_secret="test-secret",
        max_hit_counter=2.0,
    )
    runtime = ManagedContextRuntime(root, config)
    scope = runtime.scope(
        source_id="generic-source",
        adapter_kind=index.adapter,
        projection_signature_digest=managed_projection_signature_digest(
            service._projection_signature or ()
        ),
        source_signature_digest=source_signature_digest(source_signature(root)),
    )
    assert runtime.store is not None
    record_path = runtime.store.record_path(scope)
    record_path.parent.mkdir(parents=True, exist_ok=True)
    record_path.write_text("{partial", encoding="utf-8")

    assert runtime.store.read(scope, config=config, now=1000.0) is None

    def hit() -> None:
        runtime.record_hits(index, scope, ["alpha"], include_drafts=False, now=1000.0)

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(lambda _item: hit(), range(30)))

    payload = json.loads(record_path.read_text(encoding="utf-8"))
    counters = [item["counter"] for item in payload["page_hit_prior"]]
    assert counters
    assert max(counters) <= 2.0
    assert payload["generation"] == 30
    assert runtime.store.read(scope, config=config, now=1000.0) is not None

    wrong_schema = {**payload, "schema_version": "wrong"}
    assert record_from_payload(wrong_schema, scope=scope, config=config, now=1000.0) is None
    wrong_generation = {**payload, "generation": 0}
    assert record_from_payload(wrong_generation, scope=scope, config=config, now=1000.0) is None
    wrong_timestamp = {**payload, "updated_at": payload["created_at"] - 1}
    assert record_from_payload(wrong_timestamp, scope=scope, config=config, now=1000.0) is None
    other_scope = runtime.scope(
        source_id="generic-source",
        adapter_kind=index.adapter,
        projection_signature_digest=managed_projection_signature_digest(
            service._projection_signature or ()
        ),
        source_signature_digest="sha256:other",
    )
    assert record_from_payload(payload, scope=other_scope, config=config, now=1000.0) is None


def test_corrupt_salt_is_repaired_before_hit_recording(tmp_path: Path) -> None:
    root = tmp_path / "docs"
    write_markdown(root / "alpha.md", "# Alpha\n\nzzsaltneedle\n")
    service = LlmWikiService(root, source_id="generic-source")
    index = service.index()
    config = ManagedContextConfig(enabled=True, state_dir=tmp_path / "state")
    runtime = ManagedContextRuntime(root, config)
    scope = runtime.scope(
        source_id="generic-source",
        adapter_kind=index.adapter,
        projection_signature_digest=managed_projection_signature_digest(
            service._projection_signature or ()
        ),
        source_signature_digest=source_signature_digest(source_signature(root)),
    )
    assert runtime.store is not None
    salt_path = runtime.store.namespace_dir / "salt.json"
    salt_path.parent.mkdir(parents=True, exist_ok=True)
    salt_path.write_text("{partial", encoding="utf-8")
    before = source_tree_snapshot(root)

    runtime.record_hits(index, scope, ["alpha"], include_drafts=False, now=1000.0)

    salt_payload = json.loads(salt_path.read_text(encoding="utf-8"))
    assert salt_payload["schema_version"] == "managed-context-v1"
    assert salt_payload["kind"] == "managed-context-salt"
    assert isinstance(salt_payload["salt"], str) and salt_payload["salt"]
    assert managed_record_payload(config.state_dir or tmp_path)["page_hit_prior"]
    assert source_tree_snapshot(root) == before


def test_stale_record_lock_skips_hit_persistence_without_failing_public_requests(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "docs"
    write_markdown(root / "alpha.md", "# Alpha\n\nzzstalelock common text\n")
    write_markdown(root / "beta.md", "# Beta\n\nzzstalelock common text\n")
    state_dir = tmp_path / "state"
    service = LlmWikiService(
        root,
        source_id="generic-source",
        managed_context=ManagedContextConfig(
            enabled=True,
            state_dir=state_dir,
            namespace_secret="test-secret",
            max_boost=5.0,
            lexical_tie_band=0.05,
            max_hit_counter=3.0,
        ),
        _managed_context_clock=lambda: 1000.0,
    )
    for _ in range(4):
        assert service.read("beta")["id"] == "beta"
    assert service.search("zzstalelock", limit=2)[0]["page_id"] == "beta"
    index = service.index()
    assert service.managed_context.store is not None
    record_path = service.managed_context.store.record_path(service._managed_context_scope(index))
    seeded_generation = json.loads(record_path.read_text(encoding="utf-8"))["generation"]
    make_stale_sidecar_lock(record_path)
    monkeypatch.setattr(managed_context_module, "SIDECAR_LOCK_TIMEOUT_SECONDS", 0.0)
    monkeypatch.setattr(managed_context_module, "SIDECAR_LOCK_RETRY_SECONDS", 0.0)
    before = source_tree_snapshot(root)

    assert service.search("zzstalelock", limit=2)[0]["page_id"] == "alpha"
    assert service.read("alpha")["id"] == "alpha"
    context = service.context("zzstalelock", limit=2)

    assert context.evidence[0].page_id == "alpha"
    assert json.loads(record_path.read_text(encoding="utf-8"))["generation"] == seeded_generation
    assert source_tree_snapshot(root) == before


def test_old_sidecar_lock_skips_hit_persistence_without_waiting_for_timeout(
    tmp_path: Path,
) -> None:
    root = tmp_path / "docs"
    write_markdown(root / "alpha.md", "# Alpha\n\nzzoldlock alpha text\n")
    state_dir = tmp_path / "state"
    service = LlmWikiService(
        root,
        source_id="generic-source",
        managed_context=ManagedContextConfig(enabled=True, state_dir=state_dir),
        _managed_context_clock=lambda: 1000.0,
    )
    index = service.index()
    assert service.managed_context.store is not None
    record_path = service.managed_context.store.record_path(service._managed_context_scope(index))
    lock_path = make_stale_sidecar_lock(record_path)
    old_timestamp = 1000.0 - managed_context_module.SIDECAR_LOCK_STALE_SECONDS - 1.0
    os.utime(lock_path, (old_timestamp, old_timestamp))
    before = source_tree_snapshot(root)

    assert service.search("zzoldlock", limit=1)[0]["page_id"] == "alpha"

    assert not list(state_dir.rglob("*.json"))
    assert source_tree_snapshot(root) == before


def test_stale_salt_lock_skips_secret_creation_without_failing_public_requests(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "docs"
    write_markdown(root / "alpha.md", "# Alpha\n\nzzsaltlock alpha text\n")
    state_dir = tmp_path / "state"
    service = LlmWikiService(
        root,
        source_id="generic-source",
        managed_context=ManagedContextConfig(enabled=True, state_dir=state_dir),
        _managed_context_clock=lambda: 1000.0,
    )
    assert service.managed_context.store is not None
    salt_path = service.managed_context.store.namespace_dir / "salt.json"
    make_stale_sidecar_lock(salt_path)
    monkeypatch.setattr(managed_context_module, "SIDECAR_LOCK_TIMEOUT_SECONDS", 0.0)
    monkeypatch.setattr(managed_context_module, "SIDECAR_LOCK_RETRY_SECONDS", 0.0)
    before = source_tree_snapshot(root)

    assert service.search("zzsaltlock", limit=1)[0]["page_id"] == "alpha"
    assert service.read("alpha")["id"] == "alpha"
    context = service.context("zzsaltlock", limit=1)

    assert context.evidence[0].page_id == "alpha"
    assert not salt_path.exists()
    assert not list(state_dir.rglob("*.json"))
    assert source_tree_snapshot(root) == before


def test_unavailable_sidecar_path_fails_open_for_public_requests(tmp_path: Path) -> None:
    root = tmp_path / "docs"
    write_markdown(root / "alpha.md", "# Alpha\n\nzzsidecarfile alpha text\n")
    state_dir = tmp_path / "state"
    state_dir.write_text("not a directory\n", encoding="utf-8")
    service = LlmWikiService(
        root,
        source_id="generic-source",
        managed_context=ManagedContextConfig(
            enabled=True,
            state_dir=state_dir,
            namespace_secret="test-secret",
        ),
        _managed_context_clock=lambda: 1000.0,
    )
    before = source_tree_snapshot(root)

    assert service.search("zzsidecarfile", limit=1)[0]["page_id"] == "alpha"
    assert service.read("alpha")["id"] == "alpha"
    context = service.context("zzsidecarfile", limit=1)

    assert context.evidence[0].page_id == "alpha"
    assert state_dir.read_text(encoding="utf-8") == "not a directory\n"
    assert source_tree_snapshot(root) == before


def test_oversized_sidecar_record_and_salt_are_ignored(tmp_path: Path) -> None:
    root = tmp_path / "docs"
    write_markdown(root / "alpha.md", "# Alpha\n\nzzoversized alpha text\n")
    service = LlmWikiService(root, source_id="generic-source")
    index = service.index()
    state_dir = tmp_path / "state"
    config = ManagedContextConfig(
        enabled=True,
        state_dir=state_dir,
        namespace_secret="test-secret",
    )
    runtime = ManagedContextRuntime(root, config)
    scope = runtime.scope(
        source_id="generic-source",
        adapter_kind=index.adapter,
        projection_signature_digest=managed_projection_signature_digest(
            service._projection_signature or ()
        ),
        source_signature_digest=source_signature_digest(source_signature(root)),
    )
    assert runtime.store is not None
    record_path = runtime.store.record_path(scope)
    record_path.parent.mkdir(parents=True, exist_ok=True)
    record_path.write_text(
        '{"page_hit_prior":"' + ("x" * SIDECAR_RECORD_READ_MAX_BYTES) + '"}',
        encoding="utf-8",
    )

    assert runtime.store.read(scope, config=config, now=1000.0) is None

    salt_config = ManagedContextConfig(enabled=True, state_dir=state_dir)
    salt_runtime = ManagedContextRuntime(root, salt_config)
    assert salt_runtime.store is not None
    salt_path = salt_runtime.store.namespace_dir / "salt.json"
    salt_path.parent.mkdir(parents=True, exist_ok=True)
    salt_path.write_text(
        '{"schema_version":"managed-context-v1","kind":"managed-context-salt","salt":"'
        + ("s" * SIDECAR_SALT_READ_MAX_BYTES)
        + '"}',
        encoding="utf-8",
    )

    assert salt_runtime.store.secret(create=False) is None


def test_source_signature_digest_is_path_free() -> None:
    first = (("ENTRY_A", 100, 20), ("ENTRY_B", 200, 30))
    renamed = (("RENAMED_A", 100, 20), ("RENAMED_B", 200, 30))
    changed = (("ENTRY_A", 100, 20), ("ENTRY_B", 200, 31))

    assert source_signature_digest(first) == source_signature_digest(renamed)
    assert source_signature_digest(first) != source_signature_digest(changed)


def test_managed_projection_signature_digest_is_path_free() -> None:
    first = (
        _PathState("alpha.md", "file", 1, 10, 100, 20, "sha256:alpha"),
        _PathState("beta.md", "file", 1, 11, 200, 30, "sha256:beta"),
    )
    renamed = (
        _PathState("renamed-alpha.md", "file", 2, 20, 300, 20, "sha256:alpha"),
        _PathState("renamed-beta.md", "file", 2, 21, 400, 30, "sha256:beta"),
    )
    changed = (
        _PathState("alpha.md", "file", 1, 10, 100, 20, "sha256:alpha"),
        _PathState("beta.md", "file", 1, 11, 200, 31, "sha256:changed"),
    )

    assert managed_projection_signature_digest(first) == managed_projection_signature_digest(
        renamed
    )
    assert managed_projection_signature_digest(first) != managed_projection_signature_digest(
        changed
    )


def test_process_writers_advance_sidecar_generation_without_corruption(
    tmp_path: Path,
) -> None:
    root = tmp_path / "docs"
    write_markdown(root / "alpha.md", "# Alpha\n\nzzprocessneedle\n")
    state_dir = tmp_path / "state"
    hit_count = 8

    with ProcessPoolExecutor(max_workers=4) as executor:
        results = list(
            executor.map(
                _record_hit_in_process,
                [(str(root), str(state_dir), 1000.0 + index) for index in range(hit_count)],
            )
        )

    assert results == [True] * hit_count
    payload = managed_record_payload(state_dir)
    assert payload["generation"] == hit_count
    assert payload["page_hit_prior"][0]["counter"] <= 10.0


def test_process_writers_share_generated_salt_without_corruption(
    tmp_path: Path,
) -> None:
    root = tmp_path / "docs"
    write_markdown(root / "alpha.md", "# Alpha\n\nzzsaltprocess\n")
    state_dir = tmp_path / "state"
    hit_count = 6

    with ProcessPoolExecutor(max_workers=4) as executor:
        results = list(
            executor.map(
                _record_hit_with_generated_salt_in_process,
                [(str(root), str(state_dir), 1000.0 + index) for index in range(hit_count)],
            )
        )

    assert results == [True] * hit_count
    salt_payloads = [
        json.loads(path.read_text(encoding="utf-8")) for path in state_dir.rglob("salt.json")
    ]
    assert len(salt_payloads) == 1
    assert salt_payloads[0]["schema_version"] == "managed-context-v1"
    assert salt_payloads[0]["kind"] == "managed-context-salt"
    assert isinstance(salt_payloads[0]["salt"], str) and salt_payloads[0]["salt"]
    payload = managed_record_payload(state_dir)
    assert payload["generation"] == hit_count
    assert len(payload["page_hit_prior"]) == 1
    assert not list(state_dir.rglob("*.tmp"))


def write_markdown(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content.strip() + "\n", encoding="utf-8")


def write_graph(path: Path, *, target: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {"edges": [{"from": "reference-node", "to": target, "type": "supports"}]},
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )


def make_stale_sidecar_lock(path: Path) -> Path:
    lock_path = path.with_name(f".{path.name}.lockdir")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.mkdir()
    return lock_path


def _record_hit_in_process(args: tuple[str, str, float]) -> bool:
    root_value, state_dir_value, now = args
    service = LlmWikiService(
        Path(root_value),
        source_id="generic-source",
        managed_context=ManagedContextConfig(
            enabled=True,
            state_dir=Path(state_dir_value),
            namespace_secret="process-secret",
        ),
        _managed_context_clock=lambda: now,
    )
    return service.read("alpha")["id"] == "alpha"


def _record_hit_with_generated_salt_in_process(args: tuple[str, str, float]) -> bool:
    root_value, state_dir_value, now = args
    service = LlmWikiService(
        Path(root_value),
        source_id="generic-source",
        managed_context=ManagedContextConfig(
            enabled=True,
            state_dir=Path(state_dir_value),
        ),
        _managed_context_clock=lambda: now,
    )
    return service.read("alpha")["id"] == "alpha"


def source_tree_snapshot(root: Path) -> dict[str, str]:
    snapshot: dict[str, str] = {}
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        snapshot[path.relative_to(root).as_posix()] = path.read_bytes().hex()
    return snapshot


def managed_record_payload(state_dir: Path):
    payloads = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in state_dir.rglob("*.json")
        if path.name != "salt.json"
    ]
    assert len(payloads) == 1
    return payloads[0]


def step_clock(start: float):
    current = start

    def now() -> float:
        nonlocal current
        current += 1.0
        return current

    return now

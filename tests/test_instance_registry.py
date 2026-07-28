from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from typer.testing import CliRunner

from llmwiki_serve.cli import app as cli_app
from llmwiki_serve.instances import (
    InstanceRecord,
    instance_registry_dir,
    list_local_instances,
    read_instance_records,
    register_instance,
)

FIXTURE = Path(__file__).parent / "fixtures" / "sample-wiki"


def test_serve_registers_instance_until_shutdown(monkeypatch, tmp_path: Path) -> None:
    import uvicorn

    state_dir = tmp_path / "state"
    captured: dict[str, Any] = {}

    def fake_run(app: Any, *, host: str, port: int) -> None:
        captured["host"] = host
        captured["port"] = port
        captured["instances"] = [
            item.model_dump() for item in list_local_instances(state_dir=state_dir, probe=False)
        ]

    monkeypatch.setattr(uvicorn, "run", fake_run)
    result = CliRunner().invoke(
        cli_app,
        ["serve", str(FIXTURE), "--port", "9876"],
        env={"LLMWIKI_SERVE_STATE_DIR": str(state_dir)},
    )

    assert result.exit_code == 0, result.output
    assert captured["host"] == "127.0.0.1"
    assert captured["port"] == 9876
    assert len(captured["instances"]) == 1
    instance = captured["instances"][0]
    assert instance["status"] == "running"
    assert instance["host"] == "127.0.0.1"
    assert instance["port"] == 9876
    assert instance["root"] == str(FIXTURE.resolve())
    assert instance["source_id"] == "sample-packaging-llmwiki"
    assert instance["bundle_id"].startswith("sample-packaging-llmwiki:sha256:")
    assert read_instance_records(state_dir=state_dir) == []


def test_cli_ls_json_reports_health_stale_duplicates_and_overlap(
    monkeypatch,
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / "state"
    root = tmp_path / "vault-a"
    wiki = root / "wiki"
    company = wiki / "company"
    company.mkdir(parents=True)
    records = [
        make_record(pid=101, port=10765, root=wiki, source_id="vault-a", bundle_id="same-bundle"),
        make_record(pid=102, port=11003, root=wiki, source_id="vault-a", bundle_id="same-bundle"),
        make_record(pid=103, port=11001, root=root, source_id="vault-a-root", bundle_id="root"),
        make_record(
            pid=104,
            port=11004,
            root=company,
            source_id="vault-a-company",
            bundle_id="company",
        ),
        make_record(pid=999, port=11099, root=tmp_path / "stale", source_id="stale"),
    ]
    for record in records:
        register_instance(record, state_dir=state_dir)

    monkeypatch.setattr(
        "llmwiki_serve.instances.process_is_running",
        lambda pid: pid != 999,
    )
    monkeypatch.setattr(
        "llmwiki_serve.instances.probe_instance_health",
        lambda record, timeout_seconds: {
            "source_id": record.source_id,
            "bundle_id": record.bundle_id,
            "adapter": record.adapter,
            "implementation": record.implementation,
            "page_count": record.page_count,
            "approved_page_count": record.approved_page_count,
        },
    )

    result = CliRunner().invoke(cli_app, ["ls", "--json", "--state-dir", str(state_dir)])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    instances = {item["port"]: item for item in payload["instances"]}
    assert instances[10765]["status"] == "healthy"
    assert instances[10765]["healthy"] is True
    assert "duplicate-bundle" in instances[10765]["notes"]
    assert "same-root" in instances[10765]["notes"]
    assert "subfolder-root" in instances[10765]["notes"]
    assert "parent-root" in instances[11001]["notes"]
    assert "subfolder-root" in instances[11004]["notes"]
    assert instances[11099]["status"] == "stale"
    assert instances[11099]["stale"] is True

    table = CliRunner().invoke(cli_app, ["ls", "--no-probe", "--state-dir", str(state_dir)])

    assert table.exit_code == 0, table.output
    assert "PID" in table.output
    assert "127.0.0.1:10765" in table.output
    assert str(wiki.resolve()) in table.output
    assert "duplicate-bundle" in table.output

    pruned = CliRunner().invoke(
        cli_app,
        ["ls", "--json", "--state-dir", str(state_dir), "--prune-stale"],
    )

    assert pruned.exit_code == 0, pruned.output
    pruned_payload = json.loads(pruned.output)
    assert any(item["port"] == 11099 and item["pruned"] for item in pruned_payload["instances"])
    assert all(record.record.pid != 999 for record in read_instance_records(state_dir=state_dir))


def test_cli_status_aliases_ls(monkeypatch, tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    root = tmp_path / "wiki"
    root.mkdir()
    register_instance(make_record(pid=os.getpid(), port=8765, root=root), state_dir=state_dir)
    monkeypatch.setattr(
        "llmwiki_serve.instances.probe_instance_health",
        lambda record, timeout_seconds: None,
    )

    result = CliRunner().invoke(cli_app, ["status", "--json", "--state-dir", str(state_dir)])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["instances"][0]["status"] == "unhealthy"
    assert payload["instances"][0]["root"] == str(root.resolve())
    assert instance_registry_dir(state_dir).exists()


def make_record(
    *,
    pid: int,
    port: int,
    root: Path,
    source_id: str = "source",
    bundle_id: str = "bundle",
) -> InstanceRecord:
    return InstanceRecord(
        pid=pid,
        host="127.0.0.1",
        port=port,
        root=str(root.resolve(strict=False)),
        url=f"http://127.0.0.1:{port}",
        source_id=source_id,
        bundle_id=bundle_id,
        adapter="llmwiki-markdown",
        implementation="llmwiki-markdown",
        page_count=67,
        approved_page_count=67,
        started_at="2026-07-28T00:00:00Z",
    )

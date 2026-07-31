from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts.benchmark_adapters import bundle_validator as validator
from scripts.benchmark_adapters import local_corpus_materializer as materializer


def test_materializes_public_provenance_corpus_only_bundle(tmp_path: Path) -> None:
    source_root = create_openwiki_source(tmp_path)
    output_dir = tmp_path / "public-corpus-bundle"
    before = tree_hash(source_root)

    result = materializer.materialize_corpus_bundle(
        public_config(source_root=source_root, output_dir=output_dir, repo_root=tmp_path / "repo")
    )

    bundle = validator.validate_bundle(output_dir)
    rows = read_jsonl(output_dir / "corpus.jsonl")
    provenance_text = (output_dir / "provenance.json").read_text(encoding="utf-8")

    assert result.corpus_count == 3
    assert result.source_mutated is False
    assert tree_hash(source_root) == before
    assert bundle.query_ids == frozenset()
    assert bundle.qrel_count == 0
    assert bundle.evidence_count == 0
    assert bundle.query_metric_eligibility == {}
    assert (output_dir / "queries.jsonl").read_text(encoding="utf-8") == ""
    assert (output_dir / "qrels.jsonl").read_text(encoding="utf-8") == ""
    assert (output_dir / "evidence.jsonl").read_text(encoding="utf-8") == ""
    assert str(source_root) not in provenance_text
    assert "source_path" not in provenance_text
    assert [row["metadata"]["source_path"] for row in rows] == [
        "a-topic.md",
        "quickstart.md",
        "z-topic.md",
    ]


def test_materialization_is_deterministic_for_same_source(tmp_path: Path) -> None:
    source_root = create_openwiki_source(tmp_path)
    first = tmp_path / "bundle-one"
    second = tmp_path / "bundle-two"

    materializer.materialize_corpus_bundle(
        public_config(source_root=source_root, output_dir=first, repo_root=tmp_path / "repo")
    )
    materializer.materialize_corpus_bundle(
        public_config(source_root=source_root, output_dir=second, repo_root=tmp_path / "repo")
    )

    assert (first / "corpus.jsonl").read_text(encoding="utf-8") == (
        second / "corpus.jsonl"
    ).read_text(encoding="utf-8")
    assert (
        read_json(first / "provenance.json")["checksums"]
        == read_json(second / "provenance.json")["checksums"]
    )


def test_materializer_allows_repo_output_only_under_benchmark_workspace(tmp_path: Path) -> None:
    source_root = create_openwiki_source(tmp_path)
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    allowed_output = repo_root / ".llmwiki-work" / "benchmark-adapters" / "bundle"

    materializer.materialize_corpus_bundle(
        public_config(source_root=source_root, output_dir=allowed_output, repo_root=repo_root)
    )

    validator.validate_bundle(allowed_output)

    with pytest.raises(materializer.CorpusMaterializerError, match="benchmark-adapters"):
        materializer.materialize_corpus_bundle(
            public_config(
                source_root=source_root,
                output_dir=repo_root / ".llmwiki-work" / "other" / "bundle",
                repo_root=repo_root,
            )
        )

    with pytest.raises(materializer.CorpusMaterializerError, match="benchmark-adapters"):
        materializer.materialize_corpus_bundle(
            public_config(
                source_root=source_root,
                output_dir=repo_root / "benchmarks" / "bundle",
                repo_root=repo_root,
            )
        )


def test_materializer_refuses_source_nested_or_nonempty_output(tmp_path: Path) -> None:
    source_root = create_openwiki_source(tmp_path)
    nonempty = tmp_path / "nonempty"
    nonempty.mkdir()
    (nonempty / "existing.txt").write_text("existing output\n", encoding="utf-8")

    with pytest.raises(materializer.CorpusMaterializerError, match="outside source_root"):
        materializer.materialize_corpus_bundle(
            public_config(
                source_root=source_root,
                output_dir=source_root / ".llmwiki-work" / "benchmark-adapters" / "bundle",
                repo_root=tmp_path / "repo",
            )
        )

    with pytest.raises(materializer.CorpusMaterializerError, match="not empty"):
        materializer.materialize_corpus_bundle(
            public_config(source_root=source_root, output_dir=nonempty, repo_root=tmp_path / "repo")
        )


def test_materializer_cli_emits_valid_corpus_only_bundle(tmp_path: Path) -> None:
    source_root = create_openwiki_source(tmp_path)
    output_dir = tmp_path / "cli-bundle"

    exit_code = materializer.main(
        [
            "--source-root",
            str(source_root),
            "--output-dir",
            str(output_dir),
            "--bundle-id",
            "synthetic-local-corpus-20260731",
            "--dataset",
            "Synthetic Local Corpus",
            "--source-url",
            "https://example.invalid/synthetic/local-corpus",
            "--source-revision",
            "0123456789abcdef0123456789abcdef01234567",
            "--license-spdx",
            "Apache-2.0",
            "--license-url",
            "https://example.invalid/licenses/apache-2.0",
            "--license-verified-date",
            "2026-07-31",
            "--attribution",
            "Synthetic fixture attribution.",
            "--redistribution-policy",
            "redistributable",
            "--public-report-policy",
            "allowed-with-attribution",
            "--repo-root",
            str(tmp_path / "repo"),
        ]
    )

    assert exit_code == 0
    result = validator.validate_bundle(output_dir)
    assert result.query_metric_eligibility == {}


def public_config(
    *,
    source_root: Path,
    output_dir: Path,
    repo_root: Path,
) -> materializer.MaterializeCorpusConfig:
    return materializer.MaterializeCorpusConfig(
        source_root=source_root,
        output_dir=output_dir,
        bundle_id="synthetic-local-corpus-20260731",
        dataset="Synthetic Local Corpus",
        source_url="https://example.invalid/synthetic/local-corpus",
        source_revision="0123456789abcdef0123456789abcdef01234567",
        source_release=None,
        license_spdx="Apache-2.0",
        license_url="https://example.invalid/licenses/apache-2.0",
        license_verified_date="2026-07-31",
        attribution="Synthetic fixture attribution.",
        redistribution_policy="redistributable",
        public_report_policy="allowed-with-attribution",
        repo_root=repo_root,
    )


def create_openwiki_source(tmp_path: Path) -> Path:
    source_root = tmp_path / "openwiki"
    source_root.mkdir()
    write_markdown(
        source_root / "quickstart.md",
        """
---
wiki_title: Synthetic Public Corpus
review_state: approved
---
# Synthetic Public Corpus

This generated fixture is used for corpus-only benchmark materialization.
""",
    )
    write_markdown(
        source_root / "z-topic.md",
        """
---
title: Z Topic
review_state: approved
---
# Z Topic

Z topic body.
""",
    )
    write_markdown(
        source_root / "a-topic.md",
        """
---
title: A Topic
review_state: approved
---
# A Topic

A topic body.
""",
    )
    return source_root


def write_markdown(path: Path, content: str) -> None:
    path.write_text(content.strip() + "\n", encoding="utf-8")


def read_json(path: Path) -> dict[str, object]:
    parsed = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(parsed, dict)
    return parsed


def read_jsonl(path: Path) -> list[dict[str, object]]:
    rows = [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    assert all(isinstance(row, dict) for row in rows)
    return rows


def tree_hash(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()

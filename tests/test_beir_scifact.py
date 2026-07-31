from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest

from scripts.benchmark_adapters import bundle_validator as validator
from scripts.benchmark_adapters.beir_scifact import (
    OFFICIAL_SCIFACT_CORPUS_COUNT,
    OFFICIAL_SCIFACT_QREL_COUNT,
    OFFICIAL_SCIFACT_SOURCE_QUERY_COUNT,
    OFFICIAL_SCIFACT_TEST_QUERY_COUNT,
    BeirScifactMaterializerError,
    MaterializeBeirScifactResult,
    materialize_beir_scifact,
    run,
)

ARCHIVE_SHA256 = "a" * 64


def test_materialize_beir_scifact_is_deterministic_and_schema_valid(tmp_path: Path) -> None:
    input_dir = make_scifact_fixture(tmp_path / "input")
    input_digest_before = tree_digest(input_dir)

    first = materialize_synthetic_beir_scifact(input_dir, tmp_path / "out-first")
    second = materialize_synthetic_beir_scifact(input_dir, tmp_path / "out-second")

    assert tree_snapshot(first.output_dir) == tree_snapshot(second.output_dir)
    assert tree_digest(input_dir) == input_digest_before
    assert first.input_before_sha256 == input_digest_before
    assert first.input_after_sha256 == input_digest_before
    assert not first.input_mutated

    bundle_dir = first.bundle_dir
    result = validator.validate_bundle(bundle_dir)
    assert result.corpus_ids == frozenset({"doc/2", "doc:1"})
    assert result.query_ids == frozenset({"q1", "q2"})
    assert result.qrel_count == 2
    assert result.evidence_count == 0
    assert (bundle_dir / "evidence.jsonl").read_text(encoding="utf-8") == ""

    corpus_rows = read_jsonl(bundle_dir / "corpus.jsonl")
    assert [row["corpus_id"] for row in corpus_rows] == ["doc/2", "doc:1"]
    assert [row["metadata"] for row in corpus_rows] == [
        {"beir_id": "doc/2", "beir_metadata": {}},
        {
            "beir_id": "doc:1",
            "beir_metadata": {"nested": {"rank": 1}, "source": "synthetic"},
        },
    ]

    query_rows = read_jsonl(bundle_dir / "queries.jsonl")
    assert [row["query_id"] for row in query_rows] == ["q1", "q2"]
    assert "q-train" not in {row["query_id"] for row in query_rows}
    assert all(row["answerability"] == "unknown" for row in query_rows)
    assert all(row["source_split"] == "test" for row in query_rows)
    assert all(row["evaluation_split"] == "holdout" for row in query_rows)
    assert all(row["answers"] == [] for row in query_rows)
    assert all(row["tags"] == ["retrieval"] for row in query_rows)

    qrel_rows = read_jsonl(bundle_dir / "qrels.jsonl")
    assert qrel_rows == [
        {"corpus_id": "doc:1", "query_id": "q1", "relevance": 1},
        {"corpus_id": "doc/2", "query_id": "q2", "relevance": 1},
    ]

    provenance = json.loads((bundle_dir / "provenance.json").read_text(encoding="utf-8"))
    assert provenance["source_revision"] == f"sha256:{ARCHIVE_SHA256}"
    assert (
        provenance["source_url"]
        == "https://public.ukp.informatik.tu-darmstadt.de/thakur/BEIR/datasets/scifact.zip"
    )
    assert provenance["component_licenses"] == [
        {
            "attribution": "BEIR (Thakur et al.)",
            "component": "BEIR code/format",
            "license_spdx": "Apache-2.0",
            "license_url": "https://github.com/beir-cellar/beir/blob/main/LICENSE",
            "license_verified_date": "2026-07-31",
            "public_report_policy": "allowed-with-attribution",
            "redistribution_policy": "redistributable",
        },
        {
            "attribution": "SciFact (Wadden et al.)",
            "component": "SciFact claims/queries and evidence/qrels",
            "license_spdx": "CC-BY-4.0",
            "license_url": "https://github.com/allenai/scifact/blob/master/LICENSE.md",
            "license_verified_date": "2026-07-31",
            "public_report_policy": "allowed-with-attribution",
            "redistribution_policy": "allowed-with-attribution",
        },
        {
            "attribution": "SciFact/S2ORC",
            "component": "SciFact corpus abstracts",
            "license_spdx": "ODC-By-1.0",
            "license_url": "https://github.com/allenai/scifact/blob/master/LICENSE.md",
            "license_verified_date": "2026-07-31",
            "public_report_policy": "allowed-with-attribution",
            "redistribution_policy": "allowed-with-attribution",
        },
    ]

    public_gate = validator.evaluate_public_release_gate(bundle_dir)
    bundle_gate = validator.evaluate_public_release_gate(bundle_dir, mode="bundle-release")
    assert public_gate.passed
    assert public_gate.blockers == ()
    assert bundle_gate.passed
    assert bundle_gate.blockers == ()

    wiki_files = sorted(path.name for path in first.wiki_dir.glob("*.md"))
    assert len(wiki_files) == 2
    assert all("/" not in name and ":" not in name for name in wiki_files)
    multiline_title_markdown = (first.wiki_dir / "line-one-line-two.md").read_text(encoding="utf-8")
    assert corpus_rows[1]["title"] == "Line One\nLine Two"
    assert 'title: "Line One\\nLine Two"' in multiline_title_markdown
    assert "review_state: approved" in multiline_title_markdown
    assert "# Line One Line Two\n\n" in multiline_title_markdown
    assert "# Line One\nLine Two" not in multiline_title_markdown


def test_materialize_beir_scifact_rejects_bad_qrel(tmp_path: Path) -> None:
    input_dir = make_scifact_fixture(
        tmp_path / "input",
        qrels=[("q-missing", "doc:1", "1")],
    )

    with pytest.raises(BeirScifactMaterializerError, match="unknown query_id"):
        materialize_synthetic_beir_scifact(input_dir, tmp_path / "out")


def test_materialize_beir_scifact_allows_train_only_queries_without_test_qrels(
    tmp_path: Path,
) -> None:
    input_dir = make_scifact_fixture(tmp_path / "input", qrels=[("q1", "doc:1", "1")])

    result = materialize_synthetic_beir_scifact(input_dir, tmp_path / "out")

    assert result.query_count == 1
    assert [row["query_id"] for row in read_jsonl(result.bundle_dir / "queries.jsonl")] == ["q1"]
    assert read_jsonl(result.bundle_dir / "qrels.jsonl") == [
        {"corpus_id": "doc:1", "query_id": "q1", "relevance": 1}
    ]


def test_materialize_beir_scifact_rejects_zero_only_test_qrels(tmp_path: Path) -> None:
    input_dir = make_scifact_fixture(
        tmp_path / "input",
        qrels=[("q1", "doc:1", "1"), ("q2", "doc/2", "0")],
    )

    with pytest.raises(BeirScifactMaterializerError, match="missing or zero-only"):
        materialize_synthetic_beir_scifact(input_dir, tmp_path / "out")


def test_materialize_beir_scifact_rejects_qrel_unknown_corpus_id(tmp_path: Path) -> None:
    input_dir = make_scifact_fixture(
        tmp_path / "input",
        qrels=[("q1", "missing-doc", "1")],
    )

    with pytest.raises(BeirScifactMaterializerError, match="unknown corpus_id"):
        materialize_synthetic_beir_scifact(input_dir, tmp_path / "out")


@pytest.mark.parametrize("file_name", ["corpus.jsonl", "queries.jsonl"])
def test_materialize_beir_scifact_rejects_non_object_source_metadata(
    tmp_path: Path,
    file_name: str,
) -> None:
    input_dir = make_scifact_fixture(tmp_path / "input")
    rows = read_jsonl(input_dir / file_name)
    rows[0]["metadata"] = ["not", "an", "object"]
    write_jsonl(input_dir / file_name, rows)

    with pytest.raises(BeirScifactMaterializerError, match="metadata must be an object"):
        materialize_synthetic_beir_scifact(input_dir, tmp_path / "out")


@pytest.mark.parametrize("file_name", ["corpus.jsonl", "queries.jsonl"])
def test_materialize_beir_scifact_rejects_unknown_source_fields(
    tmp_path: Path,
    file_name: str,
) -> None:
    input_dir = make_scifact_fixture(tmp_path / "input")
    rows = read_jsonl(input_dir / file_name)
    rows[0]["unexpected"] = "not official BEIR SciFact shape"
    write_jsonl(input_dir / file_name, rows)

    with pytest.raises(BeirScifactMaterializerError, match="unknown fields"):
        materialize_synthetic_beir_scifact(input_dir, tmp_path / "out")


@pytest.mark.parametrize(
    ("file_name", "duplicate_id", "match"),
    [
        ("corpus.jsonl", "doc:1", "duplicate corpus _id"),
        ("queries.jsonl", "q1", "duplicate query _id"),
    ],
)
def test_materialize_beir_scifact_rejects_duplicate_source_ids(
    tmp_path: Path,
    file_name: str,
    duplicate_id: str,
    match: str,
) -> None:
    input_dir = make_scifact_fixture(tmp_path / "input")
    rows = read_jsonl(input_dir / file_name)
    rows[1]["_id"] = duplicate_id
    write_jsonl(input_dir / file_name, rows)

    with pytest.raises(BeirScifactMaterializerError, match=match):
        materialize_synthetic_beir_scifact(input_dir, tmp_path / "out")


def test_materialize_beir_scifact_enforces_official_invariants_by_default(
    tmp_path: Path,
) -> None:
    input_dir = make_scifact_fixture(tmp_path / "input")

    with pytest.raises(BeirScifactMaterializerError, match="official SciFact canonical"):
        materialize_beir_scifact(input_dir, tmp_path / "out", ARCHIVE_SHA256)


def test_materialize_beir_scifact_accepts_canonical_official_full_run_shape(
    tmp_path: Path,
) -> None:
    input_dir = make_official_canonical_scifact_fixture(tmp_path / "input")

    result = materialize_beir_scifact(input_dir, tmp_path / "out", ARCHIVE_SHA256)

    assert result.corpus_count == OFFICIAL_SCIFACT_CORPUS_COUNT
    assert result.query_count == OFFICIAL_SCIFACT_TEST_QUERY_COUNT
    assert result.qrel_count == OFFICIAL_SCIFACT_QREL_COUNT
    assert len(read_jsonl(result.bundle_dir / "queries.jsonl")) == OFFICIAL_SCIFACT_TEST_QUERY_COUNT
    assert validator.validate_bundle(result.bundle_dir).qrel_count == OFFICIAL_SCIFACT_QREL_COUNT


def test_materialize_beir_scifact_rejects_noncanonical_relevance_values(
    tmp_path: Path,
) -> None:
    input_dir = make_official_canonical_scifact_fixture(
        tmp_path / "input",
        qrel_score_overrides={0: "2"},
    )

    with pytest.raises(BeirScifactMaterializerError, match="relevance_values"):
        materialize_beir_scifact(input_dir, tmp_path / "out", ARCHIVE_SHA256)


def test_materialize_beir_scifact_rejects_nonempty_output(tmp_path: Path) -> None:
    input_dir = make_scifact_fixture(tmp_path / "input")
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    (output_dir / "existing.txt").write_text("preserve me\n", encoding="utf-8")

    with pytest.raises(BeirScifactMaterializerError, match="not empty"):
        materialize_synthetic_beir_scifact(input_dir, output_dir)

    assert (output_dir / "existing.txt").read_text(encoding="utf-8") == "preserve me\n"


def test_materialize_beir_scifact_rejects_output_inside_input(tmp_path: Path) -> None:
    input_dir = make_scifact_fixture(tmp_path / "input")

    with pytest.raises(BeirScifactMaterializerError, match="outside input_dir"):
        materialize_synthetic_beir_scifact(input_dir, input_dir / "out")


def test_materialize_beir_scifact_enforces_repo_output_workspace(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    input_dir = make_scifact_fixture(repo_root / "input")

    with pytest.raises(BeirScifactMaterializerError, match="benchmark-adapters"):
        materialize_synthetic_beir_scifact(
            input_dir,
            repo_root / "out",
            repo_root=repo_root,
        )

    result = materialize_synthetic_beir_scifact(
        input_dir,
        repo_root / ".llmwiki-work" / "benchmark-adapters" / "scifact",
        repo_root=repo_root,
    )

    validator.validate_bundle(result.bundle_dir)


def test_cli_materializes_synthetic_fixture_with_test_opt_out(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    input_dir = make_scifact_fixture(tmp_path / "input")
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    output_dir = repo_root / ".llmwiki-work" / "benchmark-adapters" / "scifact-cli"

    exit_code = run(
        [
            "--input-dir",
            str(input_dir),
            "--output-dir",
            str(output_dir),
            "--archive-sha256",
            ARCHIVE_SHA256,
            "--repo-root",
            str(repo_root),
        ],
        enforce_official_canonical_invariants=False,
    )

    captured = capsys.readouterr()
    public = json.loads(captured.out)
    assert exit_code == 0
    assert captured.err == ""
    assert public == {
        "adapter": {"name": "beir-scifact", "version": "0.1.0"},
        "archive_sha256": ARCHIVE_SHA256,
        "bundle_created": True,
        "corpus_count": 2,
        "dataset": "beir-scifact",
        "input_mutated": False,
        "local_outputs": {
            "bundle_dir": "local-only",
            "output_dir": "local-only",
            "wiki_dir": "local-only",
        },
        "qrel_count": 2,
        "query_count": 2,
    }
    assert validator.validate_bundle(output_dir / "bundle").qrel_count == 2
    assert (output_dir / "wiki").is_dir()


def test_cli_enforces_official_canonical_invariants_by_default(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    input_dir = make_scifact_fixture(tmp_path / "input")
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    output_dir = repo_root / ".llmwiki-work" / "benchmark-adapters" / "scifact-cli"

    exit_code = run(
        [
            "--input-dir",
            str(input_dir),
            "--output-dir",
            str(output_dir),
            "--archive-sha256",
            ARCHIVE_SHA256,
            "--repo-root",
            str(repo_root),
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 2
    assert captured.out == ""
    assert "official SciFact canonical invariants failed" in captured.err
    assert not output_dir.exists()


def test_cli_rejects_unsafe_output_aliasing(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo_root = tmp_path / "repo"
    input_dir = make_scifact_fixture(repo_root / ".llmwiki-work" / "benchmark-adapters" / "input")

    exit_code = run(
        [
            "--input-dir",
            str(input_dir),
            "--output-dir",
            str(input_dir / "out"),
            "--archive-sha256",
            ARCHIVE_SHA256,
            "--repo-root",
            str(repo_root),
        ],
        enforce_official_canonical_invariants=False,
    )

    captured = capsys.readouterr()
    assert exit_code == 2
    assert captured.out == ""
    assert "output_dir must be outside input_dir" in captured.err


def test_cli_success_stdout_does_not_emit_raw_corpus_or_query_text(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    input_dir = make_scifact_fixture(tmp_path / "input")
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    exit_code = run(
        [
            "--input-dir",
            str(input_dir),
            "--output-dir",
            str(repo_root / ".llmwiki-work" / "benchmark-adapters" / "scifact-cli"),
            "--archive-sha256",
            ARCHIVE_SHA256,
            "--repo-root",
            str(repo_root),
        ],
        enforce_official_canonical_invariants=False,
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "First scientific claim evidence" not in captured.out
    assert "Second scientific claim evidence" not in captured.out
    assert "Which evidence supports claim" not in captured.out
    assert "Line One" not in captured.out
    assert str(tmp_path) not in captured.out


def materialize_synthetic_beir_scifact(
    input_dir: Path,
    output_dir: Path,
    *,
    repo_root: Path | None = None,
) -> MaterializeBeirScifactResult:
    if repo_root is None:
        return materialize_beir_scifact(
            input_dir,
            output_dir,
            ARCHIVE_SHA256,
            enforce_official_canonical_invariants=False,
        )
    return materialize_beir_scifact(
        input_dir,
        output_dir,
        ARCHIVE_SHA256,
        enforce_official_canonical_invariants=False,
        repo_root=repo_root,
    )


def make_scifact_fixture(
    root: Path,
    *,
    qrels: list[tuple[str, str, str]] | None = None,
) -> Path:
    root.mkdir(parents=True)
    write_jsonl(
        root / "corpus.jsonl",
        [
            {
                "_id": "doc:1",
                "metadata": {"nested": {"rank": 1}, "source": "synthetic"},
                "title": "Line One\nLine Two",
                "text": "First scientific claim evidence.",
            },
            {
                "_id": "doc/2",
                "metadata": {},
                "title": "Shared / Title",
                "text": "Second scientific claim evidence.",
            },
        ],
    )
    write_jsonl(
        root / "queries.jsonl",
        [
            {
                "_id": "q2",
                "metadata": {"source_split": "test"},
                "text": "Which evidence supports claim two?",
            },
            {
                "_id": "q-train",
                "metadata": {"source_split": "train"},
                "text": "Which train-only claim is not in test qrels?",
            },
            {
                "_id": "q1",
                "metadata": {},
                "text": "Which evidence supports claim one?",
            },
        ],
    )
    qrels_dir = root / "qrels"
    qrels_dir.mkdir()
    qrel_rows = qrels or [("q2", "doc/2", "1"), ("q1", "doc:1", "1")]
    qrels_text = "query-id\tcorpus-id\tscore\n" + "".join(
        f"{query_id}\t{corpus_id}\t{score}\n" for query_id, corpus_id, score in qrel_rows
    )
    (qrels_dir / "test.tsv").write_text(qrels_text, encoding="utf-8")
    return root


def make_official_canonical_scifact_fixture(
    root: Path,
    *,
    qrel_score_overrides: Mapping[int, str] | None = None,
) -> Path:
    root.mkdir(parents=True)
    write_jsonl(
        root / "corpus.jsonl",
        [
            {
                "_id": f"doc-{index:04d}",
                "metadata": {},
                "text": f"Official SciFact corpus text {index}.",
                "title": f"Official Doc {index:04d}",
            }
            for index in range(OFFICIAL_SCIFACT_CORPUS_COUNT)
        ],
    )
    write_jsonl(
        root / "queries.jsonl",
        [
            {
                "_id": f"q-{index:04d}",
                "metadata": {},
                "text": f"Official SciFact query {index}?",
            }
            for index in range(OFFICIAL_SCIFACT_SOURCE_QUERY_COUNT)
        ],
    )
    qrels_dir = root / "qrels"
    qrels_dir.mkdir()
    qrel_rows: list[tuple[str, str, str]] = [
        (f"q-{index:04d}", f"doc-{index:04d}", "1")
        for index in range(OFFICIAL_SCIFACT_TEST_QUERY_COUNT)
    ]
    qrel_rows.extend(
        (f"q-{index:04d}", f"doc-{index + OFFICIAL_SCIFACT_TEST_QUERY_COUNT:04d}", "1")
        for index in range(OFFICIAL_SCIFACT_QREL_COUNT - OFFICIAL_SCIFACT_TEST_QUERY_COUNT)
    )
    for qrel_index, score in (qrel_score_overrides or {}).items():
        query_id, corpus_id, _score = qrel_rows[qrel_index]
        qrel_rows[qrel_index] = (query_id, corpus_id, score)
    qrels_text = "query-id\tcorpus-id\tscore\n" + "".join(
        f"{query_id}\t{corpus_id}\t{score}\n" for query_id, corpus_id, score in qrel_rows
    )
    (qrels_dir / "test.tsv").write_text(qrels_text, encoding="utf-8")
    return root


def write_jsonl(path: Path, rows: Sequence[Mapping[str, object]]) -> None:
    path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(candidate for candidate in root.rglob("*") if candidate.is_file()):
        relative = path.relative_to(root)
        digest.update(relative.as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def tree_snapshot(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(candidate for candidate in root.rglob("*") if candidate.is_file())
    }

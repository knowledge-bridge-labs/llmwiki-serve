from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from scripts.benchmark_adapters import bundle_validator as validator

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = ROOT / "tests" / "fixtures" / "benchmark_bundles"
VALID_BUNDLE = FIXTURE_ROOT / "valid"
NONCOMMERCIAL_BUNDLE = FIXTURE_ROOT / "invalid_noncommercial"


def test_valid_synthetic_bundle_passes_schema_and_public_report_gate() -> None:
    result = validator.validate_bundle(VALID_BUNDLE)

    assert result.corpus_ids == frozenset({"doc-alpha", "doc-beta"})
    assert result.query_ids == frozenset({"q-answerable", "q-unanswerable", "q-unknown"})
    assert result.qrel_count == 3
    assert result.evidence_count == 2

    unknown = result.query_metric_eligibility["q-unknown"]
    assert unknown.retrieval is True
    assert unknown.answerability is False
    assert unknown.abstention is False
    assert unknown.negative_final_answer_false_positive is False

    gate = validator.evaluate_public_release_gate(VALID_BUNDLE, mode="public-report")
    assert gate.passed is True
    assert gate.blockers == ()


def test_qrels_do_not_synthesize_evidence(tmp_path: Path) -> None:
    bundle = copy_bundle(tmp_path)
    (bundle / "evidence.jsonl").write_text("", encoding="utf-8")
    refresh_provenance_checksums(bundle)

    result = validator.validate_bundle(bundle)

    assert result.qrel_count == 3
    assert result.evidence_count == 0
    assert result.evidence_ids == frozenset()


def test_corpus_only_bundle_is_valid_when_query_label_files_are_empty(tmp_path: Path) -> None:
    bundle = copy_bundle(tmp_path)
    for file_name in ("queries.jsonl", "qrels.jsonl", "evidence.jsonl"):
        (bundle / file_name).write_text("", encoding="utf-8")
    refresh_provenance_checksums(bundle)

    result = validator.validate_bundle(bundle)

    assert result.query_ids == frozenset()
    assert result.qrel_count == 0
    assert result.evidence_count == 0
    assert result.query_metric_eligibility == {}


def test_empty_queries_reject_nonempty_qrels_or_evidence(tmp_path: Path) -> None:
    bundle = copy_bundle(tmp_path)
    (bundle / "queries.jsonl").write_text("", encoding="utf-8")
    refresh_provenance_checksums(bundle)

    with pytest.raises(validator.BundleValidationError, match="unknown query_id"):
        validator.validate_bundle(bundle)

    bundle = copy_bundle(tmp_path / "evidence")
    (bundle / "queries.jsonl").write_text("", encoding="utf-8")
    (bundle / "qrels.jsonl").write_text("", encoding="utf-8")
    refresh_provenance_checksums(bundle)

    with pytest.raises(validator.BundleValidationError, match="unknown query_id"):
        validator.validate_bundle(bundle)


def test_retrieval_eligibility_requires_positive_relevance(tmp_path: Path) -> None:
    bundle = copy_bundle(tmp_path)
    replace_jsonl(
        bundle / "qrels.jsonl",
        [{"query_id": "q-unanswerable", "corpus_id": "doc-beta", "relevance": 0}],
    )
    (bundle / "evidence.jsonl").write_text("", encoding="utf-8")
    refresh_provenance_checksums(bundle)

    result = validator.validate_bundle(bundle)

    assert result.query_metric_eligibility["q-unanswerable"].retrieval is False


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda bundle: replace_jsonl(
                bundle / "corpus.jsonl",
                [
                    {
                        "corpus_id": "doc-alpha",
                        "text": "Synthetic duplicate.",
                        "title": "Duplicate",
                        "metadata": {},
                    }
                ],
                append=True,
            ),
            "duplicate corpus_id",
        ),
        (
            lambda bundle: replace_jsonl(
                bundle / "qrels.jsonl",
                [{"query_id": "q-answerable", "corpus_id": "missing-doc", "relevance": 1}],
            ),
            "unknown corpus_id",
        ),
        (
            lambda bundle: replace_jsonl(
                bundle / "evidence.jsonl",
                [
                    {
                        "evidence_id": "ev-alpha",
                        "query_id": "q-answerable",
                        "corpus_id": "doc-alpha",
                        "locator": {"granularity": "document"},
                        "required_group": "hop-1",
                        "hop_index": 0,
                        "depends_on": ["missing-ev"],
                        "supports_claim_ids": ["claim-alpha"],
                    }
                ],
            ),
            "depends_on references unknown",
        ),
        (
            lambda bundle: replace_jsonl(
                bundle / "evidence.jsonl",
                [
                    {
                        "evidence_id": "ev-alpha",
                        "query_id": "q-answerable",
                        "corpus_id": "doc-alpha",
                        "locator": {"granularity": "document"},
                        "required_group": "hop-1",
                        "hop_index": 0,
                        "depends_on": [],
                        "supports_claim_ids": ["claim-alpha"],
                    },
                    {
                        "evidence_id": "ev-unknown",
                        "query_id": "q-unknown",
                        "corpus_id": "doc-beta",
                        "locator": {"granularity": "document"},
                        "required_group": "hop-1",
                        "hop_index": 0,
                        "depends_on": ["ev-alpha"],
                        "supports_claim_ids": [],
                    },
                ],
            ),
            "different query",
        ),
        (
            lambda bundle: replace_jsonl(
                bundle / "evidence.jsonl",
                [
                    {
                        "evidence_id": "ev-alpha",
                        "query_id": "q-answerable",
                        "corpus_id": "doc-alpha",
                        "locator": {"granularity": "document"},
                        "required_group": "hop-1",
                        "hop_index": 0,
                        "depends_on": ["ev-beta"],
                        "supports_claim_ids": ["claim-alpha"],
                    },
                    {
                        "evidence_id": "ev-beta",
                        "query_id": "q-answerable",
                        "corpus_id": "doc-beta",
                        "locator": {"granularity": "document"},
                        "required_group": "hop-2",
                        "hop_index": 1,
                        "depends_on": ["ev-alpha"],
                        "supports_claim_ids": ["claim-alpha"],
                    },
                ],
            ),
            "contains a cycle",
        ),
        (
            lambda bundle: replace_jsonl(
                bundle / "evidence.jsonl",
                [
                    {
                        "evidence_id": "ev-alpha",
                        "query_id": "q-answerable",
                        "corpus_id": "doc-alpha",
                        "locator": {"granularity": "document"},
                        "required_group": "hop-1",
                        "hop_index": 0,
                        "depends_on": ["ev-alpha"],
                        "supports_claim_ids": ["claim-alpha"],
                    }
                ],
            ),
            "must not reference itself",
        ),
        (
            lambda bundle: replace_jsonl(
                bundle / "evidence.jsonl",
                [
                    {
                        "evidence_id": "ev-alpha",
                        "query_id": "q-answerable",
                        "corpus_id": "doc-alpha",
                        "locator": {"granularity": "document"},
                        "required_group": "hop-1",
                        "hop_index": 0,
                        "depends_on": [],
                        "supports_claim_ids": ["missing-claim"],
                    }
                ],
            ),
            "unknown claim id",
        ),
    ],
)
def test_ids_and_references_are_validated(
    tmp_path: Path,
    mutate: Callable[[Path], None],
    message: str,
) -> None:
    bundle = copy_bundle(tmp_path)
    mutate(bundle)
    refresh_provenance_checksums(bundle)

    with pytest.raises(validator.BundleValidationError, match=message):
        validator.validate_bundle(bundle)


@pytest.mark.parametrize(
    ("locator", "message"),
    [
        ({"granularity": "char_span", "start": 12, "end": 3}, "start < end"),
        ({"granularity": "char_span", "start": 0, "end": 10_000}, "text length"),
        ({"granularity": "token_span", "start": -1, "end": 3}, "start < end"),
        ({"granularity": "section"}, "section must be a non-empty string"),
        ({"granularity": "paragraph", "paragraph": -1}, "non-negative integer"),
        ({"granularity": "passage"}, "passage must be a non-empty string"),
        ({"granularity": "document", "start": 1, "end": 2}, "only valid for span"),
        ({"granularity": "line_span", "start": 1, "end": 2}, "granularity"),
    ],
)
def test_locator_granularity_and_bounds_are_validated(
    tmp_path: Path,
    locator: dict[str, object],
    message: str,
) -> None:
    bundle = copy_bundle(tmp_path)
    replace_jsonl(
        bundle / "evidence.jsonl",
        [
            {
                "evidence_id": "ev-alpha",
                "query_id": "q-answerable",
                "corpus_id": "doc-alpha",
                "locator": locator,
                "required_group": "hop-1",
                "hop_index": 0,
                "depends_on": [],
                "supports_claim_ids": ["claim-alpha"],
            }
        ],
    )
    refresh_provenance_checksums(bundle)

    with pytest.raises(validator.BundleValidationError, match=message):
        validator.validate_bundle(bundle)


def test_passage_locator_requires_explicit_passage_identifier(tmp_path: Path) -> None:
    bundle = copy_bundle(tmp_path)
    replace_jsonl(
        bundle / "evidence.jsonl",
        [
            {
                "evidence_id": "ev-alpha",
                "query_id": "q-answerable",
                "corpus_id": "doc-alpha",
                "locator": {"granularity": "passage", "passage": "passage-1"},
                "required_group": "hop-1",
                "hop_index": 0,
                "depends_on": [],
                "supports_claim_ids": ["claim-alpha"],
            }
        ],
    )
    refresh_provenance_checksums(bundle)

    result = validator.validate_bundle(bundle)

    assert result.evidence_ids == frozenset({"ev-alpha"})


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda provenance: provenance.update(
                {"dataset": "C:\\Users\\example-user\\private\\dataset"}
            ),
            "local paths",
        ),
        (
            lambda provenance: provenance.update({"dataset": "/mnt/datasets/benchmark"}),
            "local paths",
        ),
        (
            lambda provenance: provenance.update({"dataset": "/workspace/benchmark"}),
            "local paths",
        ),
        (
            lambda provenance: provenance.update({"source_url": "http://127.0.0.1:8000/data"}),
            "private URLs",
        ),
        (
            lambda provenance: provenance.update({"source_revision": "main"}),
            "resolved immutable revision",
        ),
        (
            lambda provenance: provenance["checksums"].pop("qrels.jsonl"),
            "checksums missing entries",
        ),
        (
            lambda provenance: provenance["adapter"].pop("version"),
            "missing required fields",
        ),
        (
            lambda provenance: provenance.update({"component_licenses": []}),
            "must not be empty",
        ),
        (
            lambda provenance: provenance["component_licenses"][0].update({"attribution": ""}),
            "attribution must be a non-empty string",
        ),
    ],
)
def test_public_provenance_schema_rejects_unsafe_or_incomplete_metadata(
    tmp_path: Path,
    mutate: Callable[[dict[str, Any]], object],
    message: str,
) -> None:
    bundle = copy_bundle(tmp_path)
    provenance = read_json(bundle / "provenance.json")
    mutate(provenance)
    write_json(bundle / "provenance.json", provenance)

    with pytest.raises(validator.BundleValidationError, match=message):
        validator.validate_bundle(bundle)


def test_public_provenance_allows_local_path_words_inside_public_url_paths(
    tmp_path: Path,
) -> None:
    bundle = copy_bundle(tmp_path)
    provenance = read_json(bundle / "provenance.json")
    provenance["source_url"] = "https://example.invalid/mnt/workspace/dataset"
    provenance["component_licenses"][0]["license_url"] = (
        "https://example.invalid/workspace/licenses/apache-2.0"
    )
    write_json(bundle / "provenance.json", provenance)

    validator.validate_bundle(bundle)


def test_checksum_mismatch_is_rejected(tmp_path: Path) -> None:
    bundle = copy_bundle(tmp_path)
    with (bundle / "corpus.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "corpus_id": "doc-gamma",
                    "text": "Synthetic gamma passage.",
                    "title": "Gamma",
                    "metadata": {"dataset": "synthetic"},
                },
                sort_keys=True,
            )
            + "\n"
        )

    with pytest.raises(validator.BundleValidationError, match="checksums.corpus.jsonl mismatch"):
        validator.validate_bundle(bundle)


def test_noncommercial_and_unknown_license_components_fail_release_gate(tmp_path: Path) -> None:
    validator.validate_bundle(NONCOMMERCIAL_BUNDLE)

    gate = validator.evaluate_public_release_gate(NONCOMMERCIAL_BUNDLE, mode="public-report")

    assert gate.passed is False
    assert any("non-commercial" in blocker for blocker in gate.blockers)
    assert "does not make legal determinations" in gate.disclaimer

    bundle = copy_bundle(tmp_path)
    provenance = read_json(bundle / "provenance.json")
    provenance["component_licenses"][0]["license_spdx"] = "NOASSERTION"
    write_json(bundle / "provenance.json", provenance)

    unknown_gate = validator.evaluate_public_release_gate(bundle, mode="public-report")
    assert unknown_gate.passed is False
    assert any("unknown or unclear license" in blocker for blocker in unknown_gate.blockers)

    bundle = copy_bundle(tmp_path / "expression")
    provenance = read_json(bundle / "provenance.json")
    provenance["component_licenses"][0]["license_spdx"] = "Apache-2.0 AND NOASSERTION"
    write_json(bundle / "provenance.json", provenance)

    expression_gate = validator.evaluate_public_release_gate(bundle, mode="public-report")
    assert expression_gate.passed is False
    assert any("unknown or unclear license" in blocker for blocker in expression_gate.blockers)

    bundle = copy_bundle(tmp_path / "licenseref")
    provenance = read_json(bundle / "provenance.json")
    provenance["component_licenses"][0]["license_spdx"] = "LicenseRef-custom-data"
    write_json(bundle / "provenance.json", provenance)

    licenseref_gate = validator.evaluate_public_release_gate(bundle, mode="public-report")
    assert licenseref_gate.passed is False
    assert any("unknown or unclear license" in blocker for blocker in licenseref_gate.blockers)


def test_unclear_public_report_policy_is_a_gate_failure_not_schema_failure(
    tmp_path: Path,
) -> None:
    bundle = copy_bundle(tmp_path)
    provenance = read_json(bundle / "provenance.json")
    provenance["component_licenses"][0]["public_report_policy"] = "unclear"
    write_json(bundle / "provenance.json", provenance)

    validator.validate_bundle(bundle)
    gate = validator.evaluate_public_release_gate(bundle, mode="public-report")

    assert gate.passed is False
    assert any("public-report policy is unknown or unclear" in blocker for blocker in gate.blockers)


def test_run_manifest_allows_local_paths_only_in_local_only_locations(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    local_manifest = (
        validator.default_benchmark_workspace(repo_root)
        / "synthetic-valid-2026-07-31"
        / "run-manifest.json"
    )
    local_manifest.parent.mkdir(parents=True)
    write_json(
        local_manifest,
        {
            "cache_dir": "C:\\Users\\example-user\\.cache\\llmwiki-benchmark",
            "output_dir": str(repo_root / ".llmwiki-work" / "benchmark-adapters" / "out"),
            "seed": 20260731,
        },
    )

    validator.validate_local_run_manifest(local_manifest, repo_root=repo_root)

    committed_manifest = repo_root / "benchmarks" / "run-manifest.json"
    committed_manifest.parent.mkdir()
    write_json(committed_manifest, {"cache_dir": "C:\\Users\\example-user\\.cache"})

    with pytest.raises(validator.BundleValidationError, match="under .llmwiki-work"):
        validator.validate_local_run_manifest(committed_manifest, repo_root=repo_root)


def test_run_manifest_cli_requires_repo_root(tmp_path: Path) -> None:
    manifest = tmp_path / "run-manifest.json"
    write_json(manifest, {"cache_dir": "C:\\Users\\example-user\\.cache"})

    with pytest.raises(SystemExit):
        validator.parse_args(["validate-run-manifest", "--path", str(manifest)])


def copy_bundle(tmp_path: Path, source: Path = VALID_BUNDLE) -> Path:
    bundle = tmp_path / source.name
    shutil.copytree(source, bundle)
    return bundle


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def replace_jsonl(path: Path, rows: list[dict[str, Any]], *, append: bool = False) -> None:
    records = read_jsonl(path) if append else []
    records.extend(rows)
    path.write_text(
        "".join(json.dumps(record, sort_keys=True) + "\n" for record in records),
        encoding="utf-8",
    )


def refresh_provenance_checksums(bundle: Path) -> None:
    provenance = read_json(bundle / "provenance.json")
    provenance["checksums"] = {
        file_name: f"sha256:{validator.canonical_text_file_sha256(bundle / file_name)}"
        for file_name in validator.BUNDLE_JSONL_FILES
    }
    write_json(bundle / "provenance.json", provenance)

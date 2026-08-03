from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts import release_dist_manifest

VERSION = "0.2.9"


def write_pyproject(path: Path) -> None:
    path.write_text(f"[project]\nversion = {VERSION!r}\n", encoding="utf-8")


def write_expected_dist_files(dist_dir: Path) -> None:
    dist_dir.mkdir()
    (dist_dir / f"llmwiki_serve-{VERSION}-py3-none-any.whl").write_bytes(b"wheel")
    (dist_dir / f"llmwiki_serve-{VERSION}.tar.gz").write_bytes(b"sdist")


def symlink_or_skip(link_path: Path, target_path: Path) -> None:
    try:
        link_path.symlink_to(target_path)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"symlink creation unsupported on this platform: {exc}")


def test_manifest_write_and_verify_ignore_dist_gitignore_placeholder(tmp_path: Path) -> None:
    dist_dir = tmp_path / "dist"
    write_expected_dist_files(dist_dir)
    (dist_dir / ".gitignore").write_text("*\n", encoding="utf-8")
    pyproject = tmp_path / "pyproject.toml"
    write_pyproject(pyproject)

    manifest_path = tmp_path / "dist-sha256.json"
    manifest = release_dist_manifest.write_manifest(dist_dir, manifest_path, pyproject)

    assert set(manifest["files"]) == release_dist_manifest.expected_distribution_names(VERSION)
    assert ".gitignore" not in manifest["files"]
    assert (
        manifest_path.read_text(encoding="utf-8")
        == json.dumps(
            manifest,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    assert release_dist_manifest.verify_manifest(dist_dir, manifest_path) == json.loads(
        manifest_path.read_text(encoding="utf-8")
    )


def test_manifest_write_rejects_unrelated_regular_dist_file(tmp_path: Path) -> None:
    dist_dir = tmp_path / "dist"
    write_expected_dist_files(dist_dir)
    (dist_dir / "notes.txt").write_text("not a release artifact\n", encoding="utf-8")
    pyproject = tmp_path / "pyproject.toml"
    write_pyproject(pyproject)

    with pytest.raises(release_dist_manifest.DistManifestError, match="notes.txt"):
        release_dist_manifest.write_manifest(dist_dir, tmp_path / "dist-sha256.json", pyproject)


def test_manifest_write_rejects_expected_artifact_symlink_to_external_file(
    tmp_path: Path,
) -> None:
    dist_dir = tmp_path / "dist"
    write_expected_dist_files(dist_dir)
    wheel_path = dist_dir / f"llmwiki_serve-{VERSION}-py3-none-any.whl"
    wheel_path.unlink()
    external_target = tmp_path / "outside-wheel"
    external_target.write_bytes(b"external wheel")
    symlink_or_skip(wheel_path, external_target)
    pyproject = tmp_path / "pyproject.toml"
    write_pyproject(pyproject)

    with pytest.raises(
        release_dist_manifest.DistManifestError,
        match=r"symlink: llmwiki_serve-0\.2\.9-py3-none-any\.whl",
    ):
        release_dist_manifest.write_manifest(dist_dir, tmp_path / "dist-sha256.json", pyproject)


def test_manifest_write_rejects_placeholder_symlink(tmp_path: Path) -> None:
    dist_dir = tmp_path / "dist"
    write_expected_dist_files(dist_dir)
    placeholder_target = tmp_path / "placeholder-target"
    placeholder_target.write_text("*\n", encoding="utf-8")
    symlink_or_skip(dist_dir / ".gitignore", placeholder_target)
    pyproject = tmp_path / "pyproject.toml"
    write_pyproject(pyproject)

    with pytest.raises(
        release_dist_manifest.DistManifestError,
        match=r"symlink: \.gitignore",
    ):
        release_dist_manifest.write_manifest(dist_dir, tmp_path / "dist-sha256.json", pyproject)


def test_manifest_verify_rejects_unrelated_regular_downloaded_artifact(tmp_path: Path) -> None:
    dist_dir = tmp_path / "dist"
    write_expected_dist_files(dist_dir)
    pyproject = tmp_path / "pyproject.toml"
    write_pyproject(pyproject)
    manifest_path = tmp_path / "dist-sha256.json"
    release_dist_manifest.write_manifest(dist_dir, manifest_path, pyproject)

    (dist_dir / "llmwiki_serve-0.2.9.zip").write_bytes(b"unexpected")

    with pytest.raises(
        release_dist_manifest.DistManifestError,
        match="llmwiki_serve-0.2.9.zip",
    ):
        release_dist_manifest.verify_manifest(dist_dir, manifest_path)


def test_manifest_write_rejects_non_placeholder_dotfile(tmp_path: Path) -> None:
    dist_dir = tmp_path / "dist"
    write_expected_dist_files(dist_dir)
    (dist_dir / ".pypirc").write_text("token = leaked\n", encoding="utf-8")
    pyproject = tmp_path / "pyproject.toml"
    write_pyproject(pyproject)

    with pytest.raises(release_dist_manifest.DistManifestError, match=".pypirc"):
        release_dist_manifest.write_manifest(dist_dir, tmp_path / "dist-sha256.json", pyproject)


def test_manifest_verify_rejects_duplicate_wheel_checksum_key(tmp_path: Path) -> None:
    dist_dir = tmp_path / "dist"
    write_expected_dist_files(dist_dir)
    manifest_path = tmp_path / "dist-sha256.json"
    wheel_name = f"llmwiki_serve-{VERSION}-py3-none-any.whl"
    sdist_name = f"llmwiki_serve-{VERSION}.tar.gz"
    wheel_hash = hashlib.sha256((dist_dir / wheel_name).read_bytes()).hexdigest()
    sdist_hash = hashlib.sha256((dist_dir / sdist_name).read_bytes()).hexdigest()
    manifest_path.write_text(
        (
            "{\n"
            '  "package": "llmwiki-serve",\n'
            f'  "version": "{VERSION}",\n'
            '  "files": {\n'
            f'    "{wheel_name}": "{wheel_hash}",\n'
            f'    "{wheel_name}": "{wheel_hash}",\n'
            f'    "{sdist_name}": "{sdist_hash}"\n'
            "  }\n"
            "}\n"
        ),
        encoding="utf-8",
    )

    with pytest.raises(
        release_dist_manifest.DistManifestError,
        match=r"duplicate key.*llmwiki_serve-0\.2\.9-py3-none-any\.whl",
    ):
        release_dist_manifest.verify_manifest(dist_dir, manifest_path)


def test_manifest_verify_rejects_duplicate_top_level_key(tmp_path: Path) -> None:
    dist_dir = tmp_path / "dist"
    write_expected_dist_files(dist_dir)
    manifest_path = tmp_path / "dist-sha256.json"
    wheel_name = f"llmwiki_serve-{VERSION}-py3-none-any.whl"
    sdist_name = f"llmwiki_serve-{VERSION}.tar.gz"
    wheel_hash = hashlib.sha256((dist_dir / wheel_name).read_bytes()).hexdigest()
    sdist_hash = hashlib.sha256((dist_dir / sdist_name).read_bytes()).hexdigest()
    manifest_path.write_text(
        (
            "{\n"
            '  "package": "llmwiki-serve",\n'
            f'  "version": "{VERSION}",\n'
            f'  "version": "{VERSION}",\n'
            '  "files": {\n'
            f'    "{wheel_name}": "{wheel_hash}",\n'
            f'    "{sdist_name}": "{sdist_hash}"\n'
            "  }\n"
            "}\n"
        ),
        encoding="utf-8",
    )

    with pytest.raises(
        release_dist_manifest.DistManifestError,
        match="duplicate key.*version",
    ):
        release_dist_manifest.verify_manifest(dist_dir, manifest_path)


def test_manifest_verify_rejects_extra_top_level_key(tmp_path: Path) -> None:
    dist_dir = tmp_path / "dist"
    write_expected_dist_files(dist_dir)
    pyproject = tmp_path / "pyproject.toml"
    write_pyproject(pyproject)
    manifest_path = tmp_path / "dist-sha256.json"
    manifest = release_dist_manifest.write_manifest(dist_dir, manifest_path, pyproject)
    manifest["unexpected"] = "extra"
    manifest_path.write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")

    with pytest.raises(
        release_dist_manifest.DistManifestError,
        match="exactly package, version, and files",
    ):
        release_dist_manifest.verify_manifest(dist_dir, manifest_path)


def test_manifest_verify_rejects_invalid_sha256(tmp_path: Path) -> None:
    dist_dir = tmp_path / "dist"
    write_expected_dist_files(dist_dir)
    manifest_path = tmp_path / "dist-sha256.json"
    wheel_name = f"llmwiki_serve-{VERSION}-py3-none-any.whl"
    sdist_name = f"llmwiki_serve-{VERSION}.tar.gz"
    manifest_path.write_text(
        json.dumps(
            {
                "package": "llmwiki-serve",
                "version": VERSION,
                "files": {
                    wheel_name: "not-a-sha",
                    sdist_name: hashlib.sha256((dist_dir / sdist_name).read_bytes()).hexdigest(),
                },
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    with pytest.raises(release_dist_manifest.DistManifestError, match=wheel_name):
        release_dist_manifest.verify_manifest(dist_dir, manifest_path)

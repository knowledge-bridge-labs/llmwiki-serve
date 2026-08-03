from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tomllib
from collections.abc import Sequence
from pathlib import Path
from typing import Any

PACKAGE_NAME = "llmwiki-serve"
DIST_MODULE_NAME = "llmwiki_serve"
IGNORED_DIST_PLACEHOLDER_FILES = frozenset({".gitignore"})


class DistManifestError(RuntimeError):
    pass


def read_project_version(pyproject_path: Path) -> str:
    metadata = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    project = metadata.get("project")
    if not isinstance(project, dict):
        raise DistManifestError("pyproject.toml is missing [project]")
    version = project.get("version")
    if not isinstance(version, str) or not version:
        raise DistManifestError("pyproject.toml is missing project.version")
    return version


def expected_distribution_names(version: str) -> frozenset[str]:
    return frozenset(
        {
            f"{DIST_MODULE_NAME}-{version}-py3-none-any.whl",
            f"{DIST_MODULE_NAME}-{version}.tar.gz",
        }
    )


def validated_distribution_files(dist_dir: Path, version: str) -> tuple[Path, ...]:
    if not dist_dir.is_dir():
        raise DistManifestError(f"dist directory not found: {dist_dir}")

    expected = expected_distribution_names(version)
    dist_root = dist_dir.resolve(strict=True)
    regular_files = tuple(
        _validated_regular_dist_file(path, dist_root=dist_root)
        for path in sorted(dist_dir.iterdir(), key=lambda candidate: candidate.name)
    )
    artifact_files = [
        path for path in regular_files if path.name not in IGNORED_DIST_PLACEHOLDER_FILES
    ]
    actual = {path.name for path in artifact_files}
    if actual != expected:
        message = f"unexpected dist files: {sorted(actual)} != {sorted(expected)}"
        ignored = sorted(
            path.name for path in regular_files if path.name in IGNORED_DIST_PLACEHOLDER_FILES
        )
        if ignored:
            message = f"{message}; ignored placeholder files: {ignored}"
        raise DistManifestError(message)

    by_name = {path.name: path for path in artifact_files}
    return tuple(by_name[name] for name in sorted(expected))


def _validated_regular_dist_file(path: Path, *, dist_root: Path) -> Path:
    if path.is_symlink():
        raise DistManifestError(f"dist entry must not be a symlink: {path.name}")

    try:
        resolved_path = path.resolve(strict=True)
        resolved_path.relative_to(dist_root)
    except FileNotFoundError as exc:
        raise DistManifestError(f"dist entry disappeared while verifying: {path.name}") from exc
    except ValueError as exc:
        raise DistManifestError(f"dist entry escapes dist directory: {path.name}") from exc

    if not path.is_file():
        raise DistManifestError(f"unexpected non-file dist entry: {path.name}")

    return path


def build_manifest(dist_dir: Path, version: str) -> dict[str, Any]:
    files = validated_distribution_files(dist_dir, version)
    return {
        "package": PACKAGE_NAME,
        "version": version,
        "files": {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in files},
    }


def write_manifest(dist_dir: Path, manifest_path: Path, pyproject_path: Path) -> dict[str, Any]:
    manifest = build_manifest(dist_dir, read_project_version(pyproject_path))
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def verify_manifest(dist_dir: Path, manifest_path: Path) -> dict[str, Any]:
    if not manifest_path.is_file():
        raise DistManifestError(f"missing checksum manifest: {manifest_path}")

    try:
        manifest = json.loads(
            manifest_path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_json_keys,
        )
    except json.JSONDecodeError as exc:
        raise DistManifestError(f"invalid checksum manifest JSON: {exc.msg}") from exc
    if not isinstance(manifest, dict):
        raise DistManifestError("checksum manifest must be a JSON object")
    if set(manifest) != {"files", "package", "version"}:
        raise DistManifestError(
            "checksum manifest must contain exactly package, version, and files"
        )
    if manifest.get("package") != PACKAGE_NAME:
        raise DistManifestError("unexpected package in checksum manifest")

    version = manifest.get("version")
    if not isinstance(version, str) or not version:
        raise DistManifestError("checksum manifest is missing version")

    files = manifest.get("files")
    expected_names = expected_distribution_names(version)
    if not isinstance(files, dict) or set(files) != expected_names:
        raise DistManifestError(
            "checksum manifest must describe exactly the expected wheel and sdist"
        )

    dist_files = validated_distribution_files(dist_dir, version)
    for path in dist_files:
        expected_hash = files[path.name]
        if not is_sha256_digest(expected_hash):
            raise DistManifestError(f"invalid sha256 for {path.name}")
        observed_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        if observed_hash != expected_hash:
            raise DistManifestError(f"sha256 mismatch for {path.name}")

    return manifest


def _reject_duplicate_json_keys(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DistManifestError(f"duplicate key in checksum manifest JSON: {key}")
        result[key] = value
    return result


def is_sha256_digest(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write or verify release distribution hashes.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    write_parser = subparsers.add_parser("write")
    write_parser.add_argument("--dist-dir", type=Path, default=Path("dist"))
    write_parser.add_argument("--manifest", type=Path, default=Path("dist-sha256.json"))
    write_parser.add_argument("--pyproject", type=Path, default=Path("pyproject.toml"))

    verify_parser = subparsers.add_parser("verify")
    verify_parser.add_argument("--dist-dir", type=Path, default=Path("release-artifact/dist"))
    verify_parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("release-artifact/dist-sha256.json"),
    )

    args = parser.parse_args(argv)
    try:
        if args.command == "write":
            write_manifest(args.dist_dir, args.manifest, args.pyproject)
        else:
            verify_manifest(args.dist_dir, args.manifest)
    except DistManifestError as exc:
        print(f"release dist manifest failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = PROJECT_ROOT / "pyproject.toml"
NOTICES = PROJECT_ROOT / "THIRD_PARTY_NOTICES.md"


def main() -> int:
    project = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    declared = direct_dependency_names(project)
    notice_text = canonicalize(NOTICES.read_text(encoding="utf-8"))
    missing = sorted(name for name in declared if name not in notice_text)
    if missing:
        print(
            "THIRD_PARTY_NOTICES.md is missing direct dependency notice(s): " + ", ".join(missing),
            file=sys.stderr,
        )
        return 1
    print("THIRD_PARTY_NOTICES.md covers declared direct dependencies")
    return 0


def direct_dependency_names(project: dict[str, object]) -> set[str]:
    names: set[str] = set()
    project_table = as_dict(project.get("project"))
    for requirement in as_list(project_table.get("dependencies")):
        names.add(requirement_name(requirement))

    optional = as_dict(project_table.get("optional-dependencies"))
    for requirements in optional.values():
        for requirement in as_list(requirements):
            names.add(requirement_name(requirement))

    return {name for name in names if name}


def requirement_name(requirement: object) -> str:
    text = str(requirement).strip()
    text = text.split(";", 1)[0].split("[", 1)[0].strip()
    match = re.match(r"^[A-Za-z0-9_.-]+", text)
    return canonicalize(match.group(0)) if match else ""


def canonicalize(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def as_dict(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def as_list(value: object) -> list[object]:
    return value if isinstance(value, list) else []


if __name__ == "__main__":
    raise SystemExit(main())

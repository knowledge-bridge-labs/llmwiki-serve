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
    noticed = notice_package_names(NOTICES.read_text(encoding="utf-8"))
    missing = sorted(declared - noticed)
    if missing:
        print(
            "THIRD_PARTY_NOTICES.md is missing direct dependency notice(s): " + ", ".join(missing),
            file=sys.stderr,
        )
        return 1
    print("THIRD_PARTY_NOTICES.md covers declared direct dependencies")
    return 0


def notice_package_names(text: str) -> set[str]:
    names: set[str] = set()
    in_package_table = False
    for line in text.splitlines():
        cells = markdown_table_cells(line)
        if cells is None:
            in_package_table = False
            continue
        if not cells:
            continue
        if is_markdown_separator_row(cells):
            continue
        first_cell = canonicalize(cells[0])
        if first_cell == "package":
            in_package_table = True
            continue
        if in_package_table and first_cell:
            names.add(first_cell)
    return names


def markdown_table_cells(line: str) -> list[str] | None:
    stripped = line.strip()
    if not stripped.startswith("|") or not stripped.endswith("|"):
        return None
    return [cell.strip() for cell in stripped.strip("|").split("|")]


def is_markdown_separator_row(cells: list[str]) -> bool:
    return all(re.fullmatch(r":?-{3,}:?", cell) is not None for cell in cells)


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

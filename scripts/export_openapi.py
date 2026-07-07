from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "src"))

from llmwiki_serve.api import create_app  # noqa: E402

DEFAULT_OUTPUT = PROJECT_ROOT / "docs" / "openapi.json"
DEFAULT_ROOT = PROJECT_ROOT / "examples" / "sample-wiki"


def main() -> int:
    parser = argparse.ArgumentParser(description="Export the llmwiki-serve OpenAPI contract.")
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="Output JSON path. Defaults to docs/openapi.json.",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=DEFAULT_ROOT,
        help="Knowledge Source root used to construct the app. Defaults to examples/sample-wiki.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Fail when the output file is missing or differs from the generated schema.",
    )
    args = parser.parse_args()

    output = args.output.resolve()
    content = rendered_schema(args.root.resolve())
    if args.check:
        if not output.is_file():
            print(f"OpenAPI contract missing: {display_path(output)}", file=sys.stderr)
            return 1
        current = output.read_text(encoding="utf-8")
        if current != content:
            print(
                "OpenAPI contract is stale: run `python scripts/export_openapi.py`",
                file=sys.stderr,
            )
            return 1
        print(f"OpenAPI contract is up to date: {display_path(output)}")
        return 0

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(content, encoding="utf-8")
    print(f"Wrote {display_path(output)}")
    return 0


def rendered_schema(root: Path) -> str:
    schema: dict[str, Any] = create_app(root).openapi()
    return json.dumps(schema, indent=2, sort_keys=True) + "\n"


def display_path(path: Path) -> str:
    try:
        return str(path.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


if __name__ == "__main__":
    raise SystemExit(main())

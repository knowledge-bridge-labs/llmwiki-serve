from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.candidate_sample_artifacts import (  # noqa: E402
    generate_candidate_samples,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Generate local candidate LLMWiki sample artifacts for projection and "
            "serve-surface smoke validation."
        )
    )
    parser.add_argument(
        "output_root",
        type=Path,
        help="Directory that will receive the candidate sample folders.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Replace existing generated candidate sample directories under output_root.",
    )
    args = parser.parse_args()

    try:
        generated = generate_candidate_samples(args.output_root.resolve(), force=args.force)
    except FileExistsError as error:
        print(f"candidate sample generation failed: {error}", file=sys.stderr)
        return 1

    print(f"generated {len(generated)} candidate sample(s) under {args.output_root.resolve()}")
    for sample in generated:
        sidecar = f", sidecar={sample.sidecar_graph_path}" if sample.sidecar_graph_path else ""
        print(
            "PASS "
            f"{sample.directory_name}: adapter={sample.expected_adapter}, "
            f"representative={sample.representative_page_id}, "
            f"hidden={sample.hidden_page_id}"
            f"{sidecar}"
        )
    print(f"manifest={args.output_root.resolve() / 'candidate-samples.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

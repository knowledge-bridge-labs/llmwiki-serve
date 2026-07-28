from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType


def load_checker() -> ModuleType:
    script = Path(__file__).parents[1] / "scripts" / "check_third_party_notices.py"
    spec = importlib.util.spec_from_file_location("check_third_party_notices", script)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_notice_package_names_use_package_rows_not_substrings() -> None:
    checker = load_checker()
    text = """
| Package | Purpose | License | Source |
| --- | --- | --- | --- |
| fastapi-extra | Test helper | MIT | https://example.invalid/fastapi |
| types-PyYAML | Typing | Apache-2.0 | https://example.invalid/types-PyYAML |

This prose mentions fastapi, pydantic, and psutil, but it is not a notice row.
"""

    names = checker.notice_package_names(text)

    assert "fastapi-extra" in names
    assert "types-pyyaml" in names
    assert "fastapi" not in names
    assert "pydantic" not in names
    assert "psutil" not in names

# Tests: CLI Version Option

## Focused

- `uv run pytest -q tests/test_service.py::test_cli_version_flags_print_installed_package_version`
- `uv run pytest -q tests/test_service.py::test_cli_version_flags_print_installed_package_version tests/test_service.py::test_cli_analyzer_profile_help_is_limited_to_search_commands`
- `uv run llmwiki-serve --version`
- `uv run llmwiki-serve -v`
- `uv run llmwiki-serve --help`

## Regression

- `uv run ruff format --check src/llmwiki_serve/cli.py tests/test_service.py`
- `uv run ruff check src/llmwiki_serve/cli.py tests/test_service.py`
- `uv run mypy src`

## Release

- `uv run ruff format --check .`
- `uv run ruff check .`
- `uv run mypy src`
- `uv run mypy scripts/benchmark_adapters/scifact_runner.py`
- `uv run pytest -p no:cacheprovider`
- `uv build`
- `uv run python scripts/release_smoke.py --wheel dist/llmwiki_serve-0.2.14-py3-none-any.whl --sdist dist/llmwiki_serve-0.2.14.tar.gz`
- `uvx twine check dist/llmwiki_serve-0.2.14-py3-none-any.whl dist/llmwiki_serve-0.2.14.tar.gz`

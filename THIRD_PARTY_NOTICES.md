# Third-Party Notices

`llmwiki-serve` is licensed under Apache-2.0. This file lists the
direct runtime and development dependencies declared in `pyproject.toml`.
Exact resolved direct and transitive dependency versions are recorded in
`uv.lock`.

## Direct Runtime Dependencies

| Package | Version range | License | Homepage |
| --- | --- | --- | --- |
| FastAPI | `>=0.139.0` | MIT | <https://fastapi.tiangolo.com/> |
| MCP Python SDK | `>=1.28.1,<2` | MIT | <https://github.com/modelcontextprotocol/python-sdk> |
| Pydantic | `>=2.11` | MIT | <https://docs.pydantic.dev/> |
| PyYAML | `>=6.0` | MIT | <https://pyyaml.org/> |
| Typer | `>=0.12` | MIT | <https://typer.tiangolo.com/> |
| Uvicorn | `>=0.49.0` | BSD-3-Clause | <https://www.uvicorn.org/> |

## Direct Development Dependencies

| Package | Version range | License | Homepage |
| --- | --- | --- | --- |
| HTTPX | `>=0.27` | BSD-3-Clause | <https://www.python-httpx.org/> |
| mypy | `>=1.10` | MIT | <https://mypy-lang.org/> |
| pytest | `>=8.2` | MIT | <https://docs.pytest.org/> |
| Ruff | `>=0.6` | MIT | <https://docs.astral.sh/ruff/> |
| types-PyYAML | `>=6.0` | Apache-2.0 | <https://github.com/python/typeshed> |

## Notices

This project does not vendor third-party runtime dependencies into its wheel.
Users and redistributors should review `uv.lock` and upstream package metadata,
then retain any required license texts, copyright notices, and attribution files
when redistributing this project, dependency wheels, containers, or bundled
runtime environments.

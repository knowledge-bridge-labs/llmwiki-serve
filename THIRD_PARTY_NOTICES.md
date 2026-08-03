# Third-Party Notices

`llmwiki-serve` is licensed under Apache-2.0. This file lists the
direct runtime and development dependencies declared in `pyproject.toml`.
Exact resolved direct and transitive dependency versions are recorded in
`uv.lock`.

## Direct Runtime Dependencies

| Package | Version range | License | Homepage |
| --- | --- | --- | --- |
| FastAPI | `>=0.139.2` | MIT | <https://fastapi.tiangolo.com/> |
| mcp | `>=1.28.1,<2` | MIT | <https://github.com/modelcontextprotocol/python-sdk> |
| psutil | `>=6.1` | BSD-3-Clause | <https://github.com/giampaolo/psutil> |
| Pydantic | `>=2.11` | MIT | <https://docs.pydantic.dev/> |
| PyYAML | `>=6.0` | MIT | <https://pyyaml.org/> |
| snowballstemmer | `>=2.2.0` | BSD-3-Clause | <https://github.com/snowballstem/snowball> |
| Typer | `>=0.27.0` | MIT | <https://typer.tiangolo.com/> |
| Uvicorn | `>=0.51.0` | BSD-3-Clause | <https://www.uvicorn.org/> |

## Direct Optional Dependencies

| Package | Version range | License | Homepage |
| --- | --- | --- | --- |
| FastEmbed | `>=0.8.0,<0.9.0` | Apache-2.0 | <https://github.com/qdrant/fastembed> |
| NumPy | `>=2.4.6,<2.5.0` | BSD-3-Clause | <https://numpy.org/> |
| redis | `>=5` | MIT | <https://github.com/redis/redis-py> |

`pyproject.toml` declares optional extras `redis`, `vector`, and `dev`; it does
not declare a separate `benchmark` extra. Repository benchmark adapters use the
declared runtime/development dependencies above and require the `vector` extra
only when running vector or hybrid benchmark modes.

## Optional Model Metadata

The vector extra supports the explicit FastEmbed model candidate
`sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`, resolved by
FastEmbed `0.8.0` to Hugging Face repository
`qdrant/paraphrase-multilingual-MiniLM-L12-v2-onnx-Q` at revision
`faf4aa4225822f3bc6376869cb1164e8e3feedd0`. FastEmbed reports license
`apache-2.0` and dimension `384` for this model.

## Direct Development Dependencies

| Package | Version range | License | Homepage |
| --- | --- | --- | --- |
| certifi | `>=2026.6.17` | MPL-2.0 | <https://github.com/certifi/python-certifi> |
| HTTPX | `>=0.27` | BSD-3-Clause | <https://www.python-httpx.org/> |
| mypy | `>=2.3.0` | MIT | <https://mypy-lang.org/> |
| pytest | `>=8.2` | MIT | <https://docs.pytest.org/> |
| Ruff | `>=0.15.22` | MIT | <https://docs.astral.sh/ruff/> |
| types-psutil | `>=7.0` | Apache-2.0 | <https://github.com/python/typeshed> |
| types-PyYAML | `>=6.0` | Apache-2.0 | <https://github.com/python/typeshed> |

## Notices

This project does not vendor third-party runtime dependencies into its wheel.
Users and redistributors should review `uv.lock` and upstream package metadata,
then retain any required license texts, copyright notices, and attribution files
when redistributing this project, dependency wheels, containers, or bundled
runtime environments.

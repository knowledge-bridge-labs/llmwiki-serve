# Examples

`sample-wiki/` is a small synthetic Markdown knowledge folder used for local
smoke tests and README commands. It is public sample data with approved pages,
wikilinks, source refs, and one draft page that is withheld by default.

Run it from the repository root:

```sh
uv run llmwiki-serve manifest ./examples/sample-wiki
uv run llmwiki-serve query ./examples/sample-wiki "release readiness"
uv run llmwiki-serve serve ./examples/sample-wiki --host 127.0.0.1 --port 8765
```

# Plan: Context Graph Limit

1. Add `DEFAULT_CONTEXT_GRAPH_LIMIT = 120` and bounds `0..2000` next to
   `DEFAULT_GRAPH_LIMIT` in `service.py`; replace the literal `120` in
   `LlmWikiService.context()` with the instance setting.
2. Validate the constructor argument (integer, not bool, within bounds).
3. Thread `context_graph_limit` through `create_app` with the shared
   `validate_default_limit` helper, matching `graph_default_limit`.
4. Add `--context-graph-limit` and `LLMWIKI_CONTEXT_GRAPH_LIMIT` to `serve`
   using the existing `resolve_int_option_env` helper.
5. Document the option in README and architecture next to the other operator
   limits; add a CHANGELOG entry.

The setting lives on the service rather than on each surface handler because
it is an operator property of the served instance, like
`refresh_interval_seconds`, and every context surface already calls the same
service method.

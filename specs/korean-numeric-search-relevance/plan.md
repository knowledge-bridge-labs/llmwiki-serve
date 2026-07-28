# Plan: Korean Numeric Search Relevance

## Approach

Adjust the existing lexical search pipeline instead of adding a new dependency.
Tokenization will preserve Korean/numeric compounds and add conservative Hangul
bigrams. Ranking will use a BM25-style formula with bounded term frequency and
document length normalization. Role boosts become small multipliers applied to
the normalized score.

Parser tag extraction will keep frontmatter tags and explicit non-numeric
inline tags, but pure numeric inline hashtags will no longer become tags.

The follow-up slice keeps the same search/query/read surfaces and adds optional
controls instead of introducing a separate grep endpoint. `mode=literal` performs
case-folded exact substring matching, while `fields`, `snippet_chars`,
`min_score`, and `exclude_page_ids` let callers keep search/query payloads small
and avoid repeated pages. Read `fields` projection returns only requested
`WikiPage` fields.

## Affected Areas

- Source modules: `src/llmwiki_serve/search.py`,
  `src/llmwiki_serve/parser.py`, `src/llmwiki_serve/service.py`,
  `src/llmwiki_serve/api.py`, `src/llmwiki_serve/cli.py`,
  `src/llmwiki_serve/models.py`
- Tests: focused search/parser/API/MCP/CLI regressions in `tests/test_service.py`
- Docs/contracts: this spec and generated `docs/openapi.json`

## Risks

- Risk: changing scoring reorders existing search tests.
  Mitigation: keep route/schema stable, use conservative role multipliers, and
  assert behavior rather than exact scores.

- Risk: bigram indexing adds noisy matches for very short Korean text.
  Mitigation: generate bigrams only for Hangul sequences longer than two
  characters and keep one-character query morphemes down-weighted by ranking.

- Risk: pure numeric hashtags may have been intentional inline tags.
  Mitigation: frontmatter tags remain available for deliberate numeric tags;
  only body-harvested pure numeric hashtags are filtered.

- Risk: field projection could break clients expecting full result objects.
  Mitigation: projection is opt-in; default calls still return the full existing
  fields.

- Risk: literal matching may be mistaken for semantic phrase search.
  Mitigation: call it `mode=literal` and keep lexical ranking as the default.

## Rollout

- Update tokenizer and scorer.
- Filter numeric inline tags.
- Add focused regressions for `3차 계약`, length normalization, and numeric
  pseudo-tags.
- Add literal mode and payload controls as the issue follow-up slice.
- Regenerate/check OpenAPI after the request and response model additions.
- Run full local validation.

# Plan: Korean Numeric Search Relevance

## Approach

Adjust the existing lexical search pipeline instead of adding a new dependency.
Tokenization will preserve Korean/numeric compounds and add conservative Hangul
bigrams. Ranking will use a BM25-style formula with bounded term frequency and
document length normalization. Role boosts become small multipliers applied to
the normalized score.

Parser tag extraction will keep frontmatter tags and explicit non-numeric
inline tags, but pure numeric inline hashtags will no longer become tags.

## Affected Areas

- Source modules: `src/llmwiki_serve/search.py`,
  `src/llmwiki_serve/parser.py`
- Tests: focused search/parser regressions in `tests/test_service.py`
- Docs: this spec; README/architecture only if operator-visible behavior needs
  mention

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

## Rollout

- Update tokenizer and scorer.
- Filter numeric inline tags.
- Add focused regressions for `3차 계약`, length normalization, and numeric
  pseudo-tags.
- Keep grep/find and richer payload controls as explicit follow-up scope.
- Run full local validation.

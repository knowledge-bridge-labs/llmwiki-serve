# Logseq Judgment Notes

Source: `logseq/logseq` at commit `a9a67f61ab29972d2e2b6c7a5864e6e3306c0d9a`, source path `deps/graph-parser/test/resources/exporter-test-graph`.

The service projection selects the dedicated `logseq` adapter with implementation `logseq/logseq`. It projects 54 approved pages, 105 graph nodes, 73 graph edges, no hot/index/overview hub page, and zero source refs. Citation judgments therefore use deterministic public path-ID citations rather than invented source refs.

Because this Logseq case has no authored hot/index/overview hub and uses the dedicated Logseq adapter, managed-context on/off is expected and verified as a no-op for this case.

Support spans are intentionally empty. The qrels use projected page IDs and relative source paths only; they do not copy upstream page content.

| Query ID | Coverage label | Benchmark class | Judged page IDs and paths | Judgment basis |
| --- | --- | --- | --- | --- |
| `q-logseq-global-graph-fixture` | global | `global-map` | `pages/contents` (`pages/contents.md`), `journals/2024_01_08` (`journals/2024_01_08.md`), `journals/2025_12_17` (`journals/2025_12_17.md`) | Global fixture orientation across the contents page and representative journals. |
| `q-logseq-local-chat-gpt-alias` | local | `known-item` | `pages/chat-gpt` (`pages/chat-gpt.md`) | Local page lookup for the chat-gpt alias and type-property case. |
| `q-logseq-multihop-creativework-movie` | multi-hop | `multi-hop` | `pages/CreativeWork` (`pages/CreativeWork.md`), `pages/Movie` (`pages/Movie.md`), `pages/Interstellar` (`pages/Interstellar.md`), `journals/2024_02_23` (`journals/2024_02_23.md`) | Movie and CreativeWork pages plus movie-tagged examples form a class/instance traversal. |
| `q-logseq-negative-payroll-token` | negative | `negative` | `pages/contents` (`pages/contents.md`, relevance 0) | The requested token is absent from the projection. |
| `q-logseq-property-whiteboard-tool` | property | `known-item` | `pages/Whiteboard___Tool` (`pages/Whiteboard___Tool.md`), `pages/Whiteboard___Arrow_head_toggle` (`pages/Whiteboard___Arrow_head_toggle.md`) | Whiteboard tool pages exercise alias, type, parent, and related tool properties. |
| `q-logseq-numeric-property-values` | numeric-or-literal | `korean-numeric` | `pages/some page` (`pages/some page.md`), `pages/new page` (`pages/new page.md`), `journals/2024_02_29` (`journals/2024_02_29.md`), `journals/2024_07_24` (`journals/2024_07_24.md`) | Numeric and literal property values across page and journal files. |
| `q-logseq-citation-zlib-highlights` | citation | `citation` | `pages/zlib` (`pages/zlib.md`), `pages/hls__zlib` (`pages/hls__zlib.md`), `journals/2026_01_01` (`journals/2026_01_01.md`) | Page-id citation fallback for a zlib attachment page, its highlight page, and the journal linking it. |
| `q-logseq-global-highlight-assets` | global | `global-map` | `pages/zlib` (`pages/zlib.md`), `pages/Understanding EXPLAIN` (`pages/Understanding EXPLAIN.md`), `pages/hls__zlib` (`pages/hls__zlib.md`), `pages/hls__Understanding EXPLAIN` (`pages/hls__Understanding EXPLAIN.md`), `pages/unlinked-highlights` (`pages/unlinked-highlights.md`), `journals/2026_01_01` (`journals/2026_01_01.md`) | Global highlight and attachment pages across multiple projected asset-related pages. |
| `q-logseq-local-namespace-page` | local | `known-item` | `pages/n1___x___y` (`pages/n1___x___y.md`) | Namespace-decoding page-id case for `n1/x/y`. |
| `q-logseq-multihop-linked-filters` | multi-hop | `multi-hop` | `journals/2024_10_09` (`journals/2024_10_09.md`), `pages/chat-gpt` (`pages/chat-gpt.md`) | Journal references connect filter refs to the chat-gpt page. |
| `q-logseq-property-query-table` | property | `known-item` | `journals/2024_02_14` (`journals/2024_02_14.md`), `journals/2024_08_07` (`journals/2024_08_07.md`) | Journal pages covering query-table/query-properties and query title text properties. |
| `q-logseq-citation-pdf-asset-fallback` | citation | `citation` | `pages/hls__Sina_de_Capoeria_Batizado_2025_-_Program_Itinerary_1752179325104_0` (`pages/hls__Sina_de_Capoeria_Batizado_2025_-_Program_Itinerary_1752179325104_0.md`), `journals/2025_07_10` (`journals/2025_07_10.md`) | Page-id citation fallback for the projected PDF highlight page and journal PDF reference. |

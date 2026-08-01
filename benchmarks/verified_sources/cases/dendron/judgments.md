# Dendron Judgment Notes

Source: `dendronhq/dendron` at commit `4420715a421756518863c47005c8c49a38e37621`, source path `test-workspace`.

The service projection selects the dedicated `dendron` adapter with implementation `dendronhq/dendron`. It projects 154 approved pages, 403 graph nodes, 440 graph edges, no hot/index/overview hub page, and zero source refs. Citation judgments therefore use deterministic public path-ID citations rather than invented source refs.

Because this Dendron case has no authored hot/index/overview hub and uses the dedicated Dendron adapter, managed-context on/off is expected and verified as a no-op for this case.

Support spans are intentionally empty. The qrels use projected page IDs and relative source paths only; they do not copy upstream note content.

| Query ID | Coverage label | Benchmark class | Judged page IDs and paths | Judgment basis |
| --- | --- | --- | --- | --- |
| `q-dendron-global-alternatives` | global | `global-map` | `MJqERMm373Xgom3n46DvW` (`vault/dendron.alternatives.md`) | Global Dendron alternatives page links the compared note tools. |
| `q-dendron-local-note-lookup` | local | `known-item` | `bNkYI2WWK6Jhm2eeVwqrh` (`vault/dendron.note-lookup.md`) | Local note lookup command behavior is centered on this page. |
| `q-dendron-multihop-backlinks-targets` | multi-hop | `multi-hop` | `QUB1KiYq1NRtwofUFmsJu` (`vault/dendron.backlinks.md`), `8Ig0xWc7dTI1LgvPkbUDJ` (`vault/dendron.backlinks.target-A.md`), `ov6EWXwwYloaW0uwjZRy0` (`vault/dendron.backlinks.target-B.md`), `K9A3l5bIVNpyI9FLBuhzn` (`vault/dendron.backlinks.target-C.md`) | Target pages link back to the Backlinks page, making this a graph traversal judgment. |
| `q-dendron-negative-payroll-token` | negative | `negative` | `e86ac3ab-dbe1-47a1-bcd7-9df0d0490b40` (`vault/dendron.md`, relevance 0) | The requested token is absent from the projection. |
| `q-dendron-property-frontmatter-tags` | property | `known-item` | `pxde4MoefS0J7lHK` (`vault/dendron.ref.frontmatter-tags.multi-array.md`), `NmIheXJWFEjOFUE3` (`vault/dendron.ref.frontmatter-tags.multi.md`), `HyqmR36gQFN45GCI` (`vault/dendron.ref.frontmatter-tags.md`) | These pages exercise Dendron frontmatter tag shapes. |
| `q-dendron-numeric-journal-2020-07-12` | numeric-or-literal | `korean-numeric` | `17e29e0e-336f-4c7b-a13e-7a9adf5df402` (`vault/dendron.journal.2020.07.12.md`), `dendron.journal.2020.07.12.foo` (`vault/dendron.journal.2020.07.12.foo.md`), `dendron.journal.2020.07.12.bar` (`vault/dendron.journal.2020.07.12.bar.md`) | Date-literal hierarchy under 2020-07-12. |
| `q-dendron-citation-heading-anchors` | citation | `citation` | `FSi3bKWQeQXYTjE1PoTB0` (`vault/dendron.links.heading-anchors.md`), `bou78C3Ldc5w98Y2OX6gG` (`vault/dendron.links.heading-anchors-diff-page.md`) | Page-id citation fallback for heading-anchor pages. |
| `q-dendron-global-blog-series` | global | `global-map` | `dendron-blog` (`vault/dendron.blog.md`), `dendron-blog-one` (`vault/dendron.blog.one.md`), `dendron-blog-two` (`vault/dendron.blog.two.md`), `dendron-blog-three` (`vault/dendron.blog.three.md`) | Blog collection page and its one/two/three child entries. |
| `q-dendron-local-convert-link` | local | `known-item` | `bZPZdSnnUgvCWuZcDUCHe` (`vault/dendron.cmd.convert-link.md`) | Local command page for Convert Link cases. |
| `q-dendron-multihop-schema-integer` | multi-hop | `multi-hop` | `WhVbhkBCANk5K2M6PPMVz` (`vault/dendron.cmd.create-schema-from-hierarchy.md`), `6tG2OW2u9mYlzf9F09u4j` (`vault/languages.python.data.integer.md`) | Command page links the hierarchy workflow to the integer page. |
| `q-dendron-property-canonical-url` | property | `known-item` | `sHRD6D7exOAcYWlTfRtMm` (`vault/dendron.ref.canonicalUrl.md`) | Canonical URL frontmatter property reference. |
| `q-dendron-citation-assets-links` | citation | `citation` | `Hf27I1UR3HvKyd6HRh8C0` (`vault/dendron.ref.assets.md`), `73eb67ea-0291-45e7-8f2f-193fd6f00643` (`vault/dendron.ref.links.md`) | Page-id citation fallback for asset and link reference pages. |

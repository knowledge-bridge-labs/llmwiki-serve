# Plan: CLI Version Option

## Implementation

- Reuse the existing installed package metadata helper in `llmwiki_serve.cli`.
- Add a Typer root callback with eager `--version` and `-v` options.
- Print the version string and raise `typer.Exit`.
- Add CLI runner tests for both flags.

## Risks

- The root callback must not make existing subcommands require new arguments.
- The shorthand `-v` must not conflict with existing root options.

## Rollout

Ship as a patch-level CLI diagnostics improvement.

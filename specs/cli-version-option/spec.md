# Spec: CLI Version Option

## Status

Implementation branch.

## Problem

Operators need a supported way to ask an installed `llmwiki-serve` CLI which
package version is being executed. Today `llmwiki-serve --version` fails as an
unknown option, forcing users to inspect package-manager state or running
instance metadata.

## Goals

- Add `llmwiki-serve --version`.
- Add shorthand `llmwiki-serve -v`.
- Print only the installed package version followed by a newline.
- Exit with status code `0` before requiring any command or wiki path.

## Non-Goals

- Do not change command output for `manifest`, `query`, `search`, `serve`,
  `ls`, or `status`.
- Do not add version flags to subcommands.
- Do not change package version discovery or release metadata.

## Requirements

- `REQ-CLI-VERSION-001`: `llmwiki-serve --version` prints the installed package
  version and exits `0`.
- `REQ-CLI-VERSION-002`: `llmwiki-serve -v` behaves the same as `--version`.
- `REQ-CLI-VERSION-003`: The option is eager and does not require a subcommand.

## Compatibility

This is additive. Existing commands and options keep their current behavior.

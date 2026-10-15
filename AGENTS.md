# Agent instructions for microservice-utils

Read README.md first. This file only says where things are and what CI enforces.

## Documentation

- The index is `docs/manifest.json`. Run `python3 tools/ctx.py <topic>` to find the document for a
  feature before changing it, and update that document in the same pull request.
- Run `python3 tools/ctx.py --similar "<topic>"` before writing a new document.

## Checks that CI runs on every pull request

- `python3 tools/ctx.py --lint`: the index is valid and every document under `docs/` is indexed or pending.
- `python3 tools/docs_touch_check.py --base origin/main`: a change under a documented entrypoint
  touches its document or the index, or the PR body says `docs-check: skip <reason>`.

## Commands (detected from the repo; verify against README.md)

- `nox -e _install · dev · format · lint · test`

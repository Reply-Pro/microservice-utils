#!/usr/bin/env python3
"""Fail a pull request that changes a documented entrypoint without touching its document.

Usage:
    python3 tools/docs_touch_check.py --base origin/main [--body "$PR_BODY"] [--root DIR]

For every manifest entry that lists `entrypoints`, if a changed file is one of
them (or lives under one of them) and neither the entry's document nor any
manifest changed in the same pull request, the check fails and names the
document to update. Entries owned by another repo are skipped: their document
is not here.

The pull request body may contain `docs-check: skip <reason>` to bypass the
check; the reason is mandatory and is printed, so the bypass stays visible.
Standard library only; it shells out to git for the changed-file list.
"""
import argparse
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ctx  # noqa: E402

SKIP_MARKER = "docs-check: skip"


def changed_files(root, base):
    out = subprocess.run(["git", "-C", root, "diff", "--name-only", f"{base}...HEAD"],
                         capture_output=True, text=True, check=True)
    return [ln.strip() for ln in out.stdout.splitlines() if ln.strip()]


def _under(path, entrypoint):
    ep = entrypoint.rstrip("/")
    return path == ep or path.startswith(ep + "/")


def required_doc_updates(manifests, changed, manifest_paths=()):
    """[(id, doc path, changed file)] for entries whose entrypoints changed but not their doc."""
    changed = set(changed)
    if changed & set(manifest_paths):
        return []
    out = []
    for m in manifests:
        own = m.get("repo")
        for doc in m.get("documents", []):
            if doc.get("repo") and doc["repo"] != own:
                continue
            if doc.get("path") in changed:
                continue
            hit = next((f for f in sorted(changed) for ep in doc.get("entrypoints", [])
                        if _under(f, ep)), None)
            if hit:
                out.append((doc["id"], doc["path"], hit))
    return out


def skip_reason(body):
    """The text after the marker, or None when there is no marker."""
    if not body or SKIP_MARKER not in body:
        return None
    rest = body.split(SKIP_MARKER, 1)[1].strip()
    return rest.splitlines()[0].strip() if rest else ""


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", default=ctx.DEFAULT_ROOT)
    ap.add_argument("--base", required=True, help="base ref of the pull request")
    ap.add_argument("--body", default="", help="pull request body, for the skip marker")
    args = ap.parse_args(argv)
    root = os.path.abspath(args.root)

    reason = skip_reason(args.body)
    if reason is not None:
        if not reason:
            print(f"'{SKIP_MARKER}' needs a reason on the same line.", file=sys.stderr)
            return 1
        print(f"docs check skipped on request: {reason}")
        return 0

    manifest_paths = ctx.discover_manifests(root)
    manifests = [ctx.load_manifest(p) for p in manifest_paths]
    rel_manifests = {os.path.relpath(p, root) for p in manifest_paths}
    changed = changed_files(root, args.base)
    missing = required_doc_updates(manifests, changed, rel_manifests)
    for did, path, hit in missing:
        print(f"{hit} changed: update {path} ({did}) or the manifest, "
              f"or write '{SKIP_MARKER} <reason>' in the PR body.")
    if missing:
        return 1
    print(f"docs check: {len(changed)} changed file(s), nothing undocumented.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Fail a pull request that changes a documented entrypoint without touching its document.

Usage:
    python3 tools/docs_touch_check.py --base origin/main [--body "$PR_BODY"] [--root DIR]

For every manifest entry that lists `entrypoints`, if a changed file is one of
them (or lives under one of them) and neither the entry's document nor the
entry itself (compared with the base branch's manifest) changed in the same
pull request, the check fails and names the document to update. Editing an
unrelated entry of the index does not count. Entries owned by another repo
are skipped: their document is not here.

The pull request body may contain `docs-check: skip <reason>` to bypass the
check; the reason is mandatory and is printed, so the bypass stays visible.

When the manifest lists `coverage_roots` (for example `["src/"]`), a changed
file under one of them that no entrypoint covers is reported as a GitHub
notice annotation. That is advice only: it never fails the check.
Standard library only; it shells out to git for the changed-file list.
"""
import argparse
import json
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


def base_manifest(root, base, rel_path):
    """The manifest at `rel_path` on the base ref, or None when it does not exist there."""
    out = subprocess.run(["git", "-C", root, "show", f"{base}:{rel_path}"],
                         capture_output=True, text=True)
    if out.returncode != 0:
        return None
    return json.loads(out.stdout)


def changed_entries(head_manifests, base_manifests):
    """Ids of index entries that are new or differ from the same id on the base branch."""
    touched = set()
    for head, base in zip(head_manifests, base_manifests):
        before = {d["id"]: d for d in (base or {}).get("documents", [])}
        for doc in head.get("documents", []):
            if before.get(doc["id"]) != doc:
                touched.add(doc["id"])
    return touched


def required_doc_updates(manifests, changed, touched_entries=()):
    """[(id, doc path, changed file)] for entries whose entrypoints changed but not their doc.

    `touched_entries` are the ids whose index entry changed in the pull request; a
    changed entry counts as a documentation update for that entry only.
    """
    changed = set(changed)
    touched_entries = set(touched_entries)
    out = []
    for m in manifests:
        own = m.get("repo")
        for doc in m.get("documents", []):
            if doc.get("repo") and doc["repo"] != own:
                continue
            if doc.get("path") in changed or doc["id"] in touched_entries:
                continue
            hit = next((f for f in sorted(changed) for ep in doc.get("entrypoints", [])
                        if _under(f, ep)), None)
            if hit:
                out.append((doc["id"], doc["path"], hit))
    return out


def uncovered(manifests, changed):
    """Changed files under a `coverage_roots` dir that no entry's entrypoints cover."""
    roots = [r for m in manifests for r in m.get("coverage_roots", [])]
    entrypoints = [ep for m in manifests for d in m.get("documents", [])
                   for ep in d.get("entrypoints", [])]
    return [f for f in sorted(set(changed))
            if any(_under(f, r) for r in roots) and not any(_under(f, ep) for ep in entrypoints)]


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
    for m in manifests:
        m.pop("_path", None)
    bases = [base_manifest(root, args.base, os.path.relpath(p, root)) for p in manifest_paths]
    changed = changed_files(root, args.base)
    missing = required_doc_updates(manifests, changed, changed_entries(manifests, bases))
    for did, path, hit in missing:
        print(f"{hit} changed: update {path} ({did}) or its index entry, "
              f"or write '{SKIP_MARKER} <reason>' in the PR body.")
    if missing:
        return 1
    for path in uncovered(manifests, changed):
        print(f"::notice file={path}::no document covers {path}; "
              f"consider indexing one with this path as an entrypoint (docs/manifest.json).")
    print(f"docs check: {len(changed)} changed file(s), nothing undocumented.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

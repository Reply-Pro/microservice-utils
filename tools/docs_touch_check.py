#!/usr/bin/env python3
"""Fail a pull request that changes documented code without touching its document.

Usage:
    python3 tools/docs_touch_check.py --base origin/main [--head SHA] [--body "$PR_BODY"] [--root DIR]

The pull request is read as `git diff -U0 <merge base> HEAD`, line by line. A removed line
is resolved against the code and the manifest of the merge base, an added line against the
code and the manifest of HEAD, and each side is also read with the other side's manifest:
deleting or renaming code, or moving an entrypoint away from it, still names the document
that described it. Every changed region names its owner the way `ctx.py` resolves it (one
owner per region: the document that declares the symbol, else the one that lists the file,
else the one that lists the most specific directory; standards/doc-references.md). The
check fails, naming the document, when an owner's document did not change in the pull
request and neither did its index entry (compared with the merge base's manifest). Editing
an unrelated entry of the index does not count. Paths that match `coverage_exclude` need no
document. Documents owned by another repo own nothing here: they are not in this repo.

With `require_doc_content: true` in the manifest, or `"coverage": "enforce"`, only a change
to the document itself counts: editing its index entry (`verified`, aliases, entrypoints)
does not. With `"coverage": "enforce"` the check also fails on every gap, tie, owner that is
not `current` or invalid entry under `coverage_roots`, as `ctx.py --audit-coverage` reports
them, so coverage cannot regress through the manifest either. Both flags apply when either
side of the pull request sets them, and a pull request that removes one, narrows
`coverage_roots` or adds a `coverage_exclude` pattern under enforcement fails: weakening the
gate is a deliberate, visible decision.

The pull request body may contain `docs-check: skip <reason>` to bypass the
check; the reason is mandatory and is printed, so the bypass stays visible. What the check
would have failed on is still printed, as warnings: a skip passes one pull request, it does
not hide the deficit.

When the manifest lists `coverage_roots` (for example `["src/"]`), changed lines under one
of them that no document owns are reported as a GitHub notice annotation. A changed
document whose index entry still carries the `verified` date it had on the base branch gets
a notice too: a reminder to decide whether this change checked the document against the
code, and to move the date only if it did. Nothing is said when the date is already
today's. Both are advice only: they never fail the check.
Standard library only; it shells out to git for the diff and both versions of each file.
"""
import argparse
import collections
import datetime
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ctx  # noqa: E402

SKIP_MARKER = "docs-check: skip"
_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")

# One changed file: its text on the merge base (`old`) and on HEAD (`new`), None where it does
# not exist, and the removed / added line numbers, or None for a change git shows without
# lines (an empty file, a mode change), which belongs to the file's general owner.
FileChange = collections.namedtuple("FileChange", "path old new deleted added")


def _git(root, *args):
    return subprocess.run(["git", "-C", root, *args], capture_output=True, check=False)


def merge_base(root, base, head="HEAD"):
    out = _git(root, "merge-base", base, head)
    if out.returncode != 0:
        raise RuntimeError(f"cannot find the merge base of {base} and {head}: "
                           f"{out.stderr.decode(errors='replace').strip()}")
    return out.stdout.decode().strip()


def show(root, rev, path):
    """The text of `path` at `rev`, or None when it does not exist there."""
    out = _git(root, "show", f"{rev}:{path}")
    return out.stdout.decode("utf-8", errors="replace") if out.returncode == 0 else None


def changed_files(root, mb, head="HEAD"):
    """{path: git status letter} between the merge base and `head`; a rename is a delete and an add."""
    out = _git(root, "diff", "--name-status", "--no-renames", "-z", mb, head)
    if out.returncode != 0:
        raise RuntimeError(f"git diff failed: {out.stderr.decode(errors='replace').strip()}")
    parts = [p for p in out.stdout.decode().split("\0") if p]
    return dict(zip(parts[1::2], parts[0::2]))


def changed_lines(root, mb, path, head="HEAD"):
    """(removed, added) line numbers of one file, or (None, None) when git shows no lines.

    `--text` and `--no-textconv` keep a `.gitattributes` in the pull request from turning a
    change into "binary file differs", which would hide which symbol changed."""
    out = _git(root, "diff", "-U0", "--text", "--no-textconv", "--no-ext-diff", "--no-renames",
               "--no-color", mb, head, "--", path)
    if out.returncode != 0:
        raise RuntimeError(f"git diff of {path} failed: {out.stderr.decode(errors='replace').strip()}")
    removed, added, hunks = set(), set(), False
    for line in out.stdout.decode("utf-8", errors="replace").splitlines():
        m = _HUNK.match(line)
        if m:
            hunks = True
            old, old_n, new, new_n = (int(m.group(1)), int(m.group(2) or 1),
                                      int(m.group(3)), int(m.group(4) or 1))
            removed.update(range(old, old + old_n))
            added.update(range(new, new + new_n))
    return (removed, added) if hunks else (None, None)


def _owned_lines(cov, path, text, lines):
    """[(symbol or None, [ids], line or None)] of the given lines of one version of a file."""
    if lines is None:
        return [(None, cov.general(path)[1], None)]
    owner = cov.line_owners(path, text)
    out = []
    for n in sorted(lines):
        o = owner(n)
        if o is not None:
            out.append((o[0], o[2], n))
    return out


def assess(changes, base_cov, head_cov):
    """Who owns what a pull request changed.

    Returns {owned: {doc id: [labels]}, head_owned: {doc id: [labels]}, unowned: [{path,
    lines}], errors: [text]}. Each side of each change is read with both coverages;
    `head_owned` is what the HEAD manifest gives the added lines, and `unowned` lists added
    lines under a coverage root that it gives to nobody."""
    owned, head_owned, unowned, errors = {}, {}, [], []
    for ch in changes:
        gaps = []
        for text, lines, side in ((ch.old, ch.deleted, "base"), (ch.new, ch.added, "head")):
            if text is None:
                continue
            # Both manifests read both sides: when the pull request moves ownership, the
            # lines ask for the owner they had and for the one they get.
            for cov, which in ((base_cov, "base"), (head_cov, "head")):
                if cov.excluded(ch.path):
                    continue
                try:
                    hits = _owned_lines(cov, ch.path, text, lines)
                except ctx.CoverageError as exc:
                    errors.append(f"{exc} ({side} side)")
                    continue
                for symbol, ids, n in hits:
                    label = f"{ch.path}::{symbol}" if symbol else ch.path
                    for did in ids:
                        targets = [owned] + ([head_owned] if side == which == "head" else [])
                        for table in targets:
                            labels = table.setdefault(did, [])
                            if label not in labels:
                                labels.append(label)
                    if not ids and side == which == "head" and cov.in_roots(ch.path):
                        gaps.append(n)
        if gaps:
            unowned.append({"path": ch.path, "lines": sorted(n for n in gaps if n is not None)})
    return {"owned": owned, "head_owned": head_owned, "unowned": unowned,
            "errors": sorted(set(errors))}


def doc_paths(manifests):
    """{doc id: path} of the local documents of the manifests."""
    own = {m["repo"] for m in manifests if m and m.get("repo")}
    return {d["id"]: d.get("path") for m in manifests if m for d in m.get("documents", [])
            if "id" in d and not ctx.is_external(d, own)}


def missing_updates(owned, updated, paths):
    """[(id, doc path, first changed region)] for owners whose document was not updated."""
    return [(did, paths.get(did), labels[0]) for did, labels in sorted(owned.items())
            if did not in updated]


def file_changes(root, mb, base_cov, head_cov, head="HEAD"):
    """FileChange for each changed file some document could own or a coverage root holds."""
    out = []
    for path, status in sorted(changed_files(root, mb, head).items()):
        if not any(cov.general(path)[1] or cov.symbols(path) or cov.in_roots(path)
                   for cov in (base_cov, head_cov)):
            continue
        old = None if status.startswith("A") else show(root, mb, path)
        new = None if status.startswith("D") else show(root, head, path)
        if (old is None and not status.startswith("A")) or (new is None and not status.startswith("D")):
            raise RuntimeError(f"cannot read both versions of {path} ({status})")
        removed, added = changed_lines(root, mb, path, head)
        out.append(FileChange(path, old, new, removed, added))
    return out


def manifests_at(root, rev):
    """[(path, manifest)] of every docs/**/manifest.json committed at `rev`, the master first.

    Each side of the pull request is discovered on its own: a manifest moved or renamed by
    the pull request keeps its merge-base content on the base side."""
    out = _git(root, "ls-tree", "-r", "--name-only", "-z", rev, "--", "docs")
    if out.returncode != 0:
        raise RuntimeError(f"cannot list docs/ at {rev}: {out.stderr.decode(errors='replace').strip()}")
    paths = [p for p in out.stdout.decode().split("\0") if p.endswith("/manifest.json")]
    paths.sort(key=lambda p: (p != "docs/manifest.json", p))
    return [(p, json.loads(show(root, rev, p))) for p in paths]


def _entries(manifests):
    return {d["id"]: d for m in manifests if m for d in m.get("documents", []) if "id" in d}


def changed_entries(head_manifests, base_manifests):
    """Ids of index entries that are new or differ from the same id on the base branch,
    whichever manifest holds them on either side.

    A deleted entry is not in the result: deleting it does not update its document. The
    document is then deleted too (its content changed, which counts) or left unindexed,
    which the lint rejects; counting the deletion would let a pull request drop the entry
    to skip the update."""
    before, after = _entries(base_manifests), _entries(head_manifests)
    return {did for did, doc in after.items() if before.get(did) != doc}


def unbumped_verified(head_manifests, base_manifests, changed, today=None):
    """[(id, doc path, old date)] for local entries whose document changed in the pull
    request while their `verified` date stayed what it was on the base branch, unless that
    date is already today's (a stacked pull request has nothing to bump)."""
    changed = set(changed)
    today = str(today or datetime.date.today())
    out = []
    before = _entries(base_manifests)
    for head in head_manifests:
        own = head.get("repo")
        for doc in head.get("documents", []):
            if doc.get("repo") and doc.get("repo") != own:
                continue
            if doc.get("path") not in changed or "verified" not in doc:
                continue
            if doc["verified"] != today and before.get(doc["id"], {}).get("verified") == doc["verified"]:
                out.append((doc["id"], doc["path"], doc["verified"]))
    return out


def skip_reason(body):
    """The text after the marker, or None when there is no marker."""
    if not body or SKIP_MARKER not in body:
        return None
    rest = body.split(SKIP_MARKER, 1)[1].strip()
    return rest.splitlines()[0].strip() if rest else ""


def _spans(lines):
    out = []
    for n in lines:
        if out and n == out[-1][1] + 1:
            out[-1] = (out[-1][0], n)
        else:
            out.append((n, n))
    return ", ".join(f"{a}" if a == b else f"{a}-{b}" for a, b in out)


def _flag(manifests, name, value):
    return any(m.get(name) == value for m in manifests if m)


def policy_regressions(bases, heads):
    """Ways the pull request weakens the gate the merge base enforces: a flag removed,
    `coverage_roots` narrowed, a `coverage_exclude` pattern added."""
    out = []
    base_enforce, head_enforce = _flag(bases, "coverage", "enforce"), _flag(heads, "coverage", "enforce")
    if base_enforce and not head_enforce:
        out.append('this pull request removes "coverage": "enforce" from the manifest')
    if (_flag(bases, "require_doc_content", True) and not head_enforce
            and not _flag(heads, "require_doc_content", True)):
        out.append("this pull request removes require_doc_content from the manifest")
    if base_enforce:
        head_roots = [r for m in heads for r in m.get("coverage_roots", []) if isinstance(r, str)]
        for root in (r for m in bases if m for r in m.get("coverage_roots", [])):
            if not any(ctx.covers(root.rstrip("/"), r) for r in head_roots):
                out.append(f"this pull request narrows the coverage scope: {root} is no longer "
                           f"under coverage_roots")
        base_ex = {g for m in bases if m for g in (m.get("coverage_exclude") or [])}
        for glob in sorted({g for m in heads for g in (m.get("coverage_exclude") or [])
                            if isinstance(g, str)} - base_ex):
            out.append(f"this pull request narrows the coverage scope: coverage_exclude adds {glob}")
    return out


def run(root, base, head="HEAD", today=None):
    """(failures, notices, summary) of the check for `head` against `base`.

    A failure is a line that makes the check fail; a notice is advice. With
    `require_doc_content: true`, or `"coverage": "enforce"`, on either side, an owner's
    document counts as updated only when its content changed: editing its index entry
    (`verified`, aliases, entrypoints) is not an update. With `"coverage": "enforce"` every
    region of every file under `coverage_roots` must also have exactly one current owner
    after the change (the full `ctx.py --audit-coverage`, on the head commit), so a
    manifest change that drops coverage fails even when no code changed."""
    try:
        mb = merge_base(root, base, head)
        head_pairs, base_pairs = manifests_at(root, head), manifests_at(root, mb)
        heads, bases = [m for _, m in head_pairs], [m for _, m in base_pairs]
        base_cov, head_cov = ctx.Coverage(bases), ctx.Coverage(heads)
        changes = file_changes(root, mb, base_cov, head_cov, head)
        changed = list(changed_files(root, mb, head))
    except (RuntimeError, ValueError, OSError, TypeError) as exc:
        return [f"::error::docs check could not read the pull request: {exc}"], [], None
    both = bases + heads
    enforce = _flag(both, "coverage", "enforce")
    require_content = enforce or _flag(both, "require_doc_content", True)
    result = assess(changes, base_cov, head_cov)
    failures = [f"::error::{err}; the docs check cannot tell which document owns these lines."
                for err in result["errors"]]
    failures += [f"::error::{msg}; write '{SKIP_MARKER} <reason>' to do it deliberately."
                 for msg in policy_regressions(bases, heads)]

    base_paths, head_paths = doc_paths(bases), doc_paths(heads)
    paths = {**base_paths, **head_paths}
    updated = {did for did in result["owned"] if did in paths
               and show(root, mb, base_paths.get(did) or paths[did]) !=
               show(root, head, head_paths.get(did) or paths[did])}
    if not require_content:
        updated |= changed_entries(heads, bases)
    for did, path, label in missing_updates(result["owned"], updated, paths):
        if require_content:
            failures.append(f"{label} changed: update {path} ({did}); its index entry alone does "
                            f"not count. Or write '{SKIP_MARKER} <reason>' in the PR body.")
        else:
            failures.append(f"{label} changed: update {path} ({did}) or its index entry, "
                            f"or write '{SKIP_MARKER} <reason>' in the PR body.")

    notices, gaps = [], 0
    if enforce:
        for d in ctx.audit(root, heads, rev=head)["deficits"]:
            failures.append(f"::error file={d['path']}::{d['message']}")
    else:
        for gap in result["unowned"]:
            gaps += 1
            where = f"lines {_spans(gap['lines'])} of {gap['path']}" if gap["lines"] else gap["path"]
            notices.append(f"::notice file={gap['path']}::no document owns {where}; consider "
                           f"indexing one with this path (or path::Name) as an entrypoint "
                           f"(docs/manifest.json).")
        for did, labels in sorted(result["head_owned"].items()):
            status = head_cov.status.get(did)
            if status == "current":
                continue
            by_file = {}
            for label in labels:
                by_file.setdefault(label.split("::")[0], []).append(label)
            for path, regions in by_file.items():
                gaps += 1
                notices.append(f"::notice file={path}::{', '.join(regions)}: {did} is "
                               f"{status or 'without a status'}; only 'current' documents own "
                               f"code.")
    for rel, head_m in head_pairs:
        for did, path, old in unbumped_verified([head_m], bases, changed, today):
            notices.append(f"::notice file={rel}::{path} changed but {did} still says "
                           f"verified {old}; if this change checked it against the code, set the "
                           f"date to today, otherwise leave it.")
    if gaps:
        summary = (f"{len(changed)} changed file(s), every owner's document updated; "
                   f"{gaps} changed region(s) without a current owner (see the notices).")
    else:
        summary = f"{len(changed)} changed file(s), nothing undocumented."
    return failures, notices, summary


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", default=ctx.DEFAULT_ROOT)
    ap.add_argument("--base", required=True, help="base ref of the pull request")
    ap.add_argument("--head", default="HEAD", help="the pull request's head commit (default HEAD)")
    ap.add_argument("--body", default="", help="pull request body, for the skip marker")
    args = ap.parse_args(argv)
    root = os.path.abspath(args.root)

    reason = skip_reason(args.body)
    if reason == "":
        print(f"'{SKIP_MARKER}' needs a reason on the same line.", file=sys.stderr)
        return 1
    failures, notices, summary = run(root, args.base, args.head)
    if reason is not None:
        # The skip passes the check, never hides it: what it skipped stays in the report,
        # and the next pull request is checked again from scratch.
        for line in failures:
            text = line.split("::", 2)[-1] if line.startswith("::") else line
            print(f"::warning::skipped: {text}")
        for line in notices:
            print(line)
        print(f"docs check skipped on request: {reason}")
        return 0
    for line in failures + notices:
        print(line)
    if failures:
        return 1
    print(f"docs check: {summary}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

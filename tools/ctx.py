#!/usr/bin/env python3
"""ctx.py: lookup and lint for a repo's document index (docs/**/manifest.json).

This file is vendored into every repo unchanged. It needs no install, no network
and no workspace: everything is relative to the repo root (the parent of the
directory this file lives in, or --root).

Usage:
    ctx.py <intent|alias|text>     resolve a topic -> document path (or link) + entrypoints
    ctx.py --for <file>            the document(s) whose entrypoints cover a file you are
                                   about to edit; exit 1 when none does (read before editing)
    ctx.py --list                  list intents and documents
    ctx.py --lint [--stale-days N] validate the index; exit 1 on errors (the CI gate).
                                   An entry `verified` more than N days ago (default 90) is
                                   a warning, a malformed or future date an error
    ctx.py --similar "<text>"      documents that already cover a topic (before creating one)
    ctx.py --dups [--threshold N]  near-duplicate documents (default 0.6)
    ctx.py --sync-handbook         clone or refresh the engineering handbook into a local cache
                                   and map it, so handbook documents resolve to files here.
                                   The one command that uses the network; never run by CI

Entries that carry `repo` point at a document owned by another repository. They
resolve to a GitHub link, or to a file when a local workspace map (the JSON file
named by CTX_WORKSPACE, or ~/.config/ctx/workspace.json) knows that repo. The
format is described in the handbook: standards/doc-references.md.
"""
import argparse
import datetime
import glob
import json
import os
import re
import shutil
import subprocess
import sys

DEFAULT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GITHUB_ORG = os.environ.get("CTX_GITHUB_ORG", "Reply-Pro")
ORPHAN_EXCLUDES = {"README.md", "INDEX.md", "index.md"}
STALE_DAYS = 90
STATUSES = ("current", "proposed", "historical")
HANDBOOK_REPO = "engineering-handbook"
_LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
_WORD = re.compile(r"[a-z0-9]+")


# --------------------------------------------------------------------------- #
# Loading
# --------------------------------------------------------------------------- #
def discover_manifests(root):
    found = sorted(glob.glob(os.path.join(root, "docs", "**", "manifest.json"), recursive=True))
    master = os.path.join(root, "docs", "manifest.json")
    if master in found:
        found.remove(master)
        found.insert(0, master)
    return found


def load_manifest(path):
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    data["_path"] = path
    return data


def _env_home(env, home):
    return (os.environ if env is None else env), (os.path.expanduser("~") if home is None else home)


def workspace_map_path(env=None, home=None):
    env, home = _env_home(env, home)
    return env.get("CTX_WORKSPACE") or os.path.join(home, ".config", "ctx", "workspace.json")


def load_workspace_map(env=None, home=None):
    """repo name -> local checkout. Explicit, local, never required."""
    try:
        with open(workspace_map_path(env, home), encoding="utf-8") as fh:
            return dict(json.load(fh).get("repos", {}))
    except (OSError, ValueError):
        return {}


def cache_dir(repo, env=None, home=None):
    """Where --sync-handbook keeps its clone: $XDG_CACHE_HOME/ctx/<repo> or ~/.cache/ctx/<repo>."""
    env, home = _env_home(env, home)
    return os.path.join(env.get("XDG_CACHE_HOME") or os.path.join(home, ".cache"), "ctx", repo)


def _save_workspace_map(path, repo, checkout, env=None, home=None):
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        data = {}
    data.setdefault("repos", {})[repo] = checkout
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
        fh.write("\n")


def _git(argv, gh_available):
    """git with gh's credential helper, so a private repo cloned through gh can also be pulled."""
    if gh_available:
        return ["git", "-c", "credential.helper=", "-c", "credential.helper=!gh auth git-credential", *argv]
    return ["git", *argv]


def sync_repo(repo, env=None, home=None, run=subprocess.run, remote=None, gh_available=None):
    """Clone or fast-forward `repo` into the local cache and record it in the workspace map.

    A checkout the map already points at (someone's working copy) is left untouched; a
    mapped path that no longer exists is replaced by the cache. Clones and pulls through
    `gh` so the user's GitHub login is reused; without gh, plain git over HTTPS. Returns an
    exit code. `remote` (a URL or path) replaces the GitHub remote, for tests.
    """
    env, home = _env_home(env, home)
    if gh_available is None:
        gh_available = shutil.which("gh") is not None
    map_path = workspace_map_path(env, home)
    dest = cache_dir(repo, env, home)
    mapped = load_workspace_map(env, home).get(repo)
    if mapped and mapped != dest and os.path.isdir(mapped):
        print(f"{repo} is already mapped to {mapped}; left as it is. Pull it yourself when you want it fresh.")
        return 0

    def report(res, what):
        if res.returncode != 0:
            print(f"{what} failed:\n{(res.stderr or res.stdout or '').strip()}", file=sys.stderr)
        return res.returncode == 0

    if os.path.isdir(os.path.join(dest, ".git")):
        res = run(_git(["-C", dest, "pull", "--ff-only"], gh_available), capture_output=True, text=True)
        if not report(res, "pull"):
            return 1
    else:
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        if gh_available and remote is None:
            argv = ["gh", "repo", "clone", f"{GITHUB_ORG}/{repo}", dest, "--", "--depth", "1"]
        else:
            argv = ["git", "clone", remote or f"https://github.com/{GITHUB_ORG}/{repo}.git", dest, "--depth", "1"]
        if not report(run(argv, capture_output=True, text=True), "clone"):
            return 1
    _save_workspace_map(map_path, repo, dest)
    state = git_state(dest)
    at = f" @ {state['sha']} ({state['branch']})" if state else ""
    print(f"{repo} -> {dest}{at}\nmapped in {map_path}; handbook documents now resolve to files here.")
    return 0


class Index:
    def __init__(self):
        self.docs = {}        # id -> entry
        self.intents = {}     # intent -> [ids]
        self.aliases = {}     # alias (lower) -> [ids]
        self.own_repos = set()  # repo names declared by the manifests themselves

    @staticmethod
    def topic(doc):
        return doc.get("topic") or doc.get("feature") or ""


def build_index(manifests):
    idx = Index()
    for m in manifests:
        if m.get("repo"):
            idx.own_repos.add(m["repo"])
        for intent, ids in (m.get("intent_map") or {}).items():
            idx.intents.setdefault(intent, []).extend(ids)
        for doc in m.get("documents", []):
            idx.docs[doc["id"]] = doc
            for alias in doc.get("aliases", []):
                idx.aliases.setdefault(alias.lower(), []).append(doc["id"])
    return idx


# --------------------------------------------------------------------------- #
# Location of a document: local file, mapped checkout, or link
# --------------------------------------------------------------------------- #
def is_external(doc, own_repos):
    return bool(doc.get("repo")) and doc["repo"] not in own_repos


def locate(doc, root, wsmap, own_repos):
    """Return (kind, path) with kind in local | mapped | link."""
    path = doc.get("path", "")
    if not is_external(doc, own_repos):
        return "local", os.path.join(root, path)
    checkout = (wsmap or {}).get(doc["repo"])
    if checkout:
        return "mapped", os.path.join(checkout, path)
    return "link", f"https://github.com/{GITHUB_ORG}/{doc['repo']}/blob/main/{path}"


def git_state(path):
    """{sha, branch, dirty} of the checkout containing `path`, or None outside git."""
    def git(*args):
        out = subprocess.run(["git", "-C", path, *args], capture_output=True, text=True)
        return out.stdout.strip() if out.returncode == 0 else None
    sha = git("rev-parse", "--short=10", "HEAD")
    if sha is None:
        return None
    status = git("status", "--porcelain")
    return {"sha": sha, "branch": git("rev-parse", "--abbrev-ref", "HEAD") or "?",
            "dirty": bool(status)}


def provenance(r, root):
    """One line saying which checkout, commit and branch a hit was resolved against."""
    if r["where"] == "link":
        return f"{r['repo']} on GitHub, main"
    if r["where"] == "mapped":
        who, base = f"{r['repo']} checkout {r['checkout']}", r["checkout"]
    else:
        who, base = "this repo", root
    state = git_state(base) if base else None
    if state is None:
        return f"{who} (not a git checkout)"
    notes = [state["branch"]]
    if r["where"] == "mapped" and state["branch"] != "main":
        notes[0] += ", not main: may hold rules not merged yet"
    if state["dirty"]:
        notes.append("uncommitted changes")
    return f"{who} @ {state['sha']} ({', '.join(notes)})"


def adopted_handbook_commit(root):
    """The handbook commit `tools/HANDBOOK_VERSION` says the copied tools came from, or None."""
    try:
        with open(os.path.join(root, "tools", "HANDBOOK_VERSION"), encoding="utf-8") as fh:
            m = re.search(r"handbook commit:\s*([0-9a-f]+)", fh.readline())
    except OSError:
        return None
    return m.group(1) if m else None


def _result(idx, doc, root, wsmap):
    where, path = locate(doc, root, wsmap, idx.own_repos)
    return {
        "id": doc["id"],
        "where": where,
        "repo": doc.get("repo", ""),
        "checkout": (wsmap or {}).get(doc.get("repo", ""), ""),
        "topic": idx.topic(doc),
        "kind": doc.get("kind", ""),
        "status": doc.get("status", ""),
        "superseded_by": doc.get("superseded_by", ""),
        "path": path,
        "entrypoints": doc.get("entrypoints", []),
        "depends_on": doc.get("depends_on", []),
        "verified": doc.get("verified", ""),
    }


# --------------------------------------------------------------------------- #
# File -> document: which document describes the file I am about to edit
# --------------------------------------------------------------------------- #
def covers(path, entrypoint):
    """True when `path` is the entrypoint or lives under it (whole path segments only)."""
    ep = entrypoint.rstrip("/")
    return path == ep or path.startswith(ep + "/")


def split_entrypoint(entrypoint):
    """`<repo>:<path>` -> (repo, path); a plain path -> (None, path).

    The prefix names the repo the code lives in when that is not the repo that owns the
    manifest (a contract kept in the handbook, implemented in a service)."""
    head, sep, tail = entrypoint.partition(":")
    if sep and head and "/" not in head:
        return head, tail
    return None, entrypoint


def local_entrypoints(doc, own_repos):
    """The entrypoints of `doc` that are paths in this repo: plain ones, and prefixed ones
    whose repo is one of `own_repos`. Another repo's code is not here and never matches."""
    out = []
    for ep in doc.get("entrypoints", []):
        repo, path = split_entrypoint(ep)
        if repo is None or repo in own_repos:
            out.append(path)
    return out


def _repo_relative(path, root):
    if os.path.isabs(path):
        path = os.path.relpath(path, root)
    return os.path.normpath(path).replace(os.sep, "/")


def for_path(idx, path, root, wsmap=None):
    """Documents whose entrypoints cover `path` (repo-relative, absolute or ./-prefixed).

    Most specific first: a document that lists the file itself comes before one that
    covers its directory, so the narrower document is the one read first.
    """
    rel = _repo_relative(path, root)
    hits = []
    for doc in idx.docs.values():
        matched = [ep for ep in local_entrypoints(doc, idx.own_repos) if covers(rel, ep)]
        if matched:
            hits.append((max(len(ep.rstrip("/")) for ep in matched), _result(idx, doc, root, wsmap)))
    return [r for _, r in sorted(hits, key=lambda h: -h[0])]


def resolve(idx, query, root, wsmap=None):
    """Exact intent, then alias, then substring over id / topic / aliases."""
    q = query.strip().lower()
    if q in idx.intents:
        ids = list(idx.intents[q])
    elif q in idx.aliases:
        ids = list(idx.aliases[q])
    else:
        ids = [did for did, doc in idx.docs.items()
               if q in " ".join([did, idx.topic(doc), *doc.get("aliases", [])]).lower()]
    out, seen = [], set()
    for did in ids:
        if did in idx.docs and did not in seen:
            seen.add(did)
            out.append(_result(idx, idx.docs[did], root, wsmap))
    return out


# --------------------------------------------------------------------------- #
# Lint
# --------------------------------------------------------------------------- #
def _links_in(path):
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return []
    out = []
    for target in _LINK.findall(text):
        if "://" in target or target.startswith(("#", "mailto:")):
            continue
        out.append(target.split("#", 1)[0])
    return [t for t in out if t]


def _is_local_id(did, own_repos):
    """Ids are <repo>.<topic>; with no declared repo every id counts as local."""
    if not own_repos:
        return True
    return did.split(".", 1)[0] in own_repos


_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _parse_date(value):
    """A `YYYY-MM-DD` date, or None. fromisoformat alone also takes `20261019` and week dates."""
    value = str(value)
    if not _DATE.fullmatch(value):
        return None
    try:
        return datetime.date.fromisoformat(value)
    except ValueError:
        return None


def _check_verified(doc, today, stale_days):
    """(error, warning) for the entry's `verified` date; both None when absent or fresh."""
    if "verified" not in doc:
        return None, None
    when = _parse_date(doc["verified"])
    if when is None:
        return f"'verified' must be a YYYY-MM-DD date, got {doc['verified']!r}", None
    if when > today:
        return f"'verified' is in the future: {doc['verified']}", None
    age = (today - when).days
    if age > stale_days:
        return None, f"verified {age} days ago ({doc['verified']}); re-check it against main"
    return None, None


def lint(root, manifest_paths, wsmap=None, managed_dirs=None, excludes=None,
         today=None, stale_days=STALE_DAYS):
    errors, warnings = [], []
    manifests = []
    for mp in manifest_paths:
        try:
            manifests.append(load_manifest(mp))
        except ValueError as exc:
            errors.append(f"{mp}: invalid JSON ({exc})")
    idx = build_index(manifests)
    rel = lambda p: os.path.relpath(p, root)  # noqa: E731
    if today:
        parsed = _parse_date(today)
        if parsed is None:
            raise ValueError(f"today must be a YYYY-MM-DD date, got {today!r}")
        today = parsed
    else:
        today = datetime.date.today()

    seen_ids, registered, pending = set(), set(), {}
    for m in manifests:
        mp = rel(m["_path"])
        for p in m.get("pending", []):
            pending[os.path.realpath(os.path.join(root, p))] = p
        aliases_here = {}
        for doc in m.get("documents", []):
            did = doc.get("id")
            if not did or "path" not in doc:
                errors.append(f"{mp}: entry without id or path: {doc}")
                continue
            if did in seen_ids:
                errors.append(f"{mp}: duplicate id '{did}'")
            seen_ids.add(did)
            for a in doc.get("aliases", []):
                if a.lower() in aliases_here:
                    errors.append(f"{mp}: duplicate alias '{a}' ({aliases_here[a.lower()]}, {did})")
                aliases_here.setdefault(a.lower(), did)
            if os.path.isabs(doc["path"]):
                errors.append(f"{mp}: id '{did}' has an absolute path: {doc['path']}")
                continue
            err, warn = _check_verified(doc, today, stale_days)
            if err:
                errors.append(f"{mp}: id '{did}': {err}")
            if warn:
                warnings.append(f"{mp}: id '{did}' {warn}")
            kind, where = locate(doc, root, wsmap, idx.own_repos)
            if kind == "local":
                registered.add(os.path.realpath(where))
                if not os.path.isfile(where):
                    errors.append(f"{mp}: id '{did}' path missing: {doc['path']}")
                    continue
                for target in _links_in(where):
                    base = root if target.startswith("/") else os.path.dirname(where)
                    if not os.path.exists(os.path.join(base, target.lstrip("/"))):
                        errors.append(f"{rel(where)}: broken link '{target}' (in '{did}')")
            elif kind == "mapped" and not os.path.isfile(where):
                warnings.append(f"{mp}: id '{did}' not found in mapped checkout: {where}")
            if kind == "local":
                for ep in doc.get("entrypoints", []):
                    repo_name, ep_path = split_entrypoint(ep)
                    if repo_name is not None and not ep_path.strip("/"):
                        errors.append(f"{mp}: id '{did}' entrypoint '{ep}' has an empty path after "
                                      f"the repo prefix")
                        continue
                    if repo_name is None or repo_name in idx.own_repos:
                        base = root
                    else:
                        base = (wsmap or {}).get(repo_name)
                        if not base:
                            continue  # another repo, not checked out here: taken on trust
                    if not os.path.exists(os.path.join(base, ep_path.rstrip("/"))):
                        warnings.append(f"{mp}: id '{did}' entrypoint does not exist: {ep}")
            for dep in doc.get("depends_on", []):
                if dep not in idx.docs and _is_local_id(dep, idx.own_repos):
                    errors.append(f"{mp}: id '{did}' depends_on unknown '{dep}'")
            status = doc.get("status")
            if status is not None and status not in STATUSES:
                errors.append(f"{mp}: id '{did}' status must be one of {', '.join(STATUSES)}, "
                              f"got {status!r}")
            by = doc.get("superseded_by")
            if by:
                if by not in idx.docs and _is_local_id(by, idx.own_repos):
                    errors.append(f"{mp}: id '{did}' superseded_by unknown '{by}'")
                if status == "current":
                    errors.append(f"{mp}: id '{did}' is 'current' but has superseded_by '{by}'; "
                                  f"a superseded document is 'historical'")
        for intent, ids in (m.get("intent_map") or {}).items():
            for tid in ids:
                if tid not in idx.docs:
                    errors.append(f"{mp}: intent '{intent}' -> unknown id '{tid}'")

    # Every .md under the managed dirs must be indexed or explicitly pending.
    excludes = excludes or ORPHAN_EXCLUDES
    if managed_dirs is None:
        managed_dirs = [os.path.join(root, "docs")]
    for d in managed_dirs:
        for md in sorted(glob.glob(os.path.join(d, "**", "*.md"), recursive=True)):
            if os.path.basename(md) in excludes:
                continue
            real = os.path.realpath(md)
            if real in registered:
                continue
            if real in pending:
                warnings.append(f"pending (acknowledged, not indexed yet): {rel(md)}")
                pending.pop(real)
            else:
                errors.append(f"not indexed: {rel(md)} (index it, or list it under 'pending')")
    for real, p in pending.items():
        if not os.path.exists(real) or real in registered:
            warnings.append(f"stale 'pending' entry: {p}")
    return {"errors": errors, "warnings": warnings}


# --------------------------------------------------------------------------- #
# Near-duplicates (shingling + Jaccard), used by --dups and --similar
# --------------------------------------------------------------------------- #
def normalize(text):
    return _WORD.findall(text.lower())


def shingles(tokens, k=5):
    if len(tokens) < k:
        return {" ".join(tokens)} if tokens else set()
    return {" ".join(tokens[i:i + k]) for i in range(len(tokens) - k + 1)}


def jaccard(a, b):
    union = len(a | b)
    return len(a & b) / union if union else 0.0


def find_near_dups(path_text_map, k=5, threshold=0.6):
    sh = {p: shingles(normalize(t), k) for p, t in path_text_map.items()}
    paths = sorted(sh)
    pairs = [(a, b, round(jaccard(sh[a], sh[b]), 3))
             for i, a in enumerate(paths) for b in paths[i + 1:]
             if jaccard(sh[a], sh[b]) >= threshold]
    return sorted(pairs, key=lambda x: -x[2])


def _read(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return ""


def similar(idx, query, root, wsmap=None, k=4, threshold=0.05):
    qsh, qtokens = shingles(normalize(query), k), set(normalize(query))
    scored = []
    for doc in idx.docs.values():
        kind, where = locate(doc, root, wsmap, idx.own_repos)
        text = _read(where) if kind != "link" else ""
        score = jaccard(qsh, shingles(normalize(text), k))
        if any(set(normalize(a)) & qtokens for a in doc.get("aliases", [])):
            score += 0.5
        if score >= threshold:
            r = _result(idx, doc, root, wsmap)
            r["score"] = round(score, 3)
            scored.append(r)
    return sorted(scored, key=lambda x: -x["score"])


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _verified_label(value, today, stale_days):
    """'2026-06-01 (140 days ago, stale)': the age is what tells a reader how far to trust it."""
    when = _parse_date(value)
    if when is None:
        return f"{value} (not a date)"
    age = (today - when).days
    flag = ", stale" if age > stale_days else ""
    return f"{value} ({age} days ago{flag})"


def _print(results, today=None, stale_days=STALE_DAYS, root=None):
    if not results:
        print("(no matches)")
    today = _parse_date(today) if today else datetime.date.today()
    for r in results:
        print(f"\n### {r['id']}  [{r['topic']}]")
        print(f"  kind:        {r.get('kind') or 'not declared'}")
        status = r.get("status") or "not declared"
        if r.get("superseded_by"):
            status += f", superseded by {r['superseded_by']}"
        print(f"  status:      {status}")
        print(f"  path:        {r['path']}")
        for key in ("entrypoints", "depends_on"):
            if r.get(key):
                print(f"  {key + ':':13}{', '.join(r[key])}")
        verified = _verified_label(r["verified"], today, stale_days) if r.get("verified") else "none recorded"
        print(f"  verified:    {verified}")
        print(f"  source:      {provenance(r, root)}")
        if "score" in r:
            print(f"  score:       {r['score']}")
    adopted = adopted_handbook_commit(root) if root else None
    if results and adopted:
        print(f"\nhandbook tools in this repo were copied from commit {adopted}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("query", nargs="?")
    ap.add_argument("--for", dest="for_file", metavar="FILE",
                    help="document(s) whose entrypoints cover FILE; exit 1 when none does")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--lint", action="store_true")
    ap.add_argument("--stale-days", type=int, default=STALE_DAYS,
                    help=f"warn when an entry was verified more than N days ago (default {STALE_DAYS})")
    ap.add_argument("--dups", action="store_true")
    ap.add_argument("--similar", metavar="TEXT")
    ap.add_argument("--threshold", type=float, default=0.6)
    ap.add_argument("--root", default=DEFAULT_ROOT)
    ap.add_argument("--sync-handbook", action="store_true",
                    help="clone or refresh the engineering handbook into a local cache and map it (uses the network)")
    args = ap.parse_args(argv)
    if args.sync_handbook:
        return sync_repo(HANDBOOK_REPO)
    root = os.path.abspath(args.root)
    manifest_paths = discover_manifests(root)
    if not manifest_paths:
        print(f"No manifests found under {root}/docs", file=sys.stderr)
        return 2
    wsmap = load_workspace_map()

    if args.lint:
        report = lint(root, manifest_paths, wsmap, stale_days=args.stale_days)
        for w in report["warnings"]:
            print(f"WARN  {w}")
        for e in report["errors"]:
            print(f"ERROR {e}")
        print(f"\n{len(report['errors'])} error(s), {len(report['warnings'])} warning(s)")
        return 1 if report["errors"] else 0

    manifests = []
    for mp in manifest_paths:
        try:
            manifests.append(load_manifest(mp))
        except ValueError as exc:
            print(f"ERROR {mp}: invalid JSON ({exc})", file=sys.stderr)
            return 1
    idx = build_index(manifests)

    if args.dups:
        texts = {}
        for doc in idx.docs.values():
            kind, where = locate(doc, root, wsmap, idx.own_repos)
            if kind != "link" and _read(where):
                texts[where] = _read(where)
        pairs = find_near_dups(texts, threshold=args.threshold)
        if not pairs:
            print(f"No near-duplicates >= {args.threshold}")
        for a, b, score in pairs:
            print(f"{score}  {os.path.relpath(a, root)}  <->  {os.path.relpath(b, root)}")
        return 0
    if args.similar is not None:
        _print(similar(idx, args.similar, root, wsmap), stale_days=args.stale_days, root=root)
        return 0
    if args.for_file:
        hits = for_path(idx, args.for_file, root, wsmap)
        if not hits:
            print(f"(no document lists {_repo_relative(args.for_file, root)} as an entrypoint; "
                  f"try `--similar` before writing one)")
            return 1
        _print(hits, stale_days=args.stale_days, root=root)
        return 0
    if args.list:
        print("# Intents")
        for intent in sorted(idx.intents):
            print(f"  {intent:32} -> {', '.join(idx.intents[intent])}")
        print("\n# Documents")
        for did in sorted(idx.docs):
            doc = idx.docs[did]
            print(f"  {did}{'  (' + doc['repo'] + ')' if is_external(doc, idx.own_repos) else ''}")
        return 0
    if args.query:
        _print(resolve(idx, args.query, root, wsmap), stale_days=args.stale_days, root=root)
        return 0
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())

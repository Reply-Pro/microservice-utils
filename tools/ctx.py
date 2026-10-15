#!/usr/bin/env python3
"""ctx.py: lookup and lint for a repo's document index (docs/**/manifest.json).

This file is vendored into every repo unchanged. It needs no install, no network
and no workspace: everything is relative to the repo root (the parent of the
directory this file lives in, or --root).

Usage:
    ctx.py <intent|alias|text>     resolve a topic -> document path (or link) + entrypoints
    ctx.py --list                  list intents and documents
    ctx.py --lint                  validate the index; exit 1 on errors (the CI gate)
    ctx.py --similar "<text>"      documents that already cover a topic (before creating one)
    ctx.py --dups [--threshold N]  near-duplicate documents (default 0.6)

Entries that carry `repo` point at a document owned by another repository. They
resolve to a GitHub link, or to a file when a local workspace map (the JSON file
named by CTX_WORKSPACE, or ~/.config/ctx/workspace.json) knows that repo. The
format is described in the handbook: standards/doc-references.md.
"""
import argparse
import glob
import json
import os
import re
import sys

DEFAULT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GITHUB_ORG = os.environ.get("CTX_GITHUB_ORG", "Reply-Pro")
ORPHAN_EXCLUDES = {"README.md", "INDEX.md", "index.md"}
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


def load_workspace_map(env=None, home=None):
    """repo name -> local checkout. Explicit, local, never required."""
    env = os.environ if env is None else env
    home = os.path.expanduser("~") if home is None else home
    path = env.get("CTX_WORKSPACE") or os.path.join(home, ".config", "ctx", "workspace.json")
    try:
        with open(path, encoding="utf-8") as fh:
            return dict(json.load(fh).get("repos", {}))
    except (OSError, ValueError):
        return {}


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


def _result(idx, doc, root, wsmap):
    return {
        "id": doc["id"],
        "topic": idx.topic(doc),
        "path": locate(doc, root, wsmap, idx.own_repos)[1],
        "entrypoints": doc.get("entrypoints", []),
        "depends_on": doc.get("depends_on", []),
    }


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


def lint(root, manifest_paths, wsmap=None, managed_dirs=None, excludes=None):
    errors, warnings = [], []
    manifests = []
    for mp in manifest_paths:
        try:
            manifests.append(load_manifest(mp))
        except ValueError as exc:
            errors.append(f"{mp}: invalid JSON ({exc})")
    idx = build_index(manifests)
    rel = lambda p: os.path.relpath(p, root)  # noqa: E731

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
            for dep in doc.get("depends_on", []):
                if dep not in idx.docs and _is_local_id(dep, idx.own_repos):
                    errors.append(f"{mp}: id '{did}' depends_on unknown '{dep}'")
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
def _print(results):
    if not results:
        print("(no matches)")
    for r in results:
        print(f"\n### {r['id']}  [{r['topic']}]\n  path:        {r['path']}")
        for key in ("entrypoints", "depends_on"):
            if r.get(key):
                print(f"  {key + ':':13}{', '.join(r[key])}")
        if "score" in r:
            print(f"  score:       {r['score']}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("query", nargs="?")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--lint", action="store_true")
    ap.add_argument("--dups", action="store_true")
    ap.add_argument("--similar", metavar="TEXT")
    ap.add_argument("--threshold", type=float, default=0.6)
    ap.add_argument("--root", default=DEFAULT_ROOT)
    args = ap.parse_args(argv)
    root = os.path.abspath(args.root)
    manifest_paths = discover_manifests(root)
    if not manifest_paths:
        print(f"No manifests found under {root}/docs", file=sys.stderr)
        return 2
    wsmap = load_workspace_map()

    if args.lint:
        report = lint(root, manifest_paths, wsmap)
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
        _print(similar(idx, args.similar, root, wsmap))
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
        _print(resolve(idx, args.query, root, wsmap))
        return 0
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""ctx.py: lookup and lint for a repo's document index (docs/**/manifest.json).

This file is vendored into every repo unchanged. It needs no install, no network
and no workspace: everything is relative to the repo root (the parent of the
directory this file lives in, or --root).

Usage:
    ctx.py <intent|alias|text>     resolve a topic -> document path (or link) + entrypoints;
                                   free text prints the best LOOKUP_HITS, two lines each
    ctx.py --for <file>            the document(s) whose entrypoints cover a file you are
                                   about to edit, and who owns each region of it (symbols,
                                   the rest of the file, gaps, ties); exit 1 when no document
                                   lists it, 0 when the file is excluded from coverage
    ctx.py --list                  list intents and documents
    ctx.py --area <name>           every document of one area, one line each (id + topic):
                                   where to look when a lookup did not list the document
    ctx.py --audit-coverage        every region of every tracked file under coverage_roots has
                                   exactly one current owner; exit 1 on any deficit
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
import ast
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
LOOKUP_HITS = 3        # documents a free-text lookup prints; past them, read --area
SIMILAR_HITS = 5       # documents --similar prints before saying how many more scored
ENTRYPOINTS_SHOWN = 5  # entrypoints a lookup prints per hit; --for shows who owns each file
# Words that say nothing about the subject: every topic starts with "you need to change",
# and a reader's "add a new" or "create" names the task, not the thing it is about.
_FILLER = frozenset("""
a an and any are as at be by can do does for from has have how i if in is it its know need
of on or so that the this to what when where which who why will with you your change
add new create make start get set use exactly into
""".split())


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


def parse_entrypoint(entrypoint):
    """`[<repo>:]<path>[::<Name>]` -> (repo or None, path, symbol or None).

    `::Name` narrows a Python file to one top-level class or function. It is split off
    first, so `manage.py::main` is a root file with a symbol, not a repo prefix."""
    head, sep, symbol = entrypoint.partition("::")
    repo, path = split_entrypoint(head)
    return repo, path, (symbol if sep else None)


def local_targets(doc, own_repos):
    """[(path, symbol or None)] for the entrypoints of `doc` that are in this repo: plain
    ones, and prefixed ones whose repo is one of `own_repos`. Another repo's code is not
    here and never matches."""
    out = []
    for ep in doc.get("entrypoints", []):
        repo, path, symbol = parse_entrypoint(ep)
        if repo is None or repo in own_repos:
            out.append((path, symbol))
    return out


def local_entrypoints(doc, own_repos):
    """The paths of `local_targets`, symbols dropped: the files and directories `doc` touches."""
    return [path for path, _ in local_targets(doc, own_repos)]


def glob_match(path, pattern):
    """Glob over `/`-separated paths: `*` and `?` stay inside one directory, `**/` is zero
    or more directories and a trailing `**` is everything below."""
    out, i = [], 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        else:
            out.append({"*": "[^/]*", "?": "[^/]"}.get(pattern[i], re.escape(pattern[i])))
            i += 1
    return re.fullmatch("".join(out), path) is not None


_SYMBOL_NODES = (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)


def symbol_ranges(text):
    """{name: [(first line, last line)]} of the top-level classes and functions of a Python
    source. A range starts at the first decorator, or at the comment lines written directly
    above it, and ends with the body. Raises SyntaxError (or ValueError) when it does not parse."""
    lines = text.splitlines()
    out = {}
    for node in ast.parse(text).body:
        if not isinstance(node, _SYMBOL_NODES):
            continue
        start = min([node.lineno] + [d.lineno for d in node.decorator_list])
        while start > 1 and lines[start - 2].startswith("#"):
            start -= 1
        out.setdefault(node.name, []).append((start, node.end_lineno))
    return out


class CoverageError(Exception):
    """The regions of a file cannot be computed. Never read as "covered"."""


def _spans(numbers, lines):
    """Line numbers -> [(first, last)], joining runs separated only by blank lines."""
    spans = []
    for n in numbers:
        if spans and all(not lines[k - 1].strip() for k in range(spans[-1][1] + 1, n)):
            spans[-1] = (spans[-1][0], n)
        else:
            spans.append((n, n))
    return spans


def _span_label(spans):
    return ", ".join(f"{a}" if a == b else f"{a}-{b}" for a, b in spans)


class Coverage:
    """Who owns each region of a file, read from the manifests' entrypoints.

    A region has one effective owner, by precedence: the document that declares its symbol
    (`path::Name`), then the one that lists the file, then the one that lists the most
    specific directory above it. The file or directory owner is the general owner: it owns
    what no declared symbol does (imports, constants, module code, other symbols); that a
    symbol has its own document is not an overlap. Two documents at the same level for the
    same region are a tie, and a tie has no owner. Only local documents with
    `status: current` own code; the others stay readable, but own nothing.
    """

    def __init__(self, manifests):
        own = {m["repo"] for m in manifests if m.get("repo")}
        self.roots, self.exclude, self.status = [], [], {}
        self._paths, self._symbols = {}, {}
        for m in manifests:
            self.roots += [r for r in m.get("coverage_roots", []) if isinstance(r, str)]
            if isinstance(m.get("coverage_exclude"), list):
                self.exclude += [g for g in m["coverage_exclude"] if isinstance(g, str)]
            for doc in m.get("documents", []):
                if is_external(doc, own) or "id" not in doc:
                    continue
                self.status[doc["id"]] = doc.get("status")
                for path, symbol in local_targets(doc, own):
                    path = path.rstrip("/")
                    if symbol is None:
                        ids = self._paths.setdefault(path, [])
                    else:
                        ids = self._symbols.setdefault(path, {}).setdefault(symbol, [])
                    if doc["id"] not in ids:
                        ids.append(doc["id"])

    def excluded(self, path):
        return any(glob_match(path, g) for g in self.exclude)

    def in_roots(self, path):
        return any(covers(path, r) for r in self.roots)

    def symbols(self, path):
        """{name: [ids]} declared for `path`; symbols only exist in Python files."""
        return self._symbols.get(path, {}) if path.endswith(".py") else {}

    def _current(self, ids):
        return [i for i in ids if self.status.get(i) == "current"]

    def _general(self, path, keep):
        ids = keep(self._paths.get(path, []))
        if ids:
            return "file", ids
        dirs = [ep for ep in self._paths
                if ep != path and covers(path, ep) and keep(self._paths[ep])]
        if not dirs:
            return None, []
        return "dir", keep(self._paths[max(dirs, key=len)])

    def general(self, path):
        """(tier, [ids]) of the file's general owner: 'file', 'dir', or (None, []).

        Current documents are chosen first, at every level: a document that is not current
        never competes with one that is. Only when no current document covers the file are
        the others returned, so that they are reported as invalid owners."""
        found = self._general(path, self._current)
        return found if found[1] else self._general(path, list)

    def owners(self, path, symbol):
        """(tier, [ids]) owning `symbol` of `path` (None: the rest of the file)."""
        declared = self.symbols(path).get(symbol, []) if symbol is not None else []
        if self._current(declared):
            return "symbol", self._current(declared)
        general = self._general(path, self._current)
        if general[1]:
            return general
        return ("symbol", list(declared)) if declared else self._general(path, list)

    def line_owners(self, path, text):
        """A function line number -> (symbol or None, tier, [ids]), or None for a blank line
        outside every owned symbol. The file is parsed only when it declares symbols; a parse
        failure then raises CoverageError rather than leave the lines to the general owner."""
        lines = text.splitlines()
        by_line = {}
        declared = self.symbols(path)
        if declared:
            try:
                ranges = symbol_ranges(text)
            except (SyntaxError, ValueError) as exc:
                raise CoverageError(f"{path}: cannot parse it to find its symbols "
                                    f"({exc.__class__.__name__}: {exc})") from exc
            for name, spans in ranges.items():
                if declared.get(name):
                    tier, ids = self.owners(path, name)
                    if tier != "symbol":
                        continue  # a current general owner beats a non-current symbol one
                    for a, b in spans:
                        for n in range(a, b + 1):
                            by_line[n] = (name, "symbol", ids)
        general = (None, *self.general(path))

        def owner(n):
            if n in by_line:
                return by_line[n]
            if 0 < n <= len(lines) and not lines[n - 1].strip():
                return None
            return general
        return owner

    def regions(self, path, text):
        """({(symbol or None, tier): [ids]}, [unowned line numbers]) for one version of a file.
        A file with no code at all still needs its general owner."""
        owner = self.line_owners(path, text)
        lines = text.splitlines()
        regions, gap = {}, []
        for n in range(1, len(lines) + 1):
            o = owner(n)
            if o is None:
                continue
            if o[2]:
                regions[(o[0], o[1])] = o[2]
            else:
                gap.append(n)
        if not regions and not gap:
            tier, ids = self.general(path)
            if ids:
                regions[(None, tier)] = ids
            else:
                gap = [0]
        return regions, gap

    def audit_file(self, path, text):
        """Deficits of one file: [{kind, path, message, ...}] with kind gap | ambiguous |
        invalid | error. An excluded file has none."""
        if self.excluded(path):
            return []
        if path.endswith(".py"):
            # Every audited Python file must parse, owned per symbol or not: a file the tools
            # cannot read is never reported as covered.
            try:
                symbol_ranges(text)
            except (SyntaxError, ValueError) as exc:
                return [{"kind": "error", "path": path,
                         "message": f"{path}: does not parse ({exc.__class__.__name__}: {exc})"}]
        try:
            regions, gap = self.regions(path, text)
        except CoverageError as exc:
            return [{"kind": "error", "path": path, "message": str(exc)}]
        out = []
        if gap == [0]:
            out.append({"kind": "gap", "path": path, "lines": [],
                        "message": f"{path}: empty file and no document owns it"})
        elif gap:
            spans = _spans(gap, text.splitlines())
            out.append({"kind": "gap", "path": path, "lines": spans,
                        "message": f"{path}: no document owns lines {_span_label(spans)}"})
        for (symbol, tier), ids in regions.items():
            where = f"{path}::{symbol}" if symbol else f"{path} (rest of the file)"
            if len(ids) > 1:
                out.append({"kind": "ambiguous", "path": path, "symbol": symbol, "ids": ids,
                            "message": f"{where}: {', '.join(ids)} own it at the same level "
                                       f"({tier}); give it one owner"})
            elif self.status.get(ids[0]) != "current":
                out.append({"kind": "invalid", "path": path, "symbol": symbol, "ids": ids,
                            "message": f"{where}: owner {ids[0]} is "
                                       f"{self.status.get(ids[0]) or 'not declared'}; only "
                                       f"'current' documents own code"})
        return out


def tracked_files(root):
    """Repo-relative paths git tracks under `root`; every file on disk outside git."""
    out = subprocess.run(["git", "-C", root, "ls-files", "-z"], capture_output=True, text=True)
    if out.returncode == 0:
        return sorted(p for p in out.stdout.split("\0") if p)
    found = []
    for base, _, names in os.walk(root):
        if ".git" in base.split(os.sep):
            continue
        found += [_repo_relative(os.path.join(base, n), root) for n in names]
    return sorted(found)


def _read_source(path, strict):
    """The text of a file; undecodable bytes are an error only when its symbols are needed."""
    with open(path, "rb") as fh:
        data = fh.read()
    return data.decode("utf-8") if strict else data.decode("utf-8", errors="replace")


def _git_tree(root, rev):
    out = subprocess.run(["git", "-C", root, "ls-tree", "-r", "--name-only", "-z", rev],
                         capture_output=True, text=True)
    if out.returncode != 0:
        raise OSError(f"cannot list the files of {rev}: {out.stderr.strip()}")
    return sorted(p for p in out.stdout.split("\0") if p)


def _git_source(root, rev, path, strict):
    out = subprocess.run(["git", "-C", root, "show", f"{rev}:{path}"], capture_output=True)
    if out.returncode != 0:
        raise OSError(f"cannot read {path} at {rev}")
    return out.stdout.decode("utf-8") if strict else out.stdout.decode("utf-8", errors="replace")


def audit(root, manifests, files=None, rev=None):
    """{files, excluded, deficits} for every tracked file under the manifests' coverage_roots.

    With `rev`, the files and their content are those of that commit, read as git objects;
    without it, the files git tracks, as they are on disk.

    A deficit is a gap (code no document owns), a tie, an owner that is not `current`, an
    entry naming a symbol or path that does not exist, or a file that cannot be read or
    parsed. Nothing here reads a failure as coverage."""
    cov = Coverage(manifests)
    if files is None:
        files = _git_tree(root, rev) if rev else tracked_files(root)
    in_roots = [f for f in files if cov.in_roots(f)]
    report = {"files": [], "excluded": [], "deficits": []}
    for path in in_roots:
        if cov.excluded(path):
            report["excluded"].append(path)
            continue
        report["files"].append(path)
        try:
            strict = bool(cov.symbols(path)) or path.endswith(".py")
            text = (_git_source(root, rev, path, strict) if rev
                    else _read_source(os.path.join(root, path), strict=strict))
        except (OSError, UnicodeDecodeError) as exc:
            report["deficits"].append({"kind": "error", "path": path,
                                       "message": f"{path}: cannot read it ({exc})"})
            continue
        report["deficits"] += cov.audit_file(path, text)
        for symbol, ids in cov.symbols(path).items():
            try:
                defined = symbol_ranges(text)
            except (SyntaxError, ValueError):
                break  # already reported by audit_file
            if symbol not in defined:
                report["deficits"].append({"kind": "invalid", "path": path, "symbol": symbol,
                                           "ids": ids, "message": f"{path}::{symbol} ({', '.join(ids)}): "
                                           f"no top-level class or function has that name"})
    present = set(files)
    for ep, ids in cov._paths.items():
        if cov.in_roots(ep) and ep not in present and not any(covers(f, ep) for f in present):
            report["deficits"].append({"kind": "invalid", "path": ep, "ids": ids,
                                       "message": f"{ep} ({', '.join(ids)}): no tracked file "
                                                  f"is or lives under this entrypoint"})
    for ep, symbols in cov._symbols.items():
        if cov.in_roots(ep) and ep not in present:
            for symbol, ids in symbols.items():
                report["deficits"].append({"kind": "invalid", "path": ep, "symbol": symbol,
                                           "ids": ids, "message": f"{ep}::{symbol} "
                                           f"({', '.join(ids)}): the file is not tracked"})
    return report


def _print_audit(report, manifests):
    if not any(m.get("coverage_roots") for m in manifests):
        print("no coverage_roots in the manifest: nothing to audit.")
        return 0
    for d in report["deficits"]:
        print(f"{d['kind'].upper():10} {d['message']}")
    flawed = {d["path"] for d in report["deficits"]}
    covered = [f for f in report["files"] if f not in flawed]
    print(f"\n{len(report['files'])} file(s) audited, {len(report['excluded'])} excluded: "
          f"{len(covered)} fully covered by current documents, "
          f"{len(report['deficits'])} deficit(s).")
    return 1 if report["deficits"] else 0


def _repo_relative(path, root):
    if os.path.isabs(path):
        path = os.path.relpath(path, root)
    return os.path.normpath(path).replace(os.sep, "/")


def for_path(idx, path, root, wsmap=None):
    """Documents whose entrypoints cover `path` (repo-relative, absolute or ./-prefixed).

    Most specific first: a document that declares one of the file's symbols, then one that
    lists the file itself, then one that covers its directory, so the narrower document is
    the one read first.
    """
    rel = _repo_relative(path, root)
    hits = []
    for doc in idx.docs.values():
        matched = [(sym is not None, len(ep.rstrip("/")))
                   for ep, sym in local_targets(doc, idx.own_repos) if covers(rel, ep)]
        if matched:
            hits.append((max(matched), _result(idx, doc, root, wsmap)))
    return [r for _, r in sorted(hits, key=lambda h: h[0], reverse=True)]


def _print_coverage(cov, rel, root):
    """The regions of the file on disk and who owns each: what `--for` adds to the hits."""
    try:
        with open(os.path.join(root, rel), encoding="utf-8") as fh:
            text = fh.read()
    except (OSError, UnicodeDecodeError):
        return
    print(f"\ncoverage of {rel}:")
    try:
        regions, _ = cov.regions(rel, text)
    except CoverageError as exc:
        print(f"  error: {exc}")
        return
    for (symbol, _tier), ids in regions.items():
        print(f"  {symbol + ':' if symbol else 'rest of the file:'} {', '.join(ids)}")
    deficits = cov.audit_file(rel, text)
    for d in deficits:
        print(f"  {d['kind']}: {d['message']}")
    if not deficits:
        print("  fully covered by current documents")


def _stem(word):
    """Singular, roughly: 'actions' and 'action' are the same search term."""
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 4 and word.endswith(("ches", "shes", "sses", "xes", "zes")):
        return word[:-2]
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


def terms(text):
    """The words of `text` that name a subject, singular, without the filler. A code name is
    its words: `ActivityType` and `activity_type` are "activity" and "type"."""
    return {_stem(w) for w in normalize(_CAMEL.sub(" ", text)) if w not in _FILLER}


def _term_weights(idx, did, doc):
    """A query word found in the id or an intent counts most, then an alias, then the topic."""
    intents = " ".join(i for i, ids in idx.intents.items() if did in ids)
    return [(terms(f"{did} {intents}"), 3), (terms(" ".join(doc.get("aliases", []))), 2),
            (terms(idx.topic(doc)), 1)]


def word_matches(idx, query):
    """(ids ranked by how many query words they match, then where) for a free-text query."""
    wanted = terms(query)
    scored = []
    for did, doc in idx.docs.items():
        weights = _term_weights(idx, did, doc)
        hits = [max((w for vocab, w in weights if t in vocab), default=0) for t in wanted]
        matched = sum(1 for h in hits if h)
        if matched:
            scored.append((-matched, -sum(hits), did))
    return [did for _, _, did in sorted(scored)]


def resolve(idx, query, root, wsmap=None):
    """Exact intent, then alias (every document they name). Otherwise free text: the
    documents holding the phrase in their id / topic / aliases come first, then the ones that
    match the most of its words, so a phrase that happens to appear in one topic does not hide
    the document the words name; at most LOOKUP_HITS in all."""
    q = query.strip().lower()
    if q in idx.intents:
        ids = list(idx.intents[q])
    elif q in idx.aliases:
        ids = list(idx.aliases[q])
    elif not terms(q):
        ids = []  # only filler: as a substring it would match every topic
    else:
        phrase = {did for did, doc in idx.docs.items()
                  if q in " ".join([did, idx.topic(doc), *doc.get("aliases", [])]).lower()}
        ranked = word_matches(idx, q)
        ids = ([d for d in ranked if d in phrase] + sorted(phrase - set(ranked))
               + [d for d in ranked if d not in phrase])[:LOOKUP_HITS]
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


def _symbol_problem(path, symbol):
    """Why `path::symbol` names nothing, or None when the file defines it at top level."""
    if not path.endswith(".py"):
        return "symbols are only resolved in Python files"
    try:
        with open(path, encoding="utf-8") as fh:
            ranges = symbol_ranges(fh.read())
    except (OSError, UnicodeDecodeError, SyntaxError, ValueError) as exc:
        return f"cannot parse the file ({exc.__class__.__name__}: {exc})"
    if symbol not in ranges:
        return "no top-level class or function has that name"
    return None


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
    # Symbol and ownership problems are warnings while a repo adopts per-region owners,
    # errors once its manifest says `"coverage": "enforce"`.
    region_issues = []
    for m in manifests:
        mp = rel(m["_path"])
        if m.get("coverage") not in (None, "enforce"):
            errors.append(f"{mp}: 'coverage' must be \"enforce\" when present, got {m['coverage']!r}")
        roots = m.get("coverage_roots")
        if roots is not None and not (isinstance(roots, list) and all(isinstance(r, str) for r in roots)):
            errors.append(f"{mp}: 'coverage_roots' must be a list of directory strings")
        if "require_doc_content" in m and not isinstance(m["require_doc_content"], bool):
            errors.append(f"{mp}: 'require_doc_content' must be true or false")
        exclude = m.get("coverage_exclude")
        if exclude is not None and not (isinstance(exclude, list)
                                        and all(isinstance(g, str) for g in exclude)):
            errors.append(f"{mp}: 'coverage_exclude' must be a list of glob strings")
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
                    repo_name, ep_path, symbol = parse_entrypoint(ep)
                    if repo_name is not None and not ep_path.strip("/"):
                        errors.append(f"{mp}: id '{did}' entrypoint '{ep}' has an empty path after "
                                      f"the repo prefix")
                        continue
                    if symbol == "":
                        errors.append(f"{mp}: id '{did}' entrypoint '{ep}' has an empty symbol "
                                      f"after '::'")
                        continue
                    if repo_name is None or repo_name in idx.own_repos:
                        base = root
                    else:
                        base = (wsmap or {}).get(repo_name)
                        if not base:
                            continue  # another repo, not checked out here: taken on trust
                    target = os.path.join(base, ep_path.rstrip("/"))
                    if not os.path.exists(target):
                        warnings.append(f"{mp}: id '{did}' entrypoint does not exist: {ep}")
                    elif symbol is not None and base == root:
                        problem = _symbol_problem(target, symbol)
                        if problem:
                            region_issues.append(f"{mp}: id '{did}' entrypoint {ep}: {problem}")
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

    cov = Coverage(manifests)

    def tied(ids):
        # Only documents that could own the region compete: the current ones, or all of
        # them when none is current.
        return len(cov._current(ids) or ids) > 1

    for path, ids in cov._paths.items():
        if tied(ids):
            region_issues.append(f"entrypoint {path} is ambiguous: {', '.join(ids)} own it at "
                                 f"the same level; give it one owner and link the others")
    for path, symbols in cov._symbols.items():
        for symbol, ids in symbols.items():
            if tied(ids):
                region_issues.append(f"entrypoint {path}::{symbol} is ambiguous: {', '.join(ids)} "
                                     f"own it; give it one owner and link the others")
    enforce = any(m.get("coverage") == "enforce" for m in manifests)
    (errors if enforce else warnings).extend(region_issues)

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
    """Documents whose text shares phrases with `query`, plus up to 0.5 for the share of its
    subject words found in the id, intents, aliases or topic. Filler words count only inside
    the shared phrases, never toward that share."""
    qsh, wanted = shingles(normalize(query), k), terms(query)
    scored = []
    for did, doc in idx.docs.items():
        kind, where = locate(doc, root, wsmap, idx.own_repos)
        text = _read(where) if kind != "link" else ""
        score = jaccard(qsh, shingles(normalize(text), k))
        if wanted:
            vocab = set().union(*(v for v, _ in _term_weights(idx, did, doc)))
            score += 0.5 * len(wanted & vocab) / len(wanted)
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


def _print(results, today=None, stale_days=STALE_DAYS, root=None, all_entrypoints=False):
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
            values = r.get(key) or []
            if key == "entrypoints" and not all_entrypoints and len(values) > ENTRYPOINTS_SHOWN:
                values = values[:ENTRYPOINTS_SHOWN] + [f"(+{len(values) - ENTRYPOINTS_SHOWN} more)"]
            if values:
                print(f"  {key + ':':13}{', '.join(values)}")
        verified = _verified_label(r["verified"], today, stale_days) if r.get("verified") else "none recorded"
        print(f"  verified:    {verified}")
        print(f"  source:      {provenance(r, root)}")
        if "score" in r:
            print(f"  score:       {r['score']}")
    adopted = adopted_handbook_commit(root) if root else None
    if results and adopted:
        print(f"\nhandbook tools in this repo were copied from commit {adopted}")


def area_of(did):
    """`omnichannel-service.interactions.inbound-actions` -> `interactions`."""
    parts = did.split(".")
    return parts[1] if len(parts) > 1 else parts[0]


def _print_compact(results, today=None, stale_days=STALE_DAYS):
    """Two lines a hit: enough to pick a document without opening it."""
    if not results:
        print("(no matches)")
    today = _parse_date(today) if today else datetime.date.today()
    for r in results:
        status = r.get("status") or "status not declared"
        verified = (_verified_label(r["verified"], today, stale_days) if r.get("verified")
                    else "none recorded")
        print(f"\n### {r['id']}  [{status}, verified {verified}]  {r['path']}")
        print(f"    {r['topic']}")


def _print_area(idx, name):
    """One line per document of an area: the bounded list to read when a lookup misses."""
    hits = sorted(did for did in idx.docs if area_of(did).lower() == name.lower())
    if not hits:
        counts = {}
        for did in idx.docs:
            counts[area_of(did)] = counts.get(area_of(did), 0) + 1
        print(f"(no area {name!r}; areas: "
              f"{', '.join(f'{a} ({n})' for a, n in sorted(counts.items()))})")
        return 1
    for did in hits:
        print(f"{did}  [{idx.topic(idx.docs[did])}]")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("query", nargs="?")
    ap.add_argument("--for", dest="for_file", metavar="FILE",
                    help="document(s) whose entrypoints cover FILE; exit 1 when none does")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--area", metavar="NAME",
                    help="every document of one area (the part of the id after the repo), "
                         "one line each: read it when a lookup did not list the document")
    ap.add_argument("--audit-coverage", action="store_true",
                    help="every region of every tracked file under coverage_roots has one current "
                         "owner; exit 1 on any gap, tie, invalid owner or unreadable file")
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

    if args.audit_coverage:
        return _print_audit(audit(root, manifests), manifests)
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
        hits = similar(idx, args.similar, root, wsmap)
        _print(hits[:SIMILAR_HITS], stale_days=args.stale_days, root=root)
        if len(hits) > SIMILAR_HITS:
            print(f"\n({len(hits) - SIMILAR_HITS} more below the top {SIMILAR_HITS}, scoring "
                  f"{hits[SIMILAR_HITS]['score']} or less)")
        return 0
    if args.for_file:
        hits = for_path(idx, args.for_file, root, wsmap)
        rel = _repo_relative(args.for_file, root)
        cov = Coverage(manifests)
        if cov.excluded(rel):
            if hits:
                _print(hits, stale_days=args.stale_days, root=root, all_entrypoints=True)
            print(f"{rel} is excluded from coverage (coverage_exclude): no document is needed.")
            return 0
        if not hits:
            print(f"(no document lists {rel} as an entrypoint; "
                  f"try `--similar` before writing one)")
            return 1
        _print(hits, stale_days=args.stale_days, root=root, all_entrypoints=True)
        _print_coverage(cov, rel, root)
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
    if args.area:
        return _print_area(idx, args.area)
    if args.query:
        hits = resolve(idx, args.query, root, wsmap)
        q = args.query.strip().lower()
        if q in idx.intents or q in idx.aliases:
            _print(hits, stale_days=args.stale_days, root=root)
            return 0
        _print_compact(hits, stale_days=args.stale_days)
        where = f"--area {area_of(hits[0]['id'])}" if hits else "--area NAME"
        print(f"\n(not one of these? `ctx.py {where}` lists every document of that area, one "
              f"line each; pick from it instead of opening documents to search)")
        return 0
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())

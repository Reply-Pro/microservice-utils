#!/usr/bin/env python3
"""Fail when an indexed document is clearly not written in English.

Usage:
    python3 tools/english_check.py [--root DIR]

Documentation is English only (standards/documentation-language.md). For every
indexed local document this reads the prose (fenced and inline code are ignored),
counts common English function words against common Spanish ones, and fails when
Spanish wins or when inverted punctuation (¿ ¡) appears. Short texts are not
judged. Documents listed under `pending` are not checked until they are indexed.
There is no bypass marker: the fix is to translate. Standard library only.
"""
import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ctx  # noqa: E402

# Function words that are frequent in one language and rare in the other. Words that
# exist in both ("a", "no", "son", "con", "sin", "hay", "lo", "al") are left out.
EN_WORDS = {"the", "and", "of", "to", "in", "is", "that", "for", "with", "as", "this", "are",
            "be", "on", "it", "or", "by", "an", "not", "from", "when", "if", "we", "was",
            "were", "has", "have", "its", "which", "each", "every", "than", "then", "there",
            "these", "those", "into", "only", "also", "both", "after", "before", "any"}
ES_WORDS = {"de", "la", "el", "que", "y", "en", "los", "las", "para", "por", "una", "del",
            "se", "es", "como", "pero", "también", "cuando", "está", "sobre", "este", "esta",
            "esto", "ese", "esa", "eso", "su", "sus", "ya", "más", "porque", "si", "un",
            "muy", "entre", "desde", "hasta", "donde", "cada", "todo", "todos", "nos", "le",
            "les", "sea", "ser", "tiene", "hacer", "puede", "debe", "deben", "sólo", "solo"}
MIN_WORDS = 8
_FENCE = re.compile(r"```.*?```", re.S)
_INLINE = re.compile(r"`[^`\n]*`")
_WORD = re.compile(r"[a-záéíóúñü]+")


def prose(text):
    return _INLINE.sub(" ", _FENCE.sub(" ", text))


def problem(text):
    """None when the text passes, otherwise a one-line reason."""
    body = prose(text)
    if "¿" in body or "¡" in body:
        return "inverted punctuation (¿ ¡) found; documentation is English only"
    words = _WORD.findall(body.lower())
    en = sum(w in EN_WORDS for w in words)
    es = sum(w in ES_WORDS for w in words)
    if en + es < MIN_WORDS:
        return None
    if es > en:
        return f"reads as Spanish ({es} Spanish function words vs {en} English); translate it"
    return None


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", default=ctx.DEFAULT_ROOT)
    args = ap.parse_args(argv)
    root = os.path.abspath(args.root)
    manifests = [ctx.load_manifest(p) for p in ctx.discover_manifests(root)]
    idx = ctx.build_index(manifests)
    failures, checked = [], 0
    for doc in idx.docs.values():
        kind, where = ctx.locate(doc, root, {}, idx.own_repos)
        if kind != "local" or not os.path.isfile(where):
            continue
        checked += 1
        with open(where, encoding="utf-8", errors="replace") as fh:
            why = problem(fh.read())
        if why:
            failures.append(f"{os.path.relpath(where, root)}: {why}")
    for f in failures:
        print(f)
    if failures:
        print(f"{len(failures)} document(s) not in English.", file=sys.stderr)
        return 1
    print(f"english_check: {checked} indexed document(s) read as English.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

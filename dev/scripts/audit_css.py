#!/usr/bin/env python3
"""Audit + prune dead CSS rules from static/css/app.css.

Strategy: build the set of class names referenced anywhere in
templates / JS / Python (including dynamic Jinja interpolation
prefixes like `connect-icon-{{ id }}`). For each top-level CSS
rule, parse its selector list; a rule is safe to drop only when
EVERY simple selector in EVERY comma-separated alternative
references at least one class that is not in the referenced set.

Usage:
    python3 dev/scripts/audit_css.py            # report + dry-run
    python3 dev/scripts/audit_css.py --apply    # rewrite app.css

Conservative by design — keeps any rule that mentions a still-used
class anywhere in its selector chain so we never strip a hover /
modifier off a live element.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from typing import Iterable

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CSS_PATH = os.path.join(ROOT, "static/css/app.css")
SEARCH_DIRS = [
    os.path.join(ROOT, "app/templates"),
    os.path.join(ROOT, "static/js"),
    os.path.join(ROOT, "app"),
]
SEARCH_EXT = (".html", ".jinja", ".js", ".py")


def load_search_text() -> str:
    chunks: list[str] = []
    for sdir in SEARCH_DIRS:
        for root, _, files in os.walk(sdir):
            for fname in files:
                if fname.endswith(SEARCH_EXT):
                    with open(os.path.join(root, fname), encoding="utf-8") as f:
                        chunks.append(f.read())
    return "\n".join(chunks)


def referenced_classes(text: str, all_classes: Iterable[str]) -> set[str]:
    """Return the subset of `all_classes` that appear (as a word) in
    `text`, plus any class whose hyphen-prefix is followed by a Jinja
    `{{` interpolation in `text` — that catches dynamic patterns
    like `connect-icon-{{ row.id }}`.
    """
    refs: set[str] = set()
    # Plain word-boundary match.
    for cls in all_classes:
        if re.search(r"\b" + re.escape(cls) + r"\b", text):
            refs.add(cls)
            continue
        # Dynamic prefix match: split off the last hyphen-separated
        # token (e.g. "connect-icon-line" -> prefix="connect-icon")
        # and look for "<prefix>-{{".
        if "-" in cls:
            prefix = cls.rsplit("-", 1)[0]
            if re.search(r"\b" + re.escape(prefix) + r"-\{\{", text):
                refs.add(cls)
    return refs


def class_names_in_css(css: str) -> set[str]:
    return set(re.findall(r"\.([a-zA-Z][\w-]+)", css))


def split_rules(css: str) -> list[tuple[int, int, str, str]]:
    """Walk the CSS and yield (start, end, selector, body) for every
    top-level rule. Brace counting is depth-aware so nested @media
    / @supports blocks are returned as one chunk (we won't drop
    rules inside them).
    """
    rules: list[tuple[int, int, str, str]] = []
    i = 0
    n = len(css)
    while i < n:
        # Skip whitespace and comments.
        while i < n and css[i] in " \t\r\n":
            i += 1
        if i >= n:
            break
        if css[i:i + 2] == "/*":
            end = css.find("*/", i + 2)
            if end == -1:
                break
            i = end + 2
            continue
        # At-rules (@media, @keyframes, etc.) — skip the whole block.
        if css[i] == "@":
            # Find ; or {
            j = i
            depth = 0
            while j < n:
                if css[j] == "{":
                    depth = 1
                    j += 1
                    while j < n and depth:
                        if css[j] == "{":
                            depth += 1
                        elif css[j] == "}":
                            depth -= 1
                        j += 1
                    break
                if css[j] == ";":
                    j += 1
                    break
                j += 1
            i = j
            continue
        # Selector up to '{'.
        sel_start = i
        brace = css.find("{", i)
        if brace == -1:
            break
        selector = css[sel_start:brace].strip()
        # Body up to matching '}' (rules are flat at this level).
        depth = 1
        j = brace + 1
        while j < n and depth:
            if css[j] == "{":
                depth += 1
            elif css[j] == "}":
                depth -= 1
            j += 1
        body = css[brace:j]
        rules.append((sel_start, j, selector, body))
        i = j
    return rules


_CLASS_RE = re.compile(r"\.([a-zA-Z][\w-]+)")


def selector_classes(selector: str) -> list[set[str]]:
    """Return list-of-sets — one set per comma-separated alternative.
    Each set is the simple class names that appear in that
    alternative.
    """
    return [set(_CLASS_RE.findall(alt)) for alt in selector.split(",")]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Rewrite app.css in place")
    args = parser.parse_args()

    with open(CSS_PATH, encoding="utf-8") as f:
        css = f.read()

    css_classes = class_names_in_css(css)
    search_text = load_search_text()
    used = referenced_classes(search_text, css_classes)
    unused = css_classes - used

    print(f"Total classes in CSS    : {len(css_classes)}")
    print(f"Referenced in templates : {len(used)}")
    print(f"Unused                  : {len(unused)}")
    print()

    rules = split_rules(css)
    drop_ranges: list[tuple[int, int, str]] = []
    for start, end, selector, _body in rules:
        alts = selector_classes(selector)
        if not alts or any(not s for s in alts):
            continue  # selector contains a tag-only or other non-class — keep
        # A rule is droppable when every alternative consists
        # entirely of unused classes (drop it) — i.e. every alt is a
        # subset of `unused`. Any alternative that mentions a used
        # class keeps the whole rule (we don't split selector lists).
        if all(s.issubset(unused) for s in alts):
            drop_ranges.append((start, end, selector))

    print(f"Rules safe to drop      : {len(drop_ranges)}")
    print()
    for start, _end, selector in drop_ranges[:30]:
        line = css.count("\n", 0, start) + 1
        print(f"  L{line}: {selector[:80]}")
    if len(drop_ranges) > 30:
        print(f"  ... and {len(drop_ranges) - 30} more")

    if args.apply and drop_ranges:
        # Rewrite the file by stripping each dropped range. Iterate
        # in reverse so earlier offsets stay valid.
        out = css
        for start, end, _sel in sorted(drop_ranges, reverse=True):
            # Eat trailing newlines so the file doesn't fill with blank lines.
            tail = end
            while tail < len(out) and out[tail] == "\n":
                tail += 1
            out = out[:start] + out[tail:]
        with open(CSS_PATH, "w", encoding="utf-8") as f:
            f.write(out)
        before = len(css)
        after = len(out)
        print()
        print(f"Wrote {CSS_PATH}")
        print(f"  {before:>8} -> {after:>8} bytes ({before - after} saved)")

    return 0


if __name__ == "__main__":
    sys.exit(main())

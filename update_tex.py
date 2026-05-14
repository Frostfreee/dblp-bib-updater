#!/usr/bin/env python3
"""
Update citation keys in .tex files to match the renamed entries in updated.bib.

Reads the key mapping from update_log.txt (PUBLISHED and ARXIV_REFRESHED entries),
then rewrites all \cite{} commands in every .tex file found under TEX_DIR.

Changes are logged to tex_update_log.txt.
"""

import argparse
import re
import sys
from pathlib import Path

# Matches all \cite variants with optional bracket arg:
# \cite, \citep, \citet, \citealt, \citealp, \citeauthor, \citeyear, \nocite, etc.
CITE_RE = re.compile(r'(\\cite\w*(?:\[[^\]]*\])?\{)([^}]+)(\})')


# ---------------------------------------------------------------------------
# Build old_key → new_key mapping from update_log.txt
# ---------------------------------------------------------------------------

def build_key_map(log_path: Path) -> dict[str, str]:
    key_map: dict[str, str] = {}
    for line in log_path.read_text().splitlines():
        # [PUBLISHED]  orig_id  →  new_key  [venue]
        m = re.match(r'\[PUBLISHED\]\s+(\S+)\s+→\s+(\S+)', line)
        if m:
            orig, new_key = m.group(1), m.group(2)
            key_map[orig] = f'DBLP:{new_key}'
            continue

        # [ARXIV_REFRESHED]  orig_id  (arxiv_key)
        m = re.match(r'\[ARXIV_REFRESHED\]\s+(\S+)\s+\((\S+)\)', line)
        if m:
            orig, arxiv_key = m.group(1), m.group(2)
            new_id = f'DBLP:{arxiv_key}'
            if orig != new_id:   # only record if key actually changed
                key_map[orig] = new_id

    return key_map


# ---------------------------------------------------------------------------
# Rewrite cite keys in a single line
# ---------------------------------------------------------------------------

def replace_keys_in_line(line: str, key_map: dict[str, str]) -> tuple[str, list[tuple[str, str]]]:
    """
    Return (new_line, [(old_key, new_key), ...]) for every substitution made.
    """
    changes: list[tuple[str, str]] = []

    def replace_match(m: re.Match) -> str:
        prefix, keys_str, suffix = m.group(1), m.group(2), m.group(3)
        raw_keys = keys_str.split(',')
        new_keys = []
        for raw in raw_keys:
            stripped = raw.strip()
            if stripped in key_map:
                changes.append((stripped, key_map[stripped]))
                # preserve leading/trailing whitespace around the key
                new_keys.append(raw.replace(stripped, key_map[stripped], 1))
            else:
                new_keys.append(raw)
        return prefix + ','.join(new_keys) + suffix

    new_line = CITE_RE.sub(replace_match, line)
    return new_line, changes


# ---------------------------------------------------------------------------
# Process a single .tex file
# ---------------------------------------------------------------------------

def process_file(tex_path: Path, key_map: dict[str, str]) -> list[str]:
    """
    Rewrite tex_path in-place. Returns log lines describing every change made.
    """
    lines = tex_path.read_text(encoding='utf-8').splitlines(keepends=True)
    new_lines = []
    log_entries: list[str] = []

    for lineno, line in enumerate(lines, 1):
        new_line, changes = replace_keys_in_line(line, key_map)
        new_lines.append(new_line)
        for old_key, new_key in changes:
            log_entries.append(
                f"  line {lineno:4d}: {old_key}  →  {new_key}"
            )

    if log_entries:
        tex_path.write_text(''.join(new_lines), encoding='utf-8')

    return log_entries


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description="Rewrite \\cite{} keys in .tex files using the mapping from update_log.txt.")
    ap.add_argument("tex_dir",
                    help="Directory to search recursively for .tex files")
    ap.add_argument("--log", default="update_log.txt",
                    help="update_log.txt produced by update_refs.py (default: update_log.txt)")
    ap.add_argument("--tex-log", default="tex_update_log.txt",
                    help="Output log file (default: tex_update_log.txt)")
    args = ap.parse_args()

    log_path = Path(args.log)
    tex_dir  = Path(args.tex_dir)
    tex_log  = args.tex_log

    if not log_path.exists():
        sys.exit(f"Error: {args.log} not found — run update_refs.py first.")
    if not tex_dir.exists():
        sys.exit(f"Error: directory {args.tex_dir} not found.")

    key_map = build_key_map(log_path)
    if not key_map:
        print("No key changes found in update_log.txt. Nothing to do.")
        return

    print(f"Key mapping ({len(key_map)} entries):")
    for old, new in key_map.items():
        print(f"  {old}  →  {new}")
    print()

    tex_files = sorted(tex_dir.rglob("*.tex"))
    if not tex_files:
        sys.exit(f"No .tex files found under {TEX_DIR}.")

    all_log_lines: list[str] = []
    total_changes = 0

    for tex_path in tex_files:
        entries = process_file(tex_path, key_map)
        if entries:
            print(f"[UPDATED] {tex_path}  ({len(entries)} substitution(s))")
            all_log_lines.append(f"\n[UPDATED] {tex_path}")
            all_log_lines.extend(entries)
            total_changes += len(entries)
        else:
            print(f"[NO CHANGE] {tex_path}")
            all_log_lines.append(f"[NO CHANGE] {tex_path}")

    Path(tex_log).write_text('\n'.join(all_log_lines), encoding='utf-8')
    print(f"\n{total_changes} substitution(s) across {len(tex_files)} file(s).")
    print(f"Wrote {tex_log}")


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""
Update references.bib by fetching latest BibTeX entries from DBLP.

Strategy:
  - Non-arXiv DBLP entries (have biburl, not corr/abs): re-fetch biburl to refresh metadata.
  - arXiv entries (DBLP corr/abs key, or hand-crafted CoRR article): search DBLP by title
    for a published version. Accept only if title matches exactly (case & punctuation ignored).
    Multiple candidates → written to review_needed.txt for manual inspection.
  - Entries with no biburl and not recognizable as arXiv: search by title anyway.

Outputs:
  updated.bib         - all entries (updated where possible, originals otherwise)
  review_needed.txt   - entries needing manual selection
  update_log.txt      - per-entry result log
"""

import bibtexparser
from bibtexparser.bwriter import BibTexWriter
from bibtexparser.bparser import BibTexParser
import urllib.request
import urllib.parse
import json
import re
import argparse
import html
import time
import sys
from pathlib import Path

DBLP_SEARCH_URL = "https://dblp.org/search/publ/api"
USER_AGENT = "reference-updater/1.0 (academic research tool)"
RATE_LIMIT = 2.0       # seconds between DBLP requests
CHECKPOINT_EVERY = 10  # write output files every N entries

# Resolved from CLI args in main(); used as module-level names by helper functions.
INPUT_BIB = OUTPUT_BIB = REVIEW_FILE = LOG_FILE = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def normalize_title(title: str) -> str:
    """Lowercase, strip LaTeX/HTML markup, remove all punctuation."""
    t = html.unescape(title)           # &apos; → ', &middot; → ·, etc.
    t = re.sub(r'[{}]', '', t)         # strip LaTeX braces
    t = re.sub(r'\\\(|\\\)', '', t)    # remove \( \) math delimiters
    t = re.sub(r'\\[a-z]+', '', t)      # remove LaTeX commands: \cdot, \emph, etc.
    t = re.sub(r'\s+', ' ', t)         # collapse whitespace (incl. \n)
    t = t.strip().rstrip('.')
    t = t.lower()
    t = re.sub(r'[^a-z0-9 ]', '', t)  # remove punctuation and non-ASCII chars
    return re.sub(r'\s+', ' ', t).strip()


def is_arxiv_entry(entry: dict) -> bool:
    """Return True if entry is an arXiv preprint (not yet formally published)."""
    key = entry.get('ID', '')
    if re.search(r'journals/corr/abs', key):
        return True
    journal = entry.get('journal', '')
    if re.sub(r'[{}]', '', journal).strip().lower() == 'corr':
        return True
    # Hand-crafted arXiv: has eprint but no conference/journal venue
    has_eprint = 'eprint' in entry or 'eprinttype' in entry
    has_venue = ('booktitle' in entry or
                 ('journal' in entry and
                  re.sub(r'[{}]', '', entry.get('journal', '')).strip().lower() != 'corr'))
    return has_eprint and not has_venue


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={'User-Agent': USER_AGENT})
    with urllib.request.urlopen(req, timeout=15) as r:
        return r.read().decode('utf-8')


def search_dblp(title: str, max_hits: int = 10) -> list | None:
    """Return list of DBLP hit-info dicts, or None on network error."""
    params = urllib.parse.urlencode({'q': title, 'format': 'json', 'h': max_hits})
    try:
        data = json.loads(fetch(f"{DBLP_SEARCH_URL}?{params}"))
        return [h['info'] for h in data['result']['hits'].get('hit', [])]
    except Exception:
        return None  # distinct from "searched and found nothing" (empty list)


def find_published(title: str):
    """
    Search DBLP for a formally-published version matching title exactly
    (case & punctuation ignored).

    Returns (status, pub_candidates, arxiv_candidates):
      'found'         , [one info dict] , [...]  → auto-replace with published version
      'multiple'      , [list of dicts] , [...]  → manual review (multiple exact matches)
      'not_found'     , []              , [...]  → no non-arXiv results; arxiv_candidates
                                                   holds DBLP arXiv hits for metadata refresh
      'title_changed' , [list of dicts] , [...]  → non-arXiv results exist but title differs
      'error'         , []              , []     → network/parse failure
    """
    # Clean the query before sending to DBLP:
    # - strip LaTeX braces and commands
    # - replace non-ASCII characters (e.g. · U+00B7) with spaces so DBLP
    #   can still find papers whose titles contain special symbols
    query = re.sub(r'[{}]', '', title)
    query = re.sub(r'\\[a-zA-Z()]+', ' ', query)
    query = re.sub(r'[^\x00-\x7F]', ' ', query)  # replace non-ASCII (e.g. ·) with space
    query = re.sub(r'\s+', ' ', query).strip()

    hits = search_dblp(query)
    if hits is None:
        return 'error', [], []  # network failure — caller logs as ERROR, not NOT_FOUND

    norm = normalize_title(title)
    non_arxiv = [h for h in hits if 'journals/corr' not in h.get('key', '')]
    arxiv_hits = [h for h in hits if 'journals/corr' in h.get('key', '')
                  and normalize_title(h.get('title', '')) == norm]
    matches = [h for h in non_arxiv if normalize_title(h.get('title', '')) == norm]

    if not non_arxiv:
        return 'not_found', [], arxiv_hits
    if not matches:
        return 'title_changed', non_arxiv, arxiv_hits
    if len(matches) == 1:
        return 'found', matches, arxiv_hits
    return 'multiple', matches, arxiv_hits


def fetch_dblp_bib(key: str, retries: int = 3) -> str | None:
    """Download BibTeX string for a DBLP key, e.g. 'conf/acl/XuLDLP24'."""
    url = f"https://dblp.org/rec/{key}.bib"
    for attempt in range(1, retries + 1):
        try:
            time.sleep(RATE_LIMIT * attempt)  # back off on each retry
            return fetch(url)
        except Exception as e:
            if attempt == retries:
                return None
            print(f"(retry {attempt}/{retries-1}: {e})", end=' ', flush=True)


def parse_single_bib(bib_str: str) -> dict | None:
    """Parse a BibTeX string containing exactly one entry; return entry dict or None."""
    parser = BibTexParser(common_strings=True)
    parser.ignore_nonstandard_types = False
    try:
        db = bibtexparser.loads(bib_str, parser=parser)
        return db.entries[0] if db.entries else None
    except Exception:
        return None


def dblp_info_to_bib_key(info: dict) -> str | None:
    """Extract DBLP key from search result info dict."""
    return info.get('key')


# ---------------------------------------------------------------------------
# Resume helpers
# ---------------------------------------------------------------------------

def load_resume_state(retry_not_found: bool = False) -> tuple[dict, set, list, dict]:
    """
    Parse update_log.txt and updated.bib to build resume state.

    Returns:
      carried      : dict mapping original_id → entry dict (from updated.bib)
      retry_ids    : set of original IDs that need to be re-processed
      log_lines    : existing log lines (pre-loaded so carried entries keep their original line)
      id_to_log_idx: dict mapping orig_id → index in log_lines (for in-place updates)
    """
    log_path = Path(LOG_FILE)
    out_path = Path(OUTPUT_BIB)
    if not log_path.exists() or not out_path.exists():
        sys.exit(f"Error: --resume requires both {LOG_FILE} and {OUTPUT_BIB} from a previous run.")

    # Load raw log lines; build id→line-index map for in-place updates
    log_lines = log_path.read_text().splitlines()
    id_to_log_idx: dict[str, int] = {}
    orig_to_new: dict[str, str] = {}
    tag_of: dict[str, str] = {}

    for idx, line in enumerate(log_lines):
        m = re.match(r'\[(\w+\??)\]\s+(\S+)', line)
        if not m:
            continue
        tag, orig_id = m.group(1), m.group(2)
        tag_of[orig_id] = tag
        id_to_log_idx[orig_id] = idx
        if tag == 'PUBLISHED':
            pub = re.search(r'→\s+(\S+)', line)
            if pub:
                orig_to_new[orig_id] = pub.group(1)

    # Load updated.bib; index by entry ID
    parser = BibTexParser(common_strings=True)
    parser.ignore_nonstandard_types = False
    with open(out_path, encoding='utf-8') as f:
        out_db = bibtexparser.load(f, parser=parser)
    by_id = {e['ID']: e for e in out_db.entries}

    # Build carried dict and retry set
    carried: dict[str, dict] = {}
    retry_ids: set[str] = set()
    skip_tags = {'REFRESHED', 'PUBLISHED', 'ARXIV_REFRESHED', 'NO_TITLE', 'TITLE_CHANGED?', 'REVIEW'}
    if not retry_not_found:
        skip_tags.add('NOT_FOUND')

    for orig_id, tag in tag_of.items():
        if tag in skip_tags:
            lookup_id = orig_to_new.get(orig_id, orig_id)
            entry = by_id.get(f'DBLP:{lookup_id}') or by_id.get(lookup_id)
            if entry:
                carried[orig_id] = entry
            else:
                retry_ids.add(orig_id)
        else:  # FETCH_FAIL, ERROR, or anything unexpected
            retry_ids.add(orig_id)

    print(f"[resume] {len(carried)} entries carried from previous run, {len(retry_ids)} to retry.")
    return carried, retry_ids, log_lines, id_to_log_idx


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    global INPUT_BIB, OUTPUT_BIB, REVIEW_FILE, LOG_FILE

    ap = argparse.ArgumentParser(description="Refresh a BibTeX file against DBLP.")
    ap.add_argument("input",  nargs="?", default="references.bib",
                    help="Input BibTeX file (default: references.bib)")
    ap.add_argument("-o", "--output", default="updated.bib",
                    help="Output BibTeX file (default: updated.bib)")
    ap.add_argument("--log", default="update_log.txt",
                    help="Log file (default: update_log.txt)")
    ap.add_argument("--review", default="review_needed.txt",
                    help="Review file for ambiguous entries (default: review_needed.txt)")
    ap.add_argument("--resume", action="store_true",
                    help="Skip already-successful entries, retry only failures")
    ap.add_argument("--retry-not-found", action="store_true",
                    help="With --resume, also retry NOT_FOUND entries")
    args = ap.parse_args()

    INPUT_BIB   = args.input
    OUTPUT_BIB  = args.output
    LOG_FILE    = args.log
    REVIEW_FILE = args.review
    resume          = args.resume
    retry_not_found = args.retry_not_found

    bib_path = Path(INPUT_BIB)
    if not bib_path.exists():
        sys.exit(f"Error: {INPUT_BIB} not found")

    parser = BibTexParser(common_strings=True)
    parser.ignore_nonstandard_types = False
    with open(bib_path, encoding='utf-8') as f:
        db = bibtexparser.load(f, parser=parser)

    carried: dict = {}
    retry_ids: set = set()
    id_to_log_idx: dict = {}
    if resume:
        carried, retry_ids, log_lines, id_to_log_idx = load_resume_state(retry_not_found)
        updated_entries = []
        review_lines = []
    else:
        updated_entries = []
        review_lines = []
        log_lines = []

    def write_log(eid: str, line: str):
        """Update existing log line in-place (resume) or append (fresh run)."""
        if eid in id_to_log_idx:
            log_lines[id_to_log_idx[eid]] = line
        else:
            log_lines.append(line)

    # Maps final entry ID → orig bib key that produced it, for duplicate detection.
    final_id_map: dict[str, str] = {}

    def add_entry(new_entry: dict, orig_eid: str):
        """Append entry, logging [DUPLICATE_DROPPED] if its ID already exists."""
        fid = new_entry['ID']
        if fid in final_id_map:
            prev_eid = final_id_map[fid]
            write_log(prev_eid, f"[DUPLICATE_DROPPED] {prev_eid}  (same key as {orig_eid} → {fid})")
        final_id_map[fid] = orig_eid
        updated_entries.append(new_entry)

    writer = BibTexWriter()
    writer.indent = '  '

    def flush_outputs():
        # Deduplicate by entry ID, keeping the last occurrence (consistent with add_entry tracking).
        seen: dict[str, dict] = {}
        for e in updated_entries:
            seen[e['ID']] = e
        out_db = bibtexparser.bibdatabase.BibDatabase()
        out_db.entries = list(seen.values())
        with open(OUTPUT_BIB, 'w', encoding='utf-8') as f:
            f.write(writer.write(out_db))
        if review_lines:
            with open(REVIEW_FILE, 'w', encoding='utf-8') as f:
                f.write('\n'.join(review_lines))
        with open(LOG_FILE, 'w', encoding='utf-8') as f:
            f.write('\n'.join(log_lines))

    total = len(db.entries)
    print(f"Processing {total} entries...\n")

    for i, entry in enumerate(db.entries, 1):
        try:
            eid = entry.get('ID', f'entry_{i}')
            title = entry.get('title', '')
            biburl = entry.get('biburl', '')
            arxiv = is_arxiv_entry(entry)

            prefix = f"[{i:3}/{total}] {eid}"
            print(prefix, end=' ... ', flush=True)

            # --- Resume: carry over already-successful entries ---
            if resume:
                if eid in carried:
                    add_entry(carried[eid], eid)
                    print("skipped (carried from previous run)")
                    # keep the original log line — no change to log_lines
                    continue
                if eid not in retry_ids:
                    # Not in log at all (shouldn't happen), process normally
                    pass

            # --- Case 1: published DBLP entry (has biburl, not arXiv) ---
            if biburl and not arxiv:
                # Extract key from biburl: https://dblp.org/rec/KEY.bib
                m = re.search(r'dblp\.org/rec/(.+?)\.bib', biburl)
                if m:
                    new_bib = fetch_dblp_bib(m.group(1))
                    new_entry = parse_single_bib(new_bib) if new_bib else None
                    if new_entry:
                        add_entry(new_entry, eid)
                        print("refreshed")
                        write_log(eid, f"[REFRESHED]  {eid}")
                        continue
                print("fetch failed, keeping original")
                write_log(eid, f"[FETCH_FAIL] {eid}  (biburl: {biburl})")
                add_entry(entry, eid)
                continue

            # --- Case 2: arXiv entry (or no biburl) → search for published version ---
            if not title:
                print("no title, skipping")
                write_log(eid, f"[NO_TITLE]   {eid}")
                add_entry(entry, eid)
                continue

            time.sleep(RATE_LIMIT)
            status, candidates, arxiv_candidates = find_published(title)

            if status == 'found':
                dblp_key = dblp_info_to_bib_key(candidates[0])
                new_bib = fetch_dblp_bib(dblp_key) if dblp_key else None
                new_entry = parse_single_bib(new_bib) if new_bib else None
                if new_entry:
                    add_entry(new_entry, eid)
                    venue = candidates[0].get('venue', '?')
                    print(f"published → {dblp_key} [{venue}]")
                    write_log(eid, f"[PUBLISHED]  {eid}  →  {dblp_key}  [{venue}]")
                else:
                    add_entry(entry, eid)
                    print("bib fetch failed, keeping original")
                    write_log(eid, f"[FETCH_FAIL] {eid}  (found key: {dblp_key})")

            elif status == 'multiple':
                add_entry(entry, eid)
                print(f"multiple matches ({len(candidates)}), needs review")
                write_log(eid, f"[REVIEW]     {eid}  ({len(candidates)} candidates)")
                review_lines.append(f"\n{'='*70}")
                review_lines.append(f"ENTRY:  {eid}")
                review_lines.append(f"TITLE:  {title}")
                review_lines.append(f"CANDIDATES:")
                for c in candidates:
                    review_lines.append(f"  key:   {c.get('key')}")
                    review_lines.append(f"  title: {c.get('title')}")
                    review_lines.append(f"  venue: {c.get('venue')}  year: {c.get('year')}")
                    review_lines.append(f"  url:   https://dblp.org/rec/{c.get('key')}.bib")
                    review_lines.append("")

            elif status == 'title_changed':
                add_entry(entry, eid)
                print(f"possible title change ({len(candidates)} candidate(s)), needs review")
                write_log(eid, f"[TITLE_CHANGED?] {eid}  ({len(candidates)} candidate(s))")
                review_lines.append(f"\n{'='*70}")
                review_lines.append(f"ENTRY:  {eid}")
                review_lines.append(f"ORIG TITLE:  {title}")
                review_lines.append(f"CANDIDATES (title mismatch):")
                for c in candidates:
                    review_lines.append(f"  key:   {c.get('key')}")
                    review_lines.append(f"  title: {c.get('title')}")
                    review_lines.append(f"  venue: {c.get('venue')}  year: {c.get('year')}")
                    review_lines.append(f"  url:   https://dblp.org/rec/{c.get('key')}.bib")
                    review_lines.append("")

            elif status == 'not_found':
                # Try to refresh from DBLP's arXiv record instead of keeping original
                # Priority: search result arXiv hit > existing biburl
                arxiv_key = None
                if arxiv_candidates:
                    arxiv_key = arxiv_candidates[0].get('key')
                elif biburl:
                    m = re.search(r'dblp\.org/rec/(.+?)\.bib', biburl)
                    if m:
                        arxiv_key = m.group(1)

                if arxiv_key:
                    new_bib = fetch_dblp_bib(arxiv_key)
                    new_entry = parse_single_bib(new_bib) if new_bib else None
                    if new_entry:
                        add_entry(new_entry, eid)
                        print(f"arXiv refreshed from DBLP ({arxiv_key})")
                        write_log(eid, f"[ARXIV_REFRESHED] {eid}  ({arxiv_key})")
                        continue
                    print("arXiv fetch failed, keeping original")
                    write_log(eid, f"[FETCH_FAIL] {eid}  (arxiv key: {arxiv_key})")
                    add_entry(entry, eid)
                else:
                    add_entry(entry, eid)
                    print("not found on DBLP, keeping original")
                    write_log(eid, f"[NOT_FOUND]  {eid}")

            else:  # error
                add_entry(entry, eid)
                print("search error, keeping original")
                write_log(eid, f"[ERROR]      {eid}")

        finally:
            if i % CHECKPOINT_EVERY == 0:
                flush_outputs()
                print(f"  [checkpoint] wrote {i}/{total} entries to {OUTPUT_BIB}")

    flush_outputs()
    print(f"\nWrote {OUTPUT_BIB}")
    print(f"Wrote {REVIEW_FILE} (if any)")
    print(f"Wrote {LOG_FILE}")

    # Summary
    counts = {}
    for line in log_lines:
        tag = re.search(r'\[(\w+\??)\]', line)
        if tag:
            counts[tag.group(1)] = counts.get(tag.group(1), 0) + 1
    print("\nSummary:")
    for tag, n in sorted(counts.items()):
        print(f"  {tag}: {n}")


if __name__ == '__main__':
    main()

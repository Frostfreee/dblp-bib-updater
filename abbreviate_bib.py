#!/usr/bin/env python3
"""
Replace full conference names in booktitle with short abbreviations.

Reads a BibTeX file, and for every @inproceedings entry whose DBLP key
contains a known venue abbreviation, rewrites the booktitle field as
"ABBR YEAR" (e.g. "ACL 2024").

Entries whose venue is not in the mapping table are left unchanged and
reported at the end so you can extend the table.

Usage:
    python3 abbreviate_bib.py updated.bib                  # writes updated.abbrev.bib
    python3 abbreviate_bib.py updated.bib -o final.bib     # custom output path
"""

import argparse
import re
import sys
from pathlib import Path

import bibtexparser
from bibtexparser.bparser import BibTexParser
from bibtexparser.bwriter import BibTexWriter

# ---------------------------------------------------------------------------
# Venue mapping: DBLP key fragment → display abbreviation
# Extend this table for any venue not already listed.
# ---------------------------------------------------------------------------
VENUE_MAP: dict[str, str] = {
    # NLP / ML conferences
    "acl":          "ACL",
    "emnlp":        "EMNLP",
    "naacl":        "NAACL",
    "eacl":         "EACL",
    "coling":       "COLING",
    "ijcnlp":       "IJCNLP",
    "nips":         "NeurIPS",
    "iclr":         "ICLR",
    "icml":         "ICML",
    "aaai":         "AAAI",
    "ijcai":        "IJCAI",
    "aistats":      "AISTATS",
    "uai":          "UAI",
    "ecml":         "ECML",
    "kdd":          "KDD",
    "www":          "WWW",
    "icwsm":        "ICWSM",
    "icdm":         "ICDM",
    "bigdataconf":  "IEEE BigData",
    # Vision
    "cvpr":         "CVPR",
    "iccv":         "ICCV",
    "eccv":         "ECCV",
    "mm":           "ACM MM",
    # Security
    "sp":           "IEEE S&P",
    "ccs":          "CCS",
    "uss":          "USENIX Security",
    "ndss":         "NDSS",
    "acsac":        "ACSAC",
    "raid":         "RAID",
    "satml":        "IEEE SaTML",
    "aisec-ws":     "AISec",
    "camlis":       "CAMLIS",
    "aies":         "AIES",
    "aivr":         "IEEE AIVR",
    "aibthings":    "AIBThings",
    # Other
    "icassp":       "ICASSP",
    "smc":          "IEEE SMC",
    "hci":          "HCI",
    "ubisec":       "UbiSec",
    "icwsm":        "ICWSM",
    "ACMdis":       "ACM DIS",
}


def extract_venue(entry: dict) -> str | None:
    """Extract the venue fragment from a DBLP-style entry ID, e.g. 'acl' from 'DBLP:conf/acl/Foo24'."""
    m = re.match(r'(?:DBLP:)?conf/([^/]+)/', entry.get('ID', ''))
    return m.group(1) if m else None


def abbreviate_entry(entry: dict) -> tuple[bool, str | None]:
    """
    Rewrite the booktitle field if venue is known.

    Returns (changed, venue_fragment).
    """
    venue = extract_venue(entry)
    if venue is None:
        return False, None

    abbr = VENUE_MAP.get(venue)
    if abbr is None:
        return False, venue

    year = entry.get('year', '')
    entry['booktitle'] = f"{abbr} {year}".strip()
    return True, venue


def main():
    ap = argparse.ArgumentParser(
        description="Replace booktitle with short venue abbreviations.")
    ap.add_argument("input", nargs="?", default="updated.bib",
                    help="Input BibTeX file (default: updated.bib)")
    ap.add_argument("-o", "--output", default=None,
                    help="Output BibTeX file (default: <input-stem>.abbrev.bib)")
    args = ap.parse_args()

    in_path  = Path(args.input)
    out_path = Path(args.output) if args.output else \
               in_path.with_name(in_path.stem + ".abbrev.bib")

    if not in_path.exists():
        sys.exit(f"Error: {in_path} not found.")

    parser = BibTexParser(common_strings=True)
    parser.ignore_nonstandard_types = False
    with open(in_path, encoding='utf-8') as f:
        db = bibtexparser.load(f, parser=parser)

    changed_count = 0
    unknown_venues: set[str] = set()

    for entry in db.entries:
        if entry.get('ENTRYTYPE') != 'inproceedings':
            continue
        changed, venue = abbreviate_entry(entry)
        if changed:
            changed_count += 1
        elif venue is not None:
            unknown_venues.add(venue)

    writer = BibTexWriter()
    writer.indent = '  '
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(writer.write(db))

    print(f"Abbreviated {changed_count} entries → {out_path}")

    if unknown_venues:
        print(f"\nUnknown venues (booktitle left unchanged) — add to VENUE_MAP if needed:")
        for v in sorted(unknown_venues):
            print(f"  {v}")


if __name__ == '__main__':
    main()

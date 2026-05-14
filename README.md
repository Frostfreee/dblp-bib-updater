# dblp-bib-updater

A tool to keep a BibTeX reference file up to date by re-fetching entries from [DBLP](https://dblp.org). It handles two scripts:

- **`update_refs.py`** — refreshes `references.bib` against DBLP, detecting newly published arXiv preprints and updating metadata for already-published entries.
- **`update_tex.py`** — rewrites `\cite{}` keys in `.tex` files to match any citation key changes produced by `update_refs.py`.

All data is fetched from DBLP's free public API. No API key or account required.

## Requirements

```bash
pip install bibtexparser
```

## Workflow

```
references.bib
      │
      ▼
update_refs.py  ──→  updated.bib
                      update_log.txt
                      review_needed.txt
      │
      ▼
update_tex.py   ──→  rewrites .tex files in-place
                      tex_update_log.txt
```

---

## update_refs.py

### Usage

```bash
# Full run — processes all entries from scratch
python3 update_refs.py references.bib

# Custom output path
python3 update_refs.py references.bib -o final.bib

# Resume — skips already-successful entries, retries only failures
python3 update_refs.py references.bib --resume

# Resume and also retry NOT_FOUND entries
# (useful when re-running after adding new detection logic)
python3 update_refs.py references.bib --resume --retry-not-found

# Full option reference
python3 update_refs.py --help
```

### Output files

| File | Description |
|---|---|
| `updated.bib` | Final BibTeX file — updated where possible, originals kept otherwise |
| `update_log.txt` | Per-entry processing result |
| `review_needed.txt` | Entries requiring manual selection (multiple matches or title mismatch) |

The original `references.bib` is never modified.

### How it works

**Already-published entries** (have a DBLP `biburl`, not arXiv): re-downloads the latest BibTeX directly from DBLP.

**arXiv preprints** (DBLP `journals/corr` entries or hand-crafted arXiv entries): searches DBLP by title for a formally published version.

```
arXiv entry
    │
    ├─→ Published version found, title matches exactly  →  [PUBLISHED]
    ├─→ Published version found, title differs          →  [TITLE_CHANGED?]
    │
    └─→ No published version found
            ├─→ DBLP arXiv record exists  →  refresh metadata  →  [ARXIV_REFRESHED]
            └─→ Not on DBLP at all        →  keep original      →  [NOT_FOUND]
```

### Log tags

| Tag | Meaning |
|---|---|
| `[REFRESHED]` | Already-published entry re-downloaded from DBLP. Metadata (pages, DOI, timestamp) may have been updated. |
| `[PUBLISHED]` | arXiv preprint replaced with its formally published version. Log line shows new DBLP key and venue, e.g. `→ conf/iclr/ShenZD0025 [ICLR]`. **Citation key changes.** |
| `[ARXIV_REFRESHED]` | arXiv preprint with no published version found; metadata refreshed from DBLP's arXiv record. **Citation key may change** for hand-crafted entries. |
| `[NOT_FOUND]` | No DBLP record found at all (neither published nor arXiv). Original entry kept. |
| `[TITLE_CHANGED?]` | DBLP returned published results but no title matched exactly. The paper may have been published under a different title. Candidates listed in `review_needed.txt`. |
| `[REVIEW]` | Multiple published versions found with the same title. Candidates listed in `review_needed.txt`. |
| `[FETCH_FAIL]` | BibTeX download failed after retries. Original entry kept. Retried by `--resume`. |
| `[ERROR]` | DBLP search request failed (e.g. network timeout). Original entry kept. Retried by `--resume`. |
| `[DUPLICATE_DROPPED]` | Entry dropped because another entry resolved to the same DBLP key. |
| `[NO_TITLE]` | Entry has no title field; skipped. |

### Title matching rules

A DBLP result is accepted only if the title matches **exactly** after normalization:

- LaTeX braces `{}` are stripped (from both the bib entry and the search query)
- All whitespace (including newlines from multi-line BibTeX fields) is collapsed to a single space
- Comparison is case-insensitive
- All punctuation is ignored

Any word-level difference counts as a mismatch → `[TITLE_CHANGED?]`.

### Resume mode

`--resume` reads `update_log.txt` and `updated.bib` from the previous run:

| Previous tag | Resume behaviour |
|---|---|
| `REFRESHED`, `PUBLISHED`, `ARXIV_REFRESHED`, `NOT_FOUND`, `NO_TITLE`, `TITLE_CHANGED?`, `REVIEW` | Carried over from `updated.bib`; original log line preserved |
| `FETCH_FAIL`, `ERROR` | Re-processed; log line updated in-place on success |

### Options

| Option | Default | Description |
|---|---|---|
| `input` | `references.bib` | Input BibTeX file |
| `-o` / `--output` | `updated.bib` | Output BibTeX file |
| `--log` | `update_log.txt` | Log file path |
| `--review` | `review_needed.txt` | Review file path |
| `--resume` | — | Skip already-successful entries, retry failures |
| `--retry-not-found` | — | With `--resume`, also retry `NOT_FOUND` entries |

### Tunable constants

At the top of `update_refs.py`:

| Variable | Default | Description |
|---|---|---|
| `RATE_LIMIT` | `2.0` | Seconds between DBLP requests. Increase if you see frequent timeouts. |
| `CHECKPOINT_EVERY` | `10` | Write output files every N entries. |

---

## update_tex.py

Reads the key mapping produced by `update_refs.py` (`[PUBLISHED]` and `[ARXIV_REFRESHED]` entries in `update_log.txt`) and rewrites all `\cite{}` commands in `.tex` files accordingly.

### Usage

```bash
# Update all .tex files under a directory
python3 update_tex.py path/to/paper/

# Custom log file location
python3 update_tex.py path/to/paper/ --log update_log.txt

# Full option reference
python3 update_tex.py --help
```

### What it handles

- All `\cite` variants: `\cite`, `\citep`, `\citet`, `\citealt`, `\citealp`, `\citeauthor`, `\citeyear`, `\nocite`, etc.
- Multi-key citations: `\cite{key1, key2, key3}`
- Optional bracket arguments: `\cite[see][]{key}`
- Recursive search across all `.tex` files under the configured directory

### Options

| Option | Default | Description |
|---|---|---|
| `tex_dir` | _(required)_ | Directory to search recursively for `.tex` files |
| `--log` | `update_log.txt` | `update_log.txt` path |
| `--tex-log` | `tex_update_log.txt` | Output log file path |

### Output

Files are modified in-place. `tex_update_log.txt` records every substitution with file path and line number:

```
[UPDATED] paper/main.tex
  line   42: DBLP:journals/corr/abs-2410-02298  →  DBLP:conf/iclr/ShenZD0025
  line  107: Wang2024RePD  →  DBLP:conf/naacl/WangLX25
[NO CHANGE] paper/appendix.tex
```

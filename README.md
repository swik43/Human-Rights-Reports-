# World Reports

Turns annual human rights report PDFs from four organisations into a clean, per-country corpus.

- Amnesty International (AI)
- Human Rights Watch (HRW)
- the Internal Displacement Monitoring Centre (IDMC)
- and the U.S. State Department (US)

The final output:

- **`samples_readable/`** - the highest-fidelity human-readable version of each report (PDF / HTML / Markdown).
- **`samples_llm/`** - the same content as Markdown for LLM consumption (oversized files split into bounded parts; see [Step 7b](#step-7b-split-oversized-markdown)).
- **`sources/`** - the original parent reports, kept for provenance.

Every output file gets a stable ID like `IDMC-CR-2008-Iraq` or `AI-WR-1999(1998)-Afghanistan` (see [File ID scheme](#file-id-scheme)).

---

## Quick start

### 1. Setup

Requires Python 3.11+ and [uv](https://github.com/astral-sh/uv).

```bash
uv venv && source .venv/bin/activate
uv sync
```

### 2. input PDFs

<!-- see if we can commit them or otherwise make them easily accessible -->

The source PDFs are included in the repo under `input/` 

### 3. Build

```bash
python pipeline/run.py
```

This one command reads `input/`, does everything, and writes `samples_readable/`, `samples_llm/`, and `sources/` under `output/`.

---

## Two ways to run

There are two entry points that produce the **same** final output. They share their core logic, so results match.

### Prod - one command (recommended)

`pipeline/run.py` materialises every final file in a single pass, writing each leaf exactly once (no intermediate copies). This is what most users want.

```bash
# Everything, all orgs:
python pipeline/run.py

# Preview the plan, write nothing:
python pipeline/run.py --dry-run

# Limit to some orgs:
python pipeline/run.py --orgs idmc hrw

# Apply the conflict-years research subset (see Country/year filter):
python pipeline/run.py --filter

# Write somewhere other than output/ (handy for testing):
python pipeline/run.py --out-root /tmp/build
```

| Flag             | Effect                                                         |
| ---------------- | -------------------------------------------------------------- |
| `--orgs ORG ...` | Limit to a subset of `ai hrw idmc us` (default: all)           |
| `--filter`       | Apply the conflict-years country/year filter (research subset) |
| `--copy`         | Copy readable files instead of symlinking them into `input/`   |
| `--dry-run`      | Plan only; write nothing                                       |
| `--out-root DIR` | Write under `DIR` instead of `output/`                         |
| `--workers N`    | Parallel markdown-conversion workers (default: all cores)      |
| `--keep-scratch` | Keep `intermediate/unsplit/` after building                    |

### Dev - the step chain

The `pipeline/*.py` step scripts run the pipeline one stage at a time, each writing to `intermediate/` and emitting a manifest in `manifests/`. Use this to re-run or debug individual stages, or to inspect the audit trail. Run in order:

```bash
python pipeline/validate_input.py               # 0  inventory + sanity checks
python pipeline/unsplit_double_pages.py hrw     # 1  double-layout PDFs (hrw, idmc)
python pipeline/unsplit_double_pages.py idmc
#  steps 2–3 are human-assisted and already committed under config/ - skip
python pipeline/split_pdfs.py hrw               # 4  split WR PDFs into per-country
python pipeline/split_pdfs.py ai
python pipeline/split_pdfs.py idmc
python pipeline/standardise_names.py            # 5  standardise country names
python pipeline/filter_files.py                 # 6  conflict-years filter
python pipeline/convert_to_markdown.py          # 7  PDF/HTML -> markdown
python pipeline/split_large.py                  # 7b split oversized markdown
python pipeline/organise.py                     # 8  assign IDs -> final output
```

Every step accepts [scoping flags](#scoping-flags) (`--org`, `--year`, `--country`, `--dry-run`, `--force`) for targeted re-runs.

---

# Detailed reference

## Input structure

```
input/
  wr/                                       # full annual "world reports"
    ai/
      1999(1998)_Amnesty_International.pdf
      2019(2018)_Africa_Amnesty_International.pdf    # regional
    hrw/
      2005(2004)_World_Report_Human_Rights_Watch.pdf
    idmc/
      2004_Global.pdf
  cr/                                       # standalone per-country reports
    ai/
      Afghanistan/
        2003_Afghanistan.pdf
      Guinea, Liberia, Sierra Leone/        # multi-country parent + splits
        2001_GuineaLiberiaSierra_Leone.pdf  #   source document
        split_files/
          2001_Guinea.pdf                   #   classification candidates
          2001_Liberia.pdf
    idmc/
      Afghanistan/
        2006_Afghanistan_a.pdf
        2021_Afghanistan_IDMC_Profile.md
      Regional Reports/                     # nested subdirectories
        Africa/
          2006_Africa.pdf                   #   source document
          split_files/
            2006_West_Africa_Cote_dIvoire.pdf
    us/
      Algeria/
        1999_Algeria.md                     # .md where no PDF exists
```

- **WR** files are full annual reports (one PDF = many countries), or pre-split per-country files, depending on year/org.
- **CR** files are standalone per-country reports, already in country folders.
- The `YYYY(YYYY-1)` filename format indicates publication year and coverage year. Some files just use `YYYY`. Both are valid.

### CR scanning rules

The CR tree is not a uniform depth. Two rules handle it:

1. **Recursive scanning.** The scanner walks recursively under each `input/cr/{org}/` and collects all leaf files regardless of depth. The entity name comes from the _filename_ (via the standardisation map), not the folder path; the folder path only determines the org.

2. **The `split_files/` convention.** If a directory contains a `split_files/` subfolder, files **inside** it are classification candidates (routed to `samples_*`), and files **next to** it are source/parent documents (routed to `sources/`). With no `split_files/` subfolder, all files in the directory are classification candidates (the normal case).

Files whose entity matches no known country (e.g. an un-split `2006_Africa.pdf`) are routed to a `_general/` folder automatically.

## File ID scheme

Every classification candidate gets a unique ID used as its filename:

```
{ORG}-{TYPE}-{YEAR}-{ENTITY}[-{SUFFIX}][-p{PART}].{ext}
```

| Component | Values                           | Rules                                                                                                                                                 |
| --------- | -------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------- |
| ORG       | `AI`, `HRW`, `IDMC`, `US`        | Uppercase                                                                                                                                             |
| TYPE      | `WR`, `CR`, `CP`, `SR`           | See below                                                                                                                                             |
| YEAR      | `1999`…`2023`, or `YYYY(YYYY-1)` | Publication year, optionally with coverage year                                                                                                       |
| ENTITY    | Standardised name                | Spaces become `_` in the filename; folder names keep spaces                                                                                           |
| SUFFIX    | `-a`, `-b`, `-c`…                | Only when multiple files share the same ORG-TYPE-YEAR-ENTITY                                                                                          |
| PART      | `-p1`, `-p2`…                    | Only on the LLM markdown when [Step 7b](#step-7b-split-oversized-markdown) split an oversized file. A _separate_ axis from SUFFIX, appended after it. |

**Types:**

| Type | Meaning                                               | Source                                               |
| ---- | ----------------------------------------------------- | ---------------------------------------------------- |
| `WR` | Per-country chapter split from an annual World Report | Split from WR PDFs, or pre-split at source           |
| `CR` | Standalone country report                             | `input/cr/{ai,hrw,idmc}/`                            |
| `CP` | IDMC Country Profile                                  | `input/cr/idmc/`, files matching `*_IDMC_Profile.md` |
| `SR` | U.S. State Dept annual country report                 | `input/cr/us/`                                       |

**Examples:**

- `AI-WR-1999(1998)-Afghanistan` - AI World Report published 1999, covers 1998
- `HRW-CR-2003-Iraq` - standalone HRW country report
- `IDMC-CP-2021-India` - IDMC Country Profile
- `US-SR-2005-Sudan` - US State Dept report
- `AI-CR-2016-Afghanistan-a` - first of multiple AI Afghanistan reports in 2016
- `IDMC-CR-2008-Iraq-p2` - part 2 of an oversized report split by step 7b
- `IDMC-CR-2009-Sri_Lanka-a-p1` - part 1 of the `-a` file (both axes at once)

**Year parsing rule:** `(\d{4})(?:\((\d{4})\))?` extracts the publication year
and optional coverage year.

## Output structure

```
output/
  samples_readable/{org}/{type}/{country_folder}/{ID}.{ext}
  samples_llm/{org}/{type}/{country_folder}/{ID}.{ext}
  sources/{org}/{world_reports|country_reports|regional_reports}/{original_name}
```

- `{org}` and `{type}` are lowercase (`ai`, `wr`, …).
- `{country_folder}` keeps original casing and spaces (e.g. `Bosnia and Herzegovina`); the `{ID}` uses underscores.
- Shared folders collect related entities - e.g. `Serbia/` holds both `…-Yugoslavia_(Federal_Republic_of)` and `…-Serbia`; `Israel and Palestine/` holds `…-Israel_-_OPT`. Non-country/thematic reports go to `_general/`.

**`samples_llm/` is a structural mirror of `samples_readable/`.** It holds the Markdown version of each file, except where conversion isn't possible (e.g. scanned AI pre-2013 PDFs), in which case the original format is kept. Every readable file has a corresponding LLM entry - split files map to their `-pN` parts, all sharing the single readable.

**`sources/`** keeps the original parent reports (filenames preserved, not renamed to IDs). Each organised file records a `source_type`: `split_from_world_report`, `split_from_regional_report`, `downloaded_pre_split`, or `standalone`.

## Pipeline steps

Each step reads the previous step's output and writes a manifest to `manifests/`. Steps 2–3 require a human and are committed under `config/`, so downstream users never redo them.

### Step 0: Validate input

**`validate_input.py`** · reads `input/` · writes `manifests/0_input_inventory.json`

Walks the input tree and inventories every file (path, org, type, year, detected country). Checks that WR source PDFs named in configs exist and flags anomalies. A sanity check, not a transformation.

### Step 1: Convert double-layout PDFs (WR only)

**`unsplit_double_pages.py`** · reads `input/wr/{org}/`, `config/{org}/1_unsplit_config.json` · writes `intermediate/unsplit/{org}/`

Some WR PDFs print two pages side-by-side on each sheet. This crops them into single-page-per-sheet versions. Only processes files listed in the unsplit config; originals are untouched.

### Steps 2–3: Extract contents & build split config (WR only) - committed

The table-of-contents pages of each WR PDF are rendered to images (`dev/extract_contents_images.py`), a human extracts country/page data with Claude into `config/{org}/contents_json/`, and `dev/build_final_config.py` merges it with page offsets into `config/{org}/*_split_config.json`. **These outputs are committed**, so a fresh checkout skips this manual work.

### Step 4: Split WR PDFs

**`split_pdfs.py`** · reads `input/wr/{org}/` or `intermediate/unsplit/{org}/`, `config/{org}/*_split_config.json` · writes `intermediate/split_wr/{org}/{year}/{Country}.pdf` · manifest `4_split_wr.json`

Splits each WR PDF into per-country files per the split config. Pre-split files (`"pre_split": true` in the config) are copied through without re-splitting.

### Step 5: Standardise country names

**`standardise_names.py`** · reads `intermediate/split_wr/`, `input/cr/`, `config/country_name_standardisation.json` · writes `intermediate/standardised/` · manifest `5_standardised.json`

Renames every file to a standardised entity name (via `variant_to_standard`) and determines its `country_folder` (via `entity_to_folder`). Non-country entities route to `_general/`. CR scanning follows the two rules above; source documents next to a `split_files/` folder are marked `"is_source": true` for step 8. Unresolvable names are logged to `unknown_countries.txt` and skipped.

### Step 6: Filter by country and year

**`filter_files.py`** · reads `intermediate/standardised/`, `config/conflict_years_first_relevant.csv` · writes `intermediate/filtered/` · manifest `6_filtered.json`

Keeps a file only if its `country_folder` is in the CSV and its year ≥ that country's `First_Relevant_Year`. `_general/` files are always kept, as are sub-national entities filed under an included country (e.g. `Russia_Chechnya` under Russia). Everything else is listed in `discarded.txt`. See [Country/year filter](#countryyear-filter-research-subset).

### Step 7: Convert to markdown

**`convert_to_markdown.py`** · reads `intermediate/filtered/` · writes `intermediate/markdown/` · manifest `7_converted.json`

Produces the lowest-resolution LLM version of each file:

- PDF → Markdown via `pymupdf4llm`
- PDF below the org's `min_markdown_year` (scanned, e.g. AI pre-2013) → copied as-is
- `.html` → Markdown; `.md` → copied as-is
- conversion failure → original copied, error logged in the manifest

### Step 7b: Split oversized markdown

**`split_large.py`** · reads `intermediate/markdown/` (via `7_converted.json`) · writes `intermediate/markdown_split/` · manifest `7b_split.json`

Some Markdown files are too large for a single LLM instance. This slices any `.md` over `TARGET_TOKENS` (≈67,000, estimated as `chars / 4`) into parts cut at heading boundaries.

- **Auto packing (default):** greedily accumulate heading-delimited blocks up to
  the ceiling, then cut at the fullest heading boundary - preferring a shallower
  heading if one sits within ~20% behind it. A single section over the ceiling
  falls back to paragraph, then hard-character, splitting.
- **Overrides (`config/split_overrides.json`):** keyed by markdown path _or_
  filename → an ordered list of anchor-heading substrings to cut before, for the
  cases auto handles badly. Parts still over the ceiling are auto-packed within.

Each part is prepended with a breadcrumb comment (`<!-- split part N/M of {stem} - section: "…" -->`). Files at or under target are **not** copied - their manifest record points back at the step-7 markdown, so step 8 reads one uniform `parts` list either way.

### Step 8: Assign IDs and organise

**`organise.py`** · reads `intermediate/{filtered,markdown,markdown_split}/`, `input/wr/` · writes `output/{samples_readable,samples_llm,sources}/` · manifest `8_organised.json`

The final assembly. For each filtered file it builds the [ID](#file-id-scheme), copies the readable to `samples_readable/`, copies the LLM markdown (or each `-pN` part) to `samples_llm/`, and copies parent/source documents to `sources/`. Suffix collisions (`-a`, `-b`, …) are assigned by alphabetical sort of original filenames. Split parts get one record each, tagged `part_of` / `part_index` / `part_total`, all sharing the single readable.

## Configuration files

All configuration lives under `config/`.

**Global:**

| File                                | Used by    | Purpose                                                                     |
| ----------------------------------- | ---------- | --------------------------------------------------------------------------- |
| `country_name_standardisation.json` | step 5     | Maps variant names → standard names and folders                             |
| `conflict_years_first_relevant.csv` | steps 5, 6 | Countries + start years for the filter; also augments the known-country set |
| `split_overrides.json`              | step 7b    | Optional hand-curated split points (absent = pure auto)                     |

**Per-org (`config/{ai,hrw,idmc}/`):**

| File                     | Step | Purpose                                           | Created by                  |
| ------------------------ | ---- | ------------------------------------------------- | --------------------------- |
| `1_unsplit_config.json`  | 1    | Which PDFs are double-layout, where doubles start | Human                       |
| `*_contents_config.json` | 2–3  | Contents-page numbers + page offset               | Human                       |
| `contents_json/*.json`   | 3    | Country names + page numbers                      | Human (Claude-assisted)     |
| `*_overrides.json`       | 3    | Manual country data bypassing Claude              | Human                       |
| `*_split_config.json`    | 4    | Merged config: country + true_page + source_path  | `dev/build_final_config.py` |

### Country name standardisation

`config/country_name_standardisation.json` provides three lookup tables:

- **`variant_to_standard`** - any known spelling variant → standardised entity (`"Russian Federation"` → `"Russia"`). Not present ⇒ already standard.
- **`entity_to_folder`** - standardised entity → shared country folder where entities coexist (`"Israel_-_OPT"` → `"Israel and Palestine"`). Not present ⇒ folder is the entity name with `_` → spaces.
- **`csv_to_folder`** - names from the filter CSV → folder names where they differ (`"Ivory Coast"` → `"Cote D'Ivoire"`).

### Country/year filter (research subset)

`config/conflict_years_first_relevant.csv` defines a set of countries and a start year for each. The filter (step 6, or prod `--filter`) keeps only files whose `country_folder` is listed and whose year ≥ that country's `First_Relevant_Year`. This is a **research-specific subset** - prod runs **unfiltered by default**; pass `--filter` to apply it.

## Scoping flags

The dev step scripts support targeted re-runs:

```
--org ai|hrw|idmc|us       # process only this organisation (repeatable)
--year 2010 2015           # process only these years
--country Sudan Iraq       # process only these countries (steps 5+)
--dry-run                  # preview without writing
--force                    # re-process even if output already exists
```

Re-running a step checks the previous step's manifest and skips files that already have valid output unless `--force` is passed.

## Manifests

Each dev step writes a JSON manifest to `manifests/` recording every file it touched, plus a summary. Manifests are append-friendly: re-running a step with a narrow scope merges new results into the existing manifest rather than overwriting it. They form a complete audit trail; `manifests/` and the `intermediate/` tree can be deleted once the final output is validated.

> Manifests and the `intermediate/` tree are generated artifacts and are
> gitignored - regenerate them by running the pipeline.

## Intermediate directory

```
intermediate/
  unsplit/         # step 1: double-layout PDFs made single-page
  split_wr/        # step 4: per-country PDFs from WR splitting
  standardised/    # step 5: renamed with standardised country names
  filtered/        # step 6: only the countries/years in scope
  markdown/        # step 7: markdown conversions (mirrors filtered/)
  markdown_split/  # step 7b: parts of oversized markdown only
```

## Error handling

- **Unknown country names (step 5):** logged to `unknown_countries.txt`; the
  pipeline continues. Fix the standardisation map and re-run step 5 scoped with
  `--country`.
- **Markdown conversion failures (step 7):** logged in the manifest with
  `"status": "error"`; the readable PDF is still produced. Re-run step 7 scoped
  to the failed files.
- **Suffix collisions (step 8):** resolved deterministically (alphabetical sort
  of original filenames) and recorded in the manifest.
- **Missing source PDFs:** logged as warnings; other years/orgs continue.

## Repository layout

```
pipeline/   # the pipeline: prod entry (run.py) + the step scripts
dev/        # helpers: contents extraction, config building, verifiers, one-offs
config/     # all configuration (global + per-org), committed
input/      # source PDFs (not in repo) + committed config inputs
output/     # generated final output (gitignored)
```

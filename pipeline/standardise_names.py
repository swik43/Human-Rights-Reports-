"""
Step 5: Standardise country names.

For WR splits (from step 4) and CR files (from input/cr/):
- Standardises entity names using country_name_standardisation.json
- Routes to appropriate country folders
- Routes non-country entities to _general/
- Logs unknown entities to unknown_countries.txt

Usage:
    python pipeline/standardise_names.py
    python pipeline/standardise_names.py --org hrw --year 2010
    python pipeline/standardise_names.py --country "Russia (Chechnya)"
"""

import argparse
import csv
import json
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

from config import make_layout, make_progress
from rich.live import Live
from rich.text import Text

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SPLIT_WR_DIR = PROJECT_ROOT / "intermediate" / "split_wr"
CR_DIR = PROJECT_ROOT / "input" / "cr"
STANDARDISED_DIR = PROJECT_ROOT / "intermediate" / "standardised"
MANIFESTS_DIR = PROJECT_ROOT / "manifests"
STD_MAP_PATH = PROJECT_ROOT / "config" / "country_name_standardisation.json"
CSV_PATH = PROJECT_ROOT / "config" / "conflict_years_first_relevant.csv"

VALID_WR_ORGS = {"ai", "hrw", "idmc"}
VALID_CR_ORGS = {"ai", "hrw", "idmc", "us"}

YEAR_RE = re.compile(r"(\d{4})(?:\((\d{4})\))?")
SUFFIX_RE = re.compile(r"^(.+)_([a-z])$")
REGION_PREFIXES = ["Eastern_Africa_", "West_Africa_"]


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Step 5: Standardise country names")
    p.add_argument("--org", action="append", help="Only process this org (repeatable)")
    p.add_argument("--year", action="append", help="Only process this year (repeatable)")
    p.add_argument(
        "--country", action="append",
        help="Only process this raw country name (repeatable)",
    )
    p.add_argument("--dry-run", action="store_true", help="Preview without copying files")
    return p.parse_args()


# ── Standardisation map ──────────────────────────────────────────────


def load_maps():
    """Load standardisation map and build known entities set."""
    std = json.loads(STD_MAP_PATH.read_text())
    v2s = std["variant_to_standard"]
    e2f = std["entity_to_folder"]
    general = set(std["general_entities"])

    # Build known entities: all standardised entity names
    known = set(v2s.values()) | set(e2f.keys())

    # Add countries from CSV (these are valid even if they have no variant mapping)
    # CSV already uses standardised folder names directly
    with open(CSV_PATH) as f:
        for row in csv.DictReader(f):
            folder = row["Country"].strip()
            known.add(folder.replace(" ", "_"))

    return v2s, e2f, general, known


def resolve_entity(raw: str, v2s: dict, known: set) -> str | None:
    """Resolve a raw entity name to its standardised form, or None if unknown."""
    # 1. Exact lookup
    if raw in v2s:
        return v2s[raw]
    # 2. Normalise spaces → underscores, try again
    normalised = raw.replace(" ", "_")
    if normalised in v2s:
        return v2s[normalised]
    # 3. Check known entities set directly
    if normalised in known:
        return normalised
    return None


def get_folder(entity: str, e2f: dict, general: set) -> str:
    """Determine the country folder for a standardised entity."""
    if entity in general:
        return "_general"
    if entity in e2f:
        return e2f[entity]
    return entity.replace("_", " ")


def extract_pub_year(s: str) -> str | None:
    """Extract the 4-digit publication year from a string."""
    m = YEAR_RE.search(s)
    return m.group(1) if m else None


# ── CR filename parsing ──────────────────────────────────────────────


def parse_cr_stem(stem: str) -> tuple | None:
    """Parse a CR filename stem into components.

    Returns (year_str, raw_entity, suffix, is_profile, region_prefix) or None.
    """
    m = YEAR_RE.match(stem)
    if not m:
        return None

    year_str = m.group(0)
    rest = stem[m.end():]

    # Handle letter suffix glued to year: "1999a_Israel-OPT" → suffix "a"
    year_suffix = None
    if rest and rest[0].islower() and len(rest) > 1 and rest[1] == "_":
        year_suffix = rest[0]
        rest = rest[2:]
    elif rest.startswith("_"):
        rest = rest[1:]
    else:
        rest = rest

    body = rest
    if not body:
        return None

    # Strip IDMC Profile marker
    is_profile = body.endswith("_IDMC_Profile")
    if is_profile:
        body = body[: -len("_IDMC_Profile")]

    # Strip single-letter suffix (from body or from year-attached letter)
    suffix = year_suffix
    sm = SUFFIX_RE.match(body)
    if sm:
        body = sm.group(1)
        suffix = sm.group(2)

    # Strip region prefix
    region_prefix = None
    for prefix in REGION_PREFIXES:
        if body.startswith(prefix):
            region_prefix = prefix.rstrip("_")
            body = body[len(prefix) :]
            break

    return year_str, body, suffix, is_profile, region_prefix


def build_cr_filename(
    year_str: str, entity: str, suffix: str | None, is_profile: bool, ext: str
) -> str:
    """Build the standardised output filename for a CR file."""
    name = f"{year_str}_{entity}"
    if suffix:
        name += f"_{suffix}"
    if is_profile:
        name += "_IDMC_Profile"
    return name + ext


# ── WR processing ────────────────────────────────────────────────────


def process_wr(args, v2s, e2f, general, known, tick=lambda label: None):
    """Process WR split files from intermediate/split_wr/."""
    records = []
    unknowns = []

    org_filter = set(args.org) if args.org else None
    year_filter = set(args.year) if args.year else None
    country_filter = set(args.country) if args.country else None

    if not SPLIT_WR_DIR.exists():
        return records, unknowns

    for org in sorted(os.listdir(SPLIT_WR_DIR)):
        org_path = SPLIT_WR_DIR / org
        if not org_path.is_dir() or org.startswith("."):
            continue
        if org not in VALID_WR_ORGS:
            continue
        if org_filter and org not in org_filter:
            continue

        for year_dir_name in sorted(os.listdir(org_path)):
            year_path = org_path / year_dir_name
            if not year_path.is_dir() or year_dir_name.startswith("."):
                continue
            pub_year = extract_pub_year(year_dir_name)
            if year_filter and pub_year not in year_filter:
                continue

            for f in sorted(year_path.iterdir()):
                if f.name.startswith(".") or not f.is_file():
                    continue

                # Strip single-letter suffix (upper or lower), normalise to lowercase
                raw_stem = f.stem
                suffix = None
                sm = re.match(r"^(.+)_([a-zA-Z])$", raw_stem)
                if sm:
                    raw_stem = sm.group(1)
                    suffix = sm.group(2).lower()
                raw_entity = raw_stem

                if country_filter and raw_entity not in country_filter:
                    tick(f"{org} {year_dir_name} · {raw_entity} → (skipped)")
                    continue

                entity = resolve_entity(raw_entity, v2s, known)
                if entity is None:
                    tick(f"{org} {year_dir_name} · {raw_entity} → ?? (unknown)")
                    unknowns.append((str(f.relative_to(PROJECT_ROOT)), raw_entity))
                    records.append(_wr_record(
                        f, org, year_dir_name, raw_entity, None, None, "skipped_unknown",
                        suffix=suffix,
                    ))
                    continue

                tick(f"{org} {year_dir_name} · {raw_entity} → {entity}")
                folder = get_folder(entity, e2f, general)
                out_name = entity + (f"_{suffix}" if suffix else "") + f.suffix
                if folder == "_general":
                    dest = (
                        STANDARDISED_DIR / "wr" / org / year_dir_name
                        / "_general" / out_name
                    )
                else:
                    dest = (
                        STANDARDISED_DIR / "wr" / org / year_dir_name
                        / out_name
                    )

                if not args.dry_run:
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(f, dest)

                records.append(_wr_record(
                    f, org, year_dir_name, raw_entity, entity, folder, "ok", dest,
                    suffix=suffix,
                ))

    return records, unknowns


def _wr_record(f, org, year, raw, entity, folder, status, dest=None, suffix=None):
    return {
        "input_path": str(f.relative_to(PROJECT_ROOT)),
        "output_path": str(dest.relative_to(PROJECT_ROOT)) if dest else None,
        "org": org,
        "type": "WR",
        "year": year,
        "country_raw": raw,
        "country_standardised": entity,
        "country_folder": folder,
        "is_source": False,
        "suffix": suffix,
        "region_prefix": None,
        "status": status,
    }


# ── CR processing ────────────────────────────────────────────────────


def process_cr(args, v2s, e2f, general, known, tick=lambda label: None):
    """Process CR files from input/cr/ (recursive, split_files convention)."""
    records = []
    unknowns = []

    org_filter = set(args.org) if args.org else None
    year_filter = set(args.year) if args.year else None
    country_filter = set(args.country) if args.country else None

    if not CR_DIR.exists():
        return records, unknowns

    for org_entry in sorted(CR_DIR.iterdir()):
        if not org_entry.is_dir() or org_entry.name.startswith("."):
            continue
        org = org_entry.name.lower()
        if org not in VALID_CR_ORGS:
            continue
        if org_filter and org not in org_filter:
            continue

        for dirpath, dirnames, filenames in _sorted_walk(org_entry):
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]

            is_split_files_dir = dirpath.name == "split_files"
            has_split_files_child = "split_files" in dirnames

            for fname in sorted(filenames):
                if fname.startswith("."):
                    continue
                f = dirpath / fname
                ext = f.suffix.lower()

                # Determine source status via split_files convention
                if is_split_files_dir:
                    is_source = False
                elif has_split_files_child:
                    is_source = True
                else:
                    is_source = False

                # Parse filename
                parsed = parse_cr_stem(f.stem)
                if parsed is None:
                    tick(f"{org} · {f.stem} → ?? (unparseable)")
                    unknowns.append((str(f.relative_to(PROJECT_ROOT)), f.stem))
                    continue

                year_str, raw_entity, suffix, is_profile, region_prefix = parsed

                pub_year = extract_pub_year(year_str)
                if year_filter and pub_year not in year_filter:
                    tick(f"{org} {year_str} · {raw_entity} → (skipped)")
                    continue

                # Source files: record in manifest but don't copy
                if is_source:
                    tick(f"{org} {year_str} · {raw_entity} → (source)")
                    records.append({
                        "input_path": str(f.relative_to(PROJECT_ROOT)),
                        "output_path": None,
                        "org": org,
                        "type": _cr_type(org, is_profile),
                        "year": year_str,
                        "country_raw": raw_entity,
                        "country_standardised": None,
                        "country_folder": None,
                        "is_source": True,
                        "suffix": suffix,
                        "region_prefix": region_prefix,
                        "status": "source",
                    })
                    continue

                if country_filter and raw_entity not in country_filter:
                    tick(f"{org} {year_str} · {raw_entity} → (skipped)")
                    continue

                entity = resolve_entity(raw_entity, v2s, known)
                if entity is None:
                    tick(f"{org} {year_str} · {raw_entity} → ?? (unknown)")
                    unknowns.append((str(f.relative_to(PROJECT_ROOT)), raw_entity))
                    records.append({
                        "input_path": str(f.relative_to(PROJECT_ROOT)),
                        "output_path": None,
                        "org": org,
                        "type": _cr_type(org, is_profile),
                        "year": year_str,
                        "country_raw": raw_entity,
                        "country_standardised": None,
                        "country_folder": None,
                        "is_source": False,
                        "suffix": suffix,
                        "region_prefix": region_prefix,
                        "status": "skipped_unknown",
                    })
                    continue

                file_type = _cr_type(org, is_profile)
                folder = get_folder(entity, e2f, general)
                tick(f"{org} {year_str} · {raw_entity} → {entity}")
                new_filename = build_cr_filename(year_str, entity, suffix, is_profile, ext)

                if folder == "_general":
                    dest = STANDARDISED_DIR / "cr" / org / "_general" / new_filename
                else:
                    dest = STANDARDISED_DIR / "cr" / org / folder / new_filename

                if not args.dry_run:
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(f, dest)

                records.append({
                    "input_path": str(f.relative_to(PROJECT_ROOT)),
                    "output_path": str(dest.relative_to(PROJECT_ROOT)),
                    "org": org,
                    "type": file_type,
                    "year": year_str,
                    "country_raw": raw_entity,
                    "country_standardised": entity,
                    "country_folder": folder,
                    "is_source": False,
                    "suffix": suffix,
                    "region_prefix": region_prefix,
                    "status": "ok",
                })

    return records, unknowns


def _cr_type(org: str, is_profile: bool) -> str:
    if is_profile:
        return "CP"
    if org == "us":
        return "SR"
    return "CR"


def _sorted_walk(top: Path):
    for dirpath, dirnames, filenames in os.walk(top):
        dirnames.sort()
        yield Path(dirpath), dirnames, filenames


def _count_files() -> int:
    """Plain count of every (non-hidden) file under the WR + CR trees, for the
    progress bar total. The bar advances once per file (skipped ones included),
    so a simple count is enough. Under --org/--year scoping the bar may not reach
    100% (whole dirs are skipped without ticking), which is harmless."""
    n = 0
    for root in (SPLIT_WR_DIR, CR_DIR):
        if root.exists():
            n += sum(
                1 for f in root.rglob("*")
                if f.is_file() and not f.name.startswith(".")
            )
    return n


# ── Main ─────────────────────────────────────────────────────────────


def main():
    args = parse_args()
    v2s, e2f, general, known = load_maps()

    progress, spinner = make_progress()
    task = progress.add_task("Standardising", total=_count_files())

    with Live(make_layout(spinner, progress), refresh_per_second=10) as live:
        def tick(label: str) -> None:
            spinner.update(text=Text(label, style="gray"))
            live.update(make_layout(spinner, progress))
            progress.advance(task)

        wr_records, wr_unknowns = process_wr(args, v2s, e2f, general, known, tick)
        cr_records, cr_unknowns = process_cr(args, v2s, e2f, general, known, tick)

    all_records = wr_records + cr_records
    all_unknowns = wr_unknowns + cr_unknowns

    # Write unknown_countries.txt
    unknowns_path = PROJECT_ROOT / "unknown_countries.txt"
    if all_unknowns:
        with open(unknowns_path, "w") as f:
            for path, name in sorted(set(all_unknowns)):
                f.write(f"{name}\t{path}\n")
    elif unknowns_path.exists():
        unknowns_path.unlink()

    # Summary counts
    ok = sum(1 for r in all_records if r["status"] == "ok")
    source = sum(1 for r in all_records if r["status"] == "source")
    unknown = sum(1 for r in all_records if r["status"] == "skipped_unknown")

    # Write manifest
    scope = {}
    if args.org:
        scope["org"] = args.org
    if args.year:
        scope["year"] = args.year
    if args.country:
        scope["country"] = args.country

    manifest = {
        "step": 5,
        "name": "standardised",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "scoped_to": scope,
        "files": all_records,
        "summary": {
            "total": len(all_records),
            "ok": ok,
            "source": source,
            "skipped_unknown": unknown,
        },
    }

    if not args.dry_run:
        MANIFESTS_DIR.mkdir(parents=True, exist_ok=True)
        manifest_path = MANIFESTS_DIR / "5_standardised.json"

        # Merge with existing manifest if re-running with narrow scope
        if manifest_path.exists():
            existing = json.loads(manifest_path.read_text())
            existing_files = {r["input_path"]: r for r in existing.get("files", [])}
            for r in all_records:
                existing_files[r["input_path"]] = r
            manifest["files"] = list(existing_files.values())
            manifest["summary"] = {
                "total": len(manifest["files"]),
                "ok": sum(1 for r in manifest["files"] if r["status"] == "ok"),
                "source": sum(1 for r in manifest["files"] if r["status"] == "source"),
                "skipped_unknown": sum(
                    1 for r in manifest["files"] if r["status"] == "skipped_unknown"
                ),
            }

        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
        print(f"Manifest written to {manifest_path.relative_to(PROJECT_ROOT)}")

    # Print summary
    print(f"\nStandardised: {ok} ok, {source} sources, {unknown} unknown")
    if all_unknowns:
        unique_names = sorted(set(name for _, name in all_unknowns))
        print(f"\n{len(all_unknowns)} unknown entities ({len(unique_names)} unique):")
        for name in unique_names:
            count = sum(1 for _, n in all_unknowns if n == name)
            print(f"  {name} ({count})")
        if not args.dry_run:
            print(f"\nFull list written to unknown_countries.txt")

    if args.dry_run:
        print("\n[dry-run] No files copied, no manifest written.")


if __name__ == "__main__":
    main()

"""
Step 8: Assign IDs and organise into final output structure.

Reads intermediate/filtered/ and intermediate/markdown/, assigns each file
a unique ID, and copies into samples_readable/, samples_llm/, and sources/.

Usage:
    python pipeline/organise.py
    python pipeline/organise.py --org hrw --year 2010
    python pipeline/organise.py --country Afghanistan
"""

import argparse
import json
import re
import shutil
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
FILTERED_DIR = PROJECT_ROOT / "intermediate" / "filtered"
MARKDOWN_DIR = PROJECT_ROOT / "intermediate" / "markdown"
READABLE_DIR = PROJECT_ROOT / "output" / "samples_readable"
LLM_DIR = PROJECT_ROOT / "output" / "samples_llm"
SOURCES_DIR = PROJECT_ROOT / "output" / "sources"
MANIFESTS_DIR = PROJECT_ROOT / "manifests"
MANIFEST_4 = MANIFESTS_DIR / "4_split_wr.json"
MANIFEST_5 = MANIFESTS_DIR / "5_standardised.json"
MANIFEST_6 = MANIFESTS_DIR / "6_filtered.json"
MANIFEST_7 = MANIFESTS_DIR / "7_converted.json"
MANIFEST_7B = MANIFESTS_DIR / "7b_split.json"

YEAR_RE = re.compile(r"(\d{4})(?:\((\d{4})\))?")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Step 8: Assign IDs and organise")
    p.add_argument("--org", action="append", help="Only process this org (repeatable)")
    p.add_argument(
        "--type", action="append", help="Only process this type (repeatable)"
    )
    p.add_argument(
        "--year", action="append", help="Only process this year (repeatable)"
    )
    p.add_argument(
        "--country",
        action="append",
        help="Only process this country_folder (repeatable)",
    )
    p.add_argument(
        "--dry-run", action="store_true", help="Preview without copying files"
    )
    p.add_argument("--force", action="store_true", help="Re-copy even if output exists")
    return p.parse_args()


# ── Manifest loading ─────────────────────────────────────────────────


def load_manifest(path: Path) -> dict:
    if not path.exists():
        print(f"Error: {path.relative_to(PROJECT_ROOT)} not found.")
        raise SystemExit(1)
    return json.loads(path.read_text())


def build_step5_lookup(m5: dict) -> dict[str, dict]:
    """Map step 5 output_path -> record for quick lookup."""
    return {r["output_path"]: r for r in m5["files"] if r.get("output_path")}


def build_step4_lookup(m4: dict) -> tuple[dict[str, dict], set[str]]:
    """Build two things from step 4:
    - pre_split set: output_paths of pre-split files
    - source_pdfs: unique input_paths (the WR source PDFs)
    """
    pre_split = set()
    source_pdfs = set()
    for r in m4["files"]:
        if r.get("pre_split"):
            pre_split.add(r["output_path"])
        source_pdfs.add(r["input_path"])
    return pre_split, source_pdfs


def build_step7_lookup(m7: dict) -> dict[str, dict]:
    """Map step 7 input_path -> record (input_path is the filtered path)."""
    return {r["input_path"]: r for r in m7["files"]}


def build_step7b_lookup(m7b: dict) -> dict[str, dict]:
    """Map step 7b input_path -> record (the filtered path), if 7b was run."""
    return {r["input_path"]: r for r in m7b.get("files", []) if r.get("input_path")}


def get_llm_parts(
    entry: dict, s7b_lookup: dict, s7_lookup: dict
) -> list[dict]:
    """Return the LLM markdown parts for a filtered entry.

    Prefers the step-7b record (one entry per part); falls back to the single
    step-7 markdown output if 7b wasn't run. Each part is {path, part_index,
    part_total}. Empty if the file has no LLM output (e.g. unconvertible PDF).
    """
    s7b = s7b_lookup.get(entry["output_path"])
    if s7b is not None:
        parts = [p for p in (s7b.get("parts") or []) if p.get("path")]
        if parts:
            return parts
    s7 = s7_lookup.get(entry["output_path"], {})
    llm_output = s7.get("output_path")
    if llm_output:
        return [{"path": llm_output, "part_index": 1, "part_total": 1}]
    return []


# ── ID generation ────────────────────────────────────────────────────


def assign_suffixes(entries: list[dict], s5_lookup: dict) -> dict[str, str | None]:
    """For a list of entries sharing the same ID key, assign suffixes.

    Returns {filtered_output_path: suffix_or_None}.
    """
    if len(entries) == 1:
        # Single file — use existing suffix from step 5 if any
        e = entries[0]
        s5 = s5_lookup.get(e.get("input_path", ""), {})
        return {e["output_path"]: s5.get("suffix")}

    # Multiple files — sort by original filename for deterministic assignment
    sorted_entries = sorted(entries, key=lambda e: Path(e["output_path"]).name)
    result = {}
    for i, e in enumerate(sorted_entries):
        suffix = chr(ord("a") + i)
        result[e["output_path"]] = suffix
    return result


def build_id(
    org: str, file_type: str, year: str, entity: str, suffix: str | None
) -> str:
    """Build the file ID: {ORG}-{TYPE}-{YEAR}-{ENTITY}[-{SUFFIX}]."""
    parts = [org.upper(), file_type, year, entity]
    file_id = "-".join(parts)
    if suffix:
        file_id += f"-{suffix}"
    return file_id


def make_record(
    file_id, org, file_type, year, entity, folder, suffix,
    readable_dest, readable_ext, llm_dest, llm_format,
    source_id, source_type, original_filename,
) -> dict:
    return {
        "id": file_id,
        "org": org,
        "type": file_type,
        "year": year,
        "entity": entity,
        "country_folder": folder,
        "suffix": suffix,
        "readable_path": str(readable_dest.relative_to(PROJECT_ROOT)),
        "readable_format": readable_ext.lstrip("."),
        "llm_path": str(llm_dest.relative_to(PROJECT_ROOT)) if llm_dest else None,
        "llm_format": llm_format,
        "source_id": source_id,
        "source_type": source_type,
        "original_filename": original_filename,
        "status": "ok",
    }


# ── Main ─────────────────────────────────────────────────────────────


def main():
    args = parse_args()

    m4 = load_manifest(MANIFEST_4)
    m5 = load_manifest(MANIFEST_5)
    m6 = load_manifest(MANIFEST_6)
    m7 = load_manifest(MANIFEST_7)
    m7b = json.loads(MANIFEST_7B.read_text()) if MANIFEST_7B.exists() else {"files": []}

    s5_lookup = build_step5_lookup(m5)
    pre_split_outputs, wr_source_pdfs = build_step4_lookup(m4)
    s7_lookup = build_step7_lookup(m7)
    s7b_lookup = build_step7b_lookup(m7b)

    org_filter = set(args.org) if args.org else None
    type_filter = set(t.upper() for t in args.type) if args.type else None
    year_filter = set(args.year) if args.year else None
    country_filter = set(args.country) if args.country else None

    # Collect filtered entries, applying scoping
    filtered_entries = []
    for entry in m6["files"]:
        if entry["status"] != "ok":
            continue
        if org_filter and entry["org"] not in org_filter:
            continue
        if type_filter and entry["type"] not in type_filter:
            continue
        pub_year = YEAR_RE.search(entry["year"])
        if year_filter and pub_year and pub_year.group(1) not in year_filter:
            continue
        if country_filter and entry["country_folder"] not in country_filter:
            continue
        filtered_entries.append(entry)

    # Group by ID key to detect collisions and assign suffixes
    id_groups: dict[tuple, list[dict]] = defaultdict(list)
    for entry in filtered_entries:
        # Get entity from step 5
        s5 = s5_lookup.get(entry["input_path"], {})
        entity = s5.get("country_standardised", entry.get("country_standardised", ""))
        key = (entry["org"], entry["type"], entry["year"], entity)
        id_groups[key].append(entry)

    # Assign suffixes across all groups
    suffix_map: dict[str, str | None] = {}  # filtered_output_path -> suffix
    for key, entries in id_groups.items():
        suffixes = assign_suffixes(entries, s5_lookup)
        suffix_map.update(suffixes)

    # Process each file
    records = []
    for entry in filtered_entries:
        s5 = s5_lookup.get(entry["input_path"], {})
        entity = s5.get("country_standardised", "")
        folder = entry["country_folder"]
        org = entry["org"]
        file_type = entry["type"]
        year = entry["year"]
        suffix = suffix_map.get(entry["output_path"])

        file_id = build_id(org, file_type, year, entity, suffix)

        # Readable: from intermediate/filtered/ — never split, one whole file.
        src_readable = PROJECT_ROOT / entry["output_path"]
        readable_ext = src_readable.suffix
        readable_dest = (
            READABLE_DIR / org / file_type.lower() / folder / f"{file_id}{readable_ext}"
        )
        if not args.dry_run and (args.force or not readable_dest.exists()):
            readable_dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_readable, readable_dest)

        # Determine source_type and source_id (shared across all parts)
        if file_type == "WR":
            # Check step 5 input_path to see if it came from split_wr
            s5_input = s5.get("input_path", "")
            if s5_input in pre_split_outputs:
                source_type = "downloaded_pre_split"
                source_id = None
            else:
                source_type = "split_from_world_report"
                source_id = f"{org.upper()}-SRC-WR-{year}"
        else:
            source_type = "standalone"
            source_id = None

        # Original filename from filtered path
        original_filename = Path(entry["output_path"]).name

        # LLM: one or more parts (from step 7b; falls back to step 7).
        parts = get_llm_parts(entry, s7b_lookup, s7_lookup)
        part_total = len(parts)

        if not parts:
            # No LLM output (e.g. unconvertible PDF copied as readable only).
            records.append(make_record(
                file_id, org, file_type, year, entity, folder, suffix,
                readable_dest, readable_ext, None, None,
                source_id, source_type, original_filename,
            ))
            continue

        for part in parts:
            n = part.get("part_index", 1)
            is_split = part_total > 1
            part_id = f"{file_id}-p{n}" if is_split else file_id

            src_llm = PROJECT_ROOT / part["path"]
            llm_ext = src_llm.suffix
            llm_format = llm_ext.lstrip(".")
            llm_dest = (
                LLM_DIR / org / file_type.lower() / folder / f"{part_id}{llm_ext}"
            )
            if not args.dry_run and (args.force or not llm_dest.exists()):
                llm_dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src_llm, llm_dest)

            rec = make_record(
                part_id, org, file_type, year, entity, folder, suffix,
                readable_dest, readable_ext, llm_dest, llm_format,
                source_id, source_type, original_filename,
            )
            if is_split:
                rec["part_of"] = file_id
                rec["part_index"] = n
                rec["part_total"] = part_total
            records.append(rec)

    # ── Copy source files ────────────────────────────────────────────

    source_records = []

    # CR/regional sources from step 5 (is_source = true)
    for entry in m5["files"]:
        if not entry.get("is_source"):
            continue
        org = entry["org"]
        if org_filter and org not in org_filter:
            continue
        src = PROJECT_ROOT / entry["input_path"]
        dest = SOURCES_DIR / org / "country_reports" / src.name
        if not args.dry_run:
            if args.force or not dest.exists():
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dest)
        source_records.append(
            {
                "source_id": None,
                "org": org,
                "year": entry["year"],
                "path": str(dest.relative_to(PROJECT_ROOT)),
                "original_path": entry["input_path"],
                "type": "country_report_parent",
            }
        )

    # WR source PDFs from step 4
    for src_path_str in sorted(wr_source_pdfs):
        src = PROJECT_ROOT / src_path_str
        if not src.exists():
            continue
        # Extract org from path: input/wr/{org}/...
        parts = Path(src_path_str).parts
        org = parts[2]  # input/wr/{org}/filename
        if org_filter and org not in org_filter:
            continue

        year_match = YEAR_RE.search(src.name)
        year = year_match.group(0) if year_match else ""
        source_id = f"{org.upper()}-SRC-WR-{year}"

        dest = SOURCES_DIR / org / "world_reports" / src.name
        if not args.dry_run:
            if args.force or not dest.exists():
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dest)
        source_records.append(
            {
                "source_id": source_id,
                "org": org,
                "year": year,
                "path": str(dest.relative_to(PROJECT_ROOT)),
                "original_path": src_path_str,
                "type": "world_report",
            }
        )

    # ── Write manifest ───────────────────────────────────────────────

    scope = {}
    if args.org:
        scope["org"] = args.org
    if args.type:
        scope["type"] = args.type
    if args.year:
        scope["year"] = args.year
    if args.country:
        scope["country"] = args.country

    manifest = {
        "step": 8,
        "name": "organised",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "scoped_to": scope,
        "files": records,
        "sources": source_records,
        "summary": {
            "total_files": len(records),
            "total_sources": len(source_records),
        },
    }

    if not args.dry_run:
        MANIFESTS_DIR.mkdir(parents=True, exist_ok=True)
        manifest_path = MANIFESTS_DIR / "8_organised.json"

        if manifest_path.exists():
            existing = json.loads(manifest_path.read_text())
            existing_files = {r["id"]: r for r in existing.get("files", [])}
            for r in records:
                existing_files[r["id"]] = r
            manifest["files"] = list(existing_files.values())

            existing_sources = {r["path"]: r for r in existing.get("sources", [])}
            for r in source_records:
                existing_sources[r["path"]] = r
            manifest["sources"] = list(existing_sources.values())

            manifest["summary"] = {
                "total_files": len(manifest["files"]),
                "total_sources": len(manifest["sources"]),
            }

        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
        print(f"Manifest written to {manifest_path.relative_to(PROJECT_ROOT)}")

    print(f"\nOrganised: {len(records)} files, {len(source_records)} sources")

    if args.dry_run:
        print("\n[dry-run] No files copied, no manifest written.")


if __name__ == "__main__":
    main()

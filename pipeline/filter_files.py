"""
Step 6: Filter by country and year.

Copies files from intermediate/standardised/ to intermediate/filtered/
only if the country_folder appears in conflict_years_first_relevant.csv
and the report year >= that country's First_Relevant_Year.

_general/ files are always included.

Usage:
    python pipeline/filter_files.py
    python pipeline/filter_files.py --org hrw --year 2010
    python pipeline/filter_files.py --country Afghanistan
"""

import argparse
import csv
import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

from config import make_layout, make_progress
from rich.live import Live
from rich.text import Text

PROJECT_ROOT = Path(__file__).resolve().parent.parent
STANDARDISED_DIR = PROJECT_ROOT / "intermediate" / "standardised"
FILTERED_DIR = PROJECT_ROOT / "intermediate" / "filtered"
MANIFESTS_DIR = PROJECT_ROOT / "manifests"
MANIFEST_5 = MANIFESTS_DIR / "5_standardised.json"
CSV_PATH = PROJECT_ROOT / "config" / "conflict_years_first_relevant.csv"

YEAR_RE = re.compile(r"(\d{4})(?:\((\d{4})\))?")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Step 6: Filter by country and year")
    p.add_argument("--org", action="append", help="Only process this org (repeatable)")
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


def load_csv_filter() -> dict[str, int]:
    """Load CSV into {folder_name: first_relevant_year}."""
    result = {}
    with open(CSV_PATH) as f:
        for row in csv.DictReader(f):
            result[row["Country"].strip()] = int(row["First_Relevant_Year"])
    return result


def extract_report_year(year_str: str) -> int | None:
    """Extract the report/coverage year (parenthetical if present, else publication)."""
    m = YEAR_RE.search(year_str)
    if not m:
        return None
    # coverage year (group 2) if present, else publication year (group 1)
    return int(m.group(2) or m.group(1))


def main():
    args = parse_args()
    country_years = load_csv_filter()

    # Load step 5 manifest
    if not MANIFEST_5.exists():
        print(
            f"Error: {MANIFEST_5.relative_to(PROJECT_ROOT)} not found. Run step 5 first."
        )
        return

    manifest_5 = json.loads(MANIFEST_5.read_text())

    org_filter = set(args.org) if args.org else None
    year_filter = set(args.year) if args.year else None
    country_filter = set(args.country) if args.country else None

    records = []
    discarded = []

    progress, spinner = make_progress()
    task = progress.add_task("Filtering", total=len(manifest_5["files"]))

    with Live(make_layout(spinner, progress), refresh_per_second=10) as live:

        def tick(label: str) -> None:
            spinner.update(text=Text(label, style="gray"))
            live.update(make_layout(spinner, progress))
            progress.advance(task)

        for entry in manifest_5["files"]:
            if entry["status"] != "ok":
                tick(f"{entry['org']} {entry['year']} · skipped ({entry['status']})")
                continue

            org = entry["org"]
            year_str = entry["year"]
            folder = entry["country_folder"]

            # Apply scoping filters
            if org_filter and org not in org_filter:
                tick(f"{org} {year_str} · {folder} → (skipped)")
                continue
            pub_year = YEAR_RE.search(year_str)
            if year_filter and pub_year and pub_year.group(1) not in year_filter:
                tick(f"{org} {year_str} · {folder} → (skipped)")
                continue
            if country_filter and folder not in country_filter:
                tick(f"{org} {year_str} · {folder} → (skipped)")
                continue

            src = PROJECT_ROOT / entry["output_path"]
            # Mirror the path structure: standardised/... → filtered/...
            rel = src.relative_to(STANDARDISED_DIR)
            dest = FILTERED_DIR / rel

            report_year = extract_report_year(year_str)

            # Decide include/discard
            if folder == "_general":
                reason = "general_always_included"
                include = True
            elif folder not in country_years:
                reason = "country_not_in_csv"
                include = False
            elif report_year is None:
                reason = "unparseable_year"
                include = False
            elif report_year < country_years[folder]:
                reason = f"year_{report_year}_before_{country_years[folder]}"
                include = False
            else:
                reason = "ok"
                include = True

            status = "ok" if include else "discarded"

            if include:
                tick(f"{org} {year_str} · {folder} → included")
                if not args.dry_run:
                    if args.force or not dest.exists():
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(src, dest)
            else:
                tick(f"{org} {year_str} · {folder} → discarded ({reason})")
                discarded.append(f"{entry['output_path']}\t{reason}")

            records.append(
                {
                    "input_path": entry["output_path"],
                    "output_path": str(dest.relative_to(PROJECT_ROOT))
                    if include
                    else None,
                    "org": org,
                    "type": entry["type"],
                    "year": year_str,
                    "country_standardised": entry["country_standardised"],
                    "country_folder": folder,
                    "status": status,
                    "reason": reason,
                }
            )

    # Write discarded.txt
    discarded_path = PROJECT_ROOT / "discarded.txt"
    if not args.dry_run:
        if discarded:
            discarded_path.write_text("\n".join(sorted(discarded)) + "\n")
        elif discarded_path.exists():
            discarded_path.unlink()

    # Build manifest
    ok = sum(1 for r in records if r["status"] == "ok")
    disc = sum(1 for r in records if r["status"] == "discarded")

    scope = {}
    if args.org:
        scope["org"] = args.org
    if args.year:
        scope["year"] = args.year
    if args.country:
        scope["country"] = args.country

    manifest = {
        "step": 6,
        "name": "filtered",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "scoped_to": scope,
        "files": records,
        "summary": {
            "total": len(records),
            "ok": ok,
            "discarded": disc,
        },
    }

    if not args.dry_run:
        MANIFESTS_DIR.mkdir(parents=True, exist_ok=True)
        manifest_path = MANIFESTS_DIR / "6_filtered.json"

        # Merge with existing manifest if re-running with narrow scope
        if manifest_path.exists():
            existing = json.loads(manifest_path.read_text())
            existing_files = {r["input_path"]: r for r in existing.get("files", [])}
            for r in records:
                existing_files[r["input_path"]] = r
            manifest["files"] = list(existing_files.values())
            manifest["summary"] = {
                "total": len(manifest["files"]),
                "ok": sum(1 for r in manifest["files"] if r["status"] == "ok"),
                "discarded": sum(
                    1 for r in manifest["files"] if r["status"] == "discarded"
                ),
            }

        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
        print(f"Manifest written to {manifest_path.relative_to(PROJECT_ROOT)}")

    # Print summary
    print(f"\nFiltered: {ok} included, {disc} discarded")
    if discarded:
        # Summarise discard reasons
        reasons: dict[str, int] = {}
        for r in records:
            if r["status"] == "discarded":
                key = (
                    "year_too_early" if r["reason"].startswith("year_") else r["reason"]
                )
                reasons[key] = reasons.get(key, 0) + 1
        for reason, count in sorted(reasons.items()):
            print(f"  {reason}: {count}")
        if not args.dry_run:
            print("\nFull list written to discarded.txt")

    if args.dry_run:
        print("\n[dry-run] No files copied, no manifest written.")


if __name__ == "__main__":
    main()

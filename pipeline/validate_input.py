"""
Step 0: Validate input tree and produce an inventory manifest.

Walks input/wr/ and input/cr/, catalogues every file, cross-checks
WR source PDFs against existing configs, and reports anomalies.

Usage:
    python pipeline/validate_input.py [--org ai|hrw|idmc|us] [--dry-run]
"""

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
INPUT_DIR = PROJECT_ROOT / "input"
DATA_DIR = PROJECT_ROOT / "config"
MANIFESTS_DIR = PROJECT_ROOT / "manifests"

VALID_WR_ORGS = {"ai", "hrw", "idmc"}
VALID_CR_ORGS = {"ai", "hrw", "idmc", "us"}
VALID_EXTENSIONS = {".pdf", ".md", ".html"}

YEAR_RE = re.compile(r"(\d{4})(?:\((\d{4})\))?")


def _skip(path: Path) -> bool:
    """Skip dotfiles (.DS_Store, etc.)."""
    return path.name.startswith(".")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Step 0: Validate input tree")
    parser.add_argument(
        "--org",
        choices=sorted(VALID_WR_ORGS | VALID_CR_ORGS),
        action="append",
        help="Only validate this org (repeatable)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print results without writing the manifest",
    )
    return parser.parse_args()


def parse_year(filename: str) -> str | None:
    """Extract year (with optional coverage year) from a filename."""
    m = YEAR_RE.search(filename)
    if not m:
        return None
    pub_year = m.group(1)
    cov_year = m.group(2)
    return f"{pub_year}({cov_year})" if cov_year else pub_year


# ── Inventory ────────────────────────────────────────────────────────


def inventory_wr(org_filter: set[str] | None) -> tuple[list[dict], list[str]]:
    """Inventory all files under input/wr/."""
    files = []
    warnings = []
    wr_dir = INPUT_DIR / "wr"

    if not wr_dir.exists():
        warnings.append("input/wr/ directory does not exist")
        return files, warnings

    for entry in sorted(wr_dir.iterdir()):
        if _skip(entry):
            continue
        if not entry.is_dir():
            warnings.append(f"Unexpected file at top level of input/wr/: {entry.name}")
            continue

        org = entry.name.lower()
        if org not in VALID_WR_ORGS:
            warnings.append(f"Unexpected org directory in input/wr/: {entry.name}")
            continue

        if org_filter and org not in org_filter:
            continue

        org_files = list(entry.iterdir())
        if not org_files:
            warnings.append(f"input/wr/{org}/ is empty")
            continue

        for f in sorted(org_files):
            if _skip(f):
                continue
            if f.is_dir():
                # Some orgs may have pre-split year folders — inventory their contents
                for sub in sorted(f.iterdir()):
                    if _skip(sub):
                        continue
                    if sub.is_file():
                        record, w = _make_wr_record(sub, org)
                        files.append(record)
                        warnings.extend(w)
                    else:
                        warnings.append(
                            f"Unexpected nested directory: {sub.relative_to(PROJECT_ROOT)}"
                        )
                continue

            if not f.is_file():
                continue

            record, w = _make_wr_record(f, org)
            files.append(record)
            warnings.extend(w)

    return files, warnings


def _make_wr_record(f: Path, org: str) -> tuple[dict, list[str]]:
    warnings = []
    rel = f.relative_to(PROJECT_ROOT)
    ext = f.suffix.lower()
    year = parse_year(f.name)

    if ext not in VALID_EXTENSIONS:
        warnings.append(f"Unexpected extension {ext}: {rel}")
    if year is None:
        warnings.append(f"Cannot parse year from WR file: {rel}")

    return {
        "path": str(rel),
        "org": org,
        "category": "wr",
        "filename": f.name,
        "extension": ext,
        "year": year,
        "country": None,
        "status": "ok" if (ext in VALID_EXTENSIONS and year) else "warning",
    }, warnings


def inventory_cr(
    org_filter: set[str] | None,
) -> tuple[list[dict], list[str], list[str]]:
    """Inventory all files under input/cr/.

    Returns (files, warnings, info_notes).
    """
    files = []
    warnings = []
    info: list[str] = []
    cr_dir = INPUT_DIR / "cr"

    if not cr_dir.exists():
        warnings.append("input/cr/ directory does not exist")
        return files, warnings, info

    for entry in sorted(cr_dir.iterdir()):
        if _skip(entry):
            continue
        if not entry.is_dir():
            warnings.append(f"Unexpected file at top level of input/cr/: {entry.name}")
            continue

        org = entry.name.lower()
        if org not in VALID_CR_ORGS:
            warnings.append(f"Unexpected org directory in input/cr/: {entry.name}")
            continue

        if org_filter and org not in org_filter:
            continue

        org_dir = entry
        if not any(org_dir.iterdir()):
            warnings.append(f"input/cr/{org}/ is empty")
            continue

        # Recursive walk under the org directory.
        # Apply split_files/ convention: if a directory contains a split_files/
        # subfolder, files at that level are sources; files inside split_files/
        # are classification candidates.
        for dirpath, dirnames, filenames in sorted_walk(org_dir):
            # Skip dotfile directories
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]

            is_split_files_dir = dirpath.name == "split_files"
            has_split_files_child = "split_files" in dirnames

            if has_split_files_child:
                info.append(
                    f"split_files/ convention in CR: {dirpath.relative_to(PROJECT_ROOT)}"
                )

            for fname in sorted(filenames):
                if fname.startswith("."):
                    continue
                f = dirpath / fname

                # Determine is_source via split_files/ convention
                if is_split_files_dir:
                    is_source = False  # inside split_files/ = candidate
                elif has_split_files_child:
                    is_source = True  # next to split_files/ = source
                else:
                    is_source = False  # no split_files/ = candidate

                record, w = _make_cr_record(f, org, is_source)
                files.append(record)
                warnings.extend(w)

    return files, warnings, info


def sorted_walk(top: Path):
    """os.walk equivalent that yields (Path, dirnames, filenames) sorted."""
    import os

    for dirpath, dirnames, filenames in os.walk(top):
        dirnames.sort()
        yield Path(dirpath), dirnames, filenames


def _make_cr_record(f: Path, org: str, is_source: bool) -> tuple[dict, list[str]]:
    warnings = []
    rel = f.relative_to(PROJECT_ROOT)
    ext = f.suffix.lower()
    year = parse_year(f.name)

    if ext not in VALID_EXTENSIONS:
        warnings.append(f"Unexpected extension {ext}: {rel}")
    if year is None:
        warnings.append(f"Cannot parse year from CR file: {rel}")

    return {
        "path": str(rel),
        "org": org,
        "category": "cr",
        "filename": f.name,
        "extension": ext,
        "year": year,
        "country": None,  # derived from filename in step 5, not folder path
        "is_source": is_source,
        "status": "ok" if (ext in VALID_EXTENSIONS and year) else "warning",
    }, warnings


# ── Config cross-checks ─────────────────────────────────────────────


def collect_config_filenames(org: str) -> set[str]:
    """Gather all WR PDF filenames referenced in any config for this org."""
    filenames: set[str] = set()
    org_data = DATA_DIR / org

    if not org_data.exists():
        return filenames

    # Check every JSON config file for keys that look like PDF filenames
    # and for source_path values
    for json_file in sorted(org_data.glob("*.json")):
        try:
            data = json.loads(json_file.read_text())
        except (json.JSONDecodeError, OSError):
            continue

        if isinstance(data, dict):
            for key in data:
                if key.endswith((".pdf", ".PDF")):
                    filenames.add(key)
                # Also check source_path inside values
                val = data[key]
                if isinstance(val, dict) and "source_path" in val:
                    # source_path like "input/wr/hrw/foo.pdf" — extract filename.
                    # pre_split entries point at a directory, not a PDF; skip them
                    # (their per-country files are inventoried directly).
                    sp = Path(val["source_path"])
                    if sp.suffix.lower() == ".pdf":
                        filenames.add(sp.name)

    return filenames


def check_configs_against_input(
    org_filter: set[str] | None, wr_files_by_org: dict[str, set[str]]
) -> list[str]:
    """Check that every PDF referenced in configs exists in input/wr/{org}/."""
    warnings = []

    for org in sorted(VALID_WR_ORGS):
        if org_filter and org not in org_filter:
            continue

        config_filenames = collect_config_filenames(org)
        if not config_filenames:
            continue

        existing = wr_files_by_org.get(org, set())

        for filename in sorted(config_filenames):
            if filename not in existing:
                warnings.append(
                    f"Config references {filename} for {org} but not found in input/wr/{org}/"
                )

    return warnings


# ── Main ─────────────────────────────────────────────────────────────


def main():
    args = parse_args()
    org_filter = set(args.org) if args.org else None

    # Inventory
    wr_files, wr_warnings = inventory_wr(org_filter)
    cr_files, cr_warnings, cr_info = inventory_cr(org_filter)

    all_files = wr_files + cr_files
    warnings = wr_warnings + cr_warnings
    info = cr_info

    # Build lookup of WR filenames by org for config cross-check
    wr_files_by_org: dict[str, set[str]] = {}
    for f in wr_files:
        wr_files_by_org.setdefault(f["org"], set()).add(f["filename"])

    config_warnings = check_configs_against_input(org_filter, wr_files_by_org)
    warnings.extend(config_warnings)

    # Summary
    ok_count = sum(1 for f in all_files if f["status"] == "ok")
    warn_count = sum(1 for f in all_files if f["status"] == "warning")

    by_org: dict[str, int] = {}
    by_category: dict[str, int] = {}
    for f in all_files:
        by_org[f["org"]] = by_org.get(f["org"], 0) + 1
        by_category[f["category"]] = by_category.get(f["category"], 0) + 1

    source_count = sum(1 for f in all_files if f.get("is_source"))
    candidate_count = len(all_files) - source_count

    manifest = {
        "step": 0,
        "name": "input_inventory",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "scoped_to": {"org": list(org_filter)} if org_filter else {},
        "files": all_files,
        "info": info,
        "warnings": warnings,
        "summary": {
            "total": len(all_files),
            "ok": ok_count,
            "warning": warn_count,
            "cr_candidates": candidate_count - len(wr_files),
            "cr_sources": source_count,
            "by_org": dict(sorted(by_org.items())),
            "by_category": dict(sorted(by_category.items())),
        },
    }

    # Output
    print(f"\nInventory: {len(all_files)} files ({ok_count} ok, {warn_count} warnings)")
    print(f"  CR: {candidate_count - len(wr_files)} candidates, {source_count} sources")
    for org, count in sorted(by_org.items()):
        print(f"  {org}: {count}")
    for cat, count in sorted(by_category.items()):
        print(f"  {cat}: {count}")

    if info:
        print(f"\n{len(info)} info:")
        for i in info:
            print(f"  {i}")

    if warnings:
        print(f"\n{len(warnings)} warnings:")
        for w in warnings:
            print(f"  ⚠ {w}")
    else:
        print("\nNo warnings.")

    if args.dry_run:
        print("\n[dry-run] Manifest not written.")
        print(json.dumps(manifest, indent=2))
    else:
        MANIFESTS_DIR.mkdir(parents=True, exist_ok=True)
        out_path = MANIFESTS_DIR / "0_input_inventory.json"
        out_path.write_text(json.dumps(manifest, indent=2) + "\n")
        print(f"\nManifest written to {out_path.relative_to(PROJECT_ROOT)}")


if __name__ == "__main__":
    main()

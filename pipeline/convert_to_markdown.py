"""
Step 7: Convert files to markdown.

Reads intermediate/filtered/ (via the step 6 manifest) and writes to
intermediate/markdown/ with the same directory structure.

- PDFs: converted via pymupdf4llm (parallel)
- HTML: converted via markdownify
- MD: copied as-is
- AI PDFs before min_markdown_year (2013): copied as PDF (unconvertible scans)

Usage:
    python pipeline/convert_to_markdown.py
    python pipeline/convert_to_markdown.py --org ai --year 2015
    python pipeline/convert_to_markdown.py --country Afghanistan
"""

import argparse
import json
import re
import shutil
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import markdownify
import pymupdf4llm
from rich.live import Live
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    TextColumn,
    TimeElapsedColumn,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
FILTERED_DIR = PROJECT_ROOT / "intermediate" / "filtered"
MARKDOWN_DIR = PROJECT_ROOT / "intermediate" / "markdown"
MANIFESTS_DIR = PROJECT_ROOT / "manifests"
MANIFEST_6 = MANIFESTS_DIR / "6_filtered.json"

YEAR_RE = re.compile(r"(\d{4})(?:\((\d{4})\))?")

# Orgs where scanned PDFs before this year can't be converted to clean markdown
MIN_MARKDOWN_YEAR = {
    "ai": 2013,
}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Step 7: Convert to markdown")
    p.add_argument("--org", action="append", help="Only process this org (repeatable)")
    p.add_argument("--year", action="append", help="Only process this year (repeatable)")
    p.add_argument(
        "--country", action="append",
        help="Only process this country_folder (repeatable)",
    )
    p.add_argument("--dry-run", action="store_true", help="Preview without writing files")
    p.add_argument("--force", action="store_true", help="Re-process even if output exists")
    return p.parse_args()


def extract_pub_year(year_str: str) -> int | None:
    m = YEAR_RE.search(year_str)
    return int(m.group(1)) if m else None


def convert_pdf(pdf_path: str, out_path: str) -> None:
    """Convert a single PDF to markdown. Runs in a worker process."""
    md_text = pymupdf4llm.to_markdown(pdf_path)
    Path(out_path).write_text(md_text, encoding="utf-8")


def convert_html(html_path: Path) -> str:
    """Convert an HTML file to markdown."""
    html = html_path.read_text(encoding="utf-8")
    return markdownify.markdownify(html, heading_style="ATX", strip=["style", "script"])


def main():
    args = parse_args()

    if not MANIFEST_6.exists():
        print(f"Error: {MANIFEST_6.relative_to(PROJECT_ROOT)} not found. Run step 6 first.")
        return

    manifest_6 = json.loads(MANIFEST_6.read_text())

    org_filter = set(args.org) if args.org else None
    year_filter = set(args.year) if args.year else None
    country_filter = set(args.country) if args.country else None

    # Categorise work items
    pdf_work = []       # (src, dest, org) — need parallel conversion
    html_work = []      # (src, dest)
    md_copy = []        # (src, dest)
    pdf_copy = []       # (src, dest) — unconvertible, copy as-is
    records = []

    for entry in manifest_6["files"]:
        if entry["status"] != "ok":
            continue

        org = entry["org"]
        year_str = entry["year"]
        folder = entry["country_folder"]

        if org_filter and org not in org_filter:
            continue
        pub_year = extract_pub_year(year_str)
        if year_filter and pub_year is not None and str(pub_year) not in year_filter:
            continue
        if country_filter and folder not in country_filter:
            continue

        src = PROJECT_ROOT / entry["output_path"]
        rel = src.relative_to(FILTERED_DIR)
        ext = src.suffix.lower()

        if ext == ".md":
            dest = MARKDOWN_DIR / rel
            md_copy.append((src, dest, entry))
        elif ext == ".html":
            dest = MARKDOWN_DIR / rel.with_suffix(".md")
            html_work.append((src, dest, entry))
        elif ext == ".pdf":
            min_year = MIN_MARKDOWN_YEAR.get(org)
            if min_year and pub_year is not None and pub_year < min_year:
                dest = MARKDOWN_DIR / rel
                pdf_copy.append((src, dest, entry))
            else:
                dest = MARKDOWN_DIR / rel.with_suffix(".md")
                pdf_work.append((src, dest, entry))
        else:
            # Unknown extension — copy as-is
            dest = MARKDOWN_DIR / rel
            md_copy.append((src, dest, entry))

    total = len(pdf_work) + len(html_work) + len(md_copy) + len(pdf_copy)
    if not total:
        print("No files to process.")
        return

    print(f"Files to process: {len(pdf_work)} PDF→md, {len(html_work)} HTML→md, "
          f"{len(md_copy)} md copy, {len(pdf_copy)} PDF copy (unconvertible)")

    # --- Process non-PDF items (fast, main process) ---
    for src, dest, entry in md_copy:
        status = "copied_md"
        if not args.dry_run:
            if args.force or not dest.exists():
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dest)
        records.append(_record(entry, dest, status))

    for src, dest, entry in pdf_copy:
        status = "copied_pdf_unconvertible"
        if not args.dry_run:
            if args.force or not dest.exists():
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dest)
        records.append(_record(entry, dest, status))

    for src, dest, entry in html_work:
        try:
            if not args.dry_run:
                if args.force or not dest.exists():
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    md_text = convert_html(src)
                    dest.write_text(md_text, encoding="utf-8")
            records.append(_record(entry, dest, "ok"))
        except Exception as exc:
            records.append(_record(entry, dest, "error", str(exc)))

    # --- Process PDFs in parallel ---
    if pdf_work:
        # Filter out already-done unless --force
        to_convert = []
        for src, dest, entry in pdf_work:
            if not args.force and dest.exists():
                records.append(_record(entry, dest, "ok"))
            else:
                to_convert.append((src, dest, entry))

        if to_convert and not args.dry_run:
            progress = Progress(
                TextColumn("[bold blue]{task.description}"),
                BarColumn(),
                MofNCompleteColumn(),
                TimeElapsedColumn(),
            )
            task = progress.add_task("PDF → markdown", total=len(to_convert))
            errors = []

            with Live(progress, refresh_per_second=10):
                with ProcessPoolExecutor() as pool:
                    future_map = {}
                    for src, dest, entry in to_convert:
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        future = pool.submit(convert_pdf, str(src), str(dest))
                        future_map[future] = (src, dest, entry)

                    for future in as_completed(future_map):
                        src, dest, entry = future_map[future]
                        try:
                            future.result()
                            records.append(_record(entry, dest, "ok"))
                        except Exception as exc:
                            errors.append(f"{src.name}: {exc}")
                            records.append(_record(entry, dest, "error", str(exc)))
                        progress.advance(task)

            if errors:
                print(f"\n{len(errors)} conversion errors:")
                for err in errors:
                    print(f"  {err}")
        elif to_convert and args.dry_run:
            for src, dest, entry in to_convert:
                records.append(_record(entry, dest, "ok"))

    # --- Write manifest ---
    ok = sum(1 for r in records if r["status"] == "ok")
    copied_md = sum(1 for r in records if r["status"] == "copied_md")
    copied_pdf = sum(1 for r in records if r["status"] == "copied_pdf_unconvertible")
    errored = sum(1 for r in records if r["status"] == "error")

    scope = {}
    if args.org:
        scope["org"] = args.org
    if args.year:
        scope["year"] = args.year
    if args.country:
        scope["country"] = args.country

    manifest = {
        "step": 7,
        "name": "converted",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "scoped_to": scope,
        "files": records,
        "summary": {
            "total": len(records),
            "ok": ok,
            "copied_md": copied_md,
            "copied_pdf_unconvertible": copied_pdf,
            "error": errored,
        },
    }

    if not args.dry_run:
        MANIFESTS_DIR.mkdir(parents=True, exist_ok=True)
        manifest_path = MANIFESTS_DIR / "7_converted.json"

        if manifest_path.exists():
            existing = json.loads(manifest_path.read_text())
            existing_files = {r["input_path"]: r for r in existing.get("files", [])}
            for r in records:
                existing_files[r["input_path"]] = r
            manifest["files"] = list(existing_files.values())
            manifest["summary"] = {
                "total": len(manifest["files"]),
                "ok": sum(1 for r in manifest["files"] if r["status"] == "ok"),
                "copied_md": sum(1 for r in manifest["files"] if r["status"] == "copied_md"),
                "copied_pdf_unconvertible": sum(
                    1 for r in manifest["files"]
                    if r["status"] == "copied_pdf_unconvertible"
                ),
                "error": sum(1 for r in manifest["files"] if r["status"] == "error"),
            }

        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
        print(f"Manifest written to {manifest_path.relative_to(PROJECT_ROOT)}")

    print(f"\nConverted: {ok} ok, {copied_md} md copied, "
          f"{copied_pdf} PDF copied (unconvertible), {errored} errors")

    if args.dry_run:
        print("\n[dry-run] No files written, no manifest written.")


def _record(entry: dict, dest: Path, status: str, error: str | None = None) -> dict:
    r = {
        "input_path": entry["output_path"],
        "output_path": str(dest.relative_to(PROJECT_ROOT)),
        "org": entry["org"],
        "type": entry["type"],
        "year": entry["year"],
        "country_folder": entry["country_folder"],
        "status": status,
    }
    if error:
        r["error"] = error
    return r


if __name__ == "__main__":
    main()

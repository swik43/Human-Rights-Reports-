"""
Split report PDFs into per-country files.

Reads split_config.json for the given source. Each entry contains a source_path
(where to read the PDF from) and a list of countries with true_page values.
Each country's range runs from its true_page to the next country's true_page - 1
(last country runs to EOF).

Entries with "pre_split": true are copied through without splitting.

AI-specific: regional PDFs get output subdirectories like <year>_Africa.

Usage:
    python pipeline/split_pdfs.py hrw                # all HRW PDFs
    python pipeline/split_pdfs.py ai 2023 2015       # specific AI years
"""

import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

from config import (
    SourceConfig,
    extract_year,
    extract_year_full,
    get_source,
    make_layout,
    make_progress,
    sanitize_filename,
)
from pypdf import PdfReader, PdfWriter
from rich.live import Live
from rich.text import Text

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MANIFESTS_DIR = PROJECT_ROOT / "manifests"


def year_dir_for(pdf_name: str, output_dir: Path) -> Path:
    """Determine the output subdirectory for an AI regional PDF."""
    year_full = extract_year_full(pdf_name)
    if "Africa" in pdf_name and "Amnesty" in pdf_name and "Middle" not in pdf_name:
        return output_dir / f"{year_full}_Africa"
    if "Americas" in pdf_name:
        return output_dir / f"{year_full}_Americas"
    if "Asia_Pacific" in pdf_name:
        return output_dir / f"{year_full}_Asia_Pacific"
    if "Eastern_Europe" in pdf_name:
        return output_dir / f"{year_full}_Eastern_Europe_Central_Asia"
    if "Middle_East" in pdf_name:
        return output_dir / f"{year_full}_Middle_East_North_Africa"
    return output_dir / year_full


def get_dest_dir(pdf_name: str, cfg: SourceConfig, source_key: str) -> Path:
    """Pick the right output subdirectory for a PDF."""
    if source_key == "ai":
        return year_dir_for(pdf_name, cfg.output_dir)
    return cfg.output_dir / extract_year_full(pdf_name)


def split_pdf(
    pdf_name,
    source_path,
    countries,
    dest,
    org,
    *,
    spinner,
    live,
    progress,
    overall_task,
) -> list[dict]:
    """Split a single PDF into per-country files. Returns manifest records."""
    records = []
    dest.mkdir(parents=True, exist_ok=True)
    reader = PdfReader(source_path)
    total_pages = len(reader.pages)
    year = extract_year_full(pdf_name)

    for i, country in enumerate(countries):
        spinner.update(text=Text(f"{pdf_name} / {country['name']}", style="gray"))
        live.update(make_layout(spinner, progress))

        start_page = country["true_page"]  # 1-indexed

        if "end_page" in country:
            end_page = country["end_page"]
        elif i + 1 < len(countries):
            end_page = max(start_page, countries[i + 1]["true_page"] - 1)
        else:
            end_page = total_pages

        start_idx = start_page - 1
        end_idx = end_page - 1

        filename = sanitize_filename(country["name"]) + ".pdf"
        out_path = dest / filename

        if start_idx < 0 or start_idx >= total_pages:
            live.console.print(
                f"  WARNING: {country['name']} page {start_page} out of range (total: {total_pages})"
            )
            records.append({
                "input_path": source_path,
                "output_path": str(out_path),
                "org": org,
                "year": year,
                "country_raw": country["name"],
                "status": "error",
            })
            progress.advance(overall_task)
            continue

        end_idx = min(end_idx, total_pages - 1)

        writer = PdfWriter()
        for page_idx in range(start_idx, end_idx + 1):
            writer.add_page(reader.pages[page_idx])

        with open(out_path, "wb") as f:
            writer.write(f)

        records.append({
            "input_path": source_path,
            "output_path": str(out_path),
            "org": org,
            "year": year,
            "country_raw": country["name"],
            "status": "ok",
        })
        progress.advance(overall_task)

    return records


def copy_pre_split(
    pdf_name,
    entry,
    dest,
    org,
    *,
    spinner,
    live,
    progress,
    overall_task,
) -> list[dict]:
    """Copy pre-split files through to the output directory. Returns manifest records."""
    records = []
    dest.mkdir(parents=True, exist_ok=True)
    source_dir = Path(entry["source_path"])
    year = extract_year_full(pdf_name)

    if not source_dir.is_dir():
        live.console.print(f"  WARNING: pre_split source dir {source_dir} not found")
        progress.advance(overall_task, advance=len(entry.get("countries", [])))
        return records

    year_prefix_re = re.compile(r"^\d{4}(?:\(\d{4}\))?_")

    for f in sorted(source_dir.iterdir()):
        if f.name.startswith(".") or not f.is_file():
            continue
        spinner.update(text=Text(f"{pdf_name} / {f.name} (copy)", style="gray"))
        live.update(make_layout(spinner, progress))

        stripped_name = year_prefix_re.sub("", f.name)
        out_path = dest / stripped_name
        shutil.copy2(f, out_path)

        records.append({
            "input_path": str(f),
            "output_path": str(out_path),
            "org": org,
            "year": year,
            "country_raw": Path(stripped_name).stem,
            "status": "ok",
        })
        progress.advance(overall_task)

    return records


def main():
    cfg, year_filter = get_source()
    source_key = cfg.source_dir.name.lower()  # "hrw" or "ai"

    if not cfg.split_config_path.exists():
        print(f"Error: {cfg.split_config_path} not found.")
        return

    with open(cfg.split_config_path) as f:
        split_config = json.load(f)

    cfg.output_dir.mkdir(parents=True, exist_ok=True)

    # Pre-scan: build eligible list and count total items
    eligible: list[tuple[str, dict, Path]] = []
    total_items = 0
    skipped = 0

    for pdf_name, entry in sorted(split_config.items()):
        if year_filter:
            year = extract_year(pdf_name)
            if year not in year_filter:
                continue

        is_pre_split = entry.get("pre_split", False)

        if is_pre_split:
            source_path = Path(entry["source_path"])
            if not source_path.is_dir():
                print(f"WARNING: pre_split dir {source_path} not found, skipping")
                skipped += 1
                continue
            count = sum(1 for f in source_path.iterdir() if f.is_file() and not f.name.startswith("."))
        else:
            countries = entry.get("countries", [])
            if not countries:
                continue
            source_path = Path(entry["source_path"])
            if not source_path.exists():
                print(f"WARNING: {source_path} not found, skipping")
                skipped += 1
                continue
            count = len(countries)

        dest = get_dest_dir(pdf_name, cfg, source_key)
        eligible.append((pdf_name, entry, dest))
        total_items += count

    if not eligible:
        print("No eligible PDFs found.")
        return

    all_records: list[dict] = []
    progress, spinner = make_progress()
    overall_task = progress.add_task("Overall", total=total_items)

    with Live(make_layout(spinner, progress), refresh_per_second=10) as live:
        for pdf_name, entry, dest in eligible:
            if entry.get("pre_split", False):
                records = copy_pre_split(
                    pdf_name,
                    entry,
                    dest,
                    source_key,
                    spinner=spinner,
                    live=live,
                    progress=progress,
                    overall_task=overall_task,
                )
            else:
                records = split_pdf(
                    pdf_name,
                    entry["source_path"],
                    entry["countries"],
                    dest,
                    source_key,
                    spinner=spinner,
                    live=live,
                    progress=progress,
                    overall_task=overall_task,
                )
            all_records.extend(records)

    # Write manifest
    ok_count = sum(1 for r in all_records if r["status"] == "ok")
    err_count = sum(1 for r in all_records if r["status"] == "error")

    manifest = {
        "step": 4,
        "name": "split_wr",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "scoped_to": {
            "org": source_key,
            **({"year": sorted(year_filter)} if year_filter else {}),
        },
        "files": all_records,
        "summary": {
            "total": len(all_records),
            "ok": ok_count,
            "skipped": skipped,
            "error": err_count,
        },
    }

    MANIFESTS_DIR.mkdir(parents=True, exist_ok=True)
    manifest_path = MANIFESTS_DIR / "4_split_wr.json"

    # Merge with existing manifest if re-running with narrow scope
    if manifest_path.exists():
        existing = json.loads(manifest_path.read_text())
        existing_files = {r["output_path"]: r for r in existing.get("files", [])}
        for r in all_records:
            existing_files[r["output_path"]] = r
        manifest["files"] = list(existing_files.values())
        manifest["summary"]["total"] = len(manifest["files"])
        manifest["summary"]["ok"] = sum(1 for r in manifest["files"] if r["status"] == "ok")
        manifest["summary"]["error"] = sum(1 for r in manifest["files"] if r["status"] == "error")

    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")

    print(f"\nDone. {ok_count} country files split to {cfg.output_dir}/")
    print(f"Manifest written to {manifest_path.relative_to(PROJECT_ROOT)}")


if __name__ == "__main__":
    main()
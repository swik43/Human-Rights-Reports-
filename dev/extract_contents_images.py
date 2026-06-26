"""
Extract contents pages from report PDFs as PNG images.

Reads the contents_config.json for the given source, renders the specified
contents pages from each original PDF, and saves them as PNGs.

Usage:
    python dev/extract_contents_images.py hrw          # all HRW PDFs
    python dev/extract_contents_images.py ai 2023      # specific AI year
"""

import json

import pypdfium2 as pdfium

# config.py lives in the sibling pipeline/ package
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parents[1] / "pipeline"))
from config import extract_year, get_source, make_layout, make_progress
from rich.live import Live
from rich.text import Text

SCALE = 3  # render at 3x for readability


def main():
    cfg, year_filter = get_source()

    with open(cfg.contents_config_path) as f:
        config = json.load(f)

    output_dir = cfg.contents_images_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    # Pre-scan: build eligible list and count total pages
    eligible: list[tuple[str, dict]] = []
    total_pages = 0

    for pdf_name, info in sorted(config.items()):
        if year_filter:
            year = extract_year(pdf_name)
            if year not in year_filter:
                continue

        contents_pages = info["contents_pages"]
        if not contents_pages:
            continue

        pdf_path = cfg.source_dir / pdf_name
        if not pdf_path.exists():
            print(f"WARNING: {pdf_name} not found, skipping")
            continue

        eligible.append((pdf_name, info))
        total_pages += len(contents_pages)

    if not eligible:
        print("No eligible PDFs found.")
        return

    progress, spinner = make_progress()
    overall_task = progress.add_task("Overall", total=total_pages)

    with Live(make_layout(spinner, progress), refresh_per_second=10) as live:
        for pdf_name, info in eligible:
            contents_pages = info["contents_pages"]
            stem = pdf_name.replace(".pdf", "")
            out_dir = output_dir / stem
            out_dir.mkdir(parents=True, exist_ok=True)

            pdf = pdfium.PdfDocument(str(cfg.source_dir / pdf_name))
            for page_num in contents_pages:
                spinner.update(text=Text(f"{pdf_name} / page {page_num}", style="gray"))
                live.update(make_layout(spinner, progress))

                page = pdf[page_num - 1]  # 0-indexed
                bitmap = page.render(scale=SCALE)
                image = bitmap.to_pil()
                out_path = out_dir / f"page_{page_num}.png"
                image.save(str(out_path))

                progress.advance(overall_task)

            pdf.close()

    print(f"\nDone. {total_pages} pages extracted to {output_dir}/")


if __name__ == "__main__":
    main()

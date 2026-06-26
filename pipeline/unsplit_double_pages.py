"""
Convert double-layout PDFs into single-page-per-sheet PDFs.

Reads unsplit_config.json for the given source. For each listed PDF:
- Keeps pages before double_start as-is
- Splits every page from double_start onward into left and right halves
- Writes the result to the source's unsplit_dir

This is non-destructive -- original PDFs are never modified.

Usage:
    python pipeline/unsplit_double_pages.py <hrw|ai|idmc> [year ...]
"""

import json
import multiprocessing
import queue
from copy import deepcopy
from pathlib import Path

from config import extract_year, get_source
from pypdf import PdfReader, PdfWriter
from rich.live import Live
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    TextColumn,
    TimeElapsedColumn,
)



def split_page_halves(page):
    """Return (left_page, right_page) by cropping a page down the middle."""
    left = deepcopy(page)
    right = deepcopy(page)

    box = page.mediabox
    mid_x = (box.left + box.right) / 2

    left.mediabox.upper_right = (mid_x, box.top)
    right.mediabox.upper_left = (mid_x, box.top)
    right.mediabox.lower_left = (mid_x, box.bottom)

    return left, right


def process_pdf_worker(
    pdf_name: str,
    pdf_cfg: dict,
    source_dir: str,
    unsplit_dir: str,
    progress_queue: multiprocessing.Queue,
) -> None:
    """Process a single double-layout PDF. Runs in a worker process."""
    double_start = pdf_cfg["double_start"]
    reader = PdfReader(str(Path(source_dir) / pdf_name))
    writer = PdfWriter()

    for i, page in enumerate(reader.pages):
        page_num = i + 1
        if page_num < double_start:
            writer.add_page(page)
        else:
            left, right = split_page_halves(page)
            writer.add_page(left)
            writer.add_page(right)

        progress_queue.put((pdf_name, 1))

    out_dir = Path(unsplit_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_dir / pdf_name, "wb") as f:
        writer.write(f)


def main():
    cfg, year_filter = get_source()

    assert cfg.unsplit_dir is not None, f"No unsplit_dir configured for this source"

    if not cfg.unsplit_config_path.exists():
        print(f"Error: {cfg.unsplit_config_path} not found.")
        return

    with open(cfg.unsplit_config_path) as f:
        config = json.load(f)

    # Pre-scan: count total pages across eligible PDFs
    eligible: list[tuple[str, dict]] = []
    pdf_page_counts: dict[str, int] = {}

    for pdf_name, pdf_cfg in sorted(config.items()):
        if year_filter:
            year = extract_year(pdf_name)
            if year not in year_filter:
                continue

        pdf_path = cfg.source_dir / pdf_name
        if not pdf_path.exists():
            print(f"WARNING: {pdf_path} not found, skipping")
            continue

        reader = PdfReader(str(pdf_path))
        pdf_page_counts[pdf_name] = len(reader.pages)
        eligible.append((pdf_name, pdf_cfg))

    if not eligible:
        print("No eligible double-layout PDFs found.")
        return

    total_pages = sum(pdf_page_counts.values())

    # One progress bar per PDF
    progress = Progress(
        TextColumn("[bold blue]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
    )

    pdf_tasks = {}
    for pdf_name, _ in eligible:
        pdf_tasks[pdf_name] = progress.add_task(
            pdf_name, total=pdf_page_counts[pdf_name]
        )

    progress_queue: multiprocessing.Queue = multiprocessing.Queue()

    with Live(progress, refresh_per_second=10):
        processes = []
        for pdf_name, pdf_cfg in eligible:
            p = multiprocessing.Process(
                target=process_pdf_worker,
                args=(
                    pdf_name,
                    pdf_cfg,
                    str(cfg.source_dir),
                    str(cfg.unsplit_dir),
                    progress_queue,
                ),
            )
            processes.append(p)
            p.start()

        # Drain progress events from workers
        pages_done = 0
        while pages_done < total_pages:
            try:
                pdf_name, count = progress_queue.get(timeout=1.0)
                progress.advance(pdf_tasks[pdf_name], count)
                pages_done += count
            except queue.Empty:
                # All workers dead before all pages reported — something crashed
                if all(not p.is_alive() for p in processes):
                    break

        for p in processes:
            p.join()

    print(
        f"\nDone. {len(eligible)} PDFs processed ({total_pages} pages) to {cfg.unsplit_dir}/"
    )


if __name__ == "__main__":
    main()

"""
Shared configuration and utilities for world-reports scripts.

Provides per-source configs (HRW / AI) and common helper functions
used across extract, build, split, and convert scripts.
"""

import re
import sys
from dataclasses import dataclass
from pathlib import Path

from rich.console import Group
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    TextColumn,
    TimeElapsedColumn,
)
from rich.spinner import Spinner
from rich.text import Text


@dataclass
class SourceConfig:
    source_dir: Path
    pdf_suffix: str
    data_dir: Path
    output_dir: Path
    markdown_dir: Path
    unsplit_dir: Path | None
    min_markdown_year: int | None

    @property
    def unsplit_config_path(self) -> Path:
        return self.data_dir / "1_unsplit_config.json"

    @property
    def contents_config_path(self) -> Path:
        # HRW: step 2, AI: step 1
        prefix = "2" if self.unsplit_dir is not None else "1"
        return self.data_dir / f"{prefix}_contents_config.json"

    @property
    def overrides_path(self) -> Path:
        # HRW: step 3, AI: step 2
        prefix = "3" if self.unsplit_dir is not None else "2"
        return self.data_dir / f"{prefix}_overrides.json"

    @property
    def split_config_path(self) -> Path:
        # HRW: step 4, AI: step 3
        prefix = "4" if self.unsplit_dir is not None else "3"
        return self.data_dir / f"{prefix}_split_config.json"

    @property
    def contents_images_dir(self) -> Path:
        return self.data_dir / "contents_images"

    @property
    def contents_json_dir(self) -> Path:
        return self.data_dir / "contents_json"


SOURCES: dict[str, SourceConfig] = {
    "hrw": SourceConfig(
        source_dir=Path("input/wr/hrw"),
        pdf_suffix="World_Report_Human_Rights_Watch",
        data_dir=Path("config/hrw"),
        output_dir=Path("intermediate/split_wr/hrw"),
        markdown_dir=Path("intermediate/markdown/hrw"),
        unsplit_dir=Path("intermediate/unsplit/hrw"),
        min_markdown_year=None,
    ),
    "ai": SourceConfig(
        source_dir=Path("input/wr/ai"),
        pdf_suffix="Amnesty_International",
        data_dir=Path("config/ai"),
        output_dir=Path("intermediate/split_wr/ai"),
        markdown_dir=Path("intermediate/markdown/ai"),
        unsplit_dir=None,
        min_markdown_year=2013,
    ),
    "idmc": SourceConfig(
        source_dir=Path("input/wr/idmc"),
        pdf_suffix="IDMC",
        data_dir=Path("config/idmc"),
        output_dir=Path("intermediate/split_wr/idmc"),
        markdown_dir=Path("intermediate/markdown/idmc"),
        unsplit_dir=Path("intermediate/unsplit/idmc"),
        min_markdown_year=None,
    ),
}


def extract_year(name: str) -> str:
    """Extract leading 4-digit year from a filename or directory name."""
    match = re.match(r"(\d{4})", name)
    if match:
        return match.group(1)
    raise ValueError(f"Cannot extract year from {name}")


def extract_year_full(name: str) -> str:
    """Extract year with optional parenthetical from a filename.

    Returns 'YYYY(YYYY)' when present, otherwise 'YYYY'.
    """
    match = re.match(r"(\d{4}(?:\(\d{4}\))?)", name)
    if match:
        return match.group(1)
    raise ValueError(f"Cannot extract year from {name}")


def sanitize_filename(name: str) -> str:
    """Clean a country name for use as a filename."""
    name = name.replace("/", "-")
    name = re.sub(r'[<>:"|?*]', "", name)
    return name.strip()


def titlecase_name(name: str) -> str:
    """Normalize ALL-CAPS country names to title case."""
    if not name.isupper():
        return name
    result = name.title()
    for word in ["And", "Of", "The"]:
        result = result.replace(f" {word} ", f" {word.lower()} ")
    result = result.replace("D'", "d'")
    return result


def make_progress() -> tuple[Progress, Spinner]:
    """Create the standard rich progress bar and spinner."""
    progress = Progress(
        TextColumn("[bold blue]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
    )
    spinner = Spinner("dots", text=Text("Starting...", style="cyan"))
    return progress, spinner


def make_layout(spinner: Spinner, progress: Progress) -> Group:
    """Combine spinner and progress bar for Live display."""
    return Group(spinner, progress)


def get_source(argv: list[str] | None = None) -> tuple[SourceConfig, set[str] | None]:
    """Parse CLI args: first arg = source key, rest = optional year filter.

    Usage: script.py <hrw|ai> [year ...]
    """
    if argv is None:
        argv = sys.argv[1:]

    if not argv or argv[0] not in SOURCES:
        valid = ", ".join(SOURCES)
        print(f"Usage: <script> <{valid}> [year ...]")
        sys.exit(1)

    cfg = SOURCES[argv[0]]
    year_filter = set(argv[1:]) if len(argv) > 1 else None
    return cfg, year_filter
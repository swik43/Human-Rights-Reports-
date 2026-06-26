"""
Offset the year in all file names, directory names, and config files
for a given source (hrw or ai).

Change YEAR_OFFSET to control direction and magnitude:
  -1 = decrement by 1 year
  +1 = increment by 1 year
  +2 = increment by 2 years, etc.

Processes in the correct order to avoid collisions (highest-first when
incrementing, lowest-first when decrementing).

Usage:
    python dev/offset_years.py hrw [--dry-run]
    python dev/offset_years.py ai [--dry-run]
"""

import json
import re
import sys
from pathlib import Path

# config.py lives in the sibling pipeline/ package
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))
from config import SOURCES, SourceConfig

# ── Change this value to control the year shift ──────────────────────
YEAR_OFFSET = +1
# ─────────────────────────────────────────────────────────────────────

ROOT = Path(__file__).resolve().parents[1]

YEAR_PREFIX = re.compile(r"^(\d{4})(_.*)")
YEAR_DIR = re.compile(r"^\d{4}$")


def offset_name(name: str) -> str:
    """Apply YEAR_OFFSET to a year-prefixed name (file or dir)."""
    m = YEAR_PREFIX.match(name)
    if not m:
        return name
    return f"{int(m.group(1)) + YEAR_OFFSET}{m.group(2)}"


def offset_bare_year(name: str) -> str:
    """Apply YEAR_OFFSET to a bare year directory name like '2005'."""
    return str(int(name) + YEAR_OFFSET)


def rename_dir_tree(base: Path, dry_run: bool) -> int:
    """Rename year-prefixed files and year directories inside `base`.

    Returns the number of items processed.
    """
    if not base.is_dir():
        print(f"  Skipping (not found): {base}")
        return 0

    subdirs: list[Path] = []
    files_in_subdirs: list[Path] = []
    standalone_files: list[Path] = []

    for item in sorted(base.iterdir()):
        if item.is_dir() and (
            YEAR_DIR.match(item.name) or YEAR_PREFIX.match(item.name)
        ):
            subdirs.append(item)
            for f in sorted(item.iterdir()):
                if YEAR_PREFIX.match(f.name):
                    files_in_subdirs.append(f)
        elif item.is_file() and YEAR_PREFIX.match(item.name):
            standalone_files.append(item)

    # Process order: highest-first when incrementing, lowest-first when decrementing
    reverse = YEAR_OFFSET > 0
    tag = "DRY RUN" if dry_run else "RENAME"

    # 1. Files inside subdirectories (before dirs are renamed)
    for f in sorted(files_in_subdirs, key=lambda p: p.name, reverse=reverse):
        new_name = offset_name(f.name)
        print(f"  [{tag}] {f.relative_to(base)} → {new_name}")
        if not dry_run:
            f.rename(f.parent / new_name)

    # 2. Subdirectories
    for d in sorted(subdirs, key=lambda p: p.name, reverse=reverse):
        if YEAR_DIR.match(d.name):
            new_name = offset_bare_year(d.name)
        else:
            new_name = offset_name(d.name)
        print(f"  [{tag}] {d.name}/ → {new_name}/")
        if not dry_run:
            d.rename(d.parent / new_name)

    # 3. Standalone files
    for f in sorted(standalone_files, key=lambda p: p.name, reverse=reverse):
        new_name = offset_name(f.name)
        print(f"  [{tag}] {f.name} → {new_name}")
        if not dry_run:
            f.rename(f.parent / new_name)

    return len(files_in_subdirs) + len(subdirs) + len(standalone_files)


def patch_json_keys(path: Path, dry_run: bool) -> int:
    """Offset years in JSON object keys. Returns number of keys changed."""
    if not path.is_file():
        print(f"  Skipping (not found): {path}")
        return 0

    data = json.loads(path.read_text())
    new_data = {}
    changed = 0
    tag = "DRY RUN" if dry_run else "UPDATE"

    for key in sorted(data.keys(), reverse=YEAR_OFFSET > 0):
        new_key = offset_name(key)
        if new_key != key:
            print(f"  [{tag}] {key} → {new_key}")
            changed += 1
        new_data[new_key] = data[key]

    if not dry_run and changed:
        sorted_data = dict(sorted(new_data.items()))
        path.write_text(json.dumps(sorted_data, indent=2, ensure_ascii=False) + "\n")

    return changed


def build_locations(cfg: SourceConfig) -> list[tuple[str, Path]]:
    """Build the list of directories to process from a source config."""
    locations = [
        (f"{cfg.source_dir.name} source files", ROOT / cfg.source_dir),
        ("Output: split country PDFs", ROOT / cfg.output_dir),
    ]
    if cfg.unsplit_dir:
        locations.append(("Output: unsplit double-layout PDFs", ROOT / cfg.unsplit_dir))
    locations += [
        ("Output: markdown files", ROOT / cfg.markdown_dir),
        ("Data: contents images", ROOT / cfg.contents_images_dir),
        ("Data: contents JSON", ROOT / cfg.contents_json_dir),
    ]
    return locations


def main(source_key: str, dry_run: bool = False) -> None:
    if YEAR_OFFSET == 0:
        print("YEAR_OFFSET is 0, nothing to do.")
        return

    cfg = SOURCES[source_key]
    direction = f"+{YEAR_OFFSET}" if YEAR_OFFSET > 0 else str(YEAR_OFFSET)
    print(f"Source: {source_key}, year offset: {direction}\n")

    total = 0

    for label, path in build_locations(cfg):
        print(f"== {label} ({path.relative_to(ROOT)}) ==")
        total += rename_dir_tree(path, dry_run)
        print()

    json_configs = [
        ROOT / cfg.config_path,
        ROOT / cfg.parsed_path,
    ]

    for config_path in json_configs:
        print(f"== Config: {config_path.name} ==")
        total += patch_json_keys(config_path, dry_run)
        print()

    verb = "Would update" if dry_run else "Updated"
    print(f"{verb} {total} items total.")


if __name__ == "__main__":
    # Parse source key (first non-flag arg) and --dry-run flag
    args = [a for a in sys.argv[1:] if a != "--dry-run"]
    dry_run = "--dry-run" in sys.argv

    if not args or args[0] not in SOURCES:
        valid = ", ".join(SOURCES)
        print(f"Usage: {sys.argv[0]} <{valid}> [--dry-run]")
        sys.exit(1)

    if not dry_run:
        print("Pass --dry-run to preview changes without modifying anything.\n")

    main(args[0], dry_run=dry_run)

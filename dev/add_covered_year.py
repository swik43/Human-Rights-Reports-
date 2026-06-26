"""
Annotate all year-prefixed file names, directory names, and config keys
for a given source (hrw, ai, or idmc) with the year the report covers.

The covered year is the year the report discusses, typically the year before
the release year. Set COVERED_YEAR_OFFSET to control this:
  -1 = covers year before release  (2000 report → covers 1999)
  -2 = covers two years before release, etc.

Transforms:
  2000_Amnesty_International.pdf  →  2000(1999)_Amnesty_International.pdf
  1999/                           →  1999(1998)/

Idempotent: names that already contain the annotation (e.g. 2000(1999)_...)
don't match the year-prefix pattern and are left untouched.

NOTE: after running this script, offset_years.py will no longer match the new
filename format (its YEAR_PREFIX regex requires _ immediately after 4 digits).

Usage:
    python dev/add_covered_year.py hrw [--dry-run]
    python dev/add_covered_year.py ai [--dry-run]
    python dev/add_covered_year.py idmc [--dry-run]
"""

import json
import re
import sys
from pathlib import Path

# config.py lives in the sibling pipeline/ package
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))
from config import SOURCES, SourceConfig

# ── Change this value to control the covered-year offset ─────────────────────
COVERED_YEAR_OFFSET = -1
# ─────────────────────────────────────────────────────────────────────────────

ROOT = Path(__file__).resolve().parents[1]

YEAR_PREFIX = re.compile(r"^(\d{4})(_.*)$")
YEAR_DIR = re.compile(r"^\d{4}$")


def annotate(name: str) -> str:
    """Inject covered-year into a year-prefixed name (file or non-bare dir)."""
    m = YEAR_PREFIX.match(name)
    if not m:
        return name
    release = int(m.group(1))
    covered = release + COVERED_YEAR_OFFSET
    return f"{release}({covered}){m.group(2)}"


def annotate_bare(name: str) -> str:
    """Inject covered-year into a bare year directory name like '2005'."""
    release = int(name)
    covered = release + COVERED_YEAR_OFFSET
    return f"{release}({covered})"


def rename_dir_tree(base: Path, dry_run: bool) -> int:
    """Rename year-prefixed files and year directories inside `base`.

    Returns the number of items renamed (or that would be renamed).
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

    tag = "DRY RUN" if dry_run else "RENAME"

    # 1. Files inside subdirectories (before the dirs themselves are renamed)
    for f in sorted(files_in_subdirs, key=lambda p: p.name):
        new_name = annotate(f.name)
        print(f"  [{tag}] {f.relative_to(base)} → {new_name}")
        if not dry_run:
            f.rename(f.parent / new_name)

    # 2. Subdirectories
    for d in sorted(subdirs, key=lambda p: p.name):
        if YEAR_DIR.match(d.name):
            new_name = annotate_bare(d.name)
        else:
            new_name = annotate(d.name)
        print(f"  [{tag}] {d.name}/ → {new_name}/")
        if not dry_run:
            d.rename(d.parent / new_name)

    # 3. Standalone files
    for f in sorted(standalone_files, key=lambda p: p.name):
        new_name = annotate(f.name)
        print(f"  [{tag}] {f.name} → {new_name}")
        if not dry_run:
            f.rename(f.parent / new_name)

    return len(files_in_subdirs) + len(subdirs) + len(standalone_files)


def patch_json_keys(path: Path, dry_run: bool) -> int:
    """Annotate year-prefixed JSON object keys. Returns number of keys changed."""
    if not path.is_file():
        print(f"  Skipping (not found): {path}")
        return 0

    data = json.loads(path.read_text())
    new_data: dict = {}
    changed = 0
    tag = "DRY RUN" if dry_run else "UPDATE"

    for key in sorted(data.keys()):
        new_key = annotate(key)
        if new_key != key:
            print(f"  [{tag}] {key} → {new_key}")
            changed += 1
        new_data[new_key] = data[key]

    if not dry_run and changed:
        path.write_text(
            json.dumps(dict(sorted(new_data.items())), indent=2, ensure_ascii=False)
            + "\n"
        )

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
    cfg = SOURCES[source_key]
    print(f"Source: {source_key}, covered-year offset: {COVERED_YEAR_OFFSET:+d}\n")

    total = 0

    for label, path in build_locations(cfg):
        print(f"== {label} ({path.relative_to(ROOT)}) ==")
        total += rename_dir_tree(path, dry_run)
        print()

    config_paths = [
        ROOT / cfg.unsplit_config_path,
        ROOT / cfg.contents_config_path,
        ROOT / cfg.overrides_path,
        ROOT / cfg.split_config_path,
    ]
    for config_path in config_paths:
        print(f"== Config: {config_path.name} ==")
        total += patch_json_keys(config_path, dry_run)
        print()

    verb = "Would update" if dry_run else "Updated"
    print(f"{verb} {total} items total.")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--dry-run"]
    dry_run = "--dry-run" in sys.argv

    if not args or args[0] not in SOURCES:
        valid = ", ".join(SOURCES)
        print(f"Usage: {sys.argv[0]} <{valid}> [--dry-run]")
        sys.exit(1)

    if not dry_run:
        print("Pass --dry-run to preview changes without modifying anything.\n")

    main(args[0], dry_run=dry_run)

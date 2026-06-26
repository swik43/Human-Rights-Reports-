"""One-off script to diff parallel markdown output against a backup."""

from pathlib import Path

BACKUP = Path("output/hrw_markdown_backup")
NEW = Path("output/hrw_markdown")

backup_files = {p.relative_to(BACKUP) for p in BACKUP.rglob("*.md")}
new_files = {p.relative_to(NEW) for p in NEW.rglob("*.md")}

only_backup = sorted(backup_files - new_files)
only_new = sorted(new_files - backup_files)
common = sorted(backup_files & new_files)

if only_backup:
    print(f"Only in backup ({len(only_backup)}):")
    for f in only_backup:
        print(f"  {f}")

if only_new:
    print(f"\nOnly in new ({len(only_new)}):")
    for f in only_new:
        print(f"  {f}")

diffs = []
for f in common:
    if (BACKUP / f).read_bytes() != (NEW / f).read_bytes():
        diffs.append(f)

if diffs:
    print(f"\nContent differs ({len(diffs)}):")
    for f in diffs:
        print(f"  {f}")

if not only_backup and not only_new and not diffs:
    print(f"All {len(common)} files identical.")

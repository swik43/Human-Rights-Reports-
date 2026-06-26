"""
One-time fixup: prepend output/ to paths in manifests/8_organised.json.

Updates readable_path, llm_path (on file entries) and path (on source entries)
so they match the actual on-disk locations under output/.

Idempotent — safe to run multiple times.

Usage:
    python dev/fix_manifest_paths.py
    python dev/fix_manifest_paths.py --dry-run
"""

import argparse
import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MANIFEST_8 = PROJECT_ROOT / "manifests" / "8_organised.json"


def main() -> None:
    p = argparse.ArgumentParser(description="Fix manifest 8 paths (prepend output/)")
    p.add_argument("--dry-run", action="store_true", help="Show what would change")
    args = p.parse_args()

    if not MANIFEST_8.exists():
        print(f"Error: {MANIFEST_8.relative_to(PROJECT_ROOT)} not found.")
        raise SystemExit(1)

    m8 = json.loads(MANIFEST_8.read_text())
    changed = 0

    for entry in m8["files"]:
        for key in ("readable_path", "llm_path"):
            val = entry.get(key)
            if val and not val.startswith("output/"):
                entry[key] = "output/" + val
                changed += 1

    for source in m8.get("sources", []):
        val = source.get("path")
        if val and not val.startswith("output/"):
            source["path"] = "output/" + val
            changed += 1

    if changed == 0:
        print("Nothing to fix — all paths already have output/ prefix.")
        return

    print(f"Fixed {changed} paths.")

    if args.dry_run:
        print("[dry-run] Manifest not written.")
    else:
        MANIFEST_8.write_text(json.dumps(m8, indent=2) + "\n")
        print(f"Wrote {MANIFEST_8.relative_to(PROJECT_ROOT)}")


if __name__ == "__main__":
    main()

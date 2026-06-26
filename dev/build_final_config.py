"""
Build split_config.json from Claude's output JSONs.

Reads each JSON in the source's contents_json/ directory (name + report_page
from Claude), applies the offset from contents_config.json to compute true_page,
normalizes country names to title case, and writes split_config.json.

The output includes a source_path per PDF so split_pdfs.py knows exactly
where to read each file from (unsplit_dir for double-layout, source_dir otherwise).

Manual country data can be provided in overrides.json instead of going through
the Claude extraction step. Any PDF listed there is used as-is.

Files with offset=null in the config must provide true_page
directly in their JSON instead of report_page.

Usage:
    python dev/build_final_config.py hrw
    python dev/build_final_config.py ai
"""

import json

# config.py lives in the sibling pipeline/ package
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parents[1] / "pipeline"))
from config import get_source, titlecase_name


def main():
    cfg, _ = get_source()

    with open(cfg.contents_config_path) as f:
        contents_config = json.load(f)

    # Load unsplit config to determine which PDFs live in unsplit_dir
    unsplit_pdfs: set[str] = set()
    if cfg.unsplit_config_path.exists():
        with open(cfg.unsplit_config_path) as f:
            unsplit_pdfs = set(json.load(f).keys())

    # Load manual overrides (pdf_name -> list of {name, true_page, ...})
    overrides: dict = {}
    if cfg.overrides_path.exists():
        with open(cfg.overrides_path) as f:
            overrides = json.load(f)

    # Load all Claude-extracted country JSONs
    contents_json: dict = {}
    for path in sorted(cfg.contents_json_dir.glob("*.json")):
        with open(path) as f:
            data = json.load(f)
        for pdf_name, entries in data.items():
            contents_json[pdf_name] = entries

    result = {}

    # Process all PDFs from contents_config plus any override-only PDFs
    all_pdf_names = sorted(set(contents_config) | set(overrides))

    for pdf_name in all_pdf_names:
        pdf_cfg = contents_config.get(pdf_name, {})
        offset = pdf_cfg.get("offset")

        # Determine source path
        if pdf_name in unsplit_pdfs and cfg.unsplit_dir is not None:
            source_path = str(cfg.unsplit_dir / pdf_name)
        else:
            source_path = str(cfg.source_dir / pdf_name)

        # Get country data: overrides take priority, then contents_json
        if pdf_name in overrides:
            raw_countries = overrides[pdf_name]
            source_label = "override"
        elif pdf_name in contents_json:
            entries = contents_json[pdf_name]
            # Support both list and {"countries": [...]} wrapper
            raw_countries = (
                entries.get("countries", entries)
                if isinstance(entries, dict)
                else entries
            )
            source_label = "contents_json"
        else:
            print(f"WARNING: no country data for {pdf_name}, skipping")
            continue

        processed = []
        for country in raw_countries:
            name = titlecase_name(country["name"])

            if "true_page" in country:
                true_page = country["true_page"]
            elif offset is not None:
                true_page = offset + country["report_page"]
            else:
                print(
                    f"  ERROR: {pdf_name}/{name} has no true_page and offset is null"
                )
                continue

            entry = {"name": name, "true_page": true_page}
            if "end_page" in country:
                entry["end_page"] = country["end_page"]
            processed.append(entry)

        # AI reports: country sections overlap onto the next country's first page,
        # so include that page in the range by setting an explicit end_page.
        if cfg.data_dir.name == "ai":
            for i in range(len(processed) - 1):
                if "end_page" not in processed[i]:
                    processed[i]["end_page"] = processed[i + 1]["true_page"]

        result[pdf_name] = {"source_path": source_path, "countries": processed}
        offset_str = f"offset {offset:+d}" if offset is not None else "true_page direct"
        print(f"{pdf_name}: {len(processed)} countries ({offset_str}, {source_label})")

    result = dict(sorted(result.items()))

    with open(cfg.split_config_path, "w") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
        f.write("\n")

    total = sum(len(v["countries"]) for v in result.values())
    print(
        f"\nWrote {cfg.split_config_path} ({len(result)} PDFs, {total} total country entries)"
    )


if __name__ == "__main__":
    main()
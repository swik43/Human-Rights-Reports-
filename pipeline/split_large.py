"""
Step 7b: Split oversized markdown into heading-aligned parts.

Some converted markdown files are too large for a single LLM instance to
process accurately. This step reads the step-7 manifest and, for any markdown
file whose estimated token count exceeds TARGET_TOKENS, slices it into parts at
heading boundaries (preferring shallower headings) so each part lands near the
target size. Files at or under the target pass through untouched.

Split parts are written to intermediate/markdown_split/ mirroring the source
tree. Files that aren't split are NOT copied — their manifest record just
points back at the original step-7 markdown, so organise (step 8) reads one
uniform `parts` list either way.

Two split strategies:
  - auto (default): greedy heading-aware packing.
  - override: for files listed in config/split_overrides.json, cut before each
    named anchor heading instead (hand-curated for the cases auto handles badly).
    Any resulting part still over target is auto-packed within.

Usage:
    python pipeline/split_large.py
    python pipeline/split_large.py --org idmc
    python pipeline/split_large.py --country Iraq --dry-run
"""

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MARKDOWN_DIR = PROJECT_ROOT / "intermediate" / "markdown"
SPLIT_DIR = PROJECT_ROOT / "intermediate" / "markdown_split"
MANIFESTS_DIR = PROJECT_ROOT / "manifests"
MANIFEST_7 = MANIFESTS_DIR / "7_converted.json"
MANIFEST_7B = MANIFESTS_DIR / "7b_split.json"
OVERRIDES_PATH = PROJECT_ROOT / "config" / "split_overrides.json"

# Target size per part. "roughly 67000 tokens" — treated as a soft ceiling we
# pack up to. No exact offline Claude tokenizer exists, so we estimate from
# character count; the target tolerates the imprecision.
TARGET_TOKENS = 67_000
CHARS_PER_TOKEN = 4.0
CEILING = TARGET_TOKENS  # estimated-token ceiling per part

# When the greedy packer must cut, it defaults to the fullest heading boundary.
# If a *shallower* heading sits within this fraction of the ceiling behind that
# point, prefer it — a cleaner section break costs a little fill.
NEAR_WINDOW = 0.20

YEAR_RE = re.compile(r"(\d{4})(?:\((\d{4})\))?")
HEADING_RE = re.compile(r"^(#{1,6})\s+\S")
FENCE_RE = re.compile(r"^\s*(```|~~~)")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Step 7b: Split oversized markdown")
    p.add_argument("--org", action="append", help="Only process this org (repeatable)")
    p.add_argument("--year", action="append", help="Only process this year (repeatable)")
    p.add_argument(
        "--country", action="append",
        help="Only process this country_folder (repeatable)",
    )
    p.add_argument("--dry-run", action="store_true", help="Report the plan; write nothing")
    p.add_argument("--force", action="store_true", help="Re-split even if outputs exist")
    return p.parse_args()


def est_tokens(text: str) -> int:
    return round(len(text) / CHARS_PER_TOKEN)


# ── Markdown parsing ─────────────────────────────────────────────────


class Block:
    """A heading line plus its body, up to the next heading of any level.

    `level` is the ATX heading depth (1-6); 0 for the preamble before the
    first heading. `heading` is the heading text (empty for the preamble).
    """

    __slots__ = ("text", "level", "heading", "tokens")

    def __init__(self, text: str, level: int, heading: str):
        self.text = text
        self.level = level
        self.heading = heading
        self.tokens = est_tokens(text)


def parse_blocks(md: str) -> list[Block]:
    """Split markdown into heading-delimited blocks, ignoring fenced code."""
    lines = md.splitlines(keepends=True)
    blocks: list[Block] = []
    cur: list[str] = []
    cur_level = 0
    cur_heading = ""
    in_fence = False

    def flush():
        if cur:
            blocks.append(Block("".join(cur), cur_level, cur_heading))

    for line in lines:
        if FENCE_RE.match(line):
            in_fence = not in_fence
            cur.append(line)
            continue
        m = None if in_fence else HEADING_RE.match(line)
        if m:
            flush()
            cur = [line]
            cur_level = len(m.group(1))
            cur_heading = line.lstrip("#").strip()
        else:
            cur.append(line)
    flush()
    return blocks


# ── Packing ──────────────────────────────────────────────────────────


def pack_auto(blocks: list[Block]) -> list[tuple[int, int]]:
    """Greedy heading-aware packing. Returns [(start, end)] block-index ranges.

    Grows a part until the next block would exceed the ceiling, then cuts at the
    fullest heading boundary — unless a shallower heading sits within NEAR_WINDOW
    behind it, in which case that cleaner break wins.
    """
    n = len(blocks)
    ranges: list[tuple[int, int]] = []
    start = 0
    while start < n:
        size = 0
        end = start
        while end < n and size + blocks[end].tokens <= CEILING:
            size += blocks[end].tokens
            end += 1

        if end == start:
            # A single block exceeds the ceiling on its own — emit it alone;
            # sub-splitting happens later in split_block().
            ranges.append((start, start + 1))
            start += 1
            continue
        if end == n:
            ranges.append((start, end))
            break

        # Stopped because blocks[end] would overflow. Candidate cut points are
        # the heading boundaries start+1..end (cut *before* that block).
        near_lo = (1.0 - NEAR_WINDOW) * CEILING
        prefix = 0
        best_k = end
        best_level = blocks[end].level if end < n else 7
        running = 0
        cuts = []
        for k in range(start, end):
            running += blocks[k].tokens
            cuts.append((k + 1, running))  # cutting before block k+1 yields `running` tokens
        for k, fill in cuts:
            if fill >= near_lo and k <= end:
                lvl = blocks[k].level if k < n else 7
                # shallower heading wins; tie -> fuller part
                if lvl < best_level or (lvl == best_level and k > best_k):
                    best_level = lvl
                    best_k = k
        ranges.append((start, best_k))
        start = best_k
    return ranges


def find_override_cuts(blocks: list[Block], anchors: list[str]) -> list[tuple[int, int]]:
    """Cut before each block whose heading matches an anchor string.

    Match is case-insensitive substring on the heading text. Unmatched anchors
    are reported by the caller. Resulting parts may still exceed the ceiling;
    the caller sub-packs those with pack_auto.
    """
    cut_points = [0]
    unmatched = list(anchors)
    norm = [(i, b.heading.lower()) for i, b in enumerate(blocks)]
    for anchor in anchors:
        a = anchor.lower().strip()
        for i, h in norm:
            if i == 0:
                continue
            if a and a in h:
                if i not in cut_points:
                    cut_points.append(i)
                if anchor in unmatched:
                    unmatched.remove(anchor)
                break
    cut_points = sorted(set(cut_points))
    cut_points.append(len(blocks))
    ranges = [(cut_points[i], cut_points[i + 1]) for i in range(len(cut_points) - 1)]
    return ranges, unmatched


def split_block(block: Block) -> list[str]:
    """Sub-split one oversized block. Paragraph boundaries first, hard cut last."""
    if block.tokens <= CEILING:
        return [block.text]
    paras = block.text.split("\n\n")
    chunks: list[str] = []
    buf: list[str] = []
    size = 0
    for para in paras:
        ptok = est_tokens(para)
        if buf and size + ptok > CEILING:
            chunks.append("\n\n".join(buf))
            buf, size = [], 0
        buf.append(para)
        size += ptok
    if buf:
        chunks.append("\n\n".join(buf))
    # Hard-split any chunk still over the ceiling.
    hard_limit = int(CEILING * CHARS_PER_TOKEN)
    out: list[str] = []
    for c in chunks:
        if est_tokens(c) <= CEILING:
            out.append(c)
        else:
            for i in range(0, len(c), hard_limit):
                out.append(c[i:i + hard_limit])
    return out


# ── Driver ───────────────────────────────────────────────────────────


def plan_parts(blocks: list[Block], anchors: list[str] | None):
    """Return (parts, method, unmatched) where parts is a list of text strings."""
    if anchors:
        ranges, unmatched = find_override_cuts(blocks, anchors)
        method = "override"
    else:
        ranges, unmatched = pack_auto(blocks), []
        method = "auto"

    parts: list[str] = []
    for (s, e) in ranges:
        if e - s == 1 and blocks[s].tokens > CEILING:
            parts.extend(split_block(blocks[s]))
        else:
            text = "".join(b.text for b in blocks[s:e])
            if est_tokens(text) > CEILING:
                # An override-defined range is still too big — auto-pack within it.
                inner = pack_auto(blocks[s:e])
                for (a, b) in inner:
                    parts.append("".join(blk.text for blk in blocks[s:e][a:b]))
            else:
                parts.append(text)
    return parts, method, unmatched


def breadcrumb(stem: str, idx: int, total: int, blocks_text: str) -> str:
    first = ""
    for line in blocks_text.splitlines():
        if HEADING_RE.match(line):
            first = line.lstrip("#").strip()
            break
    section = f' — section: "{first}"' if first else ""
    return f"<!-- split part {idx}/{total} of {stem}{section} -->\n\n"


def split_markdown(
    md: str, label: str, anchors: list[str] | None = None
) -> tuple[list[str] | None, str | None, list[str]]:
    """Split markdown text into breadcrumbed parts.

    Returns (parts, method, unmatched_anchors). `parts` is a list of strings
    (each prefixed with a breadcrumb) or None when the text fits in one part.
    `label` is what the breadcrumb names the document by — the dev path passes
    the source stem; the prod path passes the file ID. Shared by both paths so
    splitting is identical everywhere.
    """
    if est_tokens(md) <= CEILING and not anchors:
        return None, None, []
    blocks = parse_blocks(md)
    parts, method, unmatched = plan_parts(blocks, anchors)
    if len(parts) <= 1:
        return None, method, unmatched
    bodies = [
        breadcrumb(label, i, len(parts), text) + text
        for i, text in enumerate(parts, 1)
    ]
    return bodies, method, unmatched


def main():
    args = parse_args()
    if not MANIFEST_7.exists():
        print(f"Error: {MANIFEST_7.relative_to(PROJECT_ROOT)} not found. Run step 7 first.")
        return

    manifest_7 = json.loads(MANIFEST_7.read_text())
    overrides = json.loads(OVERRIDES_PATH.read_text()) if OVERRIDES_PATH.exists() else {}

    org_filter = set(args.org) if args.org else None
    year_filter = set(args.year) if args.year else None
    country_filter = set(args.country) if args.country else None

    records = []
    split_count = 0
    part_total = 0
    unmatched_report: list[str] = []

    for entry in manifest_7["files"]:
        # Only markdown is splittable. Pass everything else through untouched.
        out_path = entry.get("output_path", "")
        if entry["status"] not in ("ok", "copied_md") or not out_path.endswith(".md"):
            records.append(_passthrough(entry))
            continue

        org = entry["org"]
        folder = entry["country_folder"]
        m = YEAR_RE.search(entry["year"])
        pub_year = m.group(1) if m else None
        if org_filter and org not in org_filter:
            records.append(_passthrough(entry)); continue
        if year_filter and pub_year and pub_year not in year_filter:
            records.append(_passthrough(entry)); continue
        if country_filter and folder not in country_filter:
            records.append(_passthrough(entry)); continue

        src = PROJECT_ROOT / out_path
        if not src.exists():
            records.append(_passthrough(entry, status="missing")); continue

        md = src.read_text(encoding="utf-8")
        total_tokens = est_tokens(md)
        anchors = overrides.get(out_path) or overrides.get(src.name)

        if total_tokens <= CEILING and not anchors:
            records.append(_passthrough(entry, est_tokens=total_tokens))
            continue

        bodies, method, unmatched = split_markdown(md, src.stem, anchors)
        for a in unmatched:
            unmatched_report.append(f"{src.name}: anchor not found: {a!r}")

        if not bodies:
            # Nothing to gain (e.g. one giant unsplittable block under override).
            records.append(_passthrough(entry, est_tokens=total_tokens))
            continue

        rel = src.relative_to(MARKDOWN_DIR)
        stem = src.stem
        part_records = []
        for i, body in enumerate(bodies, 1):
            part_name = f"{stem}.p{i:02d}.md"
            dest = SPLIT_DIR / rel.parent / part_name
            if not args.dry_run and (args.force or not dest.exists()):
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(body, encoding="utf-8")
            part_records.append({
                "path": str(dest.relative_to(PROJECT_ROOT)),
                "part_index": i,
                "part_total": len(bodies),
                "est_tokens": est_tokens(body),
            })

        split_count += 1
        part_total += len(part_records)
        rec = _passthrough(entry, est_tokens=total_tokens)
        rec["split"] = True
        rec["split_method"] = method
        rec["parts"] = part_records
        records.append(rec)

    manifest = {
        "step": "7b",
        "name": "split",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "target_tokens": TARGET_TOKENS,
        "files": records,
        "summary": {
            "total": len(records),
            "split": split_count,
            "parts_produced": part_total,
            "unsplit": len(records) - split_count,
        },
    }

    print(f"Files: {len(records)}  |  split: {split_count}  ->  {part_total} parts")
    if unmatched_report:
        print(f"\n{len(unmatched_report)} unmatched override anchors:")
        for u in unmatched_report:
            print(f"  {u}")

    if args.dry_run:
        print("\n[dry-run] No files written, no manifest written.")
        return

    MANIFESTS_DIR.mkdir(parents=True, exist_ok=True)
    MANIFEST_7B.write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"Manifest written to {MANIFEST_7B.relative_to(PROJECT_ROOT)}")


def _passthrough(entry: dict, status: str | None = None, est_tokens: int | None = None) -> dict:
    """A record for a file that is not split: parts points at the step-7 output."""
    r = {
        "input_path": entry.get("input_path"),
        "output_path": entry.get("output_path"),
        "org": entry.get("org"),
        "type": entry.get("type"),
        "year": entry.get("year"),
        "country_folder": entry.get("country_folder"),
        "status": status or entry.get("status"),
        "split": False,
        "split_method": None,
        "parts": [{
            "path": entry.get("output_path"),
            "part_index": 1,
            "part_total": 1,
            "est_tokens": est_tokens,
        }],
    }
    return r


if __name__ == "__main__":
    main()

"""
Lean single-pass materializer for the prod output.

Reuses the dev pipeline's own logic helpers (standardise_names, convert_to_markdown,
filter_files, organise) so the result matches the dev pipeline, but writes each
leaf node exactly once instead of copying bytes through split_wr -> standardised
-> filtered -> samples_readable:

  output/samples_readable/{org}/{type}/{folder}/{ID}.{ext}   readable version
  output/samples_llm/{org}/{type}/{folder}/{ID}.{ext}        markdown (or passthrough)
  output/sources/{org}/{world_reports|country_reports}/...   provenance

  - WR per-country PDFs are split straight to their final ID path.
  - CR/SR/CP standalone readables are symlinked into input/ (--copy to materialise).
  - Double-layout WR are unsplit into intermediate/unsplit/ (scratch, removed after).
  - Oversized LLM markdown is split into {ID}-p1.md ... {ID}-pN.md (step 7b logic),
    replacing the whole; the readable stays a single file.

This module is driven by pipeline/run.py; see build() for the entry point.
"""

import json
import os
import re
import shutil
import subprocess
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

from config import SOURCES, extract_year_full, sanitize_filename
from convert_to_markdown import convert_html, convert_pdf, extract_pub_year
from filter_files import extract_report_year, load_csv_filter
from organise import build_id
from pypdf import PdfReader, PdfWriter
from split_large import OVERRIDES_PATH, split_markdown
from standardise_names import (
    _cr_type,
    build_cr_filename,
    get_folder,
    load_maps,
    parse_cr_stem,
    resolve_entity,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
INPUT_CR = PROJECT_ROOT / "input" / "cr"
READABLE_DIR = PROJECT_ROOT / "output" / "samples_readable"
LLM_DIR = PROJECT_ROOT / "output" / "samples_llm"
SOURCES_DIR = PROJECT_ROOT / "output" / "sources"
UNSPLIT_DIR = PROJECT_ROOT / "intermediate" / "unsplit"

WR_ORGS = list(SOURCES)  # ai, hrw, idmc
CR_ORGS = ["ai", "hrw", "idmc", "us"]

YEAR_PREFIX_RE = re.compile(r"^\d{4}(?:\(\d{4}\))?_")
WR_SUFFIX_RE = re.compile(r"^(.+)_([a-zA-Z])$")


@dataclass
class Target:
    org: str
    type: str  # WR / CR / SR / CP
    year: str  # raw year string, YYYY or YYYY(YYYY)
    entity: str
    folder: str
    src_ext: str
    # readable provenance
    kind: str = "standalone"  # "wr_split" or "standalone"
    src_file: Path | None = None  # standalone: file to symlink/convert
    src_pdf: Path | None = None  # wr_split: parent pdf to cut
    page_start: int | None = None  # 1-indexed inclusive
    page_end: int | None = None
    pub_year: int | None = None
    is_profile: bool = False
    suffix: str | None = None  # assigned in suffix pass
    # filled at materialise time
    id: str = ""

    @property
    def key(self) -> tuple:
        return (self.org, self.type, self.year, self.entity)

    @property
    def std_filename(self) -> str:
        """The step-5 standardised filename (used as the suffix sort key)."""
        if self.type == "WR":
            base = self.entity + (f"_{self.suffix}" if self.suffix else "")
            return base + self.src_ext
        return build_cr_filename(
            self.year, self.entity, self.suffix, self.is_profile, self.src_ext
        )


# --------------------------------------------------------------------------- #
# Planning                                                                     #
# --------------------------------------------------------------------------- #
def plan_wr(maps, sources_out: list[dict]) -> list[Target]:
    v2s, e2f, general, known = maps
    targets: list[Target] = []
    page_counts: dict[str, int] = {}

    for org in WR_ORGS:
        cfg = SOURCES[org]
        if not cfg.split_config_path.exists():
            continue
        split_config = json.loads(cfg.split_config_path.read_text())

        for pdf_name, entry in sorted(split_config.items()):
            year = extract_year_full(pdf_name)
            pub_year = extract_pub_year(year)
            source_path = Path(entry["source_path"])

            if entry.get("pre_split"):
                # already per-country files in a folder; treat as standalone WR
                if not source_path.is_dir():
                    continue
                # this folder is the source/provenance for these splits
                sources_out.append(
                    {"org": org, "bucket": "world_reports", "path": source_path}
                )
                for f in sorted(source_path.iterdir()):
                    if f.name.startswith(".") or not f.is_file():
                        continue
                    stem = Path(YEAR_PREFIX_RE.sub("", f.name)).stem
                    raw, suffix = _strip_letter_suffix(stem)
                    entity = resolve_entity(raw, v2s, known)
                    if entity is None:
                        continue
                    targets.append(
                        Target(
                            org=org,
                            type="WR",
                            year=year,
                            entity=entity,
                            folder=get_folder(entity, e2f, general),
                            src_ext=f.suffix.lower(),
                            kind="standalone",
                            src_file=f,
                            pub_year=pub_year,
                            suffix=suffix,
                        )
                    )
                continue

            # normal split: record the parent pdf as a WR source
            if not source_path.exists():
                continue
            sources_out.append(
                {"org": org, "bucket": "world_reports", "path": source_path}
            )
            countries = entry.get("countries", [])
            total = page_counts.get(str(source_path))
            if total is None:
                total = len(PdfReader(str(source_path)).pages)
                page_counts[str(source_path)] = total

            for i, country in enumerate(countries):
                start = country["true_page"]
                if "end_page" in country:
                    end = country["end_page"]
                elif i + 1 < len(countries):
                    end = max(start, countries[i + 1]["true_page"] - 1)
                else:
                    end = total
                if start < 1 or start > total:
                    continue  # out of range (dev logs error + skips)
                end = min(end, total)

                stem = sanitize_filename(country["name"])
                raw, suffix = _strip_letter_suffix(stem)
                entity = resolve_entity(raw, v2s, known)
                if entity is None:
                    continue
                targets.append(
                    Target(
                        org=org,
                        type="WR",
                        year=year,
                        entity=entity,
                        folder=get_folder(entity, e2f, general),
                        src_ext=".pdf",
                        kind="wr_split",
                        src_pdf=source_path,
                        page_start=start,
                        page_end=end,
                        pub_year=pub_year,
                        suffix=suffix,
                    )
                )
    return targets


def plan_cr(maps, sources_out: list[dict]) -> list[Target]:
    v2s, e2f, general, known = maps
    targets: list[Target] = []

    for org in CR_ORGS:
        org_root = INPUT_CR / org
        if not org_root.is_dir():
            continue
        for dirpath, dirnames, filenames in _sorted_walk(org_root):
            dirnames.sort()
            is_split_files_dir = dirpath.name == "split_files"
            has_split_files_child = "split_files" in dirnames
            if is_split_files_dir:
                is_source = False
            elif has_split_files_child:
                is_source = True
            else:
                is_source = False

            for name in sorted(filenames):
                if name.startswith("."):
                    continue
                f = dirpath / name
                parsed = parse_cr_stem(f.stem)
                if parsed is None:
                    continue
                year_str, raw_entity, suffix, is_profile, _region = parsed

                if is_source:
                    sources_out.append(
                        {"org": org, "bucket": "country_reports", "path": f}
                    )
                    continue

                entity = resolve_entity(raw_entity, v2s, known)
                if entity is None:
                    continue
                targets.append(
                    Target(
                        org=org,
                        type=_cr_type(org, is_profile),
                        year=year_str,
                        entity=entity,
                        folder=get_folder(entity, e2f, general),
                        src_ext=f.suffix.lower(),
                        kind="standalone",
                        src_file=f,
                        pub_year=extract_pub_year(year_str),
                        is_profile=is_profile,
                        suffix=suffix,
                    )
                )
    return targets


def _strip_letter_suffix(stem: str) -> tuple[str, str | None]:
    m = WR_SUFFIX_RE.match(stem)
    if m:
        return m.group(1), m.group(2).lower()
    return stem, None


def _sorted_walk(top: Path):
    for dirpath, dirnames, filenames in os.walk(top):
        yield Path(dirpath), dirnames, filenames


def assign_suffixes(targets: list[Target]) -> None:
    """Replicate organise.assign_suffixes: single keeps parsed suffix, a >=2
    collision group gets fresh a/b/c by standardised filename."""
    groups: dict[tuple, list[Target]] = {}
    for t in targets:
        groups.setdefault(t.key, []).append(t)
    for group in groups.values():
        if len(group) == 1:
            continue  # keep parsed suffix (may be None)
        for i, t in enumerate(sorted(group, key=lambda x: x.std_filename)):
            t.suffix = chr(ord("a") + i)


def apply_filter(targets: list[Target]) -> tuple[list[Target], list[Target]]:
    country_years = load_csv_filter()
    kept, dropped = [], []
    for t in targets:
        if t.folder == "_general":
            kept.append(t)
            continue
        ry = extract_report_year(t.year)
        frj = country_years.get(t.folder)
        if frj is not None and ry is not None and ry >= frj:
            kept.append(t)
        else:
            dropped.append(t)
    return kept, dropped


# --------------------------------------------------------------------------- #
# Materialisation                                                              #
# --------------------------------------------------------------------------- #
def _symlink(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.is_symlink() or dest.exists():
        dest.unlink()
    rel = os.path.relpath(src.resolve(), dest.parent.resolve())
    os.symlink(rel, dest)


def _extract_pages(src_pdf: Path, start: int, end: int, dest: Path) -> None:
    reader = PdfReader(str(src_pdf))
    writer = PdfWriter()
    for idx in range(start - 1, end):
        writer.add_page(reader.pages[idx])
    dest.parent.mkdir(parents=True, exist_ok=True)
    with open(dest, "wb") as fh:
        writer.write(fh)


def materialize(
    targets: list[Target], copy: bool, dry_run: bool, workers: int | None = None
) -> dict:
    stats = {"readable": 0, "md": 0, "passthrough": 0, "errors": 0,
             "split_docs": 0, "parts": 0}
    pdf_jobs: list[tuple[str, str]] = []  # (readable_pdf, md_dest) for the pool

    # ---- pass 1: readables + fast llm ops; collect slow pdf->md jobs ----
    for t in targets:
        t.id = build_id(t.org, t.type, t.year, t.entity, t.suffix)
        tl = t.type.lower()
        readable_dest = READABLE_DIR / t.org / tl / t.folder / f"{t.id}{t.src_ext}"

        if dry_run:
            stats["readable"] += 1
            continue
        try:
            # ---- readable ----
            if t.kind == "wr_split":
                assert t.src_pdf is not None
                assert t.page_start is not None and t.page_end is not None
                _extract_pages(t.src_pdf, t.page_start, t.page_end, readable_dest)
            else:
                assert t.src_file is not None
                if copy:
                    readable_dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(t.src_file, readable_dest)
                else:
                    _symlink(t.src_file, readable_dest)
            stats["readable"] += 1

            # ---- llm (markdown or passthrough) ----
            job = _plan_llm(t, readable_dest, copy, stats)
            if job is not None:
                pdf_jobs.append(job)
        except Exception as exc:  # noqa: BLE001
            stats["errors"] += 1
            print(f"  ERROR {t.id}: {exc}")

    # ---- pass 2: convert pdf->md in parallel ----
    if pdf_jobs:
        print(f"Converting {len(pdf_jobs)} PDFs to markdown...")
        with ProcessPoolExecutor(max_workers=workers) as ex:
            futures = {ex.submit(convert_pdf, src, dst): dst for src, dst in pdf_jobs}
            for fut in as_completed(futures):
                try:
                    fut.result()
                    stats["md"] += 1
                except Exception as exc:  # noqa: BLE001
                    stats["errors"] += 1
                    print(f"  ERROR convert {futures[fut]}: {exc}")
    return stats


def _plan_llm(
    t: Target, readable_dest: Path, copy: bool, stats: dict
) -> tuple[str, str] | None:
    """Materialise the fast llm cases immediately; return a (src, dest) pair for
    a pdf->md conversion that should run in the pool, else None."""
    tl = t.type.lower()
    min_year = SOURCES[t.org].min_markdown_year if t.org in SOURCES else None

    def llm_path(ext: str) -> Path:
        return LLM_DIR / t.org / tl / t.folder / f"{t.id}{ext}"

    if t.src_ext == ".md":
        # .md sources are always standalone files (WR splits are always .pdf)
        assert t.src_file is not None
        dest = llm_path(".md")
        if copy:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(t.src_file, dest)
        else:
            _symlink(t.src_file, dest)
        stats["md"] += 1
        return None
    if t.src_ext == ".html":
        assert t.src_file is not None
        dest = llm_path(".md")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(convert_html(t.src_file), encoding="utf-8")
        stats["md"] += 1
        return None
    if t.src_ext == ".pdf":
        unconvertible = bool(min_year and t.pub_year and t.pub_year < min_year)
        if unconvertible:
            # keep the PDF as the llm version (point at the readable copy)
            _symlink(readable_dest, llm_path(".pdf"))
            stats["passthrough"] += 1
            return None
        dest = llm_path(".md")
        dest.parent.mkdir(parents=True, exist_ok=True)
        return (str(readable_dest), str(dest))  # converted in the pool

    _symlink(readable_dest, llm_path(t.src_ext))
    stats["passthrough"] += 1
    return None


def split_oversized(targets: list[Target], dry_run: bool, stats: dict) -> None:
    """Post-pass: split any oversized LLM markdown into {id}-pN.md parts.

    Runs after materialize() so every {id}.md already exists on disk. Replaces an
    oversized whole with its parts (removing the whole), matching the dev path's
    step 7b + organise. PDFs kept as passthrough have no {id}.md, so are skipped.
    """
    if dry_run:
        return
    overrides = json.loads(OVERRIDES_PATH.read_text()) if OVERRIDES_PATH.exists() else {}
    for t in targets:
        md_path = LLM_DIR / t.org / t.type.lower() / t.folder / f"{t.id}.md"
        if not md_path.exists():
            continue
        md = md_path.read_text(encoding="utf-8")
        anchors = overrides.get(t.id) or overrides.get(f"{t.id}.md")
        bodies, _method, _unmatched = split_markdown(md, t.id, anchors)
        if not bodies:
            continue
        for i, body in enumerate(bodies, 1):
            md_path.with_name(f"{t.id}-p{i}.md").write_text(body, encoding="utf-8")
        md_path.unlink()  # drop the whole; parts replace it (also clears a symlink)
        stats["split_docs"] += 1
        stats["parts"] += len(bodies)


def write_sources(sources_out: list[dict], copy: bool, dry_run: bool) -> int:
    seen: set[tuple] = set()
    n = 0
    for s in sources_out:
        src: Path = s["path"]
        dest = SOURCES_DIR / s["org"] / s["bucket"] / src.name
        sig = (str(dest),)
        if sig in seen:
            continue
        seen.add(sig)
        if dry_run or not src.exists():
            n += 1
            continue
        if src.is_dir():
            continue  # pre_split folder provenance; skip raw copy
        if copy:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
        else:
            _symlink(src, dest)
        n += 1
    return n


# --------------------------------------------------------------------------- #
# Entry point                                                                  #
# --------------------------------------------------------------------------- #
def ensure_unsplit(orgs: list[str], dry_run: bool) -> None:
    """Run the double-layout unsplit step into intermediate/unsplit (scratch)."""
    for org in orgs:
        cfg = SOURCES.get(org)
        if cfg is None or cfg.unsplit_dir is None:
            continue
        if not cfg.unsplit_config_path.exists():
            continue
        if dry_run:
            continue
        subprocess.run(
            [
                sys.executable,
                str(Path(__file__).parent / "unsplit_double_pages.py"),
                org,
            ],
            cwd=PROJECT_ROOT,
            check=False,
        )


def build(
    orgs: list[str],
    do_filter: bool,
    copy: bool,
    dry_run: bool,
    keep_scratch: bool,
    out_root: Path | None = None,
    workers: int | None = None,
) -> int:
    global READABLE_DIR, LLM_DIR, SOURCES_DIR
    if out_root is not None:
        out_root = Path(out_root)
        READABLE_DIR = out_root / "samples_readable"
        LLM_DIR = out_root / "samples_llm"
        SOURCES_DIR = out_root / "sources"

    orgs = orgs or CR_ORGS
    wr_orgs = [o for o in orgs if o in WR_ORGS]

    ensure_unsplit(wr_orgs, dry_run)

    maps = load_maps()
    sources_out: list[dict] = []
    targets: list[Target] = []
    if wr_orgs:
        targets += [t for t in plan_wr(maps, sources_out) if t.org in orgs]
    targets += [t for t in plan_cr(maps, sources_out) if t.org in orgs]

    assign_suffixes(targets)

    if do_filter:
        targets, dropped = apply_filter(targets)
        print(f"Filter: {len(targets)} kept, {len(dropped)} dropped")

    print(f"Planned {len(targets)} targets, {len(sources_out)} source refs.")
    stats = materialize(targets, copy, dry_run, workers)
    split_oversized(targets, dry_run, stats)
    n_src = write_sources(sources_out, copy, dry_run)

    if not dry_run and not keep_scratch and UNSPLIT_DIR.exists():
        shutil.rmtree(UNSPLIT_DIR, ignore_errors=True)

    print(
        f"Done. readable={stats['readable']} md={stats['md']} "
        f"passthrough={stats['passthrough']} "
        f"split={stats['split_docs']}->{stats['parts']} parts "
        f"sources={n_src} errors={stats['errors']}"
    )
    return 1 if stats["errors"] else 0

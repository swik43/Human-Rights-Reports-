"""
World Reports — prod build (the one command).

Turns the input PDFs into the lean output a researcher actually wants:

  output/samples_llm/{org}/{type}/{folder}/{ID}.md      markdown (LLM-readable)
  output/samples_readable/{org}/{type}/{folder}/{ID}.*  readable version
  output/sources/{org}/...                               original reports (provenance)

Each leaf node is produced once: per-country World Report chapters are split
straight to their final path; standalone country reports are symlinked into
input/ (use --copy to materialise real copies); double-layout reports are
unsplit in a scratch dir that's removed afterward. No intermediate copies.

    # Everything, all orgs (all countries, no research filter):
    python pipeline/run.py

    # Preview the plan without writing anything:
    python pipeline/run.py --dry-run

    # Apply the conflict-years country/year filter (research subset):
    python pipeline/run.py --filter

    # Materialise readables as real copies instead of symlinks:
    python pipeline/run.py --copy

This is the prod entry point. To run the full dev pipeline with all
intermediate stages and manifests, run the individual pipeline/*.py step
scripts in order (see pipeline_plan.md).
"""

import argparse
import sys
from pathlib import Path

from config import SOURCES
from prod_build import build

ALL_ORGS = list(SOURCES) + ["us"]


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Build the lean prod output (input PDFs -> markdown).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--orgs", nargs="+", choices=ALL_ORGS, metavar="ORG",
                   help=f"Limit to these orgs (default: all -> {' '.join(ALL_ORGS)})")
    p.add_argument("--filter", action="store_true", dest="do_filter",
                   help="Apply the conflict-years country/year filter (research subset)")
    p.add_argument("--copy", action="store_true",
                   help="Copy readable files instead of symlinking them into input/")
    p.add_argument("--dry-run", action="store_true",
                   help="Plan only; write nothing")
    p.add_argument("--keep-scratch", action="store_true",
                   help="Don't delete intermediate/unsplit/ after building")
    p.add_argument("--out-root", type=Path, default=None,
                   help="Write under this dir instead of output/ (for testing)")
    p.add_argument("--workers", type=int, default=None,
                   help="Parallel markdown-conversion workers (default: all cores)")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    return build(
        orgs=args.orgs,
        do_filter=args.do_filter,
        copy=args.copy,
        dry_run=args.dry_run,
        keep_scratch=args.keep_scratch,
        out_root=args.out_root,
        workers=args.workers,
    )


if __name__ == "__main__":
    sys.exit(main())

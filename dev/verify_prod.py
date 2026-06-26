"""Check the prod build split the same set as the dev path, and diagnose diffs.

Compares -pN parts under a prod out-root against the dev output, and for every
mismatch reports whether the doc is ABSENT from the other tree (file-set diff)
or PRESENT-BUT-WHOLE (markdown size differs -> threshold tipping).

Usage: python verify_prod.py [out_root]   (default: /tmp/prod_test)
"""

import sys
from collections import Counter
from pathlib import Path

out_root = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/prod_test")
prod_llm = out_root / "samples_llm"
dev_llm = Path("output/samples_llm")


def is_part(stem: str) -> bool:
    return "-p" in stem and stem.rsplit("-p", 1)[1].isdigit()


def part_docs(root: Path) -> dict[str, int]:
    """base ID -> part count, from {id}-pN.md files."""
    docs: dict[str, int] = {}
    for p in root.rglob("*-p*.md"):
        if is_part(p.stem):
            base = p.stem.rsplit("-p", 1)[0]
            docs[base] = docs.get(base, 0) + 1
    return docs


def all_doc_ids(root: Path) -> set[str]:
    """Every LLM doc id present, whether whole {id}.md or split into parts."""
    ids: set[str] = set()
    for p in root.rglob("*.md"):
        ids.add(p.stem.rsplit("-p", 1)[0] if is_part(p.stem) else p.stem)
    return ids


def org(base: str) -> str:
    return base.split("-", 1)[0] if "-" in base else "?"


prod, dev = part_docs(prod_llm), part_docs(dev_llm)
prod_all, dev_all = all_doc_ids(prod_llm), all_doc_ids(dev_llm)
prod_md = sum(1 for _ in prod_llm.rglob("*.md"))
dev_md = sum(1 for _ in dev_llm.rglob("*.md"))

print(f"prod: {len(prod):3d} split docs -> {sum(prod.values()):3d} parts | "
      f"{prod_md} md files | {len(prod_all)} docs total")
print(f"dev:  {len(dev):3d} split docs -> {sum(dev.values()):3d} parts | "
      f"{dev_md} md files | {len(dev_all)} docs total")

only_dev = set(dev) - set(prod)
only_prod = set(prod) - set(dev)


def classify(label: str, only: set[str], other_all: set[str]) -> None:
    absent = [b for b in only if b not in other_all]
    whole = [b for b in only if b in other_all]
    print(f"\n{label}: {len(only)} docs")
    print(f"  absent from other tree: {len(absent):3d}  by org: {dict(Counter(org(b) for b in absent))}")
    print(f"  present but NOT split:  {len(whole):3d}  by org: {dict(Counter(org(b) for b in whole))}")
    for b in sorted(absent)[:6]:
        print(f"    absent: {b}")
    for b in sorted(whole)[:6]:
        print(f"    whole:  {b}")


classify("dev-only split docs", only_dev, prod_all)
classify("prod-only split docs", only_prod, dev_all)

stale = [p for p in prod_llm.rglob("*.md")
         if not is_part(p.stem) and p.with_name(f"{p.stem}-p1.md").exists()]
print(f"\nstale whole files alongside parts: {len(stale)}")
ok = not stale and not only_dev and not only_prod
print("OK — prod matches dev" if ok else "MISMATCH — see breakdown above")

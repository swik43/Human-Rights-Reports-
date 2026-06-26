"""Quick check that step 7b (split) and step 8 (organise) agree. Throwaway."""

import json
from pathlib import Path

b = json.load(open("manifests/7b_split.json"))["summary"]
o = json.load(open("manifests/8_organised.json"))["files"]

parts = [r for r in o if r.get("part_of")]
split_docs = len(set(r["part_of"] for r in parts))
missing = [r["llm_path"] for r in parts if not Path(r["llm_path"]).exists()]
no_readable = [r["id"] for r in parts if not r.get("readable_path")]

print(f"7b:       split={b['split']} docs -> {b['parts_produced']} parts")
print(f"organise: {len(parts)} part records, {split_docs} split docs")
print(f"missing part files on disk: {len(missing)}")
print(f"parts missing a readable:   {len(no_readable)}")

ok = (b["parts_produced"] == len(parts)) and not missing and not no_readable
print("\n" + ("OK — everything agrees" if ok else "MISMATCH — see numbers above"))

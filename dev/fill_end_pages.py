"""Fill in missing end_page values in data/idmc/3_overrides.json.

For each PDF's list of entries, if an entry has no end_page,
sets it to the next entry's true_page. The last entry in each
PDF is left unchanged.
"""

import json
from pathlib import Path

OVERRIDES = Path(__file__).resolve().parent.parent / "config" / "idmc" / "3_overrides.json"


def main():
    data = json.loads(OVERRIDES.read_text())

    for pdf, entries in data.items():
        for i, entry in enumerate(entries):
            if "end_page" not in entry and i < len(entries) - 1:
                entry["end_page"] = entries[i + 1]["true_page"]

    OVERRIDES.write_text(json.dumps(data, indent=2) + "\n")
    print("Done — end_page filled where missing.")


if __name__ == "__main__":
    main()

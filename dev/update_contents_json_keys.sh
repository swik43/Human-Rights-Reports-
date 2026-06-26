#!/bin/bash
# Updates the internal PDF key in each contents_json file from the old
# single-year format (e.g. 2004_World_Report_Human_Rights_Watch.pdf)
# to the new two-year format (e.g. 2005(2004)_World_Report_Human_Rights_Watch.pdf)

DIR="$(dirname "$0")/../config/hrw/contents_json"

for f in "$DIR"/*_World_Report_Human_Rights_Watch.json; do
  year=$(grep -oE '"[0-9]{4}_World_Report' "$f" | grep -oE '[0-9]{4}')
  if [ -n "$year" ]; then
    new_year=$((year + 1))
    sed -i '' "s/\"${year}_World_Report_Human_Rights_Watch.pdf\"/\"${new_year}(${year})_World_Report_Human_Rights_Watch.pdf\"/" "$f"
    echo "Updated $f: ${year} -> ${new_year}(${year})"
  fi
done

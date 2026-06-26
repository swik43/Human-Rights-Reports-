#!/bin/sh

kind=$1
year=$2

case "$kind" in
  hrw)  suffix="World_Report_Human_Rights_Watch" ;;
  ai)   suffix="Amnesty_International" ;;
  idmc) suffix="IDMC" ;;
  *)    echo "Unknown kind: $kind (use 'hrw', 'ai', or 'idmc')" && exit 1 ;;
esac

$EDITOR "config/${kind}/contents_json/${year}_${suffix}.json"

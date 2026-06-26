I will provide you with the contents pages from a Human Right's Watch annual report PDF. Extract every country name and its page number as listed.
Return ONLY valid JSON in this exact format — no commentary:

```json
{
  "FILENAME.pdf": [
    { "name": "Afghanistan", "report_page": 50 },
    { "name": "Albania", "report_page": 54 }
  ]
}
```

Rules:
* Use the country name exactly as printed, converted to Title Case
* "report_page" is the page number shown next to the country name in the contents
* Only include country entries — skip headers like "Foreword", "Regional Overview", "Preface", "Abbreviations", etc.
* Maintain the order as listed in the contents
* If multiple columns, read the columns top-to-bottom, left-to-right
* I will provide the filename prefix each time. Append `_World_Report_Human_Rights_Watch.pdf` to the prefix unless the prefix already contains `_World_Report_Human_Rights_Watch`, in which case just append `.pdf`

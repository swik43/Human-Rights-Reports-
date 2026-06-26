Extract specific page ranges from a PDF into one or more output files.

Use pypdf. Ask the user for:
- The input PDF path
- One or more output files with their page ranges (1-indexed, inclusive)

Example input format:
```
output/foo.pdf: 2-4
output/bar.pdf: 5-7
```

```python
from pypdf import PdfReader, PdfWriter

reader = PdfReader(input_path)
for name, start, end in items:
    writer = PdfWriter()
    for i in range(start - 1, end):
        writer.add_page(reader.pages[i])
    writer.write(output_path)
```

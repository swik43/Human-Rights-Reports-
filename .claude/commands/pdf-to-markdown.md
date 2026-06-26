Convert one or more PDFs to Markdown files.

Use pymupdf4llm. Ask the user for:
- The input PDF path(s) or a glob pattern
- The output location (default: same directory, .md extension)

```python
import pymupdf4llm
from pathlib import Path

md = pymupdf4llm.to_markdown(str(pdf_path))
Path(output_path).write_text(md)
```

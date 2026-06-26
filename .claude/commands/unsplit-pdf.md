Unsplit a double-layout PDF (two printed pages side-by-side per PDF page) into a single-page-per-sheet PDF.

Use pypdf to crop each page into left and right halves. Ask the user for:
- The input PDF path
- The output PDF path
- Which page the double layout starts on (default: 1)

Pages before `double_start` are kept as-is (e.g. a single-page cover).

```python
from copy import deepcopy
from pypdf import PdfReader, PdfWriter

def split_page_halves(page):
    left = deepcopy(page)
    right = deepcopy(page)
    box = page.mediabox
    mid_x = (box.left + box.right) / 2
    left.mediabox.upper_right = (mid_x, box.top)
    right.mediabox.upper_left = (mid_x, box.top)
    right.mediabox.lower_left = (mid_x, box.bottom)
    return left, right
```

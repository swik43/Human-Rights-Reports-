#!/usr/bin/env python3
"""Clean HRW World Report HTML files to only contain the country heading and article text."""

from pathlib import Path
from bs4 import BeautifulSoup, Comment


def build_page(title, content):
    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>{title}</title>
<style>
body {{ font-family: Arial, Helvetica, sans-serif; max-width: 800px; margin: 40px auto; padding: 0 20px; line-height: 1.6; color: #222; }}
h1 {{ color: #333; }}
h4, h5 {{ color: #555; }}
p {{ margin-bottom: 1em; }}
a {{ color: #CC6608; }}
</style>
</head>
<body>
{content}
</body>
</html>"""


def clean_2001(soup, filepath):
    country_name = filepath.stem
    title_tag = soup.find("title")
    page_title = title_tag.get_text().strip() if title_tag else country_name

    # Content is in the TD with rowspan=4 and valign=TOP
    content_td = None
    for td in soup.find_all("td"):
        rowspan = str(td.get("rowspan", td.get("ROWSPAN", "")))
        valign = str(td.get("valign", td.get("VALIGN", ""))).upper()
        if rowspan == "4" and valign == "TOP":
            content_td = td
            break

    if not content_td:
        print(f"  WARNING: could not find content TD in {filepath.name}")
        return None

    # Remove section links UL (first ul — points to chile2.html etc. which don't exist here)
    first_ul = content_td.find("ul")
    if first_ul:
        first_ul.decompose()

    # Remove all IMG tags
    for img in content_td.find_all("img"):
        img.decompose()

    inner_html = content_td.decode_contents().strip()
    return build_page(page_title, f"<h1>{country_name}</h1>\n{inner_html}")


def clean_2002_2003(soup, filepath):
    title_tag = soup.find("title")
    page_title = title_tag.get_text().strip() if title_tag else filepath.stem

    # Content is in the first TD with class="color2"
    content_td = soup.find("td", class_="color2")
    if not content_td:
        print(f"  WARNING: could not find content TD in {filepath.name}")
        return None

    # Remove all IMG tags (blank spacers, bullet icons, map images)
    for img in content_td.find_all("img"):
        img.decompose()

    # Remove script tags
    for script in content_td.find_all("script"):
        script.decompose()

    # Remove SSI include comments (<!--#include virtual="..."-->)
    for comment in content_td.find_all(string=lambda t: isinstance(t, Comment)):
        comment.extract()

    inner_html = content_td.decode_contents().strip()
    return build_page(page_title, inner_html)


def clean_file(filepath, year):
    with open(filepath, "r", encoding="utf-8", errors="replace") as f:
        content = f.read()

    soup = BeautifulSoup(content, "html.parser")

    if year == 2001:
        result = clean_2001(soup, filepath)
    else:
        result = clean_2002_2003(soup, filepath)

    if result:
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(result)
        return True
    return False


def main():
    base_dir = Path(__file__).parent.parent / "output" / "hrw"

    for year in [2001, 2002, 2003]:
        year_dir = base_dir / str(year)
        if not year_dir.exists():
            print(f"Directory {year_dir} does not exist, skipping")
            continue

        html_files = sorted(year_dir.glob("*.html"))
        print(f"\n{year}: {len(html_files)} files")

        ok, failed = 0, 0
        for filepath in html_files:
            if clean_file(filepath, year):
                ok += 1
            else:
                failed += 1
                print(f"  FAILED: {filepath.name}")

        print(f"  {ok} cleaned, {failed} failed")


if __name__ == "__main__":
    main()

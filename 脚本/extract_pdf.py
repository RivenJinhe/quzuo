import sys
import fitz

src = sys.argv[1]
dst = sys.argv[2]
doc = fitz.open(src)
parts = []
for i, page in enumerate(doc):
    txt = page.get_text("text", sort=True)
    parts.append("\n<!-- page %d -->\n" % (i + 1) + txt)
body = "".join(parts)
with open(dst, "w", encoding="utf-8") as f:
    f.write("# PRD v0.2.4 PDF text\n\n")
    f.write(body)
print("pages:", doc.page_count)
print("chars:", len(body))

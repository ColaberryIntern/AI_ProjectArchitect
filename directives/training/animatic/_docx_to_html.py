"""Convert requirements.docx -> clean HTML for posting to Basecamp."""
import mammoth
from pathlib import Path

DOCX = Path(r"c:\Users\ali_m\OneDrive\Business\Colaberry Novedea\AI Projects\Colaberry Enterprise AI Leadership Accelerator\gov-bid-builds\utd-residential-life\requirements.docx")
OUT = Path(__file__).parent / "_utd_requirements.html"

with DOCX.open("rb") as f:
    result = mammoth.convert_to_html(f)

html = result.value
# Add a header noting source/provenance for BC reader
provenance = (
    "<p><em>Source: AI Project Architect, Professional mode  &middot;  "
    "Generated 2026-06-05  &middot;  "
    "Job: utd-residential-life-platform-colaberry-build</em></p>"
    "<hr/>"
)
OUT.write_text(provenance + html, encoding="utf-8")

print(f"OK -> {OUT}")
print(f"HTML length: {len(html):,} chars")
if result.messages:
    print(f"Conversion warnings ({len(result.messages)}):")
    for m in result.messages[:10]:
        print(f"  - {m.type}: {m.message}")

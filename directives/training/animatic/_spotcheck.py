"""Render a single slide to disk so we can eyeball the mockup style."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import build_animatic as ba

data = json.loads((ba.HERE / "scenes.json").read_text(encoding="utf-8"))

# render four exemplars covering distinct mockup types
samples = [
    ("my-day-highlighted", data["videos"][2], 1),       # 03-my-day s2: queue with highlight
    ("library-install-highlighted", data["videos"][1], 3),  # 02-library s4: install panel highlighted
    ("problem-diagram", data["videos"][0], 1),          # 01-intro s2: problem diagram
    ("bc-todo-attribution", data["videos"][3], 4),      # 04-connect s5: via tag
]

for label, video, idx in samples:
    scene = video["scenes"][idx]
    out = ba.HERE / f"_spotcheck-{label}.png"
    ba.render_slide(video["title"], video["subtitle"], scene, idx + 1, len(video["scenes"]), out)
    print(f"OK -> {out.name}")

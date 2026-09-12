from pathlib import Path

root = Path(__file__).resolve().parent
html = (root / "index.html").read_text(encoding="utf-8")
css = (root / "styles.css").read_text(encoding="utf-8")
required = ['id="features"', 'Fast', 'Safe', 'Local']
missing = [item for item in required if item not in html]
if missing:
    raise SystemExit("FAIL missing from index.html: " + ", ".join(missing))
if ".feature" not in css:
    raise SystemExit("FAIL expected feature card styles in styles.css")
print("PASS: feature section and styles detected")

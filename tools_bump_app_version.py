from pathlib import Path

path = Path("mod_app.py")
text = path.read_text("utf-8")
if "VERSION = '0.12.0'" in text:
    text = text.replace("VERSION = '0.12.0'", "VERSION = '0.14.1'")
    path.write_text(text, "utf-8", newline="\n")
print([line for line in text.splitlines() if line.startswith("VERSION")])

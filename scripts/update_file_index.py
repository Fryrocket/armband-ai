#!/usr/bin/env python3
"""Single-repo File index updater. Used by the GitHub Action."""
from __future__ import annotations
import os, re, sys
from pathlib import Path

DESCRIPTIONS = {
    "src/armband_ai/calibration.py": "fingerstick/Libre pairing, build_calibration_pairs, fit_multifeature",
    "src/armband_ai/config.py": "YAML config loading and defaults",
    "src/armband_ai/db.py": "SQLite writes, insert-time soft validation",
    "src/armband_ai/drift_monitor.py": "still-only rolling median of filt940 vs baseline",
    "src/armband_ai/features.py": "17-float feature vector, clean streak",
    "src/armband_ai/hailo.py": "Hailo HEF inference path",
    "src/armband_ai/inference_service.py": "CPU/MLP/ONNX/Hailo priority",
    "src/armband_ai/logger.py": "MQTT logger, iOS batch receiver + ACK",
    "src/armband_ai/quality.py": "raw-window quality gates",
    "src/armband_ai/queries.py": "read helpers, init_db",
    "dashboard/app.py": "Streamlit live dashboard",
}

GROUP_RULES = [
    ("Package — `src/armband_ai/`", ["src/armband_ai/"]),
    ("Dashboard", ["dashboard/"]),
    ("Docs", ["docs/", "PINOUT.md", "SETUP.md", "NOTES.md", "HARDWARE.md"]),
    ("Scripts", ["scripts/"]),
    ("Systemd units", ["systemd/"]),
    ("Config", ["config.example.yaml", "requirements.txt", "LICENSE", ".gitignore"]),
    ("Root", ["LICENSE", ".gitignore"]),
]

SKIP = {".git", ".DS_Store", "__pycache__", ".venv", "node_modules", ".pytest_cache", ".github"}

def list_files(repo: Path):
    out = []
    for root, dirs, names in os.walk(repo):
        dirs[:] = [d for d in dirs if d not in SKIP and not d.startswith(".")]
        for name in names:
            if name in SKIP or (name.startswith(".") and name != ".gitignore"):
                continue
            rel = os.path.relpath(os.path.join(root, name), repo).replace(os.sep, "/")
            if rel == "README.md":
                continue
            out.append(rel)
    return sorted(out)

def group(files):
    used, groups = set(), {}
    for heading, prefixes in GROUP_RULES:
        matched = []
        for f in files:
            if f in used: continue
            for p in prefixes:
                if f == p or f.startswith(p):
                    matched.append(f); used.add(f); break
        if matched:
            groups[heading] = matched
    rem = [f for f in files if f not in used]
    if rem: groups["Other"] = rem
    order = [h for h,_ in GROUP_RULES] + ["Other"]
    return [(h, sorted(groups[h])) for h in order if h in groups]

def render(groups):
    lines = ["## File index", ""]
    for heading, files in groups:
        lines.append(f"**{heading}**")
        for f in files:
            d = DESCRIPTIONS.get(f, "")
            lines.append(f"- [{f}]({f}) — {d}" if d else f"- [{f}]({f})")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"

def replace(text, block):
    pat = re.compile(r"(^## File index\s*\n)(.*?)(?=^## |\Z)", re.M | re.S)
    if pat.search(text):
        return pat.sub(block + "\n", text, count=1)
    lic = re.compile(r"(^## License.*)", re.M | re.S)
    m = lic.search(text)
    if m:
        return text[:m.start()] + block + "\n" + text[m.start():]
    return text.rstrip() + "\n\n" + block

def main():
    repo = Path(".").resolve()
    readme = repo / "README.md"
    if not readme.is_file():
        print("No README.md"); return 1
    files = list_files(repo)
    block = render(group(files))
    original = readme.read_text(encoding="utf-8")
    updated = replace(original, block)
    if updated == original:
        print("File index already up to date")
        return 0
    readme.write_text(updated, encoding="utf-8")
    print(f"Updated File index ({len(files)} files)")
    return 0

if __name__ == "__main__":
    sys.exit(main())

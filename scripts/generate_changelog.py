#!/usr/bin/env python3
"""Generate CHANGELOG.md from git history (Keep a Changelog + conventional commits).

Usage:
  python3 scripts/generate_changelog.py              # write CHANGELOG.md
  python3 scripts/generate_changelog.py --dry-run    # print only
  python3 scripts/generate_changelog.py --since v0.4.0
  python3 scripts/generate_changelog.py --max 200

Commit style (optional but recommended):
  feat: add BLE discovery
  fix(ios): settle partial ACK batches
  docs: update SETUP_CHECKLIST
  chore: bump file index
  refactor(quality): score raw window first
  perf: reduce deep-sleep current
  breaking: change MQTT payload schema

Plain commits are grouped under "Other".
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

TYPE_ORDER = [
    ("breaking", "Breaking Changes"),
    ("feat", "Features"),
    ("fix", "Bug Fixes"),
    ("perf", "Performance"),
    ("refactor", "Refactoring"),
    ("docs", "Documentation"),
    ("test", "Tests"),
    ("build", "Build"),
    ("ci", "CI"),
    ("chore", "Chores"),
    ("other", "Other"),
]

TYPE_ALIASES = {
    "feature": "feat",
    "bugfix": "fix",
    "bug": "fix",
    "doc": "docs",
    "documentation": "docs",
    "performance": "perf",
    "style": "chore",
    "misc": "other",
}

SKIP_SUBJECTS = re.compile(
    r"^(auto-update|chore:\s*auto-update|update file index|merge |bump version)",
    re.I,
)

CONV = re.compile(
    r"^(?P<type>[a-zA-Z]+)(?:\((?P<scope>[^)]+)\))?(?P<breaking>!)?:\s*(?P<subject>.+)$"
)


def run_git(args: list[str]) -> str:
    try:
        out = subprocess.check_output(
            ["git", *args],
            stderr=subprocess.DEVNULL,
            text=True,
        )
        return out.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ""


def parse_commits(since: str | None, max_count: int) -> list[dict]:
    fmt = "%H%x1f%s%x1f%ad%x1f%an"
    args = ["log", f"--pretty=format:{fmt}", "--date=short", f"-n{max_count}"]
    if since:
        args.append(f"{since}..HEAD")
    raw = run_git(args)
    if not raw:
        return []

    commits = []
    for line in raw.splitlines():
        parts = line.split("\x1f")
        if len(parts) < 4:
            continue
        sha, subject, day, author = parts[0], parts[1].strip(), parts[2], parts[3]
        if not subject or SKIP_SUBJECTS.search(subject):
            continue

        m = CONV.match(subject)
        if m:
            ctype = m.group("type").lower()
            ctype = TYPE_ALIASES.get(ctype, ctype)
            if ctype not in {t for t, _ in TYPE_ORDER}:
                ctype = "other"
            if m.group("breaking"):
                ctype = "breaking"
            scope = m.group("scope")
            text = m.group("subject").strip()
            if scope:
                text = f"**{scope}**: {text}"
        else:
            ctype = "other"
            text = subject

        commits.append(
            {
                "sha": sha[:7],
                "type": ctype,
                "text": text,
                "date": day,
                "author": author,
            }
        )
    return commits


def group_by_date(commits: list[dict]) -> list[tuple[str, list[dict]]]:
    buckets: dict[str, list[dict]] = defaultdict(list)
    for c in commits:
        buckets[c["date"]].append(c)
    return sorted(buckets.items(), key=lambda x: x[0], reverse=True)


def render(commits: list[dict], repo_name: str) -> str:
    today = date.today().isoformat()
    lines = [
        f"# Changelog",
        "",
        f"All notable changes to **{repo_name}** are documented here.",
        "",
        "Format follows [Keep a Changelog](https://keepachangelog.com/).",
        "Commit messages preferably use [Conventional Commits](https://www.conventionalcommits.org/).",
        "",
        f"_Auto-generated {today}. Do not edit by hand — run "
        f"`python3 scripts/generate_changelog.py` or wait for the GitHub Action._",
        "",
        "## [Unreleased]",
        "",
    ]

    if not commits:
        lines.append("_No commits found._")
        lines.append("")
        return "\n".join(lines)

    by_type: dict[str, list[dict]] = defaultdict(list)
    for c in commits:
        by_type[c["type"]].append(c)

    for key, heading in TYPE_ORDER:
        items = by_type.get(key) or []
        if not items:
            continue
        lines.append(f"### {heading}")
        lines.append("")
        for c in items:
            lines.append(f"- {c['text']} (`{c['sha']}`, {c['date']})")
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("## By date")
    lines.append("")
    for day, day_commits in group_by_date(commits):
        lines.append(f"### {day}")
        lines.append("")
        for c in day_commits:
            lines.append(f"- **{c['type']}**: {c['text']} (`{c['sha']}`)")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def detect_repo_name() -> str:
    url = run_git(["config", "--get", "remote.origin.url"])
    if url:
        m = re.search(r"[/:]([\w.-]+)/([\w.-]+?)(?:\.git)?$", url)
        if m:
            return m.group(2)
    return Path(".").resolve().name


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate CHANGELOG.md from git log")
    ap.add_argument("--dry-run", action="store_true", help="Print to stdout only")
    ap.add_argument("--since", default=None, help="Git ref to start after (e.g. v0.4.0)")
    ap.add_argument("--max", type=int, default=300, help="Max commits to scan")
    ap.add_argument(
        "--output",
        default="CHANGELOG.md",
        help="Output path (default: CHANGELOG.md)",
    )
    args = ap.parse_args()

    if not run_git(["rev-parse", "--is-inside-work-tree"]):
        print("Not a git repository", file=sys.stderr)
        return 1

    commits = parse_commits(args.since, args.max)
    body = render(commits, detect_repo_name())

    if args.dry_run:
        print(body)
        return 0

    path = Path(args.output)
    old = path.read_text(encoding="utf-8") if path.is_file() else ""
    if old == body:
        print("CHANGELOG.md already up to date")
        return 0

    path.write_text(body, encoding="utf-8")
    print(f"Wrote {path} ({len(commits)} commits)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

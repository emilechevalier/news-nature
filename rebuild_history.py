#!/usr/bin/env python3
"""Reconstruit news_history.json à partir des commits git 'news:'.

Usage:
    python3 rebuild_history.py [--dry-run]
"""

import json
import subprocess
import sys
import re
from datetime import datetime
from pathlib import Path

SCRIPT_DIR   = Path(__file__).parent
HISTORY_PATH = SCRIPT_DIR / "news_history.json"

MONTH_FR = {
    "january": "janvier", "february": "février", "march": "mars",
    "april": "avril", "may": "mai", "june": "juin",
    "july": "juillet", "august": "août", "september": "septembre",
    "october": "octobre", "november": "novembre", "december": "décembre",
    # already french
    "janvier": "janvier", "février": "février", "mars": "mars",
    "avril": "avril", "mai": "mai", "juin": "juin",
    "juillet": "juillet", "août": "août", "septembre": "septembre",
    "octobre": "octobre", "novembre": "novembre", "décembre": "décembre",
}


def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=SCRIPT_DIR)
    if r.returncode != 0:
        raise RuntimeError(f"{cmd}: {r.stderr[:200]}")
    return r.stdout.strip()


def get_news_commits():
    """Return list of (hash, subject, iso_date) for commits touching news_data.json."""
    log = run(["git", "log", "--format=%H\t%s\t%ai", "--", "news_data.json"])
    commits = []
    for line in log.splitlines():
        parts = line.split("\t", 2)
        if len(parts) == 3:
            commits.append((parts[0], parts[1], parts[2]))
    return commits


def get_news_at(commit_hash):
    """Return parsed news_data.json at a given commit, or None."""
    try:
        raw = run(["git", "show", f"{commit_hash}:news_data.json"])
        return json.loads(raw)
    except Exception:
        return None


def make_label(subject, iso_date):
    """Derive a human-readable French label from commit subject or date."""
    # Try to extract date from subject: "news: 24 mars 2026" or "news: 15 March 2026"
    m = re.search(r"(\d{1,2})\s+(\w+)\s+(\d{4})", subject, re.IGNORECASE)
    if m:
        day, month_raw, year = m.group(1), m.group(2).lower(), m.group(3)
        month_fr = MONTH_FR.get(month_raw, month_raw)
        return f"{int(day)} {month_fr} {year}"
    # Fallback: parse the git commit date
    try:
        dt = datetime.fromisoformat(iso_date[:19])
        return dt.strftime("%-d %B %Y")
    except Exception:
        return iso_date[:10]


def make_date(subject, iso_date):
    """Return YYYY-MM-DD for deduplication."""
    m = re.search(r"(\d{1,2})\s+(\w+)\s+(\d{4})", subject, re.IGNORECASE)
    if m:
        day, month_raw, year = m.group(1), m.group(2).lower(), m.group(3)
        months = {
            "january": 1, "february": 2, "march": 3, "april": 4,
            "may": 5, "june": 6, "july": 7, "august": 8,
            "september": 9, "october": 10, "november": 11, "december": 12,
            "janvier": 1, "février": 2, "mars": 3, "avril": 4,
            "mai": 5, "juin": 6, "juillet": 7, "août": 8,
            "septembre": 9, "octobre": 10, "novembre": 11, "décembre": 12,
        }
        mn = months.get(month_raw, 1)
        return f"{year}-{mn:02d}-{int(day):02d}"
    return iso_date[:10]


def main():
    dry_run = "--dry-run" in sys.argv

    commits = get_news_commits()
    print(f"{len(commits)} commit(s) trouvés touchant news_data.json")

    history = []
    seen_dates = set()

    for h, subject, iso_date in commits:
        # Only include "news:" commits (actual publications, not fixes)
        if not subject.lower().startswith("news:"):
            print(f"  skip  {h[:8]}  {subject}")
            continue

        date_key = make_date(subject, iso_date)
        if date_key in seen_dates:
            print(f"  dup   {h[:8]}  {subject} ({date_key})")
            continue

        articles = get_news_at(h)
        if not articles:
            print(f"  empty {h[:8]}  {subject}")
            continue

        label = make_label(subject, iso_date)
        history.append({"date": date_key, "label": label, "articles": articles})
        seen_dates.add(date_key)
        print(f"  ok    {h[:8]}  {label}  ({len(articles)} articles)")

    # Most recent first
    history.sort(key=lambda x: x["date"], reverse=True)

    print(f"\n{len(history)} édition(s) reconstruites")

    if dry_run:
        print("(dry-run : rien n'a été écrit)")
        return

    with open(HISTORY_PATH, "w", encoding="utf-8") as f:
        json.dump(history, f, indent=2, ensure_ascii=False)
    print(f"→ {HISTORY_PATH} mis à jour")


if __name__ == "__main__":
    main()

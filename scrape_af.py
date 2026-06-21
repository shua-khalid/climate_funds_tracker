"""
Adaptation Fund change-detection scraper.

What this does, in plain terms:
1. Downloads the AF "Apply For Funding" page.
2. Extracts the main readable text (strips menus/scripts/styles).
3. Compares it to the last saved version (stored in last_snapshot_af.txt).
4. If different, records that a change was detected, with today's date.
5. Writes the result into data.json, which the dashboard (index.html) reads.

This script does NOT try to extract a "deadline" for the Adaptation Fund,
because the live page states proposals are accepted on a rolling basis with
no calendar deadline. Instead it flags when the page's wording changes at
all, since that's the genuinely useful signal for this fund.
"""

import json
import hashlib
import os
import sys
from datetime import datetime, timezone
import urllib.request

AF_URL = "https://www.adaptation-fund.org/apply-funding/"
SNAPSHOT_FILE = "last_snapshot_af.txt"
DATA_FILE = "data.json"


def fetch_page_text(url):
    """Download the page and return a simplified, lowercase text version
    for comparison purposes (not for display)."""
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as response:
        raw_html = response.read().decode("utf-8", errors="ignore")

    # Very simple tag stripping - good enough for change detection,
    # not meant to be a full HTML parser.
    import re
    text = re.sub(r"<script.*?</script>", " ", raw_html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<style.*?</style>", " ", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip().lower()
    return text


def load_existing_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"funds": {}}


def main():
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    try:
        page_text = fetch_page_text(AF_URL)
    except Exception as e:
        print(f"ERROR: could not fetch AF page: {e}", file=sys.stderr)
        # Don't crash the whole workflow - just skip this run.
        sys.exit(0)

    new_hash = hashlib.sha256(page_text.encode("utf-8")).hexdigest()

    previous_hash = None
    if os.path.exists(SNAPSHOT_FILE):
        with open(SNAPSHOT_FILE, "r", encoding="utf-8") as f:
            previous_hash = f.read().strip()

    changed = previous_hash is not None and previous_hash != new_hash
    first_run = previous_hash is None

    # Save new snapshot for tomorrow's comparison
    with open(SNAPSHOT_FILE, "w", encoding="utf-8") as f:
        f.write(new_hash)

    # Update data.json
    data = load_existing_data()
    existing_entry = data["funds"].get("af", {})

    data["funds"]["af"] = {
        "lastChecked": today,
        "lastChanged": today if changed else existing_entry.get("lastChanged"),
        "changedSinceLastView": changed,
        "note": (
            "First scrape - nothing to compare yet. Will detect changes from tomorrow's run."
            if first_run else
            "Page content changed since last check." if changed else
            "No change detected on this check."
        ),
        "sourceUrl": AF_URL,
    }

    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

    print(f"Done. first_run={first_run} changed={changed}")


if __name__ == "__main__":
    main()

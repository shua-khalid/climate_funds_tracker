"""
Adaptation Fund change-detection scraper.

What this does:
1. Downloads the AF "Apply For Funding" page.
2. Strips HTML and extracts simplified text.
3. Compares to yesterday's saved snapshot.
4. If different, flags the change.
5. Extracts the current submission process note as latestContent
   (the most useful piece of static info that changes when AF updates
   its procedures - e.g. rolling vs deadline-based submissions).
6. Writes everything into data.json for the dashboard to read.
"""

import hashlib
import json
import os
import re
import sys
import urllib.request
from datetime import datetime, timezone

AF_URL = "https://www.adaptation-fund.org/apply-funding/"
SNAPSHOT_FILE = "last_snapshot_af.txt"
DATA_FILE = "data.json"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def fetch_raw_html(url):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as response:
        return response.read().decode("utf-8", errors="ignore")


def strip_html(html):
    text = re.sub(r"<script.*?</script>", " ", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<style.*?</style>", " ", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def extract_af_content(text):
    """
    AF has no news feed - it is static guidance text.
    Extract the current submission process note, which is the most
    likely thing to change when the fund updates its procedures.
    Specifically looks for the rolling basis / decision reference sentence.
    """
    lower = text.lower()

    # Look for the rolling submissions note
    rolling_idx = lower.find("rolling basis")
    if rolling_idx != -1:
        snippet = text[max(0, rolling_idx - 60):rolling_idx + 120].strip()
        snippet = re.sub(r"\s+", " ", snippet)
        return f"Submission process: ...{snippet}..."

    # Fallback: look for the Board meeting reference (Decision B.xx/xx)
    decision_match = re.search(r"decision b\.\d+/\d+", lower)
    if decision_match:
        idx = decision_match.start()
        snippet = text[max(0, idx - 80):idx + 80].strip()
        return f"Latest decision reference: {snippet}"

    return "Rolling submissions - no specific deadline. Review page for any procedure updates."


def load_existing_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"funds": {}}


def main():
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    try:
        raw_html = fetch_raw_html(AF_URL)
    except Exception as e:
        print(f"ERROR: could not fetch AF page: {e}", file=sys.stderr)
        sys.exit(0)

    clean_text = strip_html(raw_html)
    new_hash = hashlib.sha256(clean_text.lower().encode("utf-8")).hexdigest()

    previous_hash = None
    if os.path.exists(SNAPSHOT_FILE):
        with open(SNAPSHOT_FILE, "r", encoding="utf-8") as f:
            previous_hash = f.read().strip()

    changed = previous_hash is not None and previous_hash != new_hash
    first_run = previous_hash is None

    with open(SNAPSHOT_FILE, "w", encoding="utf-8") as f:
        f.write(new_hash)

    latest_content = None
    try:
        latest_content = extract_af_content(clean_text)
    except Exception as e:
        latest_content = f"Extraction error: {e}"

    data = load_existing_data()
    existing_entry = data["funds"].get("af", {})

    if first_run:
        note = "First scrape - baseline established. Will detect changes from tomorrow."
    elif changed:
        note = "Page content changed since last check - review for procedure updates."
    else:
        note = "No change detected on this check."

    data["funds"]["af"] = {
        "lastChecked": today,
        "lastChanged": today if changed else existing_entry.get("lastChanged"),
        "changedSinceLastView": changed,
        "note": note,
        "latestContent": latest_content,
        "sourceUrl": AF_URL,
    }

    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

    print(f"Done. first_run={first_run} changed={changed} content={latest_content}")


if __name__ == "__main__":
    main()

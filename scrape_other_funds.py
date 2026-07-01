"""
Multi-fund change-detection scraper: GCF, CIF, GEF, FRLD.

Same approach as scrape_af.py, extended to loop over several funds instead
of just one. For each fund:
1. Download its page.
2. Extract simplified text for comparison.
3. Compare to yesterday's saved version (one snapshot file per fund).
4. Record whether it changed, and when it was last checked.
5. Write everything into the same data.json the dashboard already reads.

Honesty note: GCF and CIF pages sometimes contain real dates in their text
(e.g. "until June 19, 2026"). This script does NOT attempt to parse those
dates out automatically - text scraping for arbitrary dates across
differently-formatted pages is unreliable and prone to false confidence.
Instead, the change-detection note will surface that the page changed, and
a human (you) decides whether the change includes a new deadline worth
updating in the dashboard's static fund list. This is a deliberate
design choice to avoid the tracker quietly showing a wrong date with
false confidence.
"""

import json
import hashlib
import os
import re
import sys
import urllib.request
from datetime import datetime, timezone

DATA_FILE = "data.json"

FUNDS_TO_CHECK = {
    "gcf": {
        "url": "https://www.greenclimate.fund/projects/rfp",
        "snapshot_file": "last_snapshot_gcf.txt",
    },
    "cif": {
        "url": "https://www.cif.org/news",
        "snapshot_file": "last_snapshot_cif.txt",
    },
    "gef": {
        "url": "https://www.thegef.org/newsroom",
        "snapshot_file": "last_snapshot_gef.txt",
    },
    "frld": {
        "url": "https://www.frld.org/",
        "snapshot_file": "last_snapshot_frld.txt",
    },
}


def fetch_page_text(url):
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


def check_one_fund(fund_id, config, today, data):
    url = config["url"]
    snapshot_file = config["snapshot_file"]

    try:
        page_text = fetch_page_text(url)
    except Exception as e:
        print(f"WARNING: could not fetch {fund_id} page ({url}): {e}", file=sys.stderr)
        # Record the failure so the dashboard can show "check failed"
        # rather than silently keep stale data with no explanation.
        existing_entry = data["funds"].get(fund_id, {})
        data["funds"][fund_id] = {
            **existing_entry,
            "lastChecked": today,
            "checkFailed": True,
            "note": f"Automated check failed on {today}: could not reach the page.",
        }
        return

    new_hash = hashlib.sha256(page_text.encode("utf-8")).hexdigest()

    previous_hash = None
    if os.path.exists(snapshot_file):
        with open(snapshot_file, "r", encoding="utf-8") as f:
            previous_hash = f.read().strip()

    changed = previous_hash is not None and previous_hash != new_hash
    first_run = previous_hash is None

    with open(snapshot_file, "w", encoding="utf-8") as f:
        f.write(new_hash)

    existing_entry = data["funds"].get(fund_id, {})
    data["funds"][fund_id] = {
        "lastChecked": today,
        "lastChanged": today if changed else existing_entry.get("lastChanged"),
        "changedSinceLastView": changed,
        "checkFailed": False,
        "note": (
            "First scrape - nothing to compare yet. Will detect changes from tomorrow's run."
            if first_run else
            "Page content changed since last check - review for new deadlines or calls."
            if changed else
            "No change detected on this check."
        ),
        "sourceUrl": url,
    }
    print(f"{fund_id}: first_run={first_run} changed={changed}")


def main():
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    data = load_existing_data()

    for fund_id, config in FUNDS_TO_CHECK.items():
        check_one_fund(fund_id, config, today, data)

    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

    print("Done with all funds.")


if __name__ == "__main__":
    main()

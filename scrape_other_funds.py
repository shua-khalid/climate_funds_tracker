"""
Multi-fund scraper: GCF, CIF, GEF, FRLD.

Extended from change-detection only to also extract the most recent
headline and date from each page, so the dashboard can show something
useful rather than just "page changed".

Each fund has a custom extractor tuned to the actual structure of that
site, based on directly inspecting the live pages in July 2026.
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
        "url": "https://www.greenclimate.fund/access-funding/other-funding-options",
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
    "frld_news": {
        "url": "https://www.frld.org/news",
        "snapshot_file": "last_snapshot_frld_news.txt",
    },
    "frld_b9": {
        "url": "https://www.frld.org/nodeninth-meeting-board-frld",
        "snapshot_file": "last_snapshot_frld_b9.txt",
    },
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# Matches both full (July 10, 2026) and abbreviated (Jul 10, 2026) month names
MONTHS_FULL = (
    "january|february|march|april|may|june|july|august|"
    "september|october|november|december"
)
MONTHS_ABBR = "jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec"
DATE_PATTERN = re.compile(
    r"((" + MONTHS_FULL + r"|" + MONTHS_ABBR + r")\.?\s+\d{1,2},?\s+\d{4})",
    re.IGNORECASE,
)


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


def extract_latest_cif(text):
    """CIF: '[Title][Month Day, Year]' - grab title before first date."""
    match = DATE_PATTERN.search(text)
    if not match:
        return None
    date_str = match.group(0).strip()
    before = text[max(0, match.start() - 120):match.start()].strip()
    title = re.split(r"[|\n]", before)[-1].strip()[-100:]
    return f"{title} -- {date_str}"


def extract_latest_gef(text):
    """GEF: 'Feature Story / Press Release\n[Title]\n[Month Day, Year]'"""
    match = DATE_PATTERN.search(text)
    if not match:
        return None
    date_str = match.group(0).strip()
    before = text[max(0, match.start() - 200):match.start()].strip()
    title = re.split(r"[|\n]", before)[-1].strip()[-120:]
    return f"{title} -- {date_str}"


def extract_latest_frld(text):
    """FRLD: '[Month Day, Year] . Articles and news [Title]'"""
    match = DATE_PATTERN.search(text)
    if not match:
        return None
    date_str = match.group(0).strip()
    after = text[match.end():match.end() + 200].strip()
    # Remove the content-type label (Articles and news, Videos, etc)
    after = re.sub(
        r"^[\s\.\xb7]+(?:articles and news|press releases|videos|publications|events)[\s\xb7\.]+",
        "",
        after,
        flags=re.IGNORECASE,
    )
    title = after[:120].strip()
    return f"{date_str}: {title}"


def extract_gcf_active_rfps(text):
    """GCF RFP page: find active RFP section and extract programme name."""
    lower = text.lower()
    active_idx = lower.find("request for proposals active")
    if active_idx == -1:
        return "No active RFPs section found on page."
    # Programme name appears just before the 'request for proposals active' label
    before_label = text[:active_idx].strip()
    # Take last 8 words as the programme name
    words = before_label.split()[-8:]
    name = " ".join(words)
    return f"Active RFP: {name}"


EXTRACTORS = {
    "gcf": extract_gcf_active_rfps,
    "cif": extract_latest_cif,
    "gef": extract_latest_gef,
    "frld": extract_latest_frld,
    "frld_news": extract_latest_frld,
    "frld_b9": None,  # Board meeting page: change-detection only, no news feed pattern
}


def load_existing_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"funds": {}}


def check_one_fund(fund_id, config, today, data):
    url = config["url"]
    snapshot_file = config["snapshot_file"]

    try:
        raw_html = fetch_raw_html(url)
    except Exception as e:
        print(f"WARNING: could not fetch {fund_id} ({url}): {e}", file=sys.stderr)
        existing_entry = data["funds"].get(fund_id, {})
        data["funds"][fund_id] = {
            **existing_entry,
            "lastChecked": today,
            "checkFailed": True,
            "note": f"Automated check failed on {today}: could not reach the page.",
        }
        return

    clean_text = strip_html(raw_html)
    new_hash = hashlib.sha256(clean_text.lower().encode("utf-8")).hexdigest()

    previous_hash = None
    if os.path.exists(snapshot_file):
        with open(snapshot_file, "r", encoding="utf-8") as f:
            previous_hash = f.read().strip()

    changed = previous_hash is not None and previous_hash != new_hash
    first_run = previous_hash is None

    with open(snapshot_file, "w", encoding="utf-8") as f:
        f.write(new_hash)

    extractor = EXTRACTORS.get(fund_id)
    latest_content = None
    if extractor:
        try:
            latest_content = extractor(clean_text)
        except Exception as e:
            latest_content = f"Extraction error: {e}"

    existing_entry = data["funds"].get(fund_id, {})

    if first_run:
        note = "First scrape - baseline established. Will detect changes from tomorrow."
    elif changed:
        note = "Page content changed since last check - review for new deadlines or calls."
    else:
        note = "No change detected on this check."

    data["funds"][fund_id] = {
        "lastChecked": today,
        "lastChanged": today if changed else existing_entry.get("lastChanged"),
        "changedSinceLastView": changed,
        "checkFailed": False,
        "note": note,
        "latestContent": latest_content,
        "sourceUrl": url,
    }
    print(f"{fund_id}: changed={changed} content={latest_content}")


def main():
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    data = load_existing_data()

    for fund_id, config in FUNDS_TO_CHECK.items():
        check_one_fund(fund_id, config, today, data)

    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

    print("Done.")


if __name__ == "__main__":
    main()

"""
Multi-fund scraper: GCF, CIF, GEF, FRLD.

Extracts the most recent headline, date, and (where possible) a direct
link to that specific article - not just the fund's general news page.

CIF and GEF list individual articles as <a href="..."> links with a
title and a nearby date, so those two extractors work on the RAW HTML
(before tag-stripping) to recover the article's own URL. FRLD and GCF
extractors still work on cleaned text, since their pages don't expose
the same per-article link pattern as cleanly.
"""

import json
import hashlib
import html as html_module
import os
import re
import sys
import urllib.request
import urllib.parse
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

MONTHS_FULL = (
    "january|february|march|april|may|june|july|august|"
    "september|october|november|december"
)
MONTHS_ABBR = "jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec"
DATE_PATTERN = re.compile(
    r"((" + MONTHS_FULL + r"|" + MONTHS_ABBR + r")\.?\s+\d{1,2},?\s+\d{4})",
    re.IGNORECASE,
)

# Matches <a href="...">...inner html...</a>, non-greedy so it doesn't
# swallow multiple links in one match.
LINK_PATTERN = re.compile(
    r'<a\s+[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.IGNORECASE | re.DOTALL
)

NAV_WORDS = {
    "home", "search", "menu", "skip to main content", "contact",
    "legal", "privacy", "disclaimer", "next", "previous", "first page",
    "last page", "view all", "sign up", "subscribe", "all", "news",
    "press releases", "feature stories", "multimedia", "publications",
    "blog", "partner news", "events",
}


def fetch_raw_html(url):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as response:
        return response.read().decode("utf-8", errors="ignore")


def strip_html(html):
    text = re.sub(r"<script.*?</script>", " ", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<style.*?</style>", " ", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html_module.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def clean_inner_text(inner_html):
    """Strip any nested tags from a link's inner HTML and decode entities."""
    text = re.sub(r"<[^>]+>", " ", inner_html)
    text = html_module.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def strip_content_label(title):
    """Remove leading content-type labels like 'News', 'Feature Story',
    'Press Release' that sometimes appear as a tag before the real title."""
    return re.sub(
        r"^(?:news|feature story|press release|blog|multimedia|"
        r"publication|video|podcast)\s+",
        "",
        title,
        flags=re.IGNORECASE,
    ).strip()


def find_first_article_link(raw_html, base_url):
    """
    Scan raw HTML for <a href> links whose visible text looks like a
    real article headline (reasonable length, not a nav word) and
    which has a recent-looking date either inside the link text or
    shortly after it. Returns (title, date_str, absolute_url) or None.
    """
    for match in LINK_PATTERN.finditer(raw_html):
        href = match.group(1)
        inner_text = clean_inner_text(match.group(2))

        if not inner_text or len(inner_text) < 15 or len(inner_text) > 200:
            continue
        if inner_text.strip().lower() in NAV_WORDS:
            continue
        if href.startswith("#") or href.startswith("javascript:"):
            continue

        # Priority 1: date embedded within the link's own text (most
        # reliable - guarantees the date belongs to THIS article, not
        # a neighbouring one).
        date_match = DATE_PATTERN.search(inner_text)
        if date_match:
            date_str = date_match.group(0).strip()
            title = DATE_PATTERN.sub("", inner_text).strip(" -:|")
            title = strip_content_label(title)
            absolute_url = urllib.parse.urljoin(base_url, href)
            return title, date_str, absolute_url

        # Priority 2: date appears shortly after the link closes, but
        # ONLY look up to the next <a tag (or 150 chars, whichever is
        # sooner) so we can't accidentally grab the next card's date.
        window_start = match.end()
        next_link_pos = raw_html.find("<a ", window_start)
        window_end = min(
            len(raw_html),
            window_start + 150,
            next_link_pos if next_link_pos != -1 else len(raw_html),
        )
        nearby = raw_html[window_start:window_end]
        nearby_text = strip_html(nearby)
        date_match = DATE_PATTERN.search(nearby_text)
        if date_match:
            date_str = date_match.group(0).strip()
            title = strip_content_label(inner_text)
            absolute_url = urllib.parse.urljoin(base_url, href)
            return title, date_str, absolute_url

    return None


def extract_cif_with_link(raw_html, base_url):
    result = find_first_article_link(raw_html, base_url)
    if not result:
        return None, None
    title, date_str, url = result
    return f"{title} -- {date_str}", url


def extract_gef_with_link(raw_html, base_url):
    result = find_first_article_link(raw_html, base_url)
    if not result:
        return None, None
    title, date_str, url = result
    return f"{title} -- {date_str}", url


def extract_latest_frld(text):
    """FRLD: '[Month Day, Year] . Articles and news [Title]' - text-based."""
    match = DATE_PATTERN.search(text)
    if not match:
        return None
    date_str = match.group(0).strip()
    after = text[match.end():match.end() + 400].strip()
    after = re.sub(
        r"^[\s\.\xb7]*(?:articles and news|press releases?|videos?|publications?|events?|event)[\s\xb7\.]*",
        "",
        after,
        flags=re.IGNORECASE,
    )
    sentence_match = re.search(r"[.!?](?:\s|$)", after[:300])
    if sentence_match:
        title = after[:sentence_match.end()].strip()
    else:
        truncated = after[:250]
        last_space = truncated.rfind(" ")
        title = truncated[:last_space].strip() if last_space > 0 else truncated
    return f"{date_str}: {title}"


def extract_gcf_active_rfps(text):
    """GCF RFP page: find active RFP section and extract programme name - text-based."""
    lower = text.lower()
    active_idx = lower.find("request for proposals active")
    if active_idx == -1:
        return "No active RFPs section found on page."
    before_label = text[:active_idx].strip()
    words = before_label.split()[-8:]
    name = " ".join(words)
    return f"Active RFP: {name}"


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

    # Run the appropriate extractor - CIF and GEF get per-article URLs,
    # others use the existing text-based approach.
    latest_content = None
    article_url = None
    try:
        if fund_id == "cif":
            latest_content, article_url = extract_cif_with_link(raw_html, url)
        elif fund_id == "gef":
            latest_content, article_url = extract_gef_with_link(raw_html, url)
        elif fund_id in ("frld", "frld_news"):
            latest_content = extract_latest_frld(clean_text)
        elif fund_id == "gcf":
            latest_content = extract_gcf_active_rfps(clean_text)
        # frld_b9 has no extractor - change detection only
    except Exception as e:
        latest_content = f"Extraction error: {e}"

    existing_entry = data["funds"].get(fund_id, {})

    if first_run:
        note = "First scrape - baseline established. Will detect changes from tomorrow."
    elif changed:
        note = "Page content changed since last check - review for new deadlines or calls."
    else:
        note = "No change detected on this check."

    entry = {
        "lastChecked": today,
        "lastChanged": today if changed else existing_entry.get("lastChanged"),
        "changedSinceLastView": changed,
        "checkFailed": False,
        "note": note,
        "latestContent": latest_content,
        "sourceUrl": url,
    }
    # Only add articleUrl if we actually found one - keeps data.json
    # clean for funds where we don't have per-article links yet.
    if article_url:
        entry["articleUrl"] = article_url

    data["funds"][fund_id] = entry
    print(f"{fund_id}: changed={changed} content={latest_content} article_url={article_url}")


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

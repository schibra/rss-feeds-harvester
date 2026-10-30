#!/usr/bin/env python3
"""Fetch the public RSS/Atom feeds listed in feeds.json and write data/news.json.

Stores only what a feed publishes: title, teaser/summary, link, date. No article
pages are fetched. Identifies itself honestly and fetches each feed once per run.
A feed that fails is recorded in `status` and never stops the run.
"""
import html
import json
import re
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path(__file__).parent
UA = "news-harvest/1.0 (personal RSS reader; +https://github.com/schibra/rss-feeds-harvester)"
KEEP_DAYS = 7
MAX_PER_FEED = 40
SUMMARY_CHARS = 400

NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "dc": "http://purl.org/dc/elements/1.1/",
    "content": "http://purl.org/rss/1.0/modules/content/",
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "rss1": "http://purl.org/rss/1.0/",
}
TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")


def clean(text):
    if not text:
        return ""
    text = html.unescape(TAG_RE.sub(" ", html.unescape(text)))
    return WS_RE.sub(" ", text).strip()


def shorten(text, n=SUMMARY_CHARS):
    if len(text) <= n:
        return text
    cut = text[:n].rsplit(" ", 1)[0].rstrip(",;:- ")
    return cut + "…"


def parse_date(raw):
    if not raw:
        return None
    raw = raw.strip()
    try:
        dt = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def text_of(el, *paths):
    for p in paths:
        found = el.find(p, NS)
        if found is not None and (found.text or "").strip():
            return found.text
    return ""


def parse_feed(xml_bytes):
    root = ET.fromstring(xml_bytes)
    items = []
    tag = root.tag.split("}")[-1]
    if tag == "feed":  # Atom
        for e in root.findall("atom:entry", NS):
            link = ""
            for l in e.findall("atom:link", NS):
                if l.get("rel", "alternate") == "alternate":
                    link = l.get("href", "")
                    break
            items.append({
                "title": clean(text_of(e, "atom:title")),
                "summary": clean(text_of(e, "atom:summary", "atom:content")),
                "link": link,
                "date": parse_date(text_of(e, "atom:published", "atom:updated")),
            })
    else:  # RSS 2.0 or RSS 1.0 (RDF)
        entries = root.findall(".//item") or root.findall(".//rss1:item", NS)
        for e in entries:
            link = text_of(e, "link", "rss1:link").strip()
            if not link:
                link = text_of(e, "guid").strip()
            items.append({
                "title": clean(text_of(e, "title", "rss1:title")),
                "summary": clean(text_of(e, "description", "rss1:description", "content:encoded")),
                "link": link,
                "date": parse_date(text_of(e, "pubDate", "dc:date")),
            })
    return items


ITEM_RE = re.compile(r"<(item|entry)\b[^>]*>(.*?)</\1>", re.S | re.I)


def _field(block, *names):
    for n in names:
        m = re.search(r"<%s\b[^>]*>(.*?)</%s>" % (n, n), block, re.S | re.I)
        if m:
            v = m.group(1).strip()
            c = re.match(r"<!\[CDATA\[(.*?)\]\]>$", v, re.S)
            return c.group(1) if c else v
    return ""


def parse_lenient(raw):
    """Regex fallback for feeds whose XML is not well-formed."""
    text = raw.decode("utf-8", "replace")
    items = []
    for _, block in ITEM_RE.findall(text):
        link = _field(block, "link").strip()
        if not link:
            m = re.search(r"<link\b[^>]*href=[\"']([^\"']+)", block, re.I)
            link = m.group(1) if m else _field(block, "guid").strip()
        items.append({
            "title": clean(_field(block, "title")),
            "summary": clean(_field(block, "description", "summary", "content:encoded")),
            "link": link,
            "date": parse_date(_field(block, "pubDate", "published", "updated", "dc:date")),
        })
    return items


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*"})
    with urllib.request.urlopen(req, timeout=25) as r:
        return r.read()


def main():
    feeds = json.loads((ROOT / "feeds.json").read_text("utf-8"))
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=KEEP_DAYS)
    out_path = ROOT / "data" / "news.json"

    old = {}
    if out_path.exists():
        try:
            for it in json.loads(out_path.read_text("utf-8")).get("items", []):
                old[it["link"]] = it
        except (ValueError, KeyError):
            pass

    merged = dict(old)
    status = {}
    for f in feeds:
        try:
            raw = fetch(f["url"])
        except Exception as exc:  # one broken feed must not stop the run
            status[f["id"]] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:200]}
            continue
        try:
            parsed = parse_feed(raw)
        except ET.ParseError:
            parsed = parse_lenient(raw)
            if not parsed:
                head = clean(raw[:200].decode("utf-8", "replace"))[:100]
                status[f["id"]] = {"ok": False, "error": f"unparseable; starts: {head}"}
                continue
        n = 0
        for it in parsed[:MAX_PER_FEED]:
            if not it["link"] or not it["title"]:
                continue
            dt = it["date"] or now
            if dt < cutoff:
                continue
            title = it["title"]
            summary = it["summary"]
            if summary.startswith("Article URL:"):  # hnrss boilerplate
                pts = re.search(r"Points:\s*(\d+)", summary)
                cmt = re.search(r"# Comments:\s*(\d+)", summary)
                summary = ""
                if pts:
                    summary = f"{pts.group(1)} points" + (f", {cmt.group(1)} comments" if cmt else "") + " on Hacker News."
            if summary.lower().startswith(title.lower()[:40]):
                summary = ""  # teaser that only repeats the headline
            merged[it["link"]] = {
                "feed": f["id"],
                "outlet": f["outlet"],
                "section": f["section"],
                "lang": f["lang"],
                "title": title,
                "summary": shorten(summary),
                "link": it["link"],
                "published": dt.isoformat(timespec="seconds"),
            }
            n += 1
        status[f["id"]] = {"ok": True, "items": n}

    items = [it for it in merged.values() if datetime.fromisoformat(it["published"]) >= cutoff]
    items.sort(key=lambda it: it["published"], reverse=True)

    payload = {"generated": now.isoformat(timespec="seconds"), "status": status, "items": items}
    out_path.parent.mkdir(exist_ok=True)
    new_text = json.dumps(payload, ensure_ascii=False, indent=1) + "\n"
    # Avoid a commit every hour when only the timestamp changed.
    if out_path.exists():
        try:
            prev = json.loads(out_path.read_text("utf-8"))
            if prev.get("items") == items and prev.get("status") == status:
                print("no change")
                return 0
        except ValueError:
            pass
    out_path.write_text(new_text, "utf-8")
    ok = sum(1 for s in status.values() if s["ok"])
    print(f"wrote {len(items)} items; {ok}/{len(status)} feeds ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())

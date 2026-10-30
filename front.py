#!/usr/bin/env python3
"""Build the dashboard's news CSVs from data/news.json.

  python front.py wire                  -> data/newswire.csv (every headline of the last 24 h, newest first,
                                            at most 7 per outlet; no tabloids, no Hacker News)
  python front.py front picks.json OUT  -> OUT (front page CSV) from hand-picked story links

picks.json:
{
  "focus": {"title": "US midterms", "date": "2026-11-03", "summary": "..."},      # optional
  "rows": [
    {"rank": "lead",    "section": "Lead",    "link": "<item link>", "chars": 330, "also": "<regex>"},
    {"rank": "second",  "section": "Israel",  "link": "...", "chars": 230, "also": "<regex>"},
    {"rank": "focus",   "section": "In focus", "link": "..."},
    {"rank": "feature", "section": "Denmark", "link": "...", "chars": 230},
    {"rank": "brief",   "section": "Denmark", "link": "..."}
  ]
}
Rows are written in the order given; the focus_head row is placed before the first "focus" row.
Title, outlet, date, image and teaser come from the harvested item, never from picks.json.
"""
import csv
import json
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).parent
TZ = ZoneInfo("Europe/Copenhagen")
SKIP = {"EB", "BT", "Ekstra Bladet"}
d = json.load(open(ROOT / "data" / "news.json", encoding="utf-8"))
now = datetime.fromisoformat(d["generated"])
items = d["items"]
by_link = {i["link"]: i for i in items}


def iso(i):
    return datetime.fromisoformat(i["published"]).astimezone(TZ).isoformat(timespec="minutes")


def short(t, n):
    return t if len(t) <= n else t[:n].rsplit(" ", 1)[0].rstrip(",;:- ") + "…"


def wire(out):
    recent = [i for i in items if datetime.fromisoformat(i["published"]) >= now - timedelta(hours=24)
              and i["outlet"] not in SKIP and i["feed"] != "hn"]
    recent.sort(key=lambda i: i["published"], reverse=True)
    count, rows = {}, []
    for i in recent:
        if count.get(i["outlet"], 0) >= 7:
            continue
        count[i["outlet"]] = count.get(i["outlet"], 0) + 1
        rows.append([iso(i), i["section"], i["outlet"], i["title"], i["link"]])
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["date", "section", "source", "headline", "url"])
        w.writerows(rows)
    print(len(rows), "wire rows")


def also_for(pattern, own):
    rx = re.compile(pattern, re.I)
    best = {}
    for i in items:
        if i is own or i["outlet"] == own["outlet"] or i["outlet"] in SKIP:
            continue
        if datetime.fromisoformat(i["published"]) < now - timedelta(hours=36) or not rx.search(i["title"]):
            continue
        best.setdefault(i["outlet"], []).append(i)
    out = []
    for o in sorted(best):
        lst = sorted(best[o], key=lambda x: ("/video" in x["link"], -datetime.fromisoformat(x["published"]).timestamp()))
        out.append("%s\t%s" % (o, lst[0]["link"]))
    return "\n".join(out)


def front(picks_file, out):
    picks = json.load(open(picks_file, encoding="utf-8"))
    rows = []
    focus = picks.get("focus")
    for p in picks["rows"]:
        it = by_link.get(p["link"])
        if not it:
            print("SKIP unknown link:", p["link"])
            continue
        if p["rank"] == "focus" and focus and not any(r[0] == "focus_head" for r in rows):
            rows.append(["focus_head", "In focus", focus["title"], focus.get("summary", ""), "", "", focus.get("date", ""), "", ""])
        n = p.get("chars", 0)
        rows.append([p["rank"], p["section"], it["title"], short(it["summary"], n) if n else "", it["outlet"], it["link"], iso(it),
                     it.get("image", "") if p["rank"] == "lead" else "",
                     also_for(p["also"], it) if p.get("also") else ""])
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["rank", "section", "headline", "summary", "source", "url", "date", "image", "also_by"])
        w.writerows(rows)
    print(len(rows), "front rows")


if __name__ == "__main__":
    if sys.argv[1] == "wire":
        wire(ROOT / "data" / "newswire.csv")
    elif sys.argv[1] == "front":
        front(sys.argv[2], sys.argv[3])
    else:
        sys.exit(__doc__)

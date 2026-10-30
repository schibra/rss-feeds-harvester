#!/usr/bin/env python3
"""Copenhagen forecast from MET Norway's Locationforecast (the data behind yr.no).

Writes data/hourly.csv (next 48 hours) and data/daily.csv (next 7 days).
Identifies itself with a User-Agent as MET Norway's terms require, and is
called once per daily run. A failure here never stops the news harvest.
"""
import csv
import json
import math
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).parent
LAT, LON = 55.6761, 12.5683
TZ = ZoneInfo("Europe/Copenhagen")
UA = "rss-feeds-harvester/1.0 github.com/schibra/rss-feeds-harvester"
HOURS, DAYS = 48, 7


def get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def feels_like(t, wind_ms):
    """Wind chill (Environment Canada) when it is cool and breezy, else the air temperature."""
    v = wind_ms * 3.6
    if t is not None and t <= 10 and v >= 4.8:
        return round(13.12 + 0.6215 * t - 11.37 * v ** 0.16 + 0.3965 * t * v ** 0.16, 1)
    return t


def r1(x):
    return None if x is None else round(float(x), 1)


def sun_times(day):
    """Sunrise and sunset (HH:MM local) for a date from MET Norway's sunrise API."""
    noon = datetime(day.year, day.month, day.day, 12, tzinfo=TZ)
    off = noon.strftime("%z")
    off = off[:3] + ":" + off[3:]
    q = urllib.parse.urlencode({"lat": LAT, "lon": LON, "date": day.isoformat(), "offset": off})
    try:
        p = get_json("https://api.met.no/weatherapi/sunrise/3.0/sun?" + q)["properties"]
        pick = lambda k: datetime.fromisoformat(p[k]["time"]).strftime("%H:%M")
        return pick("sunrise"), pick("sunset")
    except Exception:
        return "", ""


def main():
    url = "https://api.met.no/weatherapi/locationforecast/2.0/complete?lat=%s&lon=%s" % (LAT, LON)
    data = get_json(url)
    issued = datetime.fromisoformat(data["properties"]["meta"]["updated_at"].replace("Z", "+00:00")).astimezone(TZ)
    series = data["properties"]["timeseries"]
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)

    hourly = []
    for e in series:
        t = datetime.fromisoformat(e["time"].replace("Z", "+00:00"))
        d = e["data"]
        n1 = d.get("next_1_hours")
        if t < now or not n1:
            continue
        i = d["instant"]["details"]
        wind = i.get("wind_speed")
        temp = i.get("air_temperature")
        hourly.append({
            "time": t.astimezone(TZ).isoformat(timespec="minutes"),
            "temp_c": r1(temp),
            "feels_c": r1(feels_like(temp, wind or 0)),
            "precip_mm": r1(n1["details"].get("precipitation_amount")),
            "rain_prob": r1(n1["details"].get("probability_of_precipitation")),
            "wind_ms": r1(wind),
            "gust_ms": r1(i.get("wind_speed_of_gust")),
            "wind_dir": None if i.get("wind_from_direction") is None else round(i["wind_from_direction"]),
            "cloud_pct": None if i.get("cloud_area_fraction") is None else round(i["cloud_area_fraction"]),
            "humidity": None if i.get("relative_humidity") is None else round(i["relative_humidity"]),
            "symbol": n1["summary"]["symbol_code"],
            "issued": issued.isoformat(timespec="minutes"),
        })
        if len(hourly) >= HOURS:
            break

    # daily roll-up from every time step (hourly early on, 6-hourly later)
    days = {}
    for e in series:
        t = datetime.fromisoformat(e["time"].replace("Z", "+00:00")).astimezone(TZ)
        d = e["data"]
        i = d["instant"]["details"]
        day = days.setdefault(t.date(), {"temps": [], "rain": 0.0, "wind": [], "gust": [], "sym": []})
        if i.get("air_temperature") is not None:
            day["temps"].append(i["air_temperature"])
        if i.get("wind_speed") is not None:
            day["wind"].append(i["wind_speed"])
        if i.get("wind_speed_of_gust") is not None:
            day["gust"].append(i["wind_speed_of_gust"])
        n1, n6 = d.get("next_1_hours"), d.get("next_6_hours")
        if n1:
            day["rain"] += n1["details"].get("precipitation_amount", 0) or 0
        elif n6:
            day["rain"] += n6["details"].get("precipitation_amount", 0) or 0
        if n6:
            for k in ("air_temperature_max", "air_temperature_min"):
                if n6["details"].get(k) is not None:
                    day["temps"].append(n6["details"][k])
        sym = (n6 or n1 or {}).get("summary", {}).get("symbol_code")
        if sym:
            day["sym"].append((abs(t.hour - 12), sym))

    today = datetime.now(TZ).date()
    daily = []
    for k in sorted(days):
        if k < today or len(daily) >= DAYS:
            continue
        v = days[k]
        if not v["temps"]:
            continue
        rise, sett = sun_times(k)
        daily.append({
            "date": k.isoformat(),
            "tmin": r1(min(v["temps"])), "tmax": r1(max(v["temps"])),
            "precip_mm": r1(v["rain"]),
            "wind_max_ms": r1(max(v["wind"])) if v["wind"] else None,
            "gust_max_ms": r1(max(v["gust"])) if v["gust"] else None,
            "symbol": min(v["sym"])[1] if v["sym"] else "",
            "sunrise": rise, "sunset": sett,
            "issued": issued.isoformat(timespec="minutes"),
        })

    out = ROOT / "data"
    out.mkdir(exist_ok=True)
    for name, rows in (("hourly.csv", hourly), ("daily.csv", daily)):
        if not rows:
            raise RuntimeError("no rows for " + name)
        with open(out / name, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows({k: ("" if v is None else v) for k, v in r.items()} for r in rows)
    print("weather: %d hourly rows, %d daily rows, issued %s" % (len(hourly), len(daily), issued.isoformat(timespec="minutes")))


if __name__ == "__main__":
    main()

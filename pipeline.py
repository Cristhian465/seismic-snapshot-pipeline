"""Build a static weekly earthquake report from the USGS GeoJSON feed."""

import argparse
import csv
from datetime import datetime, timezone
from html import escape
import json
import math
from pathlib import Path
import sqlite3
import time
from urllib.error import URLError
from urllib.request import Request, urlopen

FEED = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/2.5_week.geojson"


def fetch_snapshot():
    request = Request(FEED, headers={"User-Agent": "SeismicSnapshotPipeline/1.0"})
    for delay in (1, 2, None):
        try:
            with urlopen(request, timeout=30) as response:
                return response.read()
        except (URLError, TimeoutError):
            if delay is None:
                raise
            time.sleep(delay)


def utc_time(milliseconds):
    if type(milliseconds) is not int or milliseconds <= 0:
        raise ValueError("Expected a positive Unix timestamp in milliseconds")
    return datetime.fromtimestamp(milliseconds / 1000, timezone.utc)


def number(value):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError("Expected a finite number")
    return value


def normalize(payload):
    metadata = payload["metadata"]
    features = payload["features"]
    if payload["type"] != "FeatureCollection" or not isinstance(features, list):
        raise ValueError("Expected a GeoJSON FeatureCollection")
    if metadata["status"] != 200 or metadata["count"] != len(features):
        raise ValueError("Feed status or feature count is invalid")
    generated = utc_time(metadata["generated"])
    rows = []
    for feature in features:
        fields = feature["properties"]
        if fields["type"] != "earthquake":
            continue
        event_id = feature["id"]
        geometry = feature["geometry"]
        if not isinstance(event_id, str) or not event_id.strip() or geometry["type"] != "Point":
            raise ValueError("Expected an event ID and Point geometry")
        longitude, latitude, depth = map(number, geometry["coordinates"])
        if not (-180 <= longitude <= 180 and -90 <= latitude <= 90):
            raise ValueError(f"Invalid coordinates for {event_id}")
        rows.append((
            event_id, utc_time(fields["time"]).isoformat(), number(fields["mag"]),
            depth, latitude, longitude, fields["place"] or "Location unavailable",
        ))
    if not rows:
        raise ValueError("Feed has no earthquake events")
    return generated, rows


SCHEMA = """
CREATE TABLE events (
    event_id TEXT PRIMARY KEY NOT NULL,
    occurred_utc TEXT NOT NULL,
    magnitude REAL NOT NULL,
    depth_km REAL NOT NULL,
    latitude REAL NOT NULL CHECK (latitude BETWEEN -90 AND 90),
    longitude REAL NOT NULL CHECK (longitude BETWEEN -180 AND 180),
    place TEXT NOT NULL
)
"""

DAILY = """
SELECT substr(occurred_utc, 1, 10) AS day_utc,
       count(*) AS event_count,
       round(max(magnitude), 2) AS max_magnitude,
       round(avg(depth_km), 1) AS average_depth_km
FROM events
GROUP BY day_utc
ORDER BY day_utc DESC
"""


def write_csv(path, result):
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(column[0] for column in result.description)
        writer.writerows(result)


def write_report(site, generated, daily, largest, count):
    days = "".join(
        f"<tr><td>{day}</td><td>{events}</td><td>{peak:.2f}</td><td>{depth:.1f}</td></tr>"
        for day, events, peak, depth in daily
    )
    magnitudes = "".join(
        f"<tr><td>{stamp[:10]}</td><td>{escape(place)}</td><td>{mag:.2f}</td><td>{depth:.1f}</td></tr>"
        for stamp, place, mag, depth in largest
    )
    (site / "index.html").write_text(f"""<!doctype html>
<html lang="en">
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Seismic Snapshot</title>
<style>
*{{box-sizing:border-box}}body{{margin:0;background:#f1f5f4;color:#17322d;font:16px/1.55 system-ui,sans-serif}}
main{{max-width:960px;margin:auto;padding:2rem 1.25rem 3rem}}header{{background:#153b35;color:#fff;padding:2rem;border-radius:1.15rem}}
h1{{font-size:clamp(2.3rem,6vw,4rem);line-height:1;margin:.25em 0}}h2{{margin-bottom:.5rem}}
.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:.75rem;margin-top:1rem}}
.card,.panel{{background:white;border:1px solid #cbdad6;border-radius:.8rem;padding:1rem}}
.card strong{{display:block;font-size:1.7rem}}.panel{{margin-top:1rem;overflow-x:auto}}
table{{border-collapse:collapse;width:100%;text-align:left}}th,td{{padding:.65rem;border-bottom:1px solid #dde7e4}}
a{{color:#07594b}}small{{display:block;margin-top:1.25rem}}
</style>
<main>
<header><p>USGS · GLOBAL EARTHQUAKE MONITORING</p><h1>Seismic Snapshot</h1>
<p>Earthquakes in the USGS past-seven-days magnitude 2.5+ feed.</p></header>
<div class="cards">
<div class="card"><strong>{count}</strong> earthquake records</div>
<div class="card"><strong>{largest[0][2]:.2f}</strong> maximum magnitude</div>
<div class="card"><strong>{generated:%Y-%m-%d}</strong> feed generated at {generated:%H:%M} UTC</div>
</div>
<section class="panel"><h2>Daily totals</h2><table>
<thead><tr><th scope="col">UTC date</th><th scope="col">Events</th><th scope="col">Maximum magnitude</th><th scope="col">Average depth (km)</th></tr></thead>
<tbody>{days}</tbody></table></section>
<section class="panel"><h2>Largest events</h2><table>
<thead><tr><th scope="col">UTC date</th><th scope="col">Location</th><th scope="col">Magnitude</th><th scope="col">Depth (km)</th></tr></thead>
<tbody>{magnitudes}</tbody></table></section>
<p><a href="events.csv">Download earthquake records</a> · <a href="daily-summary.csv">Download daily totals</a> · <a href="{FEED}">USGS source feed</a></p>
<small>The feed covers a rolling window. The first and last UTC dates can contain partial days. Event estimates can change as USGS reviews them.</small>
</main>
""", encoding="utf-8")


def build(raw, site, evidence):
    generated, rows = normalize(json.loads(raw))
    evidence.mkdir(parents=True, exist_ok=True)
    (evidence / "usgs.geojson").write_bytes(raw)
    database = evidence / "seismic.sqlite3"
    with sqlite3.connect(database) as connection:
        with connection:
            connection.execute("BEGIN")
            connection.execute("DROP TABLE IF EXISTS events")
            connection.execute(SCHEMA)
            connection.executemany("INSERT INTO events VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
        daily = connection.execute(DAILY).fetchall()
        largest = connection.execute("""
            SELECT occurred_utc, place, magnitude, depth_km
            FROM events
            ORDER BY magnitude DESC, occurred_utc DESC, event_id
            LIMIT 12
        """).fetchall()
        if sum(row[1] for row in daily) != len(rows):
            raise ValueError("Daily totals do not reconcile to the event count")
        site.mkdir(parents=True, exist_ok=True)
        write_csv(site / "events.csv", connection.execute(
            "SELECT * FROM events ORDER BY occurred_utc DESC, event_id"
        ))
        write_csv(site / "daily-summary.csv", connection.execute(DAILY))
        write_report(site, generated, daily, largest, len(rows))
    print(f"Validated and exported {len(rows)} earthquake events across {len(daily)} UTC dates")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, help="Replay a saved USGS GeoJSON response")
    parser.add_argument("--site", type=Path, default=Path("site"))
    parser.add_argument("--evidence", type=Path, default=Path("evidence"))
    args = parser.parse_args()
    raw = args.input.read_bytes() if args.input else fetch_snapshot()
    build(raw, args.site, args.evidence)


if __name__ == "__main__":
    main()

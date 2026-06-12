#!/usr/bin/env python3
"""Poll the BirdWeather GraphQL API and merge the latest onboard-sensor
readings into birds-sensor-history.json.

The BirdWeather API only ever returns roughly the most recent ~1000 readings
(~10 hours). Running this on a schedule (e.g. every 6h via GitHub Actions) lets
the JSON file accumulate an unbounded, continuous history that the dashboard
auto-loads on startup.

No third-party dependencies — uses only the Python standard library.
"""

import json
import os
import sys
import urllib.request
from datetime import datetime, timezone

STATION_ID = os.environ.get("BW_STATION_ID", "27598")
GQL_URL = "https://app.birdweather.com/graphql"
HISTORY_FILE = os.environ.get("BW_HISTORY_FILE", "birds-sensor-history.json")

# Must match SENSOR_FIELDS in the dashboard.
SENSOR_FIELDS = [
    "temperature", "humidity", "barometricPressure", "aqi",
    "soundPressureLevel", "eco2", "voc",
]

QUERY = """query($id:ID!){
  station(id:$id){
    name
    sensors {
      environmentHistory(last:1000){
        nodes { timestamp temperature humidity barometricPressure aqi soundPressureLevel eco2 voc }
      }
    }
  }
}"""


def fetch_nodes():
    """Return the list of environmentHistory nodes from the API."""
    body = json.dumps({"query": QUERY, "variables": {"id": STATION_ID}}).encode()
    req = urllib.request.Request(
        GQL_URL,
        data=body,
        headers={"Content-Type": "application/json", "User-Agent": "bird-dashboard-collector"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        payload = json.load(resp)
    if payload.get("errors"):
        msgs = "; ".join(e.get("message", "?") for e in payload["errors"])
        raise RuntimeError(f"GraphQL errors: {msgs}")
    station = (payload.get("data") or {}).get("station") or {}
    sensors = station.get("sensors") or {}
    hist = sensors.get("environmentHistory") or {}
    return hist.get("nodes") or []


def load_store():
    """Read existing history file into a {timestamp: {fields}} dict."""
    if not os.path.exists(HISTORY_FILE):
        return {}
    try:
        with open(HISTORY_FILE, "r") as fh:
            j = json.load(fh)
    except (ValueError, OSError):
        return {}
    nodes = j if isinstance(j, list) else (j.get("sensor") or j.get("nodes") or [])
    store = {}
    for r in nodes:
        ts = r.get("timestamp") or r.get("ts")
        if not ts:
            continue
        rec = {}
        # legacy short keys
        if r.get("t") is not None:
            rec["temperature"] = r["t"]
        if r.get("h") is not None:
            rec["humidity"] = r["h"]
        for f in SENSOR_FIELDS:
            if r.get(f) is not None:
                rec[f] = r[f]
        store[ts] = rec
    return store


def merge(store, nodes):
    """Field-by-field merge; returns count of newly added timestamps."""
    added = 0
    for r in nodes:
        ts = r.get("timestamp") or r.get("ts")
        if not ts:
            continue
        rec = store.get(ts, {})
        for f in SENSOR_FIELDS:
            if r.get(f) is not None:
                rec[f] = r[f]
        if ts not in store:
            added += 1
        store[ts] = rec
    return added


def write_store(store):
    """Write the store back out in the dashboard's export shape."""
    sensor = [dict(timestamp=ts, **store[ts]) for ts in sorted(store)]
    data = {
        "station": STATION_ID,
        "exportedAt": datetime.now(timezone.utc).isoformat(),
        "sensor": sensor,
    }
    tmp = HISTORY_FILE + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(data, fh, separators=(",", ":"))
    os.replace(tmp, HISTORY_FILE)
    return len(sensor)


def main():
    store = load_store()
    before = len(store)
    try:
        nodes = fetch_nodes()
    except Exception as exc:  # network/API failure shouldn't abort the workflow
        print(f"fetch failed: {exc}", file=sys.stderr)
        return 1
    added = merge(store, nodes)
    total = write_store(store)
    print(f"fetched {len(nodes)} nodes · added {added} new · "
          f"store {before} -> {total} readings")
    return 0


if __name__ == "__main__":
    sys.exit(main())

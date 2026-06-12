#!/usr/bin/env python3
"""Poll BirdWeather and persist rolling-window data to local JSON history files.

What this script maintains:
1) birds-sensor-history.json     (environment + light sensor readings)
2) birds-detections-history.json (recent detections, merged over time)

The API exposes only rolling windows for some endpoints. Running this on a
schedule lets the dashboard keep a long-lived local history that survives API
aging-out.

No third-party dependencies — standard library only.
"""

import json
import os
import sys
import urllib.request
from datetime import datetime, timezone

STATION_ID = os.environ.get("BW_STATION_ID", "27598")
GQL_URL = "https://app.birdweather.com/graphql"
HISTORY_FILE = os.environ.get("BW_HISTORY_FILE", "birds-sensor-history.json")
DETECTIONS_FILE = os.environ.get("BW_DETECTIONS_FILE", "birds-detections-history.json")

# Must match SENSOR_FIELDS in the dashboard.
SENSOR_FIELDS = [
    "temperature", "humidity", "barometricPressure", "aqi",
    "soundPressureLevel", "eco2", "voc",
        "clear", "nir", "f1", "f2", "f3", "f4", "f5", "f6", "f7", "f8",
]

SENSOR_QUERY = """query($id:ID!){
  station(id:$id){
    name
    sensors {
      environmentHistory(last:1000){
        nodes { timestamp temperature humidity barometricPressure aqi soundPressureLevel eco2 voc }
      }
            lightHistory(last:1000){
                nodes { timestamp clear nir f1 f2 f3 f4 f5 f6 f7 f8 }
            }
    }
  }
}"""

DETECTION_QUERY = f"""query($p:InputDuration,$after:String){{
    detections(period:$p, stationIds:["{STATION_ID}"], first:100, after:$after){{
        pageInfo {{ hasNextPage endCursor }}
        nodes {{
            timestamp speciesId confidence score
            species {{ commonName color }}
            soundscape {{ url }}
        }}
    }}
}}"""


def gql(query, variables=None, timeout=30):
        body = json.dumps({"query": query, "variables": variables or {}}).encode()
        req = urllib.request.Request(
                GQL_URL,
                data=body,
                headers={"Content-Type": "application/json", "User-Agent": "bird-dashboard-collector"},
                method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
                payload = json.load(resp)
        if payload.get("errors"):
                msgs = "; ".join(e.get("message", "?") for e in payload["errors"])
                raise RuntimeError(f"GraphQL errors: {msgs}")
        return payload.get("data") or {}


def fetch_sensor_nodes():
    """Return merged environment/light history nodes from the API."""
    data = gql(SENSOR_QUERY, {"id": STATION_ID})
    station = data.get("station") or {}
    sensors = station.get("sensors") or {}
    e_hist = (sensors.get("environmentHistory") or {}).get("nodes") or []
    l_hist = (sensors.get("lightHistory") or {}).get("nodes") or []
    return e_hist + l_hist


def fetch_detection_nodes(days=2, max_pages=30, max_nodes=6000):
    """Return recent detections from cursor-paged GraphQL detections query."""
    out = []
    after = None
    pages = 0
    while pages < max_pages and len(out) < max_nodes:
        data = gql(DETECTION_QUERY, {"p": {"count": days, "unit": "day"}, "after": after})
        det = data.get("detections") or {}
        nodes = det.get("nodes") or []
        out.extend(nodes)
        pi = det.get("pageInfo") or {}
        if not pi.get("hasNextPage"):
            break
        after = pi.get("endCursor")
        pages += 1
    return out[:max_nodes]


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


def load_detection_store():
    """Read existing detection history file into a {key: detection} dict."""
    if not os.path.exists(DETECTIONS_FILE):
        return {}
    try:
        with open(DETECTIONS_FILE, "r") as fh:
            j = json.load(fh)
    except (ValueError, OSError):
        return {}
    nodes = j if isinstance(j, list) else (j.get("detections") or j.get("nodes") or [])
    out = {}
    for d in nodes:
        ts = d.get("timestamp")
        sid = d.get("speciesId")
        if not ts or sid is None:
            continue
        sid = str(sid)
        url = ((d.get("soundscape") or {}).get("url") or "")
        key = f"{ts}|{sid}|{url}"
        out[key] = {
            "timestamp": ts,
            "speciesId": sid,
            "confidence": d.get("confidence"),
            "score": d.get("score"),
            "species": {
                "commonName": ((d.get("species") or {}).get("commonName") or f"#{sid}"),
                "color": ((d.get("species") or {}).get("color") or "#4cc9f0"),
            },
            "soundscape": ({"url": url} if url else None),
        }
    return out


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


def merge_detections(store, nodes):
    """Merge detections by (timestamp, speciesId, soundscape.url) key."""
    added = 0
    for d in nodes:
        ts = d.get("timestamp")
        sid = d.get("speciesId")
        if not ts or sid is None:
            continue
        sid = str(sid)
        url = ((d.get("soundscape") or {}).get("url") or "")
        key = f"{ts}|{sid}|{url}"
        if key not in store:
            added += 1
        store[key] = {
            "timestamp": ts,
            "speciesId": sid,
            "confidence": d.get("confidence"),
            "score": d.get("score"),
            "species": {
                "commonName": ((d.get("species") or {}).get("commonName") or f"#{sid}"),
                "color": ((d.get("species") or {}).get("color") or "#4cc9f0"),
            },
            "soundscape": ({"url": url} if url else None),
        }
    return added


def write_detections(store):
    """Write detections history file."""
    vals = sorted(store.values(), key=lambda x: x.get("timestamp") or "")
    data = {
        "station": STATION_ID,
        "exportedAt": datetime.now(timezone.utc).isoformat(),
        "detections": vals,
    }
    tmp = DETECTIONS_FILE + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(data, fh, separators=(",", ":"))
    os.replace(tmp, DETECTIONS_FILE)
    return len(vals)


def main():
    sensor_store = load_store()
    det_store = load_detection_store()
    before_s = len(sensor_store)
    before_d = len(det_store)
    status = 0

    try:
        sensor_nodes = fetch_sensor_nodes()
    except Exception as exc:  # network/API failure shouldn't abort the workflow
        print(f"sensor fetch failed: {exc}", file=sys.stderr)
        status = 1
        sensor_nodes = []

    try:
        det_nodes = fetch_detection_nodes(days=2)
    except Exception as exc:
        print(f"detection fetch failed: {exc}", file=sys.stderr)
        status = 1
        det_nodes = []

    added_s = merge(sensor_store, sensor_nodes)
    total_s = write_store(sensor_store)

    added_d = merge_detections(det_store, det_nodes)
    total_d = write_detections(det_store)

    print(
        f"sensor: fetched {len(sensor_nodes)} · added {added_s} · store {before_s} -> {total_s}"
    )
    print(
        f"detect: fetched {len(det_nodes)} · added {added_d} · store {before_d} -> {total_d}"
    )
    return status


if __name__ == "__main__":
    sys.exit(main())

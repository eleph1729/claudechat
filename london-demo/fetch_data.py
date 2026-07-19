#!/usr/bin/env python3
"""Pre-bake OpenStreetMap data for the London demo.

Downloads the same Overpass query the browser would run and saves it to
data/london.json, so the demo works offline (and loads instantly).

Usage:
    python3 fetch_data.py                      # default Westminster bbox
    python3 fetch_data.py --bbox S,W,N,E       # any other area
"""
import argparse
import json
import pathlib
import sys
import time
import urllib.parse
import urllib.request

# Keep in sync with DEFAULT_BBOX / OVERPASS_QUERY in main.js.
DEFAULT_BBOX = "51.4975,-0.1360,51.5095,-0.1160"

ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]

QUERY_TEMPLATE = """
[out:json][timeout:120];
(
  way["building"]({bbox});
  relation["building"]({bbox});
);
out geom;
(
  way["highway"]({bbox});
  way["leisure"~"^(park|garden|pitch|playground|recreation_ground)$"]({bbox});
  relation["leisure"~"^(park|garden)$"]({bbox});
  way["landuse"~"^(grass|meadow|forest|village_green|recreation_ground)$"]({bbox});
  way["natural"~"^(water|wood|scrub)$"]({bbox});
  relation["natural"="water"]({bbox});
  way["waterway"="riverbank"]({bbox});
);
out geom({bbox});
"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--bbox", default=DEFAULT_BBOX, help="south,west,north,east")
    ap.add_argument("--out", default="data/london.json")
    args = ap.parse_args()

    query = QUERY_TEMPLATE.format(bbox=args.bbox)
    body = urllib.parse.urlencode({"data": query}).encode()

    for attempt, endpoint in enumerate(ENDPOINTS):
        try:
            print(f"Querying {endpoint} …", flush=True)
            req = urllib.request.Request(
                endpoint, data=body,
                headers={"User-Agent": "london-demo-poc/1.0 (OSM data pre-bake)"},
            )
            with urllib.request.urlopen(req, timeout=180) as resp:
                data = json.load(resp)
            n = len(data.get("elements", []))
            if n == 0:
                print("Warning: response contained no elements", file=sys.stderr)
            out = pathlib.Path(args.out)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(data, separators=(",", ":")))
            print(f"Wrote {out} ({out.stat().st_size / 1e6:.1f} MB, {n} elements)")
            return 0
        except Exception as exc:  # noqa: BLE001 - report and try next mirror
            print(f"  failed: {exc}", file=sys.stderr)
            if attempt < len(ENDPOINTS) - 1:
                time.sleep(2)

    print("All Overpass mirrors failed.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())

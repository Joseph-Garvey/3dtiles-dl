from src.tile_api import TileApi
from src.bounding_volume import Sphere
from src.wgs84 import cartesian_from_degrees

import argparse
from pathlib import Path
import sys
import os

from dotenv import load_dotenv

import numpy as np
import requests
from tqdm import tqdm


def _get_elevation(lon, lat, key):
    res = requests.get(
        f"https://maps.googleapis.com/maps/api/elevation/json",
        params={
            "locations": f"{lat},{lon}",
            "key": key
        }
    )
    if not res.ok:
        raise RuntimeError(f"response not ok: {response.status_code}, {response.text}")
    data = res.json()
    if not data["status"] == "OK" or "results" not in data:
        raise RuntimeError(f"status not ok: {data['status']}, {data}")
    return data["results"][0]["elevation"]


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("-k", "--api-key",
                        help="your Google Maps 3d Tiles API key (overrides .env)",
                        required=False)
    parser.add_argument("-c", "--coords",
                        help="four corner points: lon1 lat1 lon2 lat2 lon3 lat3 lon4 lat4 [degrees]",
                        type=float,
                        nargs='+',
                        required=True)
    parser.add_argument("-o", "--out",
                        help="output directory to place tiles in",
                        required=True)

    args = parser.parse_args()

    load_dotenv()
    api_key = api_key or os.getenv("GOOGLE_MAPS_API_KEY")
    if not api_key:
        print("API key required: set GOOGLE_MAPS_API_KEY in .env or pass -k")
        sys.exit(-1)

    if len(args.coords) != 8:
        print("Must provide four corner points: -c lon1 lat1 lon2 lat2 lon3 lat3 lon4 lat4")
        sys.exit(-1)

    corners = [(args.coords[i], args.coords[i + 1]) for i in range(0, 8, 2)]
    centroid_lon = sum(lon for lon, _ in corners) / 4
    centroid_lat = sum(lat for _, lat in corners) / 4

    print("Querying elevation...")
    elevation = _get_elevation(centroid_lon, centroid_lat, api_key)

    corner_points = [cartesian_from_degrees(lon, lat, elevation) for lon, lat in corners]
    center = cartesian_from_degrees(centroid_lon, centroid_lat, elevation)
    radius = max(np.linalg.norm(p - center) for p in corner_points)

    api = TileApi(key=api_key)
    print("Traversing tile hierarchy...")
    tiles = list(tqdm(api.get(Sphere(center, radius))))

    outdir = Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)
    print("Downloading tiles...")
    for i, t in tqdm(enumerate(tiles), total=len(tiles)):
        with open(outdir / Path(f"{t.basename}.glb"), "wb") as f:
            f.write(t.data)

# Google Maps 3D Tiles Downloader

Download Google Maps 3D Tiles as glTF (these can, for example, be imported into Blender).

## Setup

1. Copy `.env` and add your API key:
   ```
   GOOGLE_MAPS_API_KEY=your_key_here
   ```

2. Install dependencies:
   ```
   uv sync
   ```

## Usage

```
usage: download_tiles.py [-h] [-k API_KEY] -c COORDS [COORDS ...] -o OUT

options:
  -h, --help            show this help message and exit
  -k API_KEY, --api-key API_KEY
                        your Google Maps 3d Tiles API key (overrides .env)
  -c COORDS [COORDS ...], --coords COORDS [COORDS ...]
                        four corner points: lon1 lat1 lon2 lat2 lon3 lat3 lon4 lat4 [degrees]
  -o OUT, --out OUT     output directory to place tiles in
```

## Example

```
python -m scripts.download_tiles -c -71.072 42.352 -71.068 42.352 -71.068 42.348 -71.072 42.348 -o tiles
```

The four coordinates define the corners of the area to fetch (in any order). Elevation is queried automatically from the Google Maps Elevation API using the centroid of the provided points.

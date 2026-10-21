"""OpenStreetMap-backed emergency location cache."""
import os
import sqlite3
from datetime import datetime, timedelta, timezone

import osmnx as ox

# Keep the first-time Overpass lookup bounded so opening the dashboard cannot
# wait on a stalled public endpoint for several minutes.
ox.settings.requests_timeout = 30


PROJECT_ROOT = os.path.dirname(os.path.dirname(__file__))
DATABASE_PATH = os.getenv(
    "EMERGENCY_DATABASE_PATH",
    os.path.join(PROJECT_ROOT, "cache", "emergency_locations.sqlite3"),
)
LOCATION_TYPES = {
    "hospital", "shelter", "assembly_point", "fire_station",
    "police_station", "emergency_center",
}
OSM_TAGS = {
    "amenity": ["hospital", "shelter", "fire_station", "police", "emergency_service"],
    "emergency": ["assembly_point", "ambulance_station", "fire_service", "police", "disaster_response"],
}
CACHE_MAX_AGE = timedelta(hours=24)


def _connect():
    os.makedirs(os.path.dirname(os.path.abspath(DATABASE_PATH)), exist_ok=True)
    connection = sqlite3.connect(DATABASE_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute(
        """CREATE TABLE IF NOT EXISTS emergency_locations (
            osm_id TEXT PRIMARY KEY, type TEXT NOT NULL, name TEXT NOT NULL,
            latitude REAL NOT NULL, longitude REAL NOT NULL, updated_at TEXT NOT NULL
        )"""
    )
    connection.execute(
        "CREATE TABLE IF NOT EXISTS emergency_cache_meta (id INTEGER PRIMARY KEY CHECK(id=1), fetched_at TEXT NOT NULL)"
    )
    connection.commit()
    return connection


def classify_osm_feature(properties: dict) -> str | None:
    amenity = properties.get("amenity")
    emergency = properties.get("emergency")
    mapping = {
        "hospital": "hospital",
        "shelter": "shelter",
        "fire_station": "fire_station",
        "police": "police_station",
        "emergency_service": "emergency_center",
    }
    if isinstance(amenity, str) and amenity in mapping:
        return mapping[amenity]
    emergency_mapping = {
        "assembly_point": "assembly_point",
        "ambulance_station": "emergency_center",
        "fire_service": "fire_station",
        "police": "police_station",
        "disaster_response": "emergency_center",
    }
    return emergency_mapping.get(emergency) if isinstance(emergency, str) else None


def _cached_locations():
    with _connect() as connection:
        rows = connection.execute(
            "SELECT osm_id, type, name, latitude, longitude FROM emergency_locations ORDER BY type, name"
        ).fetchall()
        meta = connection.execute("SELECT fetched_at FROM emergency_cache_meta WHERE id = 1").fetchone()
    return [dict(row) for row in rows], meta["fetched_at"] if meta else None


def _graph_bbox(graph):
    if graph is None or graph.number_of_nodes() == 0:
        return None
    xs = [float(data["x"]) for _, data in graph.nodes(data=True) if "x" in data]
    ys = [float(data["y"]) for _, data in graph.nodes(data=True) if "y" in data]
    if not xs or not ys:
        return None
    return min(xs), min(ys), max(xs), max(ys)


def _features_to_locations(features, fetched_at: str) -> list[dict]:
    locations = []
    for (element_type, osm_id), row in features.iterrows():
        location_type = classify_osm_feature(row)
        geometry = row.get("geometry")
        if location_type not in LOCATION_TYPES or geometry is None or not hasattr(geometry, "geom_type") or geometry.is_empty:
            continue
        point = geometry if geometry.geom_type == "Point" else geometry.representative_point()
        if not (-90 <= point.y <= 90 and -180 <= point.x <= 180):
            continue
        name = row.get("name")
        if not isinstance(name, str) or not name.strip():
            name = f"Unnamed {location_type.replace('_', ' ')}"
        locations.append({
            "osm_id": f"{element_type}/{osm_id}",
            "type": location_type,
            "name": name.strip(),
            "latitude": float(point.y),
            "longitude": float(point.x),
            "updated_at": fetched_at,
        })
    return locations


def _refresh(graph):
    bbox = _graph_bbox(graph)
    if bbox is None:
        raise RuntimeError("The local road graph has no usable bounds")
    features = ox.features_from_bbox(bbox, tags=OSM_TAGS)
    fetched_at = datetime.now(timezone.utc).isoformat()
    locations = _features_to_locations(features, fetched_at)
    with _connect() as connection:
        connection.execute("DELETE FROM emergency_locations")
        connection.executemany(
            "INSERT INTO emergency_locations (osm_id, type, name, latitude, longitude, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            [(item["osm_id"], item["type"], item["name"], item["latitude"], item["longitude"], fetched_at) for item in locations],
        )
        connection.execute(
            "INSERT INTO emergency_cache_meta (id, fetched_at) VALUES (1, ?) ON CONFLICT(id) DO UPDATE SET fetched_at=excluded.fetched_at",
            (fetched_at,),
        )
    return locations, fetched_at


def get_locations(graph, *, force_refresh: bool = False) -> dict:
    locations, fetched_at = _cached_locations()
    now = datetime.now(timezone.utc)
    cache_time = None
    if fetched_at:
        cache_time = datetime.fromisoformat(fetched_at)
        if cache_time.tzinfo is None:
            cache_time = cache_time.replace(tzinfo=timezone.utc)
    cache_is_fresh = cache_time is not None and now - cache_time < CACHE_MAX_AGE
    if force_refresh or not cache_is_fresh:
        try:
            locations, fetched_at = _refresh(graph)
            cache_time = datetime.fromisoformat(fetched_at)
        except Exception as exc:
            if fetched_at is None:
                return {
                    "locations": [], "source": "OpenStreetMap", "fetched_at": None,
                    "stale": True, "warning": "OSM location data is unavailable and no local cache exists.",
                }
            warning = f"Could not refresh OpenStreetMap data ({type(exc).__name__}); showing the saved snapshot."
        else:
            warning = None
    else:
        warning = None
    stale = cache_time is None or now - cache_time >= CACHE_MAX_AGE
    return {
        "locations": locations,
        "source": "OpenStreetMap",
        "fetched_at": fetched_at,
        "stale": stale,
        "warning": warning,
    }

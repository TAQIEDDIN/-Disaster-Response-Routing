"""
Rota Hesaplama Motoru
=====================
Enkaz lokasyonlarını harita ağına entegre edip güvenli rota hesaplar.

Eski yaklaşım:  Tek node sil → shortest_path
Yeni yaklaşım:  Yarıçaplı edge ağırlıklandırma → weighted shortest_path + alternatif rota
"""
import logging
import math
import os
import ast
import re
import osmnx as ox
import networkx as nx
from backend.config import DANGER_WEIGHT_MULTIPLIER

logger = logging.getLogger(__name__)

# osmnx cache ayarları
ox.settings.use_cache = True
ox.settings.log_console = False

# Global harita grafiği (sunucu açılışında bir kez yüklenir)
G_REGION = None

ROAD_STATUSES = {"open", "blocked", "restricted", "unknown"}
ROUTING_MODES = {"fastest", "lowest_risk", "balanced"}
STATUS_RISK = {"open": 0.0, "restricted": 0.35, "unknown": 0.55, "blocked": 1.0}
DEFAULT_SPEED_KPH = {
    "motorway": 90.0, "trunk": 70.0, "primary": 60.0,
    "secondary": 50.0, "tertiary": 40.0, "residential": 30.0,
    "living_street": 15.0, "service": 15.0,
}


def load_city_graph(city_name: str) -> None:
    """Şehir yol ağını RAM'e yükler. Sunucu başlangıcında 1 kez çağrılır."""
    global G_REGION
    
    # Yerel bir .graphml dosyası var mı kontrol et (Daha hızlı yükleme için)
    cache_file = "antakya_graph.graphml"
    
    if os.path.exists(cache_file):
        logger.info(f"📂 Yerel harita dosyası bulundu: {cache_file}. Yükleniyor...")
        try:
            G_REGION = ox.load_graphml(filepath=cache_file)
            logger.info(f"✅ Yerel harita hazır! ({G_REGION.number_of_nodes()} düğüm)")
            return
        except Exception as e:
            logger.warning(f"⚠️ Yerel dosya okunamadı, OSM'den denenecek: {e}")

    logger.info(f"🌍 {city_name} yol ağı OSM üzerinden indiriliyor/yükleniyor...")
    try:
        G_REGION = ox.graph_from_place(city_name, network_type="drive")
        # Sonraki sefer için kaydet
        ox.save_graphml(G_REGION, filepath=cache_file)
        logger.info(f"✅ {city_name} yol ağı hazır! ({G_REGION.number_of_nodes()} düğüm)")
    except Exception as e:
        logger.error(f"❌ Harita yüklenemedi: {e}")
        raise


def _haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """İki GPS noktası arasındaki mesafeyi metre cinsinden hesaplar (Haversine formülü)."""
    R = 6_371_000  # Dünya yarıçapı (metre)
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _dist_to_segment(p_lat: float, p_lon: float, a_lat: float, a_lon: float, b_lat: float, b_lon: float) -> float:
    """
    P (Enkaz) noktasının AB doğru parçasına (yol) olan en kısa mesafesini (metre) hesaplar.
    Düzlemsel geometri (Equirectangular projeksiyon) yaklaşımı kullanılır.
    """
    cos_lat = math.cos(math.radians(p_lat))
    
    # Derece cinsinden XY düzlemine oturt
    px, py = p_lon * cos_lat, p_lat
    ax, ay = a_lon * cos_lat, a_lat
    bx, by = b_lon * cos_lat, b_lat
    
    l2 = (ax - bx)**2 + (ay - by)**2
    if l2 == 0:
        return _haversine_distance(p_lat, p_lon, a_lat, a_lon)
        
    # P noktasından AB doğrusuna inen dikmenin t parametresi (sınırlandırılmış: [0, 1])
    t = max(0, min(1, ((px - ax) * (bx - ax) + (py - ay) * (by - ay)) / l2))
    
    proj_x = ax + t * (bx - ax)
    proj_y = ay + t * (by - ay)
    
    proj_lon = proj_x / cos_lat
    proj_lat = proj_y
    
    return _haversine_distance(p_lat, p_lon, proj_lat, proj_lon)


def _apply_danger_weights(
    G: nx.MultiDiGraph,
    debris_list: list[dict]
) -> nx.MultiDiGraph:
    """
    Enkaz noktaları etrafındaki edge'lere tehlike ağırlığı uygular.
    
    PERFORMANS İYİLEŞTİRMESİ:
    Sadece enkazın yakınındaki (bounding box) edge'leri kontrol eder.
    O(Debris * Edges) karmaşıklığını büyük ölçüde azaltır.
    """
    # Sabit: 1 derece enlem/boylam yaklaşık kaç metre? (Antakya civarı)
    METERS_PER_DEG = 111320.0
    
    # Tüm edge'lere orijinal ağırlığı kaydet (yoksa length kullan)
    for u, v, k, data in G.edges(keys=True, data=True):
        if "danger_weight" not in data:
            data["danger_weight"] = float(data.get("length", 100))

    for debris in debris_list:
        d_lat = debris["lat"]
        d_lon = debris["lon"]
        radius = debris["danger_radius_m"]
        
        # Mekansal Filtre: Sokaklar çok uzun olabileceği için margin'i devasa tut (örn: radius + 2000m)
        # Eğer sokak segmenti 2km'den uzun değilse (ki şehir içinde imkansız) asla gözden kaçırmaz.
        lat_margin = (radius + 2000.0) / METERS_PER_DEG
        lon_margin = (radius + 2000.0) / (METERS_PER_DEG * 0.8) # cos(36) ~ 0.8
        
        affected_edges = 0

        for u, v, k, data in G.edges(keys=True, data=True):
            u_lat = G.nodes[u]["y"]
            u_lon = G.nodes[u]["x"]
            v_lat = G.nodes[v]["y"]
            v_lon = G.nodes[v]["x"]
            
            mid_lat = (u_lat + v_lat) / 2
            mid_lon = (u_lon + v_lon) / 2

            # HIZLI FİLTRE: Bounding Box. U, V veya Mid noktalarından en az BİRİ margin içindeyse hesapla
            if (abs(u_lat - d_lat) > lat_margin and abs(v_lat - d_lat) > lat_margin and abs(mid_lat - d_lat) > lat_margin) or \
               (abs(u_lon - d_lon) > lon_margin and abs(v_lon - d_lon) > lon_margin and abs(mid_lon - d_lon) > lon_margin):
                continue

            # Noktanın doğru parçasına dik olan EN KISA mesafesini hesapla
            distance = _dist_to_segment(d_lat, d_lon, u_lat, u_lon, v_lat, v_lon)

            # Sadece radius kadar değil, %20 güvenlik payıyla (radius * 1.2) kontrol edelim
            if distance < (radius * 1.2):
                # Kademeli ceza (multiplier) yerine devasa SABİT bir ceza ekliyoruz (+ 1000 km).
                # Böylece yol kenarından bile geçse kesinlikle o sokağa girmeyi reddedecek.
                data["danger_weight"] += 1_000_000.0
                affected_edges += 1

        if affected_edges > 0:
            logger.info(
                f"Enkaz ({d_lat:.5f}, {d_lon:.5f}): "
                f"yarıçap={radius}m, etkilenen_kenar={affected_edges}"
            )

    return G


def _scalar(value):
    """Normalize OSMnx/GraphML list-like tag values without assuming a type."""
    if isinstance(value, (list, tuple, set)):
        return next(iter(value), None)
    if isinstance(value, str) and value.startswith("["):
        try:
            parsed = ast.literal_eval(value)
            if isinstance(parsed, (list, tuple)):
                return next(iter(parsed), None)
        except (ValueError, SyntaxError):
            pass
    return value


def _parse_speed_kph(value, highway) -> float:
    """Use OSM maxspeed where available; otherwise use documented road-class estimates."""
    parsed = _scalar(value)
    if parsed is not None:
        try:
            raw_speed = str(parsed).lower()
            match = re.search(r"\d+(?:\.\d+)?", raw_speed)
            speed = float(match.group()) if match else 0.0
            if "mph" in raw_speed:
                speed *= 1.60934
            if speed > 0:
                return speed
        except (TypeError, ValueError):
            pass
    road_class = str(_scalar(highway) or "").lower()
    for prefix, speed in DEFAULT_SPEED_KPH.items():
        if road_class == prefix or road_class.startswith(prefix + "_"):
            return speed
    return 25.0


def _osmid_tokens(value) -> set[str]:
    if isinstance(value, str) and value.startswith("["):
        try:
            value = ast.literal_eval(value)
        except (ValueError, SyntaxError):
            pass
    if isinstance(value, (list, tuple, set)):
        return {str(item) for item in value}
    return set() if value is None else {str(value)}


def _status_overrides(road_statuses) -> tuple[dict[str, str], dict[tuple[str, str, str], str]]:
    """Build status lookups by OSM way id or explicit graph edge (u, v, key)."""
    by_osmid, by_edge = {}, {}
    for item in road_statuses or []:
        if not isinstance(item, dict):
            raise ValueError("road_statuses items must be objects")
        status = str(item.get("status", "unknown")).lower()
        if status not in ROAD_STATUSES:
            raise ValueError(f"Unknown road status: {status}")
        if item.get("osmid") is not None:
            by_osmid[str(item["osmid"])] = status
        elif all(item.get(key) is not None for key in ("u", "v")):
            by_edge[(str(item["u"]), str(item["v"]), str(item.get("key", 0)))] = status
        else:
            raise ValueError("Each road status needs osmid or u/v edge identifiers")
    return by_osmid, by_edge


def verified_blocked_road_override(G: nx.MultiDiGraph, latitude: float, longitude: float,
                                   max_offset_m: float = 75.0) -> list[dict]:
    """Map a verified road-closure report to its nearest OSM way, if close enough."""
    return verified_blocked_road_overrides(G, [{"latitude": latitude, "longitude": longitude}], max_offset_m)


def verified_blocked_road_overrides(G: nx.MultiDiGraph, reports: list[dict],
                                    max_offset_m: float = 75.0) -> list[dict]:
    """Batch-map verified closure reports to nearby OSM ways."""
    points = []
    for report in reports:
        try:
            points.append((float(report["longitude"]), float(report["latitude"])))
        except (KeyError, TypeError, ValueError):
            continue
    if not points:
        return []
    try:
        is_projected = ox.projection.is_projected(G.graph.get("crs", "epsg:4326"))
        if is_projected:
            from shapely.geometry import Point
            projected_points = [
                ox.projection.project_geometry(Point(x, y), crs="epsg:4326", to_crs=G.graph["crs"])[0]
                for x, y in points
            ]
            xs, ys = [point.x for point in projected_points], [point.y for point in projected_points]
        else:
            xs, ys = [point[0] for point in points], [point[1] for point in points]
        edges, distances = ox.distance.nearest_edges(G, X=xs, Y=ys, return_dist=True)
        overrides = {}
        for edge, distance in zip(edges, distances):
            distance_m = float(distance) if is_projected else float(distance) * 111_320.0
            if distance_m > max_offset_m:
                continue
            u, v, key = edge
            edge_data = G.get_edge_data(u, v, key) or {}
            for token in sorted(_osmid_tokens(edge_data.get("osmid"))):
                overrides[token] = {"osmid": token, "status": "blocked"}
        return list(overrides.values())
    except (KeyError, TypeError, ValueError, RuntimeError):
        logger.warning("Could not map a verified blocked-road report to the local road graph")
        return []


def _edge_status(data: dict, u, v, key, by_osmid, by_edge) -> str:
    override = by_edge.get((str(u), str(v), str(key)))
    if override is None:
        override = next((by_osmid[token] for token in _osmid_tokens(data.get("osmid")) if token in by_osmid), None)
    raw = override if override is not None else data.get("road_status", data.get("status"))
    status = str(_scalar(raw) or "unknown").lower()
    if status not in ROAD_STATUSES:
        status = "unknown"
    # OSM access=no is a restriction, not proof of a disaster-related closure.
    if override is None and raw is None and str(_scalar(data.get("access")) or "").lower() == "no":
        status = "restricted"
    return status


def _prepare_route_graph(G: nx.MultiDiGraph, debris_list: list[dict], road_statuses, mode: str):
    by_osmid, by_edge = _status_overrides(road_statuses)
    route_graph = G.copy()
    blocked_edges = []

    for u, v, key, data in list(route_graph.edges(keys=True, data=True)):
        status = _edge_status(data, u, v, key, by_osmid, by_edge)
        data["_route_status"] = status
        data["_risk"] = STATUS_RISK[status]
        if status == "blocked":
            blocked_edges.append((u, v, key))
            continue

        try:
            length = max(float(data.get("length", 1.0)), 0.1)
        except (TypeError, ValueError):
            length = 1.0
        speed_kph = _parse_speed_kph(data.get("maxspeed"), data.get("highway"))
        data["_length_m"] = length
        data["_travel_time_s"] = length / (speed_kph / 3.6)
        data["_hazard"] = False

    for u, v, key in blocked_edges:
        route_graph.remove_edge(u, v, key)

    # Map AI/manual debris to risk exposure; these are risk signals, not confirmed road closures.
    for debris in debris_list or []:
        try:
            d_lat, d_lon = float(debris["lat"]), float(debris["lon"])
            radius = max(float(debris.get("danger_radius_m", 50)), 0.0) * 1.2
        except (KeyError, TypeError, ValueError):
            continue
        lat_margin = (radius + 2000.0) / 111320.0
        lon_margin = (radius + 2000.0) / (111320.0 * max(math.cos(math.radians(d_lat)), 0.1))
        for u, v, key, data in route_graph.edges(keys=True, data=True):
            u_lat, u_lon = route_graph.nodes[u]["y"], route_graph.nodes[u]["x"]
            v_lat, v_lon = route_graph.nodes[v]["y"], route_graph.nodes[v]["x"]
            if (abs(u_lat - d_lat) > lat_margin and abs(v_lat - d_lat) > lat_margin) or \
               (abs(u_lon - d_lon) > lon_margin and abs(v_lon - d_lon) > lon_margin):
                continue
            if _dist_to_segment(d_lat, d_lon, u_lat, u_lon, v_lat, v_lon) <= radius:
                data["_hazard"] = True
                try:
                    hazard_risk = float(debris.get("risk_score", 0.9))
                    if hazard_risk > 1:
                        hazard_risk /= 100
                except (TypeError, ValueError):
                    hazard_risk = 0.9
                data["_risk"] = max(data["_risk"], min(max(hazard_risk, 0.0), 1.0))

    for _, _, _, data in route_graph.edges(keys=True, data=True):
        risk = min(max(float(data["_risk"]), 0.0), 1.0)
        if mode == "fastest":
            cost = data["_travel_time_s"]
        elif mode == "lowest_risk":
            cost = data["_length_m"] * (0.05 + 1.5 * risk)
        else:  # balanced: travel time with a substantial, explicit risk cost
            cost = data["_travel_time_s"] * (1.0 + 3.0 * risk)
        data["_route_cost"] = max(float(cost), 0.001)

    return route_graph


def _best_edge_data(G: nx.MultiDiGraph, u, v) -> dict:
    edges = G.get_edge_data(u, v)
    if not edges:
        raise RuntimeError(f"Missing graph edge {u}->{v}")
    return min(edges.values(), key=lambda item: item.get("_route_cost", float("inf")))


def _route_details(G: nx.MultiDiGraph, nodes: list, start_lat: float, start_lon: float,
                   end_lat: float, end_lon: float) -> tuple[list[list[float]], dict]:
    coords = [[start_lat, start_lon]]
    distance_m, travel_time_s, risk_distance = 0.0, 0.0, 0.0
    unknown_segments = []
    for i, node in enumerate(nodes):
        node_data = G.nodes[node]
        if i == 0:
            distance_m += _haversine_distance(start_lat, start_lon, node_data["y"], node_data["x"])
            travel_time_s += _haversine_distance(start_lat, start_lon, node_data["y"], node_data["x"]) / (25.0 / 3.6)
            coords.append([node_data["y"], node_data["x"]])
            continue

        u, v = nodes[i - 1], node
        edge = _best_edge_data(G, u, v)
        edge_length = edge["_length_m"]
        distance_m += edge_length
        travel_time_s += edge["_travel_time_s"]
        risk_distance += edge_length * edge["_risk"]
        if edge["_route_status"] == "unknown":
            unknown_segments.append({
                "from": [G.nodes[u]["y"], G.nodes[u]["x"]],
                "to": [G.nodes[v]["y"], G.nodes[v]["x"]],
            })
        if "geometry" in edge:
            edge_coords = list(edge["geometry"].coords)
            coords.extend([[lat, lon] for lon, lat in edge_coords[1:]])
        else:
            coords.append([node_data["y"], node_data["x"]])

    end_node = G.nodes[nodes[-1]]
    end_snap_m = _haversine_distance(end_node["y"], end_node["x"], end_lat, end_lon)
    distance_m += end_snap_m
    travel_time_s += end_snap_m / (25.0 / 3.6)
    coords.append([end_lat, end_lon])

    risk_score = (risk_distance / max(distance_m - end_snap_m, 0.1)) * 100.0
    metrics = {
        "distance_m": round(distance_m, 1),
        "estimated_time_s": round(travel_time_s),
        "risk_score": round(risk_score, 1),
        "unknown_segments": unknown_segments,
    }
    return coords, metrics


def calculate_route(
    nw_lat: float, nw_lon: float,
    se_lat: float, se_lon: float,
    start_lat: float, start_lon: float,
    end_lat: float, end_lon: float,
    debris_list: list[dict],
    mode: str = "balanced",
    road_statuses: list[dict] | None = None,
) -> dict:
    """Calculate a primary and alternative route while excluding confirmed blocked edges.

    ``road_statuses`` accepts records with ``osmid`` or graph edge ``u``/``v``/optional ``key``
    plus one of open, blocked, restricted, or unknown. OSMnx edges without a known status remain
    unknown. Travel time uses OSM maxspeed or a documented road-class estimate.
    """
    if G_REGION is None:
        raise RuntimeError("The road map is not loaded. Call load_city_graph() first.")
    mode = str(mode).lower()
    if mode not in ROUTING_MODES:
        raise ValueError(f"Unsupported routing mode: {mode}")

    route_graph = _prepare_route_graph(G_REGION, debris_list, road_statuses, mode)
    try:
        start_node = ox.distance.nearest_nodes(route_graph, X=start_lon, Y=start_lat)
        end_node = ox.distance.nearest_nodes(route_graph, X=end_lon, Y=end_lat)
    except Exception as e:
        logger.error(f"Düğüm bulunamadı: {e}")
        raise RuntimeError("The start or destination point is not close enough to the road network.")

    if start_node == end_node:
        start_coord = [route_graph.nodes[start_node]["y"], route_graph.nodes[start_node]["x"]]
        connector_m = _haversine_distance(start_lat, start_lon, end_lat, end_lon)
        return {
            "primary_route": [[start_lat, start_lon], start_coord, [end_lat, end_lon]],
            "alternative_route": None,
            "routing_mode": mode,
            "primary_metrics": {"distance_m": round(connector_m, 1), "estimated_time_s": round(connector_m / (25 / 3.6)), "risk_score": 0.0, "unknown_segments": []},
            "alternative_metrics": None,
        }

    try:
        primary_nodes = nx.shortest_path(route_graph, start_node, end_node, weight="_route_cost")
    except nx.NetworkXNoPath as exc:
        # Never fall back to an unweighted path: that could cross a confirmed closed road.
        raise RuntimeError("No route found; removing blocked roads left no connected path.") from exc

    primary_coords, primary_metrics = _route_details(route_graph, primary_nodes, start_lat, start_lon, end_lat, end_lon)

    alternative_graph = route_graph.copy()
    for u, v in zip(primary_nodes, primary_nodes[1:]):
        for edge in alternative_graph[u][v].values():
            edge["_route_cost"] *= 1.5
    alternative_coords, alternative_metrics = None, None
    try:
        alternative_nodes = nx.shortest_path(alternative_graph, start_node, end_node, weight="_route_cost")
        if alternative_nodes != primary_nodes:
            alternative_coords, alternative_metrics = _route_details(alternative_graph, alternative_nodes, start_lat, start_lon, end_lat, end_lon)
    except nx.NetworkXNoPath:
        pass

    logger.info(
        f"✅ Rota hazır ({mode}): {primary_metrics['distance_m']}m, "
        f"risk={primary_metrics['risk_score']}, unknown={len(primary_metrics['unknown_segments'])}"
    )
    return {
        "primary_route": primary_coords,
        "alternative_route": alternative_coords,
        "routing_mode": mode,
        "primary_metrics": primary_metrics,
        "alternative_metrics": alternative_metrics,
    }

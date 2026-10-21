"""
Drone / Helikopter Analiz Motoru (v2)
=====================================
Drone görüntüsünü ai_engine.py ile analiz eder (SAHI + NMS),
ve rotayı routing_engine.py ile gerçek sokak ağı üzerinden hesaplar.

Artık kendi AI istemcisi veya piksel A* mantığı YOKTUR.
Tüm iş, ana motorlara delege edilmiştir.
"""
import logging
from backend.ai_engine import analyze_image, pixel_to_gps

logger = logging.getLogger(__name__)


def gps_to_pixel(
    lat: float, lon: float,
    img_w: int, img_h: int,
    nw_lat: float, nw_lon: float,
    se_lat: float, se_lon: float
) -> tuple[float, float]:
    """
    GPS koordinatını piksel koordinatına çevirir.
    pixel_to_gps'in tersi.
    """
    lat_range = nw_lat - se_lat
    lon_range = se_lon - nw_lon

    if lat_range == 0 or lon_range == 0:
        return (0.0, 0.0)

    px = ((lon - nw_lon) / lon_range) * img_w
    py = ((nw_lat - lat) / lat_range) * img_h
    return (px, py)


def analyze_drone_image_v2(
    image_path: str,
    img_w: int, img_h: int,
    nw_lat: float, nw_lon: float,
    se_lat: float, se_lon: float
) -> list[dict]:
    """
    Drone görüntüsünü ai_engine.py ile analiz eder.
    Sonuçları hem GPS hem piksel koordinatlarıyla döndürür.
    
    Bu fonksiyon, ai_engine.analyze_image() ile BİREBİR aynı pipeline'ı
    kullanır: SAHI tile + NMS + Risk Skoru + GPS dönüşüm.
    """
    logger.info(f"🚁 Drone SAHI analizi başlatılıyor: {image_path} ({img_w}x{img_h})")
    logger.info(f"   GPS Sınırları: NW({nw_lat:.5f},{nw_lon:.5f}) → SE({se_lat:.5f},{se_lon:.5f})")

    # ai_engine.py'nin tam pipeline'ını çağır
    debris_list = analyze_image(
        image_path, img_w, img_h,
        nw_lat, nw_lon, se_lat, se_lon
    )

    logger.info(f"🚁 Drone SAHI analizi tamamlandı: {len(debris_list)} enkaz tespit edildi")
    return debris_list


def route_gps_to_pixels(
    gps_route: list[list[float]],
    img_w: int, img_h: int,
    nw_lat: float, nw_lon: float,
    se_lat: float, se_lon: float
) -> list[list[float]]:
    """
    GPS rotasını (routing_engine'den dönen [[lat,lon],...]) piksel
    koordinatlarına çevirir, böylece canvas üzerine çizilebilir.
    Görüntü sınırları dışına çıkan noktalar kırpılır (clamp).
    """
    pixel_route = []
    for point in gps_route:
        lat, lon = point[0], point[1]
        px, py = gps_to_pixel(lat, lon, img_w, img_h, nw_lat, nw_lon, se_lat, se_lon)
        # Clamp: Piksel koordinatlarini goruntu sinirlari icinde tut
        px = max(0, min(img_w, px))
        py = max(0, min(img_h, py))
        pixel_route.append([round(px, 1), round(py, 1)])
    return pixel_route

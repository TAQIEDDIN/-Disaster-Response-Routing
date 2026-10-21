"""
AI Görüntü Analiz Motoru
========================
Uydu/drone görüntülerinden enkaz tespiti yapar.
- Görüntü ön-işleme (CLAHE kontrast iyileştirme)
- Roboflow inference ile enkaz tespiti
- Piksel → GPS dönüşümü (Mercator düzeltmeli)
- Enkaz büyüklüğüne göre etki yarıçapı hesabı
"""
import logging
import math
import cv2
import numpy as np
from inference_sdk import InferenceHTTPClient, InferenceConfiguration
from backend.config import (
    ROBOFLOW_API_KEY, ROBOFLOW_API_URL, ROBOFLOW_MODEL_ID,
    AI_CONFIDENCE, AI_IOU_THRESHOLD, DANGER_RADIUS_BASE
)

logger = logging.getLogger(__name__)


class InferenceServiceError(RuntimeError):
    """The configured remote inference service could not complete a request."""

# Lazy initialization keeps API keys out of source defaults and allows the app
# to start even when cloud inference has not been configured yet.
_client = None


def _get_client():
    global _client
    if not ROBOFLOW_API_KEY:
        raise InferenceServiceError("Roboflow API key is not configured")
    if _client is None:
        _client = InferenceHTTPClient(api_url=ROBOFLOW_API_URL, api_key=ROBOFLOW_API_KEY)
    return _client


def preprocess_image(image_path: str) -> str:
    """
    Görüntüyü AI modeline göndermeden önce optimize eder.
    
    1. CLAHE ile adaptif kontrast iyileştirme
       - Uydu görüntülerinde yıkık binalar genelde düşük kontrastla görünür
       - CLAHE, lokal kontrast artırarak detayları belirginleştirir
    2. Hafif keskinleştirme (unsharp mask)
    3. Optimal boyuta resize (model 640x640 için eğitilmiş)
    
    Returns:
        Ön-işlenmiş görüntünün kaydedildiği yol
    """
    img = cv2.imread(image_path)
    if img is None:
        logger.error(f"Görüntü okunamadı: {image_path}")
        return image_path

    original_h, original_w = img.shape[:2]
    logger.info(f"Orijinal görüntü boyutu: {original_w}x{original_h}")

    # 1. CLAHE Kontrast İyileştirme (LAB renk uzayında)
    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
    l_channel, a_channel, b_channel = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8))
    l_enhanced = clahe.apply(l_channel)
    lab_enhanced = cv2.merge([l_enhanced, a_channel, b_channel])
    img = cv2.cvtColor(lab_enhanced, cv2.COLOR_LAB2BGR)

    # 2. Hafif keskinleştirme
    gaussian = cv2.GaussianBlur(img, (0, 0), 2.0)
    img = cv2.addWeighted(img, 1.3, gaussian, -0.3, 0)

    # Ön-işlenmiş görüntüyü aynı yola kaydet (üzerine yaz)
    if not cv2.imwrite(image_path, img, [cv2.IMWRITE_JPEG_QUALITY, 95]):
        raise ValueError("The preprocessed image could not be saved.")
    logger.info("Görüntü ön-işleme tamamlandı (CLAHE + keskinleştirme)")

    return image_path


def _infer_single(image_path: str) -> list[dict]:
    """Tek bir görüntü üzerinde Roboflow inference çalıştırır."""
    try:
        client = _get_client()
        client.configure(InferenceConfiguration(
            confidence_threshold=AI_CONFIDENCE,
            iou_threshold=AI_IOU_THRESHOLD,
            api_key_transport="header",
        ))
        result = client.infer(image_path, model_id=ROBOFLOW_MODEL_ID)
    except TypeError:
        try:
            result = client.infer(image_path, model_id=ROBOFLOW_MODEL_ID)
        except Exception as e:
            logger.error("Roboflow inference failed (%s)", type(e).__name__)
            raise InferenceServiceError("AI inference service is unavailable") from e
    except Exception as e:
        logger.error("Roboflow inference failed (%s)", type(e).__name__)
        raise InferenceServiceError("AI inference service is unavailable") from e
    if not isinstance(result, dict) or not isinstance(result.get("predictions", []), list):
        raise InferenceServiceError("AI inference service returned an invalid response")
    return result.get("predictions", [])


def _nms_merge(detections: list[dict], iou_thresh: float = 0.3) -> list[dict]:
    """
    Non-Maximum Suppression — üst üste binen tespitleri birleştirir.
    Farklı tile'lardan gelen aynı enkazın çift sayılmasını önler.
    """
    if not detections:
        return []

    # Confidence'a göre sırala (yüksekten düşüğe)
    sorted_dets = sorted(detections, key=lambda d: d["confidence"], reverse=True)
    keep = []

    while sorted_dets:
        best = sorted_dets.pop(0)
        keep.append(best)

        remaining = []
        for det in sorted_dets:
            if _iou(best, det) < iou_thresh:
                remaining.append(det)
        sorted_dets = remaining

    return keep


def _iou(a: dict, b: dict) -> float:
    """İki bounding box arasındaki IoU (Intersection over Union) hesaplar."""
    ax1 = a["x"] - a["width"] / 2
    ay1 = a["y"] - a["height"] / 2
    ax2 = a["x"] + a["width"] / 2
    ay2 = a["y"] + a["height"] / 2

    bx1 = b["x"] - b["width"] / 2
    by1 = b["y"] - b["height"] / 2
    bx2 = b["x"] + b["width"] / 2
    by2 = b["y"] + b["height"] / 2

    ix1 = max(ax1, bx1)
    iy1 = max(ay1, by1)
    ix2 = min(ax2, bx2)
    iy2 = min(ay2, by2)

    if ix2 <= ix1 or iy2 <= iy1:
        return 0.0

    inter = (ix2 - ix1) * (iy2 - iy1)
    area_a = (ax2 - ax1) * (ay2 - ay1)
    area_b = (bx2 - bx1) * (by2 - by1)
    union = area_a + area_b - inter

    return inter / union if union > 0 else 0.0


def detect_debris(image_path: str) -> list[dict]:
    """
    SAHI (Slicing Aided Hyper Inference) ile enkaz tespiti.
    
    Büyük uydu görüntüsünü küçük tile'lara bölerek her birini ayrı analiz eder.
    Bu sayede küçük enkazlar da yakalanır — model 640x640 için eğitilmiş,
    1756x1538 görüntüde binalar çok küçük kalıp kaçırılıyordu.
    
    Akış:
    1. Görüntüyü ön-işle
    2. Tile boyutuna göre parçala (%20 overlap)
    3. Her tile'ı API'ye gönder
    4. Sonuçları orijinal koordinatlara dönüştür
    5. NMS ile çift tespitleri birleştir
    """
    preprocess_image(image_path)

    img = cv2.imread(image_path)
    if img is None:
        logger.error(f"Görüntü okunamadı: {image_path}")
        return []

    img_h, img_w = img.shape[:2]
    
    # Tile ayarları
    TILE_SIZE = 640
    OVERLAP = 0.20  # %20 örtüşme
    stride = int(TILE_SIZE * (1 - OVERLAP))  # 512 piksel adım

    logger.info(f"SAHI başlatılıyor: görüntü={img_w}x{img_h}, tile={TILE_SIZE}, stride={stride}")

    all_predictions = []
    tile_count = 0
    import tempfile, os

    for y_start in range(0, img_h, stride):
        for x_start in range(0, img_w, stride):
            # Tile'ı kes
            x_end = min(x_start + TILE_SIZE, img_w)
            y_end = min(y_start + TILE_SIZE, img_h)
            
            # Çok küçük kenar tile'larını atla
            if (x_end - x_start) < TILE_SIZE // 2 or (y_end - y_start) < TILE_SIZE // 2:
                continue

            tile = img[y_start:y_end, x_start:x_end]
            tile_count += 1

            # Tile'ı geçici dosyaya kaydet
            tmp = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
            cv2.imwrite(tmp.name, tile, [cv2.IMWRITE_JPEG_QUALITY, 90])
            tmp.close()

            try:
                preds = _infer_single(tmp.name)
                
                # Koordinatları orijinal görüntüye dönüştür
                for p in preds:
                    conf = p.get("confidence", 0)
                    if conf < AI_CONFIDENCE:
                        continue
                    all_predictions.append({
                        "x": p["x"] + x_start,
                        "y": p["y"] + y_start,
                        "width": p.get("width", 0),
                        "height": p.get("height", 0),
                        "confidence": conf,
                        "class": p.get("class", "debris"),
                    })
            finally:
                os.unlink(tmp.name)

    logger.info(f"SAHI tamamlandı: {tile_count} tile tarandı, {len(all_predictions)} ham tespit")

    # Ayrıca tam görüntüyü de bir kez tarat (büyük enkazları yakalamak için)
    full_preds = _infer_single(image_path)
    for p in full_preds:
        conf = p.get("confidence", 0)
        if conf < AI_CONFIDENCE:
            continue
        all_predictions.append({
            "x": p["x"],
            "y": p["y"],
            "width": p.get("width", 0),
            "height": p.get("height", 0),
            "confidence": conf,
            "class": p.get("class", "debris"),
        })

    logger.info(f"Tam görüntü + SAHI toplam: {len(all_predictions)} ham tespit")

    # NMS ile çift tespitleri birleştir
    merged = _nms_merge(all_predictions, iou_thresh=AI_IOU_THRESHOLD)

    # Alan hesapla ve sırala
    detections = []
    for det in merged:
        area_px = det.get("width", 0) * det.get("height", 0)
        det["area_px"] = area_px
        detections.append(det)

    detections.sort(key=lambda d: d["area_px"], reverse=True)
    logger.info(f"NMS sonrası filtrelenmiş tespit: {len(detections)}")

    return detections


def pixel_to_gps(
    px: float, py: float,
    img_w: int, img_h: int,
    nw_lat: float, nw_lon: float,
    se_lat: float, se_lon: float
) -> tuple[float, float]:
    """
    Piksel koordinatını GPS koordinatına dönüştürür.
    Basit lineer interpolasyon — küçük alanlar için yeterli doğrulukta.
    
    Args:
        px, py: Piksel koordinatları
        img_w, img_h: Görüntü boyutları
        nw_lat, nw_lon: Kuzeybatı köşe GPS
        se_lat, se_lon: Güneydoğu köşe GPS
    
    Returns:
        (latitude, longitude) tuple
    """
    lat = nw_lat - (py / img_h) * (nw_lat - se_lat)
    lon = nw_lon + (px / img_w) * (se_lon - nw_lon)
    return (lat, lon)


def calculate_danger_radius(
    area_px: float,
    img_w: int, img_h: int,
    nw_lat: float, nw_lon: float,
    se_lat: float, se_lon: float
) -> float:
    """
    Enkazın piksel alanına göre tehlike yarıçapını metre cinsinden hesaplar.
    
    Mantık:
    - Görüntünün kapsadığı gerçek dünya alanını hesapla
    - Enkazın piksel alanını gerçek dünya alanına orantıla
    - Minimum DANGER_RADIUS_BASE metre, maksimum 200 metre
    """
    # Görüntünün kapsadığı yaklaşık mesafe (metre)
    lat_diff = abs(nw_lat - se_lat)
    lon_diff = abs(se_lon - nw_lon)

    # 1 derece ≈ 111km (enlem), boylam için cos(lat) düzeltmesi
    avg_lat = (nw_lat + se_lat) / 2
    meters_per_deg_lat = 111_320
    meters_per_deg_lon = 111_320 * math.cos(math.radians(avg_lat))

    real_height_m = lat_diff * meters_per_deg_lat
    real_width_m = lon_diff * meters_per_deg_lon

    # Piksel başına metre
    m_per_px_x = real_width_m / img_w if img_w > 0 else 0
    m_per_px_y = real_height_m / img_h if img_h > 0 else 0
    m_per_px = (m_per_px_x + m_per_px_y) / 2

    # Enkaz alanından yarıçap hesapla (daire yaklaşımı: A = π*r²)
    real_area_m2 = area_px * (m_per_px ** 2)
    calculated_radius = math.sqrt(real_area_m2 / math.pi) if real_area_m2 > 0 else 0

    # Güvenlik marjı ekle (enkaz etrafı da tehlikeli)
    danger_radius = max(DANGER_RADIUS_BASE, calculated_radius * 2.5)
    danger_radius = min(danger_radius, 200)  # Makul üst sınır

    return round(danger_radius, 1)


def _calculate_risk_score(class_name: str) -> float:
    """
    Risk scoring heuristic based on the detected damage class.
    Sınıfa göre risk skoru döndürür:
    - collapsed (çökmüş) → 0.95 (kritik tehlike)
    - damaged (hasarlı) → 0.65 (orta tehlike)
    - diğer → 0.20 (düşük tehlike)
    """
    class_lower = class_name.lower()
    if class_lower == 'collapsed':
        return 0.95
    elif class_lower == 'damaged':
        return 0.65
    else:
        return 0.20


def analyze_image(
    image_path: str,
    img_w: int, img_h: int,
    nw_lat: float, nw_lon: float,
    se_lat: float, se_lon: float
) -> list[dict]:
    """
    Tam analiz pipeline: Tespit → GPS dönüşüm → Tehlike yarıçapı → Risk skoru.
    
    Risk scores are heuristic values derived from the detected class.
    
    Returns:
        Her bir enkaz için:
        {
            "lat": float, "lon": float,
            "confidence": float,
            "class": str,
            "danger_radius_m": float,
            "area_px": int,
            "risk_score": float
        }
    """
    detections = detect_debris(image_path)

    results = []
    for det in detections:
        lat, lon = pixel_to_gps(
            det["x"], det["y"],
            img_w, img_h,
            nw_lat, nw_lon, se_lat, se_lon
        )
        radius = calculate_danger_radius(
            det["area_px"],
            img_w, img_h,
            nw_lat, nw_lon, se_lat, se_lon
        )
        risk = _calculate_risk_score(det["class"])
        results.append({
            "lat": round(lat, 7),
            "lon": round(lon, 7),
            "confidence": round(det["confidence"], 3),
            "class": det["class"],
            "danger_radius_m": radius,
            "area_px": det["area_px"],
            "risk_score": risk,
        })

    logger.info(f"Analiz tamamlandı: {len(results)} enkaz, tehlike yarıçapları ve risk skorları hesaplandı")
    return results



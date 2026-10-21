"""
Afet Rota Sistemi — Ana Sunucu
================================
FastAPI web sunucusu. AI analiz motoru ve rota motorunu birleştirir.
"""
import logging
import os
import json
import math
import uuid
from datetime import datetime, timezone
from contextlib import asynccontextmanager

import webbrowser
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from backend.config import HOST, PORT, DEFAULT_CITY, ROBOFLOW_API_KEY
from backend.ai_engine import analyze_image, pixel_to_gps, InferenceServiceError
from backend.analysis_store import save_analysis, get_analysis, list_analyses, analysis_metrics, update_analysis_review
from backend.upload_utils import validate_geo_bounds as _validate_geo_bounds, save_validated_upload as _save_validated_upload
from backend.upload_utils import decode_image_content
from backend import report_store
from backend import emergency_store
from backend.realtime import event_manager
from backend.drone_engine import analyze_drone_image_v2, route_gps_to_pixels, gps_to_pixel
from backend.routing_engine import load_city_graph, calculate_route
from backend.routing_engine import ROUTING_MODES, ROAD_STATUSES, verified_blocked_road_overrides

# Loglama ayarları
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)-20s | %(levelname)-7s | %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger(__name__)
def _include_verified_road_reports(road_statuses):
    from backend.routing_engine import G_REGION
    combined = list(road_statuses or [])
    if G_REGION is None:
        return combined, 0
    reports = report_store.list_reports(report_type="blocked_road", status="Verified", limit=500)
    overrides = verified_blocked_road_overrides(G_REGION, reports)
    combined.extend(overrides)
    return combined, len(overrides)


def _public_report(report: dict) -> dict:
    safe_report = dict(report)
    safe_report.pop("reporter_name", None)
    safe_report.pop("contact", None)
    safe_report.pop("health_status", None)
    return safe_report

def _parse_routing_options(mode: str, road_statuses_json: str | None):
    mode = (mode or "balanced").strip().lower()
    if mode not in ROUTING_MODES:
        raise HTTPException(status_code=422, detail=f"routing_mode must be one of: {', '.join(sorted(ROUTING_MODES))}")
    if not road_statuses_json:
        return mode, None
    try:
        road_statuses = json.loads(road_statuses_json)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=422, detail="road_statuses must be valid JSON") from exc
    if not isinstance(road_statuses, list):
        raise HTTPException(status_code=422, detail="road_statuses must be a JSON array")
    for item in road_statuses:
        if not isinstance(item, dict) or str(item.get("status", "unknown")).lower() not in ROAD_STATUSES:
            raise HTTPException(status_code=422, detail="Each road status must be an object with a supported status")
        if item.get("osmid") is None and not all(item.get(key) is not None for key in ("u", "v")):
            raise HTTPException(status_code=422, detail="Each road status needs an osmid or u/v edge identifiers")
    return mode, road_statuses


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Sunucu başlangıcında haritayı yükle ve tarayıcıyı aç."""
    load_city_graph(DEFAULT_CITY)
    
    # Harita yüklendikten sonra tarayıcıyı otomatik aç
    url = f"http://{HOST}:{PORT}/static/index.html"
    logger.info(f"🌍 Uygulama hazir! Tarayici aciliyor: {url}")
    webbrowser.open(url)
    yield


app = FastAPI(
    title="Afet Rota Sistemi API",
    description="Uydu görüntüsünden enkaz tespiti ve güvenli rota hesaplama",
    version="2.0.0",
    lifespan=lifespan,
)

# Frontend statik dosyaları sun
frontend_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "frontend")
if os.path.isdir(frontend_dir):
    # Ana dizini (index.html) doğrudan / yolundan sun
    @app.get("/")
    async def read_index():
        return FileResponse(os.path.join(frontend_dir, "index.html"))

    @app.get("/service-worker.js", include_in_schema=False)
    async def service_worker():
        # The root scope lets the worker serve the app shell on a later offline visit.
        return FileResponse(
            os.path.join(frontend_dir, "service-worker.js"),
            media_type="application/javascript",
            headers={"Service-Worker-Allowed": "/", "Cache-Control": "no-cache"},
        )
    
    app.mount("/static", StaticFiles(directory=frontend_dir), name="frontend")


@app.post("/api/otonom-analiz")
async def otonom_analiz(
    nw_lat: float = Form(...), nw_lon: float = Form(...),
    se_lat: float = Form(...), se_lon: float = Form(...),
    baslangic_lat: float = Form(...), baslangic_lon: float = Form(...),
    hedef_lat: float = Form(...), hedef_lon: float = Form(...),
    uydu_fotosu: UploadFile = File(...),
    manuel_enkazlar: str = Form(None),
    routing_mode: str = Form("balanced"),
    road_statuses: str = Form(None),
):
    """
    Otonom afet analiz endpoint'i.
    
    1. Uydu fotoğrafını al
    2. AI ile enkaz tespit et
    3. Güvenli rota hesapla
    4. Sonuçları döndür
    """
    logger.info("=" * 50)
    logger.info("📥 YENİ ANALİZ İSTEĞİ")
    logger.info(f"   Viewport: NW({nw_lat:.5f}, {nw_lon:.5f}) → SE({se_lat:.5f}, {se_lon:.5f})")
    logger.info(f"   Başlangıç: ({baslangic_lat:.5f}, {baslangic_lon:.5f})")
    logger.info(f"   Hedef:     ({hedef_lat:.5f}, {hedef_lon:.5f})")
    routing_mode, road_status_list = _parse_routing_options(routing_mode, road_statuses)
    road_status_list, verified_roads_applied = _include_verified_road_reports(road_status_list)
    bounds = _validate_geo_bounds(nw_lat, nw_lon, se_lat, se_lon)
    tmp_path = None

    try:
        tmp_path, img_w, img_h = await _save_validated_upload(uydu_fotosu)
        logger.info(f"📸 Görüntü boyutu: {img_w}x{img_h}")

        # 1. AI ANALIZ
        logger.info("🧠 AI analiz başlatılıyor...")
        debris_list = analyze_image(
            tmp_path, img_w, img_h,
            nw_lat, nw_lon, se_lat, se_lon
        )
        analysis = save_analysis(
            source="map_upload", filename=uydu_fotosu.filename or "map-image",
            image_width=img_w, image_height=img_h, bounds=bounds, detections=debris_list,
        )
        await event_manager.publish("ai_analysis_completed", {
            "analysis_id": analysis["analysis_id"], "source": "map_upload",
            "detection_count": len(debris_list), "ai_status": "prediction_unverified",
        })
        
        # Manuel enkaz isleme (UI'den gelen)
        if manuel_enkazlar:
            import json
            try:
                manual_list = json.loads(manuel_enkazlar)
                for m in manual_list:
                    debris_list.append({
                        "lat": m["lat"],
                        "lon": m["lon"],
                        "confidence": 1.0,
                        "class": "manual_report",
                        "danger_radius_m": m.get("radius", 50),
                        "risk_score": 100
                    })
            except Exception as e:
                logger.error(f"Manuel enkaz parse hatası: {e}")

        logger.info(f"🚨 {len(debris_list)} toplam enkaz (Yapay Zeka + Manuel) tespit/dahil edildi")

        # 2. ROTA HESAPLA
        logger.info("🗺️ Güvenli rota hesaplanıyor...")
        route_result = calculate_route(
            nw_lat, nw_lon, se_lat, se_lon,
            baslangic_lat, baslangic_lon,
            hedef_lat, hedef_lon,
            debris_list,
            mode=routing_mode,
            road_statuses=road_status_list,
        )

        # 3. SONUÇ
        response = {
            "durum": "basarili",
            "analysis_id": analysis["analysis_id"],
            "ai_status": "prediction_unverified",
            "geo_reliable": True,
            "geolocation_note": "Estimated from supplied image bounds; verify in the field.",
            "tespit_sayisi": len(debris_list),
            "enkazlar": [
                {
                    "lat": d["lat"],
                    "lon": d["lon"],
                    "confidence": d["confidence"],
                    "sinif": d["class"],
                    "tehlike_yaricapi_m": d["danger_radius_m"],
                    "risk_score": d["risk_score"],
                }
                for d in debris_list
            ],
            "guvenli_rota": route_result["primary_route"],
            "alternatif_rota": route_result.get("alternative_route"),
            "routing_mode": route_result["routing_mode"],
            "verified_blocked_roads_applied": verified_roads_applied,
            "primary_metrics": route_result["primary_metrics"],
            "alternative_metrics": route_result.get("alternative_metrics"),
        }

        logger.info(f"✅ Analiz tamamlandı: {len(debris_list)} enkaz, rota hazır")
        return response

    except HTTPException:
        raise
    except InferenceServiceError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    except RuntimeError as e:
        logger.error(f"Runtime hatası: {e}")
        raise HTTPException(status_code=502, detail="Analysis could not be completed") from e
    except Exception as e:
        logger.error(f"Beklenmeyen hata: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Analysis could not be completed") from e
    finally:
        # Geçici dosyayı temizle
        try:
            if tmp_path:
                os.unlink(tmp_path)
        except OSError:
            pass


@app.post("/api/goruntu-analiz")
async def goruntu_analiz(
    nw_lat: float = Form(...), nw_lon: float = Form(...),
    se_lat: float = Form(...), se_lon: float = Form(...),
    goruntu: UploadFile = File(...),
):
    """
    Harici görüntü analiz endpoint'i.

    Kullanıcının yüklediği enkaz görüntüsünü AI motoruyla analiz eder.
    Rota hesabı yapmaz, sadece enkaz tespiti ve konum bilgisi döndürür.
    """
    logger.info("=" * 50)
    logger.info("🖼️ HARİCİ GÖRÜNTÜ ANALİZ İSTEĞİ")
    logger.info(f"   Viewport: NW({nw_lat:.5f}, {nw_lon:.5f}) → SE({se_lat:.5f}, {se_lon:.5f})")
    logger.info(f"   Dosya: {goruntu.filename}")
    bounds = _validate_geo_bounds(nw_lat, nw_lon, se_lat, se_lon)
    tmp_path = None

    try:
        tmp_path, img_w, img_h = await _save_validated_upload(goruntu)
        logger.info(f"📸 Görüntü boyutu: {img_w}x{img_h}")

        # AI Analiz
        logger.info("🧠 AI analiz başlatılıyor...")
        debris_list = analyze_image(
            tmp_path, img_w, img_h,
            nw_lat, nw_lon, se_lat, se_lon
        )
        analysis = save_analysis(
            source="image_upload", filename=goruntu.filename or "uploaded-image",
            image_width=img_w, image_height=img_h, bounds=bounds, detections=debris_list,
        )
        await event_manager.publish("ai_analysis_completed", {
            "analysis_id": analysis["analysis_id"], "source": "image_upload",
            "detection_count": len(debris_list), "ai_status": "prediction_unverified",
        })
        logger.info(f"🚨 {len(debris_list)} enkaz tespit edildi")

        response = {
            "durum": "basarili",
            "analysis_id": analysis["analysis_id"],
            "ai_status": "prediction_unverified",
            "geo_reliable": True,
            "geolocation_note": "Estimated from supplied image bounds; verify in the field.",
            "tespit_sayisi": len(debris_list),
            "enkazlar": [
                {
                    "lat": d["lat"],
                    "lon": d["lon"],
                    "confidence": d["confidence"],
                    "sinif": d["class"],
                    "tehlike_yaricapi_m": d["danger_radius_m"],
                    "risk_score": d["risk_score"],
                }
                for d in debris_list
            ],
        }

        logger.info(f"✅ Görüntü analizi tamamlandı: {len(debris_list)} enkaz")
        return response

    except HTTPException:
        raise
    except InferenceServiceError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    except RuntimeError as e:
        logger.error(f"Runtime hatası: {e}")
        raise HTTPException(status_code=502, detail="Analysis could not be completed") from e
    except Exception as e:
        logger.error(f"Beklenmeyen hata: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Analysis could not be completed") from e
    finally:
        try:
            if tmp_path:
                os.unlink(tmp_path)
        except OSError:
            pass


@app.post("/api/drone-analiz")
async def drone_analiz(
    resim: UploadFile = File(...),
    nw_lat: float = Form(...),
    nw_lon: float = Form(...),
    se_lat: float = Form(...),
    se_lon: float = Form(...),
    start_lat: float = Form(...),
    start_lon: float = Form(...),
    end_lat: float = Form(...),
    end_lon: float = Form(...),
    manuel_enkazlar: str = Form(None),
    routing_mode: str = Form("balanced"),
    road_statuses: str = Form(None),
):
    """
    Drone/Helikopter Modu v2:
    1. Görüntüyü ai_engine.py ile SAHI analiz et (tile-based, NMS)
    2. routing_engine.py ile gerçek sokak ağında güvenli rota hesapla
    3. GPS rotayı piksel koordinatlarına çevirip döndür (canvas çizimi için)
    """
    logger.info("=" * 50)
    logger.info(f"🚁 DRONE ANALİZ v2 İSTEĞİ")
    logger.info(f"   GPS Sınırları: NW({nw_lat:.5f},{nw_lon:.5f}) → SE({se_lat:.5f},{se_lon:.5f})")
    logger.info(f"   Başlangıç: ({start_lat:.5f},{start_lon:.5f})")
    logger.info(f"   Hedef: ({end_lat:.5f},{end_lon:.5f})")
    routing_mode, road_status_list = _parse_routing_options(routing_mode, road_statuses)
    road_status_list, verified_roads_applied = _include_verified_road_reports(road_status_list)
    bounds = _validate_geo_bounds(nw_lat, nw_lon, se_lat, se_lon)
    tmp_path = None
    try:
        tmp_path, img_w, img_h = await _save_validated_upload(resim)
        logger.info(f"📸 Drone görüntüsü kaydedildi: {tmp_path}")
        logger.info(f"📸 Görüntü boyutu: {img_w}x{img_h}")

        # 2. SAHI AI Analiz (ai_engine.py — Ana harita ile aynı motor)
        logger.info("🧠 Drone SAHI analizi başlatılıyor...")
        debris_list = analyze_drone_image_v2(
            tmp_path, img_w, img_h,
            nw_lat, nw_lon, se_lat, se_lon
        )
        analysis = save_analysis(
            source="drone_upload", filename=resim.filename or "drone-image",
            image_width=img_w, image_height=img_h, bounds=bounds, detections=debris_list,
        )
        await event_manager.publish("ai_analysis_completed", {
            "analysis_id": analysis["analysis_id"], "source": "drone_upload",
            "detection_count": len(debris_list), "ai_status": "prediction_unverified",
        })
        
        # Manuel enkaz isleme (UI'den gelen)
        if manuel_enkazlar:
            import json
            try:
                manual_list = json.loads(manuel_enkazlar)
                for m in manual_list:
                    debris_list.append({
                        "lat": m["lat"],
                        "lon": m["lon"],
                        "confidence": 1.0,
                        "class": "manual_report",
                        "danger_radius_m": m.get("radius", 50),
                        "risk_score": 100
                    })
            except Exception as e:
                logger.error(f"Manuel enkaz parse hatası: {e}")

        logger.info(f"🚨 {len(debris_list)} toplam enkaz (Yapay Zeka + Manuel) tespit/dahil edildi")

        # 3. Güvenli Rota Hesapla (routing_engine.py — Gerçek sokak ağı)
        logger.info("🗺️ Drone rotası hesaplanıyor (OSM sokak ağı)...")
        route_result = calculate_route(
            nw_lat, nw_lon, se_lat, se_lon,
            start_lat, start_lon,
            end_lat, end_lon,
            debris_list,
            mode=routing_mode,
            road_statuses=road_status_list,
        )

        # 4. GPS rotayı piksel koordinatlarına çevir (canvas çizimi için)
        pixel_route = route_gps_to_pixels(
            route_result["primary_route"],
            img_w, img_h,
            nw_lat, nw_lon, se_lat, se_lon
        )

        pixel_alt_route = None
        if route_result.get("alternative_route"):
            pixel_alt_route = route_gps_to_pixels(
                route_result["alternative_route"],
                img_w, img_h,
                nw_lat, nw_lon, se_lat, se_lon
            )

        # 5. Engel piksel koordinatlarını da hesapla
        engeller_with_pixels = []
        for d in debris_list:
            px, py = gps_to_pixel(
                d["lat"], d["lon"],
                img_w, img_h,
                nw_lat, nw_lon, se_lat, se_lon
            )
            engeller_with_pixels.append({
                "lat": d["lat"],
                "lon": d["lon"],
                "x": round(px),
                "y": round(py),
                "w": round(d.get("area_px", 400) ** 0.5),  # Yaklaşık genişlik
                "h": round(d.get("area_px", 400) ** 0.5),  # Yaklaşık yükseklik
                "confidence": d["confidence"],
                "sinif": d["class"],
                "risk_score": d["risk_score"],
                "tehlike_yaricapi_m": d["danger_radius_m"],
            })

        response = {
            "durum": "basarili",
            "analysis_id": analysis["analysis_id"],
            "ai_status": "prediction_unverified",
            "geo_reliable": True,
            "geolocation_note": "Estimated from supplied image bounds; verify in the field.",
            "tespit_sayisi": len(debris_list),
            "engeller": engeller_with_pixels,
            "rota": pixel_route,
            "rota_alt": pixel_alt_route,
            "gps_rota": route_result["primary_route"],
            "gps_rota_alt": route_result.get("alternative_route"),
            "routing_mode": route_result["routing_mode"],
            "verified_blocked_roads_applied": verified_roads_applied,
            "primary_metrics": route_result["primary_metrics"],
            "alternative_metrics": route_result.get("alternative_metrics"),
        }

        logger.info(f"✅ Drone analizi tamamlandı: {len(debris_list)} enkaz, rota: {len(pixel_route)} nokta")
        return response

    except HTTPException:
        raise
    except InferenceServiceError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    except RuntimeError as e:
        logger.error(f"Runtime hatası: {e}")
        raise HTTPException(status_code=502, detail="Analysis could not be completed") from e
    except Exception as e:
        logger.error(f"Drone analiz hatası: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Analysis could not be completed") from e
    finally:
        try:
            if tmp_path:
                os.unlink(tmp_path)
        except OSError:
            pass


@app.get("/api/health")
def health_check():
    """Sunucu sağlık kontrolü."""
    from backend.routing_engine import G_REGION
    return {
        "status": "ok",
        "city": DEFAULT_CITY,
        "graph_loaded": G_REGION is not None,
        "nodes": G_REGION.number_of_nodes() if G_REGION else 0,
        "edges": G_REGION.number_of_edges() if G_REGION else 0,
    }


class AnalysisReviewUpdate(BaseModel):
    status: str
    note: str = ""


@app.get("/api/analyses")
def recent_analyses(limit: int = 20):
    """Return recent saved AI analyses for local review."""
    return {"analyses": list_analyses(limit)}


@app.get("/api/analyses/{analysis_id}")
def analysis_details(analysis_id: str):
    """Retrieve a previously stored AI analysis by its identifier."""
    analysis = get_analysis(analysis_id)
    if analysis is None:
        raise HTTPException(status_code=404, detail="Analysis not found")
    return analysis


@app.patch("/api/analyses/{analysis_id}/review")
async def review_analysis(analysis_id: str, review: AnalysisReviewUpdate,
                         ):
    allowed = {"accepted", "rejected", "needs_field_verification"}
    if review.status not in allowed or len(review.note) > 1000:
        raise HTTPException(status_code=422, detail="Unsupported review status or note is too long")
    result = update_analysis_review(
        analysis_id, review_status=review.status, note=review.note.strip(),
        reviewer="local operator",
    )
    if result is None:
        raise HTTPException(status_code=404, detail="Analysis not found")
    await event_manager.publish("ai_analysis_reviewed", {
        "analysis_id": analysis_id, "review_status": review.status,
        "ai_status": "prediction_unverified",
    })
    return result


class ReportStatusUpdate(BaseModel):
    status: str


@app.post("/api/reports", status_code=201)
async def create_incident_report(
    report_type: str = Form(...),
    latitude: float = Form(...),
    longitude: float = Form(...),
    description: str = Form(...),
    severity: str = Form(...),
    reporter_name: str | None = Form(None),
    collapse_rate: str | None = Form(None),
    people_count: int | None = Form(None),
    health_status: str | None = Form(None),
    contact: str | None = Form(None),
    image: UploadFile | None = File(None),
):
    """Create an incident report; new reports always enter as Reported."""
    if report_type not in report_store.INCIDENT_TYPES:
        raise HTTPException(status_code=422, detail="Unsupported incident type")
    if severity not in report_store.SEVERITIES:
        raise HTTPException(status_code=422, detail="Severity must be low, medium, high, or critical")
    if not (math.isfinite(latitude) and -90 <= latitude <= 90 and
            math.isfinite(longitude) and -180 <= longitude <= 180):
        raise HTTPException(status_code=422, detail="Location is outside valid latitude/longitude ranges")
    description = description.strip()
    if not description or len(description) > 2000:
        raise HTTPException(status_code=422, detail="Description must contain 1 to 2000 characters")
    if people_count is not None and people_count < 0:
        raise HTTPException(status_code=422, detail="People count cannot be negative")

    report_id = str(uuid.uuid4())
    image_path = None
    if image is not None and image.filename:
        content = await image.read(10 * 1024 * 1024 + 1)
        decoded = decode_image_content(content, max_bytes=10 * 1024 * 1024)
        image_path = report_store.save_report_image(decoded, report_id)
    try:
        report = report_store.create_report(
            report_id=report_id,
            report_type=report_type,
            latitude=latitude,
            longitude=longitude,
            description=description,
            severity=severity,
            reporter_name=(reporter_name or "").strip() or None,
            collapse_rate=collapse_rate,
            people_count=people_count,
            health_status=health_status,
            contact=(contact or "").strip() or None,
            image_path=image_path,
        )
        await event_manager.publish("incident_created", {
            "id": report["id"], "type": report["type"],
            "latitude": report["latitude"], "longitude": report["longitude"],
            "severity": report["severity"], "status": report["status"],
        })
        return _public_report(report)
    except Exception:
        if image_path and os.path.exists(image_path):
            os.unlink(image_path)
        raise


@app.get("/api/reports")
def incident_reports(status: str | None = None, limit: int = 200):
    if status is not None and status not in report_store.REPORT_STATUSES:
        raise HTTPException(status_code=422, detail="Unsupported report status")
    return {"reports": [
        _public_report(report)
        for report in report_store.list_reports(status=status, limit=limit)
    ]}


@app.get("/api/reports/{report_id}")
def incident_report_details(report_id: str):
    report = report_store.get_report(report_id)
    if report is None:
        raise HTTPException(status_code=404, detail="Report not found")
    return _public_report(report)


@app.get("/api/reports/{report_id}/image")
def incident_report_image(report_id: str):
    image_path = report_store.get_image_path(report_id)
    if image_path is None:
        raise HTTPException(status_code=404, detail="Report image not found")
    return FileResponse(image_path, media_type="image/jpeg")


@app.get("/api/emergency-locations")
def emergency_locations(force_refresh: bool = False):
    """Return cached OSM facilities, refreshing the local snapshot when stale."""
    from backend.routing_engine import G_REGION
    return emergency_store.get_locations(G_REGION, force_refresh=force_refresh)


@app.get("/api/dashboard")
def dashboard_data(
    severity: str | None = None,
    report_type: str | None = None,
    status: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
):
    """Command-center data sourced from persisted reports, analyses, and OSM cache."""
    if severity is not None and severity not in report_store.SEVERITIES:
        raise HTTPException(status_code=422, detail="Unsupported severity filter")
    if report_type is not None and report_type not in report_store.INCIDENT_TYPES:
        raise HTTPException(status_code=422, detail="Unsupported incident type filter")
    if status is not None and status not in report_store.REPORT_STATUSES:
        raise HTTPException(status_code=422, detail="Unsupported report status filter")
    for value in (date_from, date_to):
        if value is not None:
            try:
                datetime.strptime(value, "%Y-%m-%d")
            except ValueError as exc:
                raise HTTPException(status_code=422, detail="Dates must use YYYY-MM-DD") from exc
    if date_from and date_to and date_from > date_to:
        raise HTTPException(status_code=422, detail="date_from must be on or before date_to")

    filters = {
        "severity": severity, "report_type": report_type, "status": status,
        "date_from": date_from, "date_to": date_to,
    }
    metrics = report_store.report_metrics(**filters)
    reports = report_store.list_reports(**filters, limit=12)
    ai = analysis_metrics()
    ai_history = list_analyses(10)
    locations, fetched_at = emergency_store._cached_locations()
    fetched = datetime.fromisoformat(fetched_at) if fetched_at else None
    if fetched and fetched.tzinfo is None:
        fetched = fetched.replace(tzinfo=timezone.utc)
    emergency_stale = fetched is None or datetime.now(timezone.utc) - fetched >= emergency_store.CACHE_MAX_AGE
    from backend.routing_engine import G_REGION
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "filters": filters,
        "stats": {
            **metrics,
            "ai_detections": ai["detection_count"],
            "ai_analyses": ai["analysis_count"],
            "emergency_locations": len(locations),
        },
        "recent_reports": [_public_report(report) for report in reports],
        "recent_analyses": ai_history,
        "system_status": {
            "api": "ok",
            "road_graph_loaded": G_REGION is not None,
            "ai_inference_configured": bool(ROBOFLOW_API_KEY),
            "emergency_data_stale": emergency_stale,
            "emergency_data_fetched_at": fetched_at,
        },
    }


@app.patch("/api/reports/{report_id}/status")
async def change_incident_status(report_id: str, update: ReportStatusUpdate,
                                 ):
    try:
        report = report_store.update_report_status(report_id, update.status)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if report is None:
        raise HTTPException(status_code=404, detail="Report not found")
    event_data = {
        "id": report["id"], "type": report["type"],
        "latitude": report["latitude"], "longitude": report["longitude"],
        "severity": report["severity"], "status": report["status"],
    }
    await event_manager.publish("incident_status_changed", event_data)
    if report["type"] == "blocked_road" and report["status"] == "Verified":
        await event_manager.publish("road_report_verified", event_data)
    return _public_report(report)


@app.websocket("/ws/updates")
async def websocket_updates(websocket: WebSocket):
    await event_manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        await event_manager.disconnect(websocket)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=HOST, port=PORT)

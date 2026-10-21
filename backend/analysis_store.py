"""Small local SQLite store for AI analysis metadata and predictions.

Uploaded images are deliberately not retained; only sanitized metadata and
the model's result JSON are stored for later review.
"""
import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone


PROJECT_ROOT = os.path.dirname(os.path.dirname(__file__))
DATABASE_PATH = os.getenv(
    "ANALYSIS_DATABASE_PATH",
    os.path.join(PROJECT_ROOT, "cache", "analysis_results.sqlite3"),
)


def save_analysis(*, source: str, filename: str, image_width: int, image_height: int,
                  bounds: dict, detections: list[dict]) -> dict:
    os.makedirs(os.path.dirname(os.path.abspath(DATABASE_PATH)), exist_ok=True)
    analysis_id = str(uuid.uuid4())
    created_at = datetime.now(timezone.utc).isoformat()
    payload = {
        "analysis_id": analysis_id,
        "created_at": created_at,
        "source": source,
        "filename": os.path.basename(filename or "uploaded-image"),
        "image_width": int(image_width),
        "image_height": int(image_height),
        "geolocation": "estimated_from_supplied_bounds",
        "bounds": bounds,
        "ai_status": "prediction_unverified",
        "detections": detections,
    }
    with sqlite3.connect(DATABASE_PATH) as connection:
        connection.execute(
            """CREATE TABLE IF NOT EXISTS ai_analyses (
                analysis_id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                source TEXT NOT NULL,
                payload_json TEXT NOT NULL
            )"""
        )
        connection.execute(
            "INSERT INTO ai_analyses (analysis_id, created_at, source, payload_json) VALUES (?, ?, ?, ?)",
            (analysis_id, created_at, source, json.dumps(payload, ensure_ascii=False)),
        )
    return payload


def get_analysis(analysis_id: str) -> dict | None:
    if not os.path.exists(DATABASE_PATH):
        return None
    with sqlite3.connect(DATABASE_PATH) as connection:
        row = connection.execute(
            "SELECT payload_json FROM ai_analyses WHERE analysis_id = ?",
            (analysis_id,),
        ).fetchone()
    return json.loads(row[0]) if row else None


def list_analyses(limit: int = 20) -> list[dict]:
    if not os.path.exists(DATABASE_PATH):
        return []
    limit = max(1, min(int(limit), 100))
    with sqlite3.connect(DATABASE_PATH) as connection:
        rows = connection.execute(
            "SELECT payload_json FROM ai_analyses ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [json.loads(row[0]) for row in rows]


def analysis_metrics() -> dict:
    if not os.path.exists(DATABASE_PATH):
        return {"analysis_count": 0, "detection_count": 0}
    with sqlite3.connect(DATABASE_PATH) as connection:
        rows = connection.execute("SELECT payload_json FROM ai_analyses").fetchall()
    detections = 0
    for row in rows:
        try:
            detections += len(json.loads(row[0]).get("detections", []))
        except (TypeError, ValueError):
            continue
    return {"analysis_count": len(rows), "detection_count": detections}


def update_analysis_review(analysis_id: str, *, review_status: str, note: str,
                          reviewer: str) -> dict | None:
    analysis = get_analysis(analysis_id)
    if analysis is None:
        return None
    analysis["review"] = {
        "status": review_status,
        "note": note,
        "reviewer": reviewer,
        "reviewed_at": datetime.now(timezone.utc).isoformat(),
        "physical_verification": "not_established_by_ai_review",
    }
    with sqlite3.connect(DATABASE_PATH) as connection:
        connection.execute(
            "UPDATE ai_analyses SET payload_json = ? WHERE analysis_id = ?",
            (json.dumps(analysis, ensure_ascii=False), analysis_id),
        )
    return analysis

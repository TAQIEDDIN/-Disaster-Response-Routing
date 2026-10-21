"""Local SQLite persistence for community incident reports."""
import cv2
import os
import sqlite3
import uuid
from datetime import datetime, timezone


PROJECT_ROOT = os.path.dirname(os.path.dirname(__file__))
DATABASE_PATH = os.getenv(
    "REPORT_DATABASE_PATH",
    os.path.join(PROJECT_ROOT, "cache", "incident_reports.sqlite3"),
)
INCIDENT_IMAGE_DIR = os.getenv(
    "REPORT_IMAGE_DIR",
    os.path.join(PROJECT_ROOT, "cache", "incident_images"),
)
INCIDENT_TYPES = {
    "blocked_road", "building_damage", "fire", "flood",
    "infrastructure_damage", "other_emergency",
}
SEVERITIES = {"low", "medium", "high", "critical"}
REPORT_STATUSES = ("Reported", "Under Review", "Verified", "Resolved")


def _connect():
    os.makedirs(os.path.dirname(os.path.abspath(DATABASE_PATH)), exist_ok=True)
    connection = sqlite3.connect(DATABASE_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute(
        """CREATE TABLE IF NOT EXISTS incident_reports (
            id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            type TEXT NOT NULL,
            latitude REAL NOT NULL,
            longitude REAL NOT NULL,
            description TEXT NOT NULL,
            severity TEXT NOT NULL,
            status TEXT NOT NULL,
            reporter_name TEXT,
            collapse_rate TEXT,
            people_count INTEGER,
            health_status TEXT,
            contact TEXT,
            image_path TEXT
        )"""
    )
    connection.commit()
    return connection


def create_report(*, report_id: str | None = None, report_type: str, latitude: float, longitude: float,
                  description: str, severity: str, reporter_name: str | None = None,
                  collapse_rate: str | None = None, people_count: int | None = None,
                  health_status: str | None = None, contact: str | None = None,
                  image_path: str | None = None) -> dict:
    report_id = report_id or str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    report = {
        "id": report_id,
        "created_at": now,
        "updated_at": now,
        "type": report_type,
        "latitude": float(latitude),
        "longitude": float(longitude),
        "description": description,
        "severity": severity,
        "status": "Reported",
        "reporter_name": reporter_name,
        "collapse_rate": collapse_rate,
        "people_count": people_count,
        "health_status": health_status,
        "contact": contact,
        "image_url": f"/api/reports/{report_id}/image" if image_path else None,
    }
    with _connect() as connection:
        connection.execute(
            """INSERT INTO incident_reports (
                id, created_at, updated_at, type, latitude, longitude,
                description, severity, status, reporter_name, collapse_rate,
                people_count, health_status, contact, image_path
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (report_id, now, now, report_type, latitude, longitude, description,
             severity, "Reported", reporter_name, collapse_rate, people_count,
             health_status, contact, image_path),
        )
    return report


def _row_to_report(row):
    report = dict(row)
    image_path = report.pop("image_path")
    report["image_url"] = f"/api/reports/{report['id']}/image" if image_path else None
    return report


def get_report(report_id: str) -> dict | None:
    with _connect() as connection:
        row = connection.execute("SELECT * FROM incident_reports WHERE id = ?", (report_id,)).fetchone()
    return _row_to_report(row) if row else None


def _report_filters(*, severity=None, report_type=None, status=None, date_from=None, date_to=None):
    clauses, values = [], []
    for column, value in (("severity", severity), ("type", report_type), ("status", status)):
        if value is not None:
            clauses.append(f"{column} = ?")
            values.append(value)
    if date_from is not None:
        clauses.append("date(created_at) >= date(?)")
        values.append(date_from)
    if date_to is not None:
        clauses.append("date(created_at) <= date(?)")
        values.append(date_to)
    return (" WHERE " + " AND ".join(clauses) if clauses else ""), values


def list_reports(*, status: str | None = None, severity: str | None = None,
                 report_type: str | None = None, date_from: str | None = None,
                 date_to: str | None = None, limit: int = 200) -> list[dict]:
    limit = max(1, min(int(limit), 500))
    where, values = _report_filters(
        status=status, severity=severity, report_type=report_type,
        date_from=date_from, date_to=date_to,
    )
    with _connect() as connection:
        rows = connection.execute(
            f"SELECT * FROM incident_reports{where} ORDER BY created_at DESC LIMIT ?",
            (*values, limit),
        ).fetchall()
    return [_row_to_report(row) for row in rows]


def report_metrics(*, severity=None, report_type=None, status=None,
                   date_from=None, date_to=None) -> dict:
    where, values = _report_filters(
        severity=severity, report_type=report_type, status=status,
        date_from=date_from, date_to=date_to,
    )
    with _connect() as connection:
        total = connection.execute(f"SELECT COUNT(*) FROM incident_reports{where}", values).fetchone()[0]
        active = connection.execute(
            f"SELECT COUNT(*) FROM incident_reports{where}{' AND' if where else ' WHERE'} status != ?",
            (*values, "Resolved"),
        ).fetchone()[0]
        blocked = connection.execute(
            f"SELECT COUNT(*) FROM incident_reports{where}{' AND' if where else ' WHERE'} type = ? AND status = ?",
            (*values, "blocked_road", "Verified"),
        ).fetchone()[0]
    return {"total_reports": total, "active_incidents": active, "verified_blocked_roads": blocked}


def update_report_status(report_id: str, new_status: str) -> dict | None:
    if new_status not in REPORT_STATUSES:
        raise ValueError("Unsupported report status")
    now = datetime.now(timezone.utc).isoformat()
    with _connect() as connection:
        row = connection.execute("SELECT status FROM incident_reports WHERE id = ?", (report_id,)).fetchone()
        if row is None:
            return None
        old_status = row["status"]
        next_index = REPORT_STATUSES.index(old_status) + 1
        if next_index >= len(REPORT_STATUSES) or REPORT_STATUSES[next_index] != new_status:
            raise ValueError("Reports must move one step at a time through the review workflow")
        connection.execute(
            "UPDATE incident_reports SET status = ?, updated_at = ? WHERE id = ?",
            (new_status, now, report_id),
        )
    return get_report(report_id)


def get_image_path(report_id: str) -> str | None:
    with _connect() as connection:
        row = connection.execute("SELECT image_path FROM incident_reports WHERE id = ?", (report_id,)).fetchone()
    if not row or not row["image_path"] or not os.path.isfile(row["image_path"]):
        return None
    return row["image_path"]


def save_report_image(image, report_id: str) -> str:
    os.makedirs(INCIDENT_IMAGE_DIR, exist_ok=True)
    path = os.path.join(INCIDENT_IMAGE_DIR, f"{report_id}.jpg")
    if not cv2.imwrite(path, image, [cv2.IMWRITE_JPEG_QUALITY, 90]):
        raise OSError("Could not save incident image")
    return path

"""Validation helpers for uploaded georeferenced images."""
import io
import tempfile

import cv2
import numpy as np
from fastapi import HTTPException, UploadFile
from PIL import Image, UnidentifiedImageError


MAX_UPLOAD_BYTES = 20 * 1024 * 1024
MAX_IMAGE_PIXELS = 40_000_000


def validate_geo_bounds(nw_lat, nw_lon, se_lat, se_lon):
    values = (nw_lat, nw_lon, se_lat, se_lon)
    if not all(np.isfinite(value) for value in values):
        raise HTTPException(status_code=422, detail="GPS bounds must be finite numbers")
    if not (-90 <= nw_lat <= 90 and -90 <= se_lat <= 90 and
            -180 <= nw_lon <= 180 and -180 <= se_lon <= 180):
        raise HTTPException(status_code=422, detail="GPS bounds are outside valid latitude/longitude ranges")
    if nw_lat <= se_lat or nw_lon >= se_lon:
        raise HTTPException(status_code=422, detail="NW/SE bounds must describe a non-empty north-west to south-east area")
    return {
        "nw_lat": nw_lat, "nw_lon": nw_lon,
        "se_lat": se_lat, "se_lon": se_lon,
    }


def save_image_content(content: bytes, *, max_bytes: int | None = None):
    image = decode_image_content(content, max_bytes=max_bytes)
    height, width = image.shape[:2]
    handle = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
    try:
        handle.write(content)
        return handle.name, width, height
    finally:
        handle.close()


def decode_image_content(content: bytes, *, max_bytes: int | None = None):
    max_bytes = MAX_UPLOAD_BYTES if max_bytes is None else max_bytes
    if not content:
        raise HTTPException(status_code=400, detail="The uploaded image is empty")
    if len(content) > max_bytes:
        limit_mb = max(1, max_bytes // (1024 * 1024))
        raise HTTPException(status_code=413, detail=f"Image exceeds the {limit_mb} MB upload limit")
    try:
        with Image.open(io.BytesIO(content)) as header_image:
            width, height = header_image.size
            if width * height > MAX_IMAGE_PIXELS:
                raise HTTPException(status_code=413, detail="Image dimensions exceed the supported limit")
            header_image.verify()
    except HTTPException:
        raise
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise HTTPException(status_code=400, detail="The uploaded file is not a valid image") from exc
    image = cv2.imdecode(np.frombuffer(content, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise HTTPException(status_code=400, detail="The uploaded file is not a valid image")
    height, width = image.shape[:2]
    if width * height > MAX_IMAGE_PIXELS:
        raise HTTPException(status_code=413, detail="Image dimensions exceed the supported limit")
    return image


async def save_validated_upload(upload: UploadFile):
    content = await upload.read(MAX_UPLOAD_BYTES + 1)
    return save_image_content(content)

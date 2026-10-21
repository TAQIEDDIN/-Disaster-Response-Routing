# Afet Rota Sistemi | Disaster Response Routing

A local-first disaster-response web prototype for map-based damage review, incident reporting, and safer route planning. The current road graph covers Antakya, Hatay, Türkiye.

> **Prototype notice:** This project is a decision-support prototype. AI detections, map data, facility listings, and calculated routes must be checked by qualified responders before operational use. The application does not provide authenticated roles and is not ready for public internet deployment.

**Teknolojiler:** Python, FastAPI, Leaflet, OpenStreetMap/OSMnx, NetworkX, SQLite, Roboflow, WebSocket ve tarayıcı çevrimdışı önbelleği.

## Computer vision / AI pipeline

The repository integrates the pretrained Roboflow model `earthquake-damage-detection-xmfgr/1`; it does **not** contain model training code or a local YOLO model. The application-side computer-vision work is the inference and post-processing pipeline in `backend/ai_engine.py`:

1. **Validate and decode the upload.** FastAPI accepts the image, checks it, and writes a temporary image for analysis. The temporary file is removed after the request.
2. **Enhance contrast and detail with OpenCV.** The image is converted to LAB; CLAHE (`clipLimit=2.5`, `tileGridSize=8×8`) is applied to the luminance channel, then a Gaussian unsharp mask is used for sharpening. This is preprocessing, not a guarantee of higher model accuracy.
3. **Run tiled and full-image inference.** The custom SAHI-style loop sends 640-pixel windows with 20% overlap to Roboflow, then also sends the full image to help capture larger objects. Confidence and IoU thresholds come from `AI_CONFIDENCE` and `AI_IOU_THRESHOLD`.
4. **Restore and merge detections.** Tile offsets are added back to each predicted box so detections use the original image coordinate system. IoU-based non-maximum suppression merges overlapping duplicate boxes from neighboring tiles and the full-image pass.
5. **Add approximate geospatial context.** Each detection center is converted from pixel coordinates to latitude/longitude by linear interpolation between supplied northwest and southeast bounds. This assumes the image corresponds to those bounds; it is approximate georeferencing, not a camera calibration or orthorectification step.
6. **Calculate separate operational heuristics.** Bounding-box area is used for a capped hazard-radius estimate. A simple class rule assigns risk values (`collapsed` 0.95, `damaged` 0.65, other 0.20). These are application heuristics, not probabilities produced by the model and not field-verified severity labels.
7. **Persist and expose the result.** Confidence, class, estimated coordinates, risk metadata, bounds, and analysis time are saved in SQLite. The uploaded image is not retained. The UI marks AI findings as predictions awaiting verification; inference failures return an API error rather than being reported as a successful empty detection list.

**CV engineering scope:** this project demonstrates model integration, image preprocessing, tiled inference, box-coordinate restoration, overlap deduplication, approximate pixel-to-map projection, and post-inference risk logic. It should be described as a Roboflow model integration and inference pipeline—not as training a new model.

## Features

- **Risk-aware routing:** Fastest, lowest-risk, and balanced modes; `open`, `blocked`, `restricted`, and `unknown` road states; primary and alternative routes with distance, estimated time, risk, and unknown-segment information. Confirmed blocked edges are excluded from route calculation.
- **AI image analysis:** Analyze uploaded aerial/drone imagery with the configured Roboflow model, show confidence values, and retain analysis metadata locally. Predictions are marked as unverified. Approximate map placement depends on usable geographic bounds.
- **Incident reports:** Report road blockages, building damage, fire, flood, infrastructure damage, and other emergencies. Reports follow `Reported → Under Review → Verified → Resolved`; optional images are stored locally.
- **Emergency locations:** Show and filter hospitals, shelters, assembly points, fire stations, police stations, and emergency centers from OpenStreetMap data.
- **Command dashboard:** Display persisted incident, analysis, and emergency-location data with filters and system status; statistics are derived from backend data rather than demo values.
- **Live updates:** WebSocket events refresh relevant map and dashboard information when reports, verification, or AI analyses change.
- **Offline preparation:** Cache the application shell and selected map/report data in the browser, indicate stale data, and queue eligible image-free reports for later synchronization.
- **Web interface:** Responsive English-language map and command panel with route comparison, incident tools, and aerial-image workspace.

## How it works

1. Select start and destination points or enter coordinates.
2. Choose a routing mode and, if available, provide road-status overrides.
3. For image analysis, upload an aerial/drone image or analyze the current map view.
4. The backend validates the image, calls Roboflow, and stores analysis metadata in SQLite.
5. AI detections and verified incident reports contribute to route risk; confirmed blocked roads are excluded.
6. The frontend displays the primary and alternative routes, hazard indicators, reports, and emergency locations.

## Technology

| Area | Tools |
| --- | --- |
| Backend API | Python, FastAPI, Uvicorn |
| Routing | OSMnx, OpenStreetMap road graph, NetworkX |
| Image analysis | Roboflow Inference SDK, OpenCV, NumPy |
| Frontend | HTML, CSS, JavaScript, Leaflet |
| Local persistence | SQLite |
| Live updates | WebSocket |
| Offline support | Service Worker, browser storage |

## Requirements

- Python 3.10 or newer
- A Roboflow API key for AI inference
- Internet access for Roboflow requests, OpenStreetMap facility refreshes, and external map tiles

The included `antakya_graph.graphml` road graph allows routing without downloading the road network at startup. It does not contain live road-closure updates.

## Setup

### Windows PowerShell

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

### macOS / Linux

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Edit `.env` and set `ROBOFLOW_API_KEY`. Keep `.env` private and never commit it. The repository's `.env.example` uses port `10000`; set `PORT` there if you want another port. Keep `HOST=127.0.0.1` for local-only use.

Start the server from the project root:

```bash
python -m backend.app
```

The server loads the Antakya graph and opens the application using the `HOST` and `PORT` values from `.env`. With the supplied example configuration, the URL is `http://127.0.0.1:10000/static/index.html`. The API documentation is available at `http://127.0.0.1:10000/docs`.

Windows users can alternatively run `start.bat`; macOS/Linux users can run `bash start.sh`.

## Configuration

| Variable | Purpose |
| --- | --- |
| `ROBOFLOW_API_KEY` | Roboflow inference credential; required for AI analysis |
| `ROBOFLOW_API_URL` | Inference endpoint; defaults to `https://serverless.roboflow.com` |
| `ROBOFLOW_MODEL_ID` | Model version; defaults to `earthquake-damage-detection-xmfgr/1` |
| `AI_CONFIDENCE`, `AI_IOU_THRESHOLD` | Detection thresholds |
| `DEFAULT_CITY` | Road-graph region; defaults to Antakya, Hatay, Türkiye |
| `DANGER_RADIUS_BASE`, `DANGER_WEIGHT_MULTIPLIER` | Route hazard weighting settings |
| `HOST`, `PORT` | Local server address; example configuration uses `127.0.0.1:10000` |

## Main API endpoints

- `POST /api/otonom-analiz` — analyze the current map view and calculate routes
- `POST /api/goruntu-analiz` — analyze an uploaded image
- `POST /api/drone-analiz` — analyze drone imagery and calculate routes
- `GET /api/analyses` and `GET /api/analyses/{analysis_id}` — read saved AI analyses
- `POST /api/reports`, `GET /api/reports`, `PATCH /api/reports/{id}/status` — manage incident reports
- `GET /api/emergency-locations` — retrieve cached OpenStreetMap facilities
- `GET /api/dashboard` — retrieve dashboard data and filters
- `GET /api/health` — check service status
- `WS /ws/updates` — receive live events

See `/docs` while the server is running for request and response schemas.

## Data and limitations

- The included road graph covers Antakya and may be out of date. It does not provide live traffic or automatically know about road closures.
- Only explicitly supplied blocked-road overrides and verified blocked-road reports that match a road segment are excluded from routes. Unreported hazards cannot be inferred by the router.
- AI output is not field verification. Geographic placement from an image is approximate and depends on correct image bounds; ordinary screenshots without reliable bounds should not be treated as geolocated evidence.
- Emergency facilities come from OpenStreetMap and may be incomplete or stale. Their listing does not confirm that a facility is open or operational.
- SQLite databases and uploaded report images are stored on the local server under `cache/`. They are not shared across devices unless the server storage is shared.
- Offline mode caches selected application data and can queue eligible reports, but AI inference, route calculation, live updates, and map tiles still need connectivity. Cached information may be stale.
- The API currently has no login or role enforcement. Report creation and status updates are open to clients that can reach the server. Keep it bound to localhost; do not expose it publicly without adding authentication, authorization, and deployment security.
- Live events are held in process memory; multi-worker deployment needs a shared event broker.

## Tests

Run the backend test suite from the project root:

```bash
python -m unittest discover -s tests -v
```

`test_ui.py` is a separate optional browser smoke check. Install Playwright and its Chromium browser first (`pip install playwright` and `python -m playwright install chromium`), start the app, then run `python test_ui.py`. It reads `HOST` and `PORT` from `.env`; set `UI_TEST_URL` to override the address.

## Project layout

```text
backend/          FastAPI endpoints, routing, AI, SQLite stores, WebSocket events
frontend/         Map UI, browser logic, CSS, service worker, optional Firebase integration
tests/            Backend unit tests
test_ui.py        Optional Playwright smoke check
cache/            Local runtime databases and caches (ignored by Git)
antakya_graph.graphml
.env.example
requirements.txt
start.bat
start.sh
```

`cache/`, `.env`, Python virtual environments, compiled Python files, and model-weight files are excluded by `.gitignore`.

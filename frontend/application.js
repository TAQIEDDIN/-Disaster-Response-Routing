/**
 * Afet Rota Sistemi — Frontend Controller
 * ========================================
 * Harita etkilesimi, koordinat girisi, goruntu analiz, hava durumu,
 * deprem verileri, acil numara, enkaz bildirim ve sonuc gorsellestirme.
 */

// === CONFIG ===
const API_URL = window.location.origin;
const MAP_CENTER = [36.2023, 36.1613]; // Antakya
const MAP_ZOOM = 17;

// === STATE ===
let map;
let markers = { A: null, B: null };
let routeLayer = null;
let altRouteLayer = null;
let enkazLayers = [];
let dangerZoneLayers = [];
let routeLabelLayers = [];
let isAnalyzing = false;
let debrisReportMode = false;
let debrisSelectionPending = false;
let debrisReportLatLng = null;
let reportedDebrisMarkers = [];
let emergencyLocationMarkers = [];
const visibleEmergencyTypes = new Set(['hospital', 'shelter', 'assembly_point', 'fire_station', 'police_station', 'emergency_center']);
let realtimeSocket = null;
let realtimeRetryTimer = null;
let realtimeHeartbeat = null;
let realtimeRetryDelay = 1000;
const REPORTS_CACHE_KEY = 'afet-rota-reports-snapshot-v1';
const EMERGENCY_CACHE_KEY = 'afet-rota-emergency-snapshot-v1';
const LAST_SYNC_KEY = 'afet-rota-last-sync-v1';

// === DOM ELEMENTS ===
const statusBox = document.getElementById('statusBox');
const analizBtn = document.getElementById('analizBtn');
const resetBtn = document.getElementById('resetBtn');
const statsGrid = document.getElementById('statsGrid');
const findingsPanel = document.getElementById('findingsPanel');
const findingsList = document.getElementById('findingsList');
const findingsCount = document.getElementById('findingsCount');
const findingsEmpty = document.getElementById('findingsEmpty');
const progressSteps = document.getElementById('progressSteps');
const legendPanel = document.getElementById('legendPanel');

const stepCapture = document.getElementById('stepCapture');
const stepAI = document.getElementById('stepAI');
const stepRoute = document.getElementById('stepRoute');
const stepDone = document.getElementById('stepDone');

const statEnkaz = document.getElementById('statEnkaz');
const statRota = document.getElementById('statRota');
const statRouteMeta = document.getElementById('statRouteMeta');
const routingMode = document.getElementById('routingMode');

const coordALat = document.getElementById('coordALat');
const coordALon = document.getElementById('coordALon');
const coordBLat = document.getElementById('coordBLat');
const coordBLon = document.getElementById('coordBLon');
const applyABtn = document.getElementById('applyABtn');
const applyBBtn = document.getElementById('applyBBtn');

const controlPanel = document.querySelector('.control-panel');
const hidePanelBtn = document.getElementById('hidePanelBtn');
const showPanelBtn = document.getElementById('showPanelBtn');

const droneFileInput = document.getElementById('droneFileInput');
const droneModeOverlay = document.getElementById('droneModeOverlay');
const droneCanvas = document.getElementById('droneCanvas');
let droneCtx = null;
const droneAnalyzeBtn = document.getElementById('droneAnalyzeBtn');
const droneCloseBtn = document.getElementById('droneCloseBtn');
const droneInstruction = document.getElementById('droneInstruction');

let droneImageObj = null;
let dronePoints = { A: null, B: null };
let isDroneAnalyzing = false;

const themeToggleBtn = document.getElementById('themeToggleBtn');

const earthquakeBtn = document.getElementById('earthquakeBtn');
const emergencyBtn = document.getElementById('emergencyBtn');
const earthquakeModal = document.getElementById('earthquakeModal');
const emergencyModal = document.getElementById('emergencyModal');
const earthquakeModalClose = document.getElementById('earthquakeModalClose');
const emergencyModalClose = document.getElementById('emergencyModalClose');
const dashboardModal = document.getElementById('dashboardModal');
const dashboardFilters = document.getElementById('dashboardFilters');

const weatherBtn = document.getElementById('weatherBtn');
const weatherPanel = document.getElementById('weatherPanel');
const weatherPanelClose = document.getElementById('weatherPanelClose');

const reportDebrisBtn = document.getElementById('reportDebrisBtn');
const debrisReportModal = document.getElementById('debrisReportModal');
const debrisReportClose = document.getElementById('debrisReportClose');
const debrisReportForm = document.getElementById('debrisReportForm');

// ============================================
// THEME MANAGEMENT
// ============================================

function initTheme() {
    const savedTheme = localStorage.getItem('afet-rota-theme') || 'light';
    document.documentElement.setAttribute('data-theme', savedTheme);
}

function readLocalJson(key, fallback) {
    try { return JSON.parse(localStorage.getItem(key) || 'null') ?? fallback; }
    catch { return fallback; }
}

function writeLocalJson(key, value) {
    try { localStorage.setItem(key, JSON.stringify(value)); }
    catch (error) { console.warn(`Yerel önbellek yazılamadı (${key}):`, error.message); }
}

function updateOfflineStatus() {
    const status = document.getElementById('offlineStatus');
    if (!status) return;
    const queuedReports = readLocalJson('enkaz_bildirimleri', []);
    const queued = (Array.isArray(queuedReports) ? queuedReports : []).filter(item => item.id?.startsWith('local_')).length;
    const lastSync = localStorage.getItem(LAST_SYNC_KEY);
    const lastSyncDate = lastSync ? new Date(lastSync) : null;
    const stale = !lastSyncDate || Number.isNaN(lastSyncDate.getTime()) || Date.now() - lastSyncDate.getTime() > 24 * 60 * 60 * 1000;
    const syncLabel = lastSyncDate && !Number.isNaN(lastSyncDate.getTime())
        ? lastSyncDate.toLocaleString('en-US')
        : 'No previous sync';
    status.classList.toggle('is-offline', !navigator.onLine);
    status.classList.toggle('is-stale', stale);
    if (!navigator.onLine) {
        status.textContent = `Offline · saved data may be stale · last sync: ${syncLabel}${queued ? ` · ${queued} pending report${queued === 1 ? '' : 's'}` : ''}`;
    } else if (!lastSync) {
        status.textContent = `Online · server sync has not been confirmed${queued ? ` · ${queued} pending report${queued === 1 ? '' : 's'}` : ''}`;
    } else if (stale) {
        status.textContent = `Online · saved data may be stale · last sync: ${syncLabel}${queued ? ` · ${queued} pending report${queued === 1 ? '' : 's'}` : ''}`;
    } else {
        status.textContent = `Online · last sync: ${syncLabel}${queued ? ` · ${queued} pending report${queued === 1 ? '' : 's'}` : ''}`;
    }
}

function recordSuccessfulSync() {
    try { localStorage.setItem(LAST_SYNC_KEY, new Date().toISOString()); }
    catch (error) { console.warn('وقت المزامنة ما تخزنش محلياً:', error.message); }
    updateOfflineStatus();
}

function registerOfflineAppShell() {
    if ('serviceWorker' in navigator && (location.protocol === 'https:' || location.hostname === 'localhost' || location.hostname === '127.0.0.1')) {
        navigator.serviceWorker.register('/service-worker.js', { scope: '/' })
            .catch(error => console.warn('Offline app shell could not be enabled:', error));
    }
    updateOfflineStatus();
}

function toggleTheme() {
    const current = document.documentElement.getAttribute('data-theme');
    const next = current === 'dark' ? 'light' : 'dark';
    document.documentElement.setAttribute('data-theme', next);
    localStorage.setItem('afet-rota-theme', next);
}

initTheme();

// ============================================
// WEATHER (Open-Meteo — ucretsiz, API key gereksiz)
// ============================================

const WEATHER_CODES = {
    0: { desc: 'Clear', icon: 'sunny' },
    1: { desc: 'Mostly clear', icon: 'partly_cloudy' },
    2: { desc: 'Partly cloudy', icon: 'partly_cloudy' },
    3: { desc: 'Overcast', icon: 'cloudy' },
    45: { desc: 'Fog', icon: 'fog' },
    48: { desc: 'Freezing fog', icon: 'fog' },
    51: { desc: 'Light drizzle', icon: 'drizzle' },
    53: { desc: 'Moderate drizzle', icon: 'drizzle' },
    55: { desc: 'Dense drizzle', icon: 'drizzle' },
    61: { desc: 'Light rain', icon: 'rain' },
    63: { desc: 'Moderate rain', icon: 'rain' },
    65: { desc: 'Heavy rain', icon: 'rain' },
    71: { desc: 'Light snow', icon: 'snow' },
    73: { desc: 'Moderate snow', icon: 'snow' },
    75: { desc: 'Heavy snow', icon: 'snow' },
    77: { desc: 'Snow grains', icon: 'snow' },
    80: { desc: 'Rain showers', icon: 'rain' },
    81: { desc: 'Moderate showers', icon: 'rain' },
    82: { desc: 'Heavy showers', icon: 'rain' },
    85: { desc: 'Light snow showers', icon: 'snow' },
    86: { desc: 'Heavy snow showers', icon: 'snow' },
    95: { desc: 'Thunderstorm', icon: 'storm' },
    96: { desc: 'Thunderstorm with hail', icon: 'storm' },
    99: { desc: 'Severe hail', icon: 'storm' }
};

function getWeatherAdvisory(code, temp, windSpeed, visibility) {
    const advisories = [];

    // Sis uyarisi
    if (code === 45 || code === 48 || (visibility && visibility < 1000)) {
        advisories.push('Fog may reduce satellite and drone image quality and lead to inaccurate detections.');
    }

    // Yagmur/firtina uyarisi
    if (code >= 61 && code <= 67 || code >= 80 || code >= 95) {
        advisories.push('Rain may make search and rescue operations more difficult. Teams should proceed with caution.');
    }

    // Kar uyarisi
    if (code >= 71 && code <= 77 || code === 85 || code === 86) {
        advisories.push('Snow may worsen road conditions and delay route calculations.');
    }

    // Ruzgar uyarisi
    if (windSpeed && windSpeed > 40) {
        advisories.push('Strong winds may make drone flights dangerous. Use caution.');
    }

    // Sicaklik uyarisi
    if (temp !== undefined) {
        if (temp > 38) advisories.push('Extreme heat. Hydration is critical for search and rescue personnel.');
        if (temp < 0) advisories.push('Freezing conditions may put trapped people at risk of hypothermia. Prioritize urgent assistance.');
    }

    // Normal hava
    if (advisories.length === 0) {
        advisories.push('Weather conditions are suitable for operations. Image analysis and route calculations can proceed normally.');
    }

    return advisories.join(' ');
}

async function fetchWeather() {
    const content = document.getElementById('weatherContent');
    content.innerHTML = '<div class="eq-loading"><div class="spinner"></div>Loading weather…</div>';

    try {
        const lat = MAP_CENTER[0];
        const lon = MAP_CENTER[1];
        const url = `https://api.open-meteo.com/v1/forecast?latitude=${lat}&longitude=${lon}&current=temperature_2m,relative_humidity_2m,weather_code,wind_speed_10m,visibility&timezone=auto`;

        const response = await fetch(url);
        if (!response.ok) throw new Error(`HTTP ${response.status}`);

        const data = await response.json();
        const current = data.current;

        const code = current.weather_code;
        const temp = current.temperature_2m;
        const humidity = current.relative_humidity_2m;
        const windSpeed = current.wind_speed_10m;
        const visibility = current.visibility;

        const weatherInfo = WEATHER_CODES[code] || { desc: 'Unknown', icon: 'unknown' };
        const advisory = getWeatherAdvisory(code, temp, windSpeed, visibility);

        // Sehir ismi (harita merkez koordinatina gore)
        const cityName = 'Antakya / Hatay';

        content.innerHTML = `
            <div class="weather-main">
                <div class="weather-temp">${Math.round(temp)}&deg;</div>
                <div>
                    <div class="weather-desc">${weatherInfo.desc}</div>
                    <div class="weather-city">${cityName}</div>
                </div>
            </div>
            <div class="weather-details">
                <div class="weather-detail-item">
                    <div class="weather-detail-label">Humidity</div>
                    <div class="weather-detail-value">%${humidity}</div>
                </div>
                <div class="weather-detail-item">
                    <div class="weather-detail-label">Wind</div>
                    <div class="weather-detail-value">${windSpeed} km/h</div>
                </div>
                <div class="weather-detail-item">
                    <div class="weather-detail-label">Visibility</div>
                    <div class="weather-detail-value">${visibility ? (visibility / 1000).toFixed(1) + ' km' : '-'}</div>
                </div>
                <div class="weather-detail-item">
                    <div class="weather-detail-label">Temperature</div>
                    <div class="weather-detail-value">${temp.toFixed(1)}&deg;C</div>
                </div>
            </div>
            <div class="weather-advisory">
                <div class="weather-advisory-title">Operational Advisory</div>
                <div class="weather-advisory-text">${advisory}</div>
            </div>
        `;
    } catch (error) {
        console.error('Hava durumu hatasi:', error);
        content.innerHTML = '<div class="eq-error">Weather data could not be loaded.</div>';
    }
}

// ============================================
// EARTHQUAKE DATA (Kandilli Rasathanesi)
// ============================================

const EARTHQUAKE_API = 'https://api.orhanaydogdu.com.tr/deprem/kandilli/live';

async function fetchEarthquakes() {
    const content = document.getElementById('earthquakeListContent');
    content.innerHTML = '<div class="eq-loading"><div class="spinner"></div>Loading earthquake data…</div>';

    try {
        const response = await fetch(EARTHQUAKE_API);
        if (!response.ok) throw new Error(`HTTP ${response.status}`);

        const data = await response.json();
        const quakes = data.result || [];

        if (quakes.length === 0) {
            content.innerHTML = '<div class="eq-loading">No earthquake data available.</div>';
            return;
        }

        const items = quakes.slice(0, 50);
        let html = '<div class="earthquake-list">';

        items.forEach(q => {
            const mag = parseFloat(q.mag) || 0;
            let magClass = 'mag-low';
            if (mag >= 5) magClass = 'mag-critical';
            else if (mag >= 4) magClass = 'mag-high';
            else if (mag >= 3) magClass = 'mag-mid';

            const location = q.title || q.lokasyon || 'Unknown';
            const depth = q.depth || '-';
            const date = q.date || '';

            let timeStr = '';
            if (date) {
                const d = new Date(date);
                if (!isNaN(d.getTime())) {
                    timeStr = d.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' });
                } else {
                    timeStr = date.split(' ').pop() || '';
                }
            }

            html += `
                <div class="earthquake-item">
                    <div class="eq-magnitude ${magClass}">${mag.toFixed(1)}</div>
                    <div class="eq-info">
                        <div class="eq-location">${location}</div>
                        <div class="eq-details">
                            <span>Depth: ${depth} km</span>
                        </div>
                    </div>
                    <div class="eq-time">${timeStr}</div>
                </div>
            `;
        });

        html += '</div>';
        content.innerHTML = html;

    } catch (error) {
        console.error('Deprem verisi alinirken hata:', error);
        content.innerHTML = '<div class="eq-error">Earthquake data could not be loaded. Please try again later.</div>';
    }
}

// ============================================
// EMERGENCY NUMBERS (Guncellenmis liste)
// ============================================

const EMERGENCY_NUMBERS = [
    { number: '112', name: 'Emergency Call Center', desc: 'Unified emergency line for ambulance, fire, and police' },
    { number: '122', name: 'AFAD', desc: 'Disaster and Emergency Management Authority' },
    { number: '181', name: 'Red Crescent', desc: 'Turkish Red Crescent assistance and donations' },
    { number: '155', name: 'Police Emergency', desc: 'General Directorate of Security hotline' },
    { number: '156', name: 'Gendarmerie', desc: 'Gendarmerie General Command hotline' },
    { number: '182', name: 'Governor’s Office', desc: 'Governor’s Office information and coordination line' },
    { number: '153', name: 'Municipality', desc: 'Municipal services and reporting line' },
];

function renderEmergencyNumbers() {
    const content = document.getElementById('emergencyListContent');
    let html = '';

    EMERGENCY_NUMBERS.forEach(item => {
        html += `
            <div class="emergency-item">
                <div class="emergency-number">${item.number}</div>
                <div class="emergency-info">
                    <div class="emergency-name">${item.name}</div>
                    <div class="emergency-desc">${item.desc}</div>
                </div>
            </div>
        `;
    });

    content.innerHTML = html;
}

const EMERGENCY_LOCATION_LABELS = {
    hospital: 'Hospital', shelter: 'Shelter', assembly_point: 'Assembly point',
    fire_station: 'Fire station', police_station: 'Police station', emergency_center: 'Emergency center'
};
const EMERGENCY_LOCATION_GLYPHS = {
    hospital: '+', shelter: '⌂', assembly_point: '⌖',
    fire_station: '♨', police_station: '✦', emergency_center: '✚'
};

function escapePopupText(value) {
    return String(value ?? '').replace(/[&<>"']/g, ch => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));
}

function renderEmergencyLocationMarkers(locations) {
    emergencyLocationMarkers.forEach(item => map.removeLayer(item.marker));
    emergencyLocationMarkers = [];
    (locations || []).forEach(location => {
        if (!EMERGENCY_LOCATION_LABELS[location.type]) return;
        const icon = L.divIcon({
            className: '',
            html: `<div class="emergency-location-marker" title="${escapePopupText(EMERGENCY_LOCATION_LABELS[location.type])}"><span>${EMERGENCY_LOCATION_GLYPHS[location.type]}</span></div>`,
            iconSize: [30, 30],
            iconAnchor: [15, 25]
        });
        const marker = L.marker([location.latitude, location.longitude], { icon, keyboard: true });
        marker.bindPopup(
            `<strong>${escapePopupText(location.name)}</strong><br>${escapePopupText(EMERGENCY_LOCATION_LABELS[location.type])}` +
            `<small class="emergency-popup-note">OpenStreetMap listing · current operating status is unverified</small>`
        );
        emergencyLocationMarkers.push({ marker, type: location.type });
        if (visibleEmergencyTypes.has(location.type)) marker.addTo(map);
    });
}

function applyEmergencyLocationFilters() {
    emergencyLocationMarkers.forEach(item => {
        if (visibleEmergencyTypes.has(item.type)) item.marker.addTo(map);
        else if (map.hasLayer(item.marker)) map.removeLayer(item.marker);
    });
}

async function loadEmergencyLocations(forceRefresh = false) {
    const syncEl = document.getElementById('emergencyLocationSync');
    const refreshButton = document.getElementById('refreshEmergencyLocations');
    if (!syncEl) return;
    if (refreshButton) refreshButton.disabled = true;
    syncEl.textContent = forceRefresh ? 'Refreshing OpenStreetMap data…' : 'Loading OpenStreetMap locations…';
    try {
        const url = `${API_URL}/api/emergency-locations${forceRefresh ? '?force_refresh=true' : ''}`;
        const response = await fetch(url, { cache: 'no-store' });
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
        writeLocalJson(EMERGENCY_CACHE_KEY, {
            locations: data.locations || [], fetched_at: data.fetched_at,
            stale: Boolean(data.stale), cached_at: new Date().toISOString()
        });
        recordSuccessfulSync();
        renderEmergencyLocationMarkers(data.locations);
        const count = (data.locations || []).length;
        const fetchedAt = data.fetched_at ? new Date(data.fetched_at).toLocaleString('en-US') : null;
        const freshness = data.stale ? ' · stale data; verify before use' : '';
        const note = data.warning ? ` · ${data.warning}` : '';
        syncEl.textContent = `${count} OpenStreetMap locations${fetchedAt ? ` · updated ${fetchedAt}` : ''}${freshness}${note}`;
    } catch (error) {
        const cached = readLocalJson(EMERGENCY_CACHE_KEY, null);
        if (cached?.locations) {
            renderEmergencyLocationMarkers(cached.locations);
            const fetchedAt = cached.fetched_at ? new Date(cached.fetched_at).toLocaleString('en-US') : 'unknown';
            syncEl.textContent = `${cached.locations.length} cached OpenStreetMap locations · STALE CACHE, last data: ${fetchedAt} · ${error.message}`;
        } else {
            syncEl.textContent = `Could not load emergency locations: ${error.message}`;
        }
        updateOfflineStatus();
    } finally {
        if (refreshButton) refreshButton.disabled = false;
    }
}

function reportLabel(type) {
    return ({
        blocked_road: 'Blocked road', building_damage: 'Building damage', fire: 'Fire',
        flood: 'Flooding', infrastructure_damage: 'Infrastructure damage',
        other_emergency: 'Other emergency'
    })[type] || type || 'Incident';
}

function reportStatusLabel(status) {
    return ({ 'Reported': 'Reported', 'Under Review': 'Under review', 'Verified': 'Verified', 'Resolved': 'Resolved' })[status] || status;
}

async function loadDashboardData() {
    const updated = document.getElementById('dashboardUpdated');
    const statsEl = document.getElementById('dashboardStats');
    const reportsEl = document.getElementById('dashboardReports');
    updated.textContent = 'Loading server data…';
    reportsEl.innerHTML = '<div class="dashboard-empty">Loading reports…</div>';
    try {
        const query = new URLSearchParams();
        if (dashboardFilters) {
            new FormData(dashboardFilters).forEach((value, key) => { if (value) query.set(key, value); });
        }
        const response = await fetch(`${API_URL}/api/dashboard?${query.toString()}`, { cache: 'no-store' });
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);

        const stats = data.stats;
        const cards = [
            ['Active incidents', stats.active_incidents],
            ['Verified blocked roads', stats.verified_blocked_roads],
            ['AI detections · awaiting verification', stats.ai_detections],
            ['Emergency locations (OSM)', stats.emergency_locations]
        ];
        statsEl.innerHTML = cards.map(([label, value]) => `<div class="dashboard-stat"><span>${escapePopupText(label)}</span><strong>${Number(value || 0).toLocaleString('en-US')}</strong></div>`).join('');
        document.getElementById('dashboardReportCount').textContent = `${stats.total_reports} reports · ${stats.ai_analyses} analyses`;

        const system = data.system_status;
        const tag = (label, ok) => `<span class="${ok ? 'status-ok' : 'status-warn'}">${escapePopupText(label)}</span>`;
        const osmtime = system.emergency_data_fetched_at ? new Date(system.emergency_data_fetched_at).toLocaleString('en-US') : 'Not yet synced';
        document.getElementById('dashboardSystemStatus').innerHTML = [
            tag(`API: ${system.api}`, system.api === 'ok'),
            tag(`Road network: ${system.road_graph_loaded ? 'ready' : 'not loaded'}`, system.road_graph_loaded),
            tag(`Roboflow: ${system.ai_inference_configured ? 'configured' : 'API key missing'}`, system.ai_inference_configured),
            tag(`OSM: ${system.emergency_data_stale ? 'stale' : 'current'} · ${osmtime}`, !system.emergency_data_stale)
        ].join('');

        const reports = data.recent_reports || [];
        if (!reports.length) {
            reportsEl.innerHTML = '<div class="dashboard-empty">No reports match these filters.</div>';
        } else {
            reportsEl.innerHTML = reports.map(report => {
                const date = new Date(report.created_at).toLocaleString('en-US');
                return `<article class="dashboard-report-row"><div><strong>${escapePopupText(reportLabel(report.type))}</strong><p>${escapePopupText(report.severity)} · ${escapePopupText(reportStatusLabel(report.status))}</p></div><p>${escapePopupText(report.description)}</p><span>${escapePopupText(date)}</span></article>`;
            }).join('');
        }
        const aiReviewSection = document.getElementById('dashboardAiReviewSection');
        const aiReviewEl = document.getElementById('dashboardAiReviews');
        aiReviewSection.hidden = false;
        if (!aiReviewSection.hidden) {
            const analyses = data.recent_analyses || [];
            aiReviewEl.innerHTML = analyses.length ? analyses.map(analysis => {
                const existingReview = analysis.review;
                const confidenceValues = (analysis.detections || []).map(item => Number(item.confidence || 0));
                const averageConfidence = confidenceValues.length
                    ? Math.round(confidenceValues.reduce((sum, value) => sum + value, 0) / confidenceValues.length * 100)
                    : 0;
                const analysisId = escapePopupText(analysis.analysis_id);
                return `<article class="dashboard-ai-card"><div><strong>${analysisId}</strong><p>${escapePopupText(analysis.source)} · ${(analysis.detections || []).length} detections · avg. confidence ${averageConfidence}</p><p>AI prediction · not field verified${existingReview ? ` · review: ${escapePopupText(existingReview.status)}` : ''}</p></div><div class="dashboard-ai-actions"><button type="button" data-analysis-id="${analysisId}" data-review="accepted">Accept AI result</button><button type="button" data-analysis-id="${analysisId}" data-review="rejected">Reject AI result</button><button type="button" data-analysis-id="${analysisId}" data-review="needs_field_verification">Field verification required</button></div><textarea class="dashboard-ai-note" data-analysis-note="${analysisId}" maxlength="1000" placeholder="Analyst note (optional)">${existingReview ? escapePopupText(existingReview.note || '') : ''}</textarea></article>`;
            }).join('') : '<div class="dashboard-empty">No AI analyses to review.</div>';
        }
        updated.textContent = `Last updated: ${new Date(data.generated_at).toLocaleTimeString('en-US')}`;
    } catch (error) {
        updated.textContent = `Could not load dashboard data: ${error.message}`;
        statsEl.innerHTML = '';
        reportsEl.innerHTML = '<div class="dashboard-empty">Could not connect to the server. Try again.</div>';
    }
}

function connectRealtimeUpdates() {
    if (realtimeSocket && [WebSocket.OPEN, WebSocket.CONNECTING].includes(realtimeSocket.readyState)) return;
    const scheme = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    realtimeSocket = new WebSocket(`${scheme}//${window.location.host}/ws/updates`);
    realtimeSocket.addEventListener('open', () => {
        realtimeRetryDelay = 1000;
        if (realtimeHeartbeat) clearInterval(realtimeHeartbeat);
        realtimeHeartbeat = setInterval(() => {
            if (realtimeSocket?.readyState === WebSocket.OPEN) realtimeSocket.send('ping');
        }, 25000);
    });
    realtimeSocket.addEventListener('message', async (message) => {
        let event;
        try { event = JSON.parse(message.data); } catch { return; }
        const reportId = event.data?.id;
        if (['incident_created', 'incident_status_changed', 'road_report_verified'].includes(event.event) && reportId) {
            try {
                const response = await fetch(`${API_URL}/api/reports/${encodeURIComponent(reportId)}`, { cache: 'no-store' });
                if (response.ok) upsertIncidentMarker(await response.json());
            } catch (error) {
                console.warn('Canlı olay bildirimi haritaya eklenemedi:', error.message);
            }
            if (event.event === 'road_report_verified') setStatus('Blocked road report verified; this road will be excluded from the next route calculation.', 'info');
            if (event.event === 'incident_created') setStatus('New incident report received.', 'info');
        }
        if (dashboardModal?.classList.contains('active')) loadDashboardData();
    });
    realtimeSocket.addEventListener('close', () => {
        if (realtimeHeartbeat) clearInterval(realtimeHeartbeat);
        clearTimeout(realtimeRetryTimer);
        realtimeRetryTimer = setTimeout(connectRealtimeUpdates, realtimeRetryDelay);
        realtimeRetryDelay = Math.min(Math.round(realtimeRetryDelay * 1.8), 30000);
    });
    realtimeSocket.addEventListener('error', () => realtimeSocket.close());
}

// ============================================
// DEBRIS REPORT (Firebase Firestore)
// ============================================

function startDebrisReportMode() {
    debrisReportMode = true;
    debrisSelectionPending = false;
    
    // Eger onceden kalmis gecici marker varsa silmiyoruz, kullanici silene kadar kalir
    // ama form acildiginda mevcut olan koordinat null kalmamasi lazim.
    if (!window._tempDebrisMarker) {
        debrisReportLatLng = null;
        document.getElementById('debrisReportCoords').style.display = 'none';
        document.getElementById('debrisReportInstruction').textContent = 'Complete the form and select a location on the map.';
    }

    document.getElementById('debrisFormMessage').textContent = '';
    document.getElementById('debrisFormMessage').className = 'debris-form-message';
    debrisReportForm.reset();

    // Modali HEMEN ac (Kullanici "Haritadan Konum Sec" butonuna basicak)
    openModal(debrisReportModal);

    setStatus('Incident report form opened.', 'info');
}

function cancelDebrisReportMode() {
    debrisReportMode = false;
    debrisSelectionPending = false;
    // Kullanici X ile bilerek kapatirsa gecici isareti temizle (opsiyonel)
    closeModal(debrisReportModal);
    setStatus('Ready. Select points A and B on the map.', 'info');
}

// "Haritadan Konum Sec" Butonu Tiklaninca
document.addEventListener('DOMContentLoaded', () => {
    const btn = document.getElementById('selectDebrisMapBtn');
    if(btn) {
        btn.addEventListener('click', () => {
            debrisSelectionPending = true;
            closeModal(debrisReportModal); // Haritayi gorebilmesi icin modali gecici gizle
            setStatus('Click the map to select the incident location.', 'loading');
        });
    }

    // "Otomatik Konum Sec (Merkez)" Butonu Tiklaninca
    const autoBtn = document.getElementById('autoDebrisMapBtn');
    if(autoBtn) {
        autoBtn.addEventListener('click', () => {
            const center = map.getCenter();
            debrisReportLatLng = { lat: center.lat, lng: center.lng };
            
            // Koordinatlari goster
            document.getElementById('debrisReportCoords').style.display = 'block';
            document.getElementById('debrisReportLatLng').textContent = `Latitude: ${center.lat.toFixed(6)}, Longitude: ${center.lng.toFixed(6)}`;
            
            // Gecici marker ekle
            if (window._tempDebrisMarker) { map.removeLayer(window._tempDebrisMarker); }
            const iconHTML = `
                <div class="debris-select-marker-inner" style="background:#ef4444; border-color:white; box-shadow:0 4px 10px rgba(0,0,0,0.5);">
                    !
                    <div class="debris-select-marker-close" onclick="window.removeTempDebrisMarker(event)">×</div>
                </div>`;
            window._tempDebrisMarker = L.marker([center.lat, center.lng], {
                icon: L.divIcon({
                    className: 'debris-select-marker',
                    html: iconHTML,
                    iconSize: [40, 40],
                    iconAnchor: [20, 20]
                })
            }).addTo(map);
            
            showDebrisMessage('Location set to the map center.', 'success');
        });
    }

    // "Use Device GPS" Butonu Tiklaninca
    const gpsBtn = document.getElementById('autoGpsMapBtn');
    if(gpsBtn) {
        gpsBtn.addEventListener('click', () => {
            if (!navigator.geolocation) {
                Swal.fire('Error', 'Your browser does not support GPS location.', 'error');
                return;
            }

            gpsBtn.textContent = "⏳ Finding location…";
            gpsBtn.disabled = true;

            navigator.geolocation.getCurrentPosition(
                (position) => {
                    gpsBtn.textContent = "📡 Use Device GPS";
                    gpsBtn.disabled = false;

                    const realLat = position.coords.latitude;
                    const realLng = position.coords.longitude;

                    // Antakya Afet Bolgesi Bounding Box (Yaklasik)
                    // Lat: 36.1 - 36.4, Lng: 36.0 - 36.3
                    const isInsideDisasterZone = (realLat >= 36.1 && realLat <= 36.4 && realLng >= 36.0 && realLng <= 36.3);

                    let targetLat, targetLng;

                    if (!isInsideDisasterZone) {
                        // Afet bolgesi disinda - Simulasyon Modu
                        const center = map.getCenter();
                        targetLat = center.lat;
                        targetLng = center.lng;

                        // Modali gecici kapat, Swal'i goster
                        closeModal(debrisReportModal);

                        Swal.fire({
                            title: 'Simulation Mode Active',
                            html: `<b>Gerçek konumunuz tespit edildi:</b><br><span style="font-size:13px; color:#64748b;">(Latitude: ${realLat.toFixed(4)}, Longitude: ${realLng.toFixed(4)})</span><br><br>Sistem şu an <b>Antakya Afet Bölgesine</b> kilitli olduğu için hücresel konumunuz simülasyon amacıyla afet bölgesi merkezine yansıtılıyor.`,
                            icon: 'info',
                            confirmButtonText: 'Anladim',
                            confirmButtonColor: '#3b82f6'
                        }).then(() => {
                            // Swal kapatilinca modali tekrar ac
                            openModal(debrisReportModal);
                        });
                    } else {
                        // Afet bolgesi icinde -> gercek konumu kullan
                        targetLat = realLat;
                        targetLng = realLng;
                        showDebrisMessage('Your actual location was successfully captured within the disaster area.', 'success');
                    }

                    debrisReportLatLng = { lat: targetLat, lng: targetLng };
                    
                    document.getElementById('debrisReportCoords').style.display = 'block';
                    document.getElementById('debrisReportLatLng').textContent = `Latitude: ${targetLat.toFixed(6)}, Longitude: ${targetLng.toFixed(6)}`;
                    
                    if (window._tempDebrisMarker) { map.removeLayer(window._tempDebrisMarker); }
                    const iconHTML = `
                        <div class="debris-select-marker-inner" style="background:#ef4444; border-color:white; box-shadow:0 4px 10px rgba(0,0,0,0.5);">
                            !
                            <div class="debris-select-marker-close" onclick="window.removeTempDebrisMarker(event)">×</div>
                        </div>`;
                    window._tempDebrisMarker = L.marker([targetLat, targetLng], {
                        icon: L.divIcon({
                            className: 'debris-select-marker',
                            html: iconHTML,
                            iconSize: [40, 40],
                            iconAnchor: [20, 20]
                        })
                    }).addTo(map);
                    
                    map.flyTo([targetLat, targetLng], 17);
                },
                (error) => {
                    gpsBtn.textContent = "📡 Use Device GPS";
                    gpsBtn.disabled = false;
                    
                    let msg = "An unknown error occurred.";
                    if (error.code === 1) msg = "Konum erisimi reddedildi.";
                    else if (error.code === 2) msg = "Location could not be found.";
                    else if (error.code === 3) msg = "Zaman asimi.";
                    
                    Swal.fire('Location Error', msg, 'error');
                },
                { enableHighAccuracy: true, timeout: 10000, maximumAge: 0 }
            );
        });
    }
});

// Ozel marker'daki "X" kapatma butonuna basilinca
window.removeTempDebrisMarker = function(event) {
    if (event) event.stopPropagation();
    if (window._tempDebrisMarker) {
        map.removeLayer(window._tempDebrisMarker);
        window._tempDebrisMarker = null;
    }
    debrisReportLatLng = null;
    document.getElementById('debrisReportCoords').style.display = 'none';
    document.getElementById('debrisReportLatLng').textContent = '';
};

async function submitDebrisReport(e) {
    e.preventDefault();

    if (!debrisReportLatLng) {
        showDebrisMessage('Select a point on the map first!', 'error');
        return;
    }

    const msgEl = document.getElementById('debrisFormMessage');
    const submitBtn = document.getElementById('debrisSubmitBtn');

    // Form verilerini topla
    const reportData = {
        type: document.getElementById('incidentType').value,
        severity: document.getElementById('incidentSeverity').value,
        lat: debrisReportLatLng.lat,
        lng: debrisReportLatLng.lng,
        isim: document.getElementById('debrisName').value.trim() || null,
        yikilma_orani: document.getElementById('debrisCollapseRate').value || null,
        kisi_sayisi: document.getElementById('debrisPersonCount').value ? parseInt(document.getElementById('debrisPersonCount').value, 10) : null,
        saglik_durumu: document.getElementById('debrisHealthStatus').value || null,
        iletisim: document.getElementById('debrisPhone').value.trim() || null,
        aciklama: document.getElementById('debrisNote').value.trim(),
        tarih: new Date().toISOString(),
        durum: 'Reported'
    };

    submitBtn.disabled = true;
    showDebrisMessage('Submitting…', '');

    const saveDebrisLocalFallback = (data) => {
        const saved = JSON.parse(localStorage.getItem('enkaz_bildirimleri') || '[]');
        data.id = 'local_' + Date.now();
        data.offline = true;
        saved.push(data);
        localStorage.setItem('enkaz_bildirimleri', JSON.stringify(saved));
    };

    try {
        const formData = new FormData();
        formData.append('report_type', reportData.type);
        formData.append('severity', reportData.severity);
        formData.append('latitude', String(reportData.lat));
        formData.append('longitude', String(reportData.lng));
        formData.append('description', reportData.aciklama);
        if (reportData.isim) formData.append('reporter_name', reportData.isim);
        if (reportData.yikilma_orani) formData.append('collapse_rate', reportData.yikilma_orani);
        if (reportData.kisi_sayisi !== null) formData.append('people_count', String(reportData.kisi_sayisi));
        if (reportData.saglik_durumu) formData.append('health_status', reportData.saglik_durumu);
        if (reportData.iletisim) formData.append('contact', reportData.iletisim);
        const image = document.getElementById('incidentImage').files[0];
        if (image) formData.append('image', image);

        try {
            const response = await fetch(`${API_URL}/api/reports`, { method: 'POST', body: formData });
            const result = await response.json();
            if (!response.ok) throw new Error(result.detail || `HTTP ${response.status}`);
            Object.assign(reportData, {
                id: result.id,
                backendReport: true,
                status: result.status,
                image_url: result.image_url,
                updated_at: result.updated_at
            });
            showDebrisMessage('Incident report saved · status: Reported.', 'success');
        } catch (error) {
            if (image) throw new Error(`Could not upload the report with its photo: ${error.message}`);
            saveDebrisLocalFallback(reportData);
            showDebrisMessage('You are offline. The report has been queued on this device.', 'success');
        }

        // Gecici marker'i kaldir
        if (window._tempDebrisMarker) {
            map.removeLayer(window._tempDebrisMarker);
            window._tempDebrisMarker = null;
        }

        // Haritaya kalici marker ekle
        if (reportData.backendReport) upsertIncidentMarker(reportData);
        else addDebrisMarkerToMap(reportData);

        // Modu kapat
        setTimeout(() => {
            debrisReportMode = false;
            debrisSelectionPending = false;
            closeModal(debrisReportModal);
            setStatus('Incident report added.', 'success');
        }, 1500);

    } catch (error) {
        // Bu sadece en distaki odengunemeyen hatalar icin
        console.error('Enkaz bildirimi islenemedi:', error);
        showDebrisMessage(error.message || 'Could not save the report.', 'error');
    } finally {
        submitBtn.disabled = false;
    }
}

function showDebrisMessage(msg, type) {
    const el = document.getElementById('debrisFormMessage');
    el.textContent = msg;
    el.className = 'debris-form-message' + (type ? ' ' + type : '');
}

function upsertIncidentMarker(report) {
    const id = report.id;
    const existingIndex = reportedDebrisMarkers.findIndex(marker => marker.options.debrisId === id);
    if (existingIndex >= 0) {
        map.removeLayer(reportedDebrisMarkers[existingIndex]);
        reportedDebrisMarkers.splice(existingIndex, 1);
    }
    if (report.status === 'Resolved') return;
    addDebrisMarkerToMap({
        ...report,
        lat: report.latitude ?? report.lat,
        lng: report.longitude ?? report.lng,
        isim: report.reporter_name ?? report.isim,
        yikilma_orani: report.collapse_rate ?? report.yikilma_orani,
        kisi_sayisi: report.people_count ?? report.kisi_sayisi,
        saglik_durumu: report.health_status ?? report.saglik_durumu,
        iletisim: report.contact ?? report.iletisim,
        aciklama: report.description ?? report.aciklama,
        tarih: report.created_at ?? report.tarih,
        image_url: report.image_url || null,
        backendReport: true
    });
}

function addDebrisMarkerToMap(data) {
    const collapseLabels = {
        'hafif': 'Minor damage',
        'orta': 'Moderate damage',
        'agir': 'Severe damage',
        'tamamen': 'Destroyed'
    };
    const healthLabels = {
        'iyi': 'Good · no injuries',
        'hafif_yarali': 'Minor injuries',
        'agir_yarali': 'Severe injuries',
        'bilinmiyor': 'Unknown'
    };

    const collapseColor = {
        'hafif': '#22c55e',
        'orta': '#f59e0b',
        'agir': '#ef4444',
        'tamamen': '#991b1b'
    };

    const severityColor = { low: '#22c55e', medium: '#f59e0b', high: '#f97316', critical: '#dc2626' };
    const color = severityColor[data.severity] || collapseColor[data.yikilma_orani] || '#ef4444';
    
    // Rastgele ID atamasini saglama
    if (!data.id) data.id = 'temp_' + Date.now() + '_' + Math.random().toString(36).substring(7);

    const customIcon = L.divIcon({
        className: 'debris-select-marker',
        html: `
            <div class="debris-select-marker-inner" style="background: ${color}; border-color: white; box-shadow: 0 4px 10px rgba(0,0,0,0.5); animation: none;">
                !
                ${data.backendReport ? '' : `<div class="debris-select-marker-close" onclick="window.removePermanentDebris('${data.id}', event)">×</div>`}
            </div>`,
        iconSize: [40, 40],
        iconAnchor: [20, 20]
    });

    const marker = L.marker([data.lat, data.lng], {
        icon: customIcon,
        debrisId: data.id,
        yikilma_orani: data.yikilma_orani // Eklenen alan (rota motoru icin)
    }).addTo(map);

    // Popup content keeps reported text escaped because it is user supplied.
    const escapeHtml = (value) => String(value).replace(/[&<>"']/g, ch => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));
    const typeLabels = {
        blocked_road: 'Blocked road', building_damage: 'Building damage', fire: 'Fire',
        flood: 'Flooding', infrastructure_damage: 'Infrastructure damage',
        other_emergency: 'Other emergency'
    };
    const statusLabels = { 'Reported': 'Reported', 'Under Review': 'Under review', 'Verified': 'Verified', 'Resolved': 'Resolved' };
    const severityLabels = { low: 'Low', medium: 'Medium', high: 'High', critical: 'Critical' };
    let popupHtml = '<div style="font-family:Inter,sans-serif;font-size:13px;min-width:180px;">';
    popupHtml += `<b style="font-size:14px;">${escapeHtml(typeLabels[data.type] || 'Incident report')}</b><br>`;
    if (data.backendReport) popupHtml += `<b>Status:</b> ${escapeHtml(statusLabels[data.status] || data.status)}<br>`;
    else if (data.offline || String(data.id).startsWith('local_')) popupHtml += '<b>Status:</b> Queued on this device · not sent to server<br>';
    if (data.severity) popupHtml += `<b>Severity:</b> ${escapeHtml(severityLabels[data.severity] || data.severity)}<br>`;
    if (data.isim) popupHtml += `<b>Reported by:</b> ${escapeHtml(data.isim)}<br>`;
    if (data.yikilma_orani) popupHtml += `<b>Damage:</b> ${escapeHtml(collapseLabels[data.yikilma_orani] || data.yikilma_orani)}<br>`;
    if (data.kisi_sayisi) popupHtml += `<b>People:</b> ${escapeHtml(data.kisi_sayisi)}<br>`;
    if (data.saglik_durumu) popupHtml += `<b>Medical:</b> ${escapeHtml(healthLabels[data.saglik_durumu] || data.saglik_durumu)}<br>`;
    if (data.iletisim) popupHtml += `<b>Contact:</b> ${escapeHtml(data.iletisim)}<br>`;
    if (data.aciklama) popupHtml += `<b>Description:</b> ${escapeHtml(data.aciklama)}<br>`;
    if (data.image_url) popupHtml += `<img data-report-image="${escapeHtml(data.id)}" alt="Loading incident photo" style="display:block;width:100%;max-height:130px;object-fit:cover;border-radius:8px;margin-top:8px;">`;
    if (data.tarih) {
        const d = new Date(data.tarih);
        popupHtml += `<span style="color:#94a3b8;font-size:11px;">${d.toLocaleString('en-US')}</span><br>`;
    }
    popupHtml += `<hr style="margin:5px 0; border:0; border-top:1px solid #334155;">`;
    popupHtml += `<span style="color:#94a3b8;font-size:11px;">Coordinates: ${Number(data.lat).toFixed(6)}, ${Number(data.lng).toFixed(6)}</span>`;
    const statusOrder = ['Reported', 'Under Review', 'Verified', 'Resolved'];
    if (data.backendReport && statusOrder.indexOf(data.status) >= 0 && data.status !== 'Resolved') {
        const nextStatus = statusOrder[statusOrder.indexOf(data.status) + 1];
        popupHtml += `<button class="report-status-action" onclick="window.advanceIncidentStatus('${escapeHtml(data.id)}','${nextStatus}')">Mark as ${escapeHtml(statusLabels[nextStatus])}</button>`;
    }
    popupHtml += '</div>';

    marker.bindPopup(popupHtml);
    if (data.image_url && data.backendReport) {
        marker.on('popupopen', async () => {
            const imageElement = marker.getPopup().getElement()?.querySelector('[data-report-image]');
            if (!imageElement || marker._incidentImageUrl) return;
            try {
                const response = await fetch(`${API_URL}/api/reports/${encodeURIComponent(data.id)}/image`);
                if (!response.ok) throw new Error(`HTTP ${response.status}`);
                marker._incidentImageUrl = URL.createObjectURL(await response.blob());
                imageElement.src = marker._incidentImageUrl;
                imageElement.alt = 'Photo attached to report';
            } catch (error) {
                imageElement.remove();
            }
        });
        marker.on('popupclose', () => {
            if (marker._incidentImageUrl) {
                URL.revokeObjectURL(marker._incidentImageUrl);
                marker._incidentImageUrl = null;
            }
        });
    }
    reportedDebrisMarkers.push(marker);
}

// Firebase'den veya localStorage'dan mevcut bildirimleri yukle
async function loadExistingDebrisReports() {
    let reportsLoadedFromServer = false;
    try {
        try {
            const response = await fetch(`${API_URL}/api/reports`);
            if (response.ok) {
                const payload = await response.json();
                (payload.reports || []).filter(report => report.status !== 'Resolved').forEach(report => {
                    upsertIncidentMarker(report);
                });
                const safeSnapshot = (payload.reports || []).map(report => ({
                    id: report.id, type: report.type, latitude: report.latitude, longitude: report.longitude,
                    description: report.description, severity: report.severity, status: report.status,
                    created_at: report.created_at, updated_at: report.updated_at
                }));
                writeLocalJson(REPORTS_CACHE_KEY, safeSnapshot);
                recordSuccessfulSync();
                reportsLoadedFromServer = true;
            }
        } catch (apiError) {
            console.warn('Sunucudaki raporlar yuklenemedi:', apiError.message);
        }
        if (!reportsLoadedFromServer) {
            const cachedReports = readLocalJson(REPORTS_CACHE_KEY, []);
            cachedReports.filter(report => report.status !== 'Resolved').forEach(report => upsertIncidentMarker(report));
            if (cachedReports.length) setStatus(`${cachedReports.length} cached reports shown · data may be stale and offline.`, 'info');
            updateOfflineStatus();
        }
        if (db) {
            const snapshot = await db.collection('enkaz_bildirimleri').where('durum', '==', 'aktif').get();
            snapshot.forEach(doc => {
                const data = doc.data();
                data.id = doc.id;
                addDebrisMarkerToMap(data);
            });
            if (snapshot.size > 0) {
                console.log(`${snapshot.size} enkaz bildirimi Firebase'den yuklendi.`);
            }
        }
        const saved = JSON.parse(localStorage.getItem('enkaz_bildirimleri') || '[]');
        saved.forEach(data => addDebrisMarkerToMap(data));
        if (saved.length > 0) console.log(`${saved.length} yerel bildirim yuklendi.`);
    } catch (error) {
        console.error('Enkaz bildirimleri yuklenirken hata:', error);
    }
}

// ============================================
// MARKET SİLME
// ============================================

window.removePermanentDebris = async function(id, event) {
    if(event) event.stopPropagation();
    if(confirm('If the rescue operation is complete or this report is invalid, are you sure you want to remove it from the map?')) {
        // Haritadan kaldir
        const idx = reportedDebrisMarkers.findIndex(m => m.options.debrisId === id);
        if(idx !== -1) {
            map.removeLayer(reportedDebrisMarkers[idx]);
            reportedDebrisMarkers.splice(idx, 1);
        }
        
        // Veritabanindan sil (Local vs Firebase)
        if (id.startsWith('local_') || id.startsWith('temp_')) {
            let saved = JSON.parse(localStorage.getItem('enkaz_bildirimleri') || '[]');
            saved = saved.filter(item => item.id !== id);
            localStorage.setItem('enkaz_bildirimleri', JSON.stringify(saved));
            setStatus('Report removed from local storage.', 'success');
        } else {
            // Firebase REST ile sil
            if (typeof firebaseConfig !== 'undefined' && firebaseConfig.projectId) {
                const restUrl = `https://firestore.googleapis.com/v1/projects/${firebaseConfig.projectId}/databases/(default)/documents/enkaz_bildirimleri/${id}`;
                try {
                    await fetch(restUrl, { method: 'DELETE' });
                    setStatus('Report removed from the cloud database.', 'success');
                } catch(e) {
                    console.error('Firebase silme hatasi:', e);
                }
            }
        }
    }
}

window.advanceIncidentStatus = async function(id, currentStatus) {
    const order = ['Reported', 'Under Review', 'Verified', 'Resolved'];
    const currentIndex = order.indexOf(currentStatus);
    if (currentIndex < 0 || currentIndex >= order.length - 1) return;
    const nextStatus = order[currentIndex + 1];
    try {
        const response = await fetch(`${API_URL}/api/reports/${encodeURIComponent(id)}/status`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ status: nextStatus })
        });
        const result = await response.json();
        if (!response.ok) throw new Error(result.detail || `HTTP ${response.status}`);
        const index = reportedDebrisMarkers.findIndex(marker => marker.options.debrisId === id);
        if (index >= 0) {
            map.removeLayer(reportedDebrisMarkers[index]);
            reportedDebrisMarkers.splice(index, 1);
        }
        if (result.status !== 'Resolved') {
            upsertIncidentMarker(result);
        }
        setStatus(`Status updated: ${nextStatus}.`, 'success');
    } catch (error) {
        console.error('Rapor durumu güncellenemedi:', error);
        setStatus(error.message || 'Could not update report status.', 'error');
    }
};

// ============================================
// MODAL MANAGEMENT
// ============================================

function openModal(modal) {
    modal.classList.add('active');
}

function closeModal(modal) {
    modal.classList.remove('active');
}

// ============================================
// MAP INIT
// ============================================

function initMap() {
    map = L.map('map', {
        preferCanvas: true,
        maxZoom: 22,
        zoomControl: false
    }).setView(MAP_CENTER, MAP_ZOOM);

    L.control.zoom({ position: 'bottomright' }).addTo(map);

    L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', {
        attribution: 'Maxar | Esri',
        maxZoom: 22,
        maxNativeZoom: 18,
        crossOrigin: ''
    }).addTo(map);

    map.on('click', onMapClick);
}

function onMapClick(e) {
    // Eger kullanici haritadan enkaz secmek icin bekliyorsa
    if (debrisReportMode && debrisSelectionPending) {
        debrisSelectionPending = false;
        debrisReportLatLng = { lat: e.latlng.lat, lng: e.latlng.lng };

        // Oncekini temizle
        if (window._tempDebrisMarker) { map.removeLayer(window._tempDebrisMarker); }

        // Ozel custom divIcon marker
        const iconHTML = `
            <div class="debris-select-marker-inner">
                !
                <div class="debris-select-marker-close" onclick="window.removeTempDebrisMarker(event)">×</div>
            </div>`;
            
        const customIcon = L.divIcon({
            className: 'debris-select-marker',
            html: iconHTML,
            iconSize: [40, 40],
            iconAnchor: [20, 20]
        });

        window._tempDebrisMarker = L.marker([e.latlng.lat, e.latlng.lng], { icon: customIcon }).addTo(map);

        // Modali geri ac ve bilgiyi guncelle
        document.getElementById('debrisReportCoords').style.display = 'block';
        document.getElementById('debrisReportLatLng').textContent = `Latitude: ${e.latlng.lat.toFixed(6)}, Longitude: ${e.latlng.lng.toFixed(6)}`;
        document.getElementById('debrisReportInstruction').textContent = 'Location selected. Complete the form.';
        
        openModal(debrisReportModal);
        setStatus('Location selected. Continue completing the form.', 'success');
        return;
    }

    if (isAnalyzing) return;

    if (!markers.A) {
        setMarker('A', e.latlng.lat, e.latlng.lng);
        setStatus('Start point A selected. Now select destination B.', 'info');
    } else if (!markers.B) {
        setMarker('B', e.latlng.lat, e.latlng.lng);
        setStatus('Points A and B are ready! Select “Start Analysis” to run the analysis.', 'info');
    }
}

function setMarker(point, lat, lng) {
    const color = point === 'A' ? '#3b82f6' : '#ef4444';
    const label = point === 'A' ? 'Baslangic (A)' : 'Hedef (B)';

    if (markers[point]) {
        map.removeLayer(markers[point]);
    }

    markers[point] = L.marker([lat, lng], {
        draggable: true,
        icon: createMarkerIcon(point, color)
    }).addTo(map);

    markers[point].bindPopup(`<b>${label}</b>`).openPopup();

    markers[point].on('dragend', function (e) {
        const pos = e.target.getLatLng();
        updateCoordInputs(point, pos.lat, pos.lng);
    });

    updateCoordInputs(point, lat, lng);
    map.panTo([lat, lng]);
}

function updateCoordInputs(point, lat, lng) {
    if (point === 'A') {
        coordALat.value = lat.toFixed(6);
        coordALon.value = lng.toFixed(6);
    } else {
        coordBLat.value = lat.toFixed(6);
        coordBLon.value = lng.toFixed(6);
    }
}

function applyCoordinate(point) {
    const latInput = point === 'A' ? coordALat : coordBLat;
    const lonInput = point === 'A' ? coordALon : coordBLon;

    const lat = parseFloat(latInput.value);
    const lon = parseFloat(lonInput.value);

    if (isNaN(lat) || isNaN(lon)) {
        setStatus('Invalid coordinates. Latitude and longitude must be numbers.', 'error');
        return;
    }

    if (lat < -90 || lat > 90) {
        setStatus('Latitude must be between -90 and 90.', 'error');
        return;
    }

    if (lon < -180 || lon > 180) {
        setStatus('Longitude must be between -180 and 180.', 'error');
        return;
    }

    setMarker(point, lat, lon);

    const pointLabel = point === 'A' ? 'Baslangic (A)' : 'Hedef (B)';
    setStatus(`${pointLabel} point set: ${lat.toFixed(6)}, ${lon.toFixed(6)}`, 'info');

    if (markers.A && markers.B) {
        setTimeout(() => {
            setStatus('Points A and B are ready! Select “Start Analysis” to run the analysis.', 'info');
        }, 1500);
    }
}

function createMarkerIcon(label, color) {
    return L.divIcon({
        className: 'custom-marker',
        html: `<div style="
            background: ${color};
            width: 32px; height: 32px;
            border-radius: 50%;
            display: flex; align-items: center; justify-content: center;
            color: white; font-weight: 700; font-size: 14px;
            box-shadow: 0 3px 12px ${color}88;
            border: 2px solid white;
            font-family: Inter, sans-serif;
        ">${label}</div>`,
        iconSize: [32, 32],
        iconAnchor: [16, 16]
    });
}

// ============================================
// MESAFE HESABI (Haversine)
// ============================================
function haversineDistance(coords) {
    if (!coords || coords.length < 2) return 0;
    let total = 0;
    const R = 6371000;
    for (let i = 0; i < coords.length - 1; i++) {
        const [lat1, lon1] = coords[i];
        const [lat2, lon2] = coords[i + 1];
        const dLat = (lat2 - lat1) * Math.PI / 180;
        const dLon = (lon2 - lon1) * Math.PI / 180;
        const a = Math.sin(dLat / 2) ** 2 + Math.cos(lat1 * Math.PI / 180) * Math.cos(lat2 * Math.PI / 180) * Math.sin(dLon / 2) ** 2;
        total += R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
    }
    return total;
}

function formatDist(meters) {
    return meters >= 1000 ? `${(meters / 1000).toFixed(2)} km` : `${Math.round(meters)} m`;
}

function formatTime(meters) {
    // Araç hızı: 20 km/s (afet bölgesi yavaş seyir)
    const min = Math.round(meters / 20000 * 60);
    return min < 1 ? '<1 min' : `~${min} min`;
}

function formatEstimatedTime(seconds) {
    const minutes = Math.max(1, Math.round((Number(seconds) || 0) / 60));
    if (minutes >= 60) return `~${Math.floor(minutes / 60)} hr ${minutes % 60} min`;
    return `~${minutes} min`;
}

function summarizeRouteMetrics(metrics) {
    if (!metrics) return '';
    const unknownCount = Array.isArray(metrics.unknown_segments) ? metrics.unknown_segments.length : 0;
    return `${formatDist(metrics.distance_m)} · ${formatEstimatedTime(metrics.estimated_time_s)} · Risk ${metrics.risk_score}% · ${unknownCount} unknown segments`;
}

// ============================================
// ROTA ETİKETİ
// ============================================
function drawRouteLabel(routeCoords, type, metrics = null) {
    const totalMeters = haversineDistance(routeCoords);
    const distText = formatDist(metrics?.distance_m ?? totalMeters);
    const timeText = metrics?.estimated_time_s != null
        ? formatEstimatedTime(metrics.estimated_time_s)
        : formatTime(totalMeters);
    const isAlt = type === 'alt';

    // Rotanın %40 noktasını al (alt ve main çakışmasın)
    const idx = isAlt
        ? Math.floor(routeCoords.length * 0.35)
        : Math.floor(routeCoords.length * 0.60);
    const point = routeCoords[Math.max(0, Math.min(idx, routeCoords.length - 1))];

    const html = isAlt
        ? `<div class="route-label-bubble route-label-bubble-alt">
               <span class="route-label-tag">ALT</span>
               <span class="route-label-dist route-label-dist-alt">${distText}</span>
               <span class="route-label-sep">·</span>
               <span class="route-label-time">${timeText}</span>
           </div>`
        : `<div class="route-label-bubble">
               <span class="route-label-dist">${distText}</span>
               <span class="route-label-sep">·</span>
               <span class="route-label-time">${timeText}</span>
           </div>`;

    // iconAnchor: etiket genişliği ~120px, yükseklik ~28px — ortala
    const icon = L.divIcon({
        className: '',
        html: html,
        iconSize: [140, 30],
        iconAnchor: [70, 15]
    });

    const lm = L.marker(point, { icon, interactive: false, zIndexOffset: 500 }).addTo(map);
    routeLabelLayers.push(lm);
}

// === ANALIZ ===
async function otonomAnalizEt() {
    if (!markers.A || !markers.B) {
        setStatus('Select points A and B on the map first!', 'error');
        return;
    }
    if (isAnalyzing) return;

    isAnalyzing = true;
    analizBtn.disabled = true;
    clearResults();
    showProgress(true);

    try {
        setStep('capture');
        setStatus('Capturing the map image…', 'loading');

        const bounds = map.getBounds();
        const nw = bounds.getNorthWest();
        const se = bounds.getSouthEast();

        const scale = Math.min(window.devicePixelRatio || 1, 2);
        const canvas = await html2canvas(document.getElementById('map'), {
            useCORS: true,
            scale: scale,
            logging: false,
            backgroundColor: null,
            removeContainer: true
        });

        setStep('ai');
        setStatus('AI is analyzing the image…', 'loading');

        const blob = await new Promise(resolve => {
            canvas.toBlob(resolve, 'image/png', 1.0);
        });

        const formData = new FormData();
        formData.append('uydu_fotosu', blob, 'viewport.png');
        formData.append('nw_lat', nw.lat);
        formData.append('nw_lon', nw.lng);
        formData.append('se_lat', se.lat);
        formData.append('se_lon', se.lng);
        formData.append('baslangic_lat', markers.A.getLatLng().lat);
        formData.append('baslangic_lon', markers.A.getLatLng().lng);
        formData.append('hedef_lat', markers.B.getLatLng().lat);
        formData.append('hedef_lon', markers.B.getLatLng().lng);
        formData.append('routing_mode', routingMode.value);

        // Kullanicinin manuel ekledigi enkazlari da rotaya gonder
        const activeManualDebris = reportedDebrisMarkers.map(m => {
            return {
                lat: m.getLatLng().lat,
                lon: m.getLatLng().lng,
                radius: m.options.yikilma_orani === 'hafif' ? 15 : (m.options.yikilma_orani === 'orta' ? 25 : 40)
            };
        });
        formData.append('manuel_enkazlar', JSON.stringify(activeManualDebris));

        setStep('route');
        setStatus('Calculating a safe route…', 'loading');

        const response = await fetch(`${API_URL}/api/otonom-analiz`, {
            method: 'POST',
            body: formData
        });

        if (!response.ok) {
            throw new Error(`Sunucu hatasi: ${response.status}`);
        }

        const res = await response.json();

        if (res.durum === 'basarili') {
            setStep('done');
            drawResults(res);
            showStats(res);
            setStatus(
                `Analysis complete. <b>${(res.enkazlar || []).filter(item => item.sinif !== 'manual_report').length}</b> AI findings awaiting verification; route: ${summarizeRouteMetrics(res.primary_metrics)}.`,
                'success'
            );
        } else {
            setStatus(`${res.mesaj || 'Unknown error'}`, 'error');
        }

    } catch (e) {
        console.error('Analiz hatasi:', e);
        setStatus(`Connection error. Is the server running? (${e.message})`, 'error');
    } finally {
        isAnalyzing = false;
        analizBtn.disabled = false;
    }
}

// === SONUC CIZIMI ===
function drawResults(res) {
    renderFindings(res.enkazlar || []);
    if (res.enkazlar) {
        res.enkazlar.forEach(enkaz => {
            const coord = [enkaz.lat, enkaz.lon];
            const radius = enkaz.tehlike_yaricapi_m || 50;

            const dangerZone = L.circle(coord, {
                radius: radius,
                color: '#ef4444',
                fillColor: '#ef4444',
                fillOpacity: 0.12,
                weight: 1,
                dashArray: '5,5'
            }).addTo(map);
            dangerZoneLayers.push(dangerZone);

            const confidencePercent = Math.round((enkaz.confidence || 0) * 100);
            const riskScore = enkaz.risk_score || 0.2;
            const riskPercent = Math.round(riskScore * 100);
            const riskLabel = riskScore >= 0.9 ? 'CRITICAL' : riskScore >= 0.5 ? 'MEDIUM' : 'LOW';
            const markerColor = riskScore >= 0.9 ? '#dc2626' : riskScore >= 0.5 ? '#f97316' : '#22c55e';
            const markerFill = riskScore >= 0.9 ? '#f87171' : riskScore >= 0.5 ? '#fdba74' : '#86efac';

            if (enkaz.sinif !== 'manual_report') {
                const marker = L.circleMarker(coord, {
                    radius: 7,
                    color: markerColor,
                    fillColor: markerFill,
                    fillOpacity: 0.9,
                    weight: 2
                }).addTo(map);
                marker.bindPopup(
                    `<div style="font-family:Inter,sans-serif;font-size:13px;">
                        <b>Damage Detection</b><br>
                        Class: ${enkaz.sinif || 'debris'}<br>
                        Confidence: ${confidencePercent}%<br>
                        Hazard Radius: ${radius} m<br>
                        Risk: %${riskPercent} (${riskLabel})
                    </div>`
                );
                enkazLayers.push(marker);
            }
        });
    }

    if (res.alternatif_rota && res.alternatif_rota.length > 1) {
        altRouteLayer = L.polyline(res.alternatif_rota, {
            color: '#ffe500',
            weight: 7,
            opacity: 0.96,
            dashArray: '8,8',
            lineCap: 'round',
            className: 'route-line-alternative'
        }).addTo(map);
        
        drawRouteLabel(res.alternatif_rota, 'alt', res.alternative_metrics);
    }

    if (res.guvenli_rota && res.guvenli_rota.length > 1) {
        const glow = L.polyline(res.guvenli_rota, {
            color: '#00e676',
            weight: 16,
            opacity: 0.32,
            lineCap: 'round',
            lineJoin: 'round',
            className: 'route-line-primary-glow'
        }).addTo(map);
        enkazLayers.push(glow);

        routeLayer = L.polyline(res.guvenli_rota, {
            color: '#00e676',
            weight: 6,
            opacity: 1.0,
            lineCap: 'round',
            lineJoin: 'round',
            className: 'route-line-primary'
        }).addTo(map);
        
        drawRouteLabel(res.guvenli_rota, 'main', res.primary_metrics);
    }

    legendPanel.classList.add('visible');
}

function renderFindings(enkazlar) {
    const aiFindings = enkazlar.filter(item => item.sinif !== 'manual_report');
    findingsList.replaceChildren();
    findingsCount.textContent = String(aiFindings.length);
    findingsEmpty.hidden = aiFindings.length > 0;

    aiFindings.forEach((item, index) => {
        const confidence = Math.max(0, Math.min(100, Math.round((Number(item.confidence) || 0) * 100)));
        const row = document.createElement('li');
        row.className = 'finding-item';

        const main = document.createElement('div');
        main.className = 'finding-main';
        const name = document.createElement('span');
        name.className = 'finding-name';
        name.textContent = item.sinif || 'Unknown object';
        const meta = document.createElement('span');
        meta.className = 'finding-meta';
        meta.textContent = `AI prediction · Point ${index + 1}`;
        main.append(name, meta);

        const score = document.createElement('span');
        score.className = 'finding-confidence';
        score.textContent = `${confidence}%`;

        const meter = document.createElement('span');
        meter.className = 'finding-meter';
        meter.setAttribute('aria-label', `Guven skoru yuzde ${confidence}`);
        const fill = document.createElement('span');
        fill.style.width = `${confidence}%`;
        meter.append(fill);

        row.append(main, score, meter);
        findingsList.append(row);
    });

    findingsPanel.hidden = false;
}

// === UI HELPERS ===
function setStatus(html, type) {
    statusBox.innerHTML = html;
    statusBox.className = 'status-box';
    if (type === 'loading') statusBox.classList.add('status-loading');
    if (type === 'success') statusBox.classList.add('status-success');
    if (type === 'error') statusBox.classList.add('status-error');
}

function showProgress(show) {
    progressSteps.classList.toggle('visible', show);
    [stepCapture, stepAI, stepRoute, stepDone].forEach(s => {
        s.className = 'progress-step';
    });
    resetStepIcons();
}

function resetStepIcons() {
    const steps = [stepCapture, stepAI, stepRoute, stepDone];
    steps.forEach((s, i) => {
        s.querySelector('.step-icon').textContent = (i + 1).toString();
    });
}

function setStep(step) {
    const steps = ['capture', 'ai', 'route', 'done'];
    const elements = [stepCapture, stepAI, stepRoute, stepDone];
    const currentIdx = steps.indexOf(step);

    elements.forEach((el, i) => {
        if (i < currentIdx) {
            el.className = 'progress-step done';
            el.querySelector('.step-icon').textContent = '\u2713';
        } else if (i === currentIdx) {
            el.className = 'progress-step active';
        } else {
            el.className = 'progress-step';
        }
    });
}

function showStats(res) {
    statEnkaz.textContent = res.tespit_sayisi || 0;
    
    // Rota mesafesi, tahmini sure ve risk backend olcumlerinden gelir.
    if (res.guvenli_rota && res.guvenli_rota.length > 1) {
        const metrics = res.primary_metrics;
        if (metrics) {
            statRota.textContent = `${formatDist(metrics.distance_m)} · ${formatEstimatedTime(metrics.estimated_time_s)}`;
            statRouteMeta.textContent = `Risk ${metrics.risk_score}% · ${metrics.unknown_segments.length} unknown segments`;
        } else {
            const dist = haversineDistance(res.guvenli_rota);
            statRota.textContent = `${formatDist(dist)} · ${formatTime(dist)}`;
            statRouteMeta.textContent = '';
        }
    } else {
        statRota.textContent = '—';
        statRouteMeta.textContent = '';
    }
    
    statsGrid.classList.add('visible');
}

function clearResults() {
    if (routeLayer) { map.removeLayer(routeLayer); routeLayer = null; }
    if (altRouteLayer) { map.removeLayer(altRouteLayer); altRouteLayer = null; }
    enkazLayers.forEach(l => { try { map.removeLayer(l); } catch(e) {} });
    dangerZoneLayers.forEach(l => { try { map.removeLayer(l); } catch(e) {} });
    routeLabelLayers.forEach(l => { try { map.removeLayer(l); } catch(e) {} });
    enkazLayers = [];
    dangerZoneLayers = [];
    routeLabelLayers = [];
    statsGrid.classList.remove('visible');
    statRouteMeta.textContent = '';
    findingsPanel.hidden = true;
    legendPanel.classList.remove('visible');
    showProgress(false);
}

function resetAll() {
    clearResults();
    if (markers.A) { map.removeLayer(markers.A); markers.A = null; }
    if (markers.B) { map.removeLayer(markers.B); markers.B = null; }

    coordALat.value = '';
    coordALon.value = '';
    coordBLat.value = '';
    coordBLon.value = '';

    if (droneCtx && droneCanvas) {
        droneCtx.clearRect(0, 0, droneCanvas.width, droneCanvas.height);
    }
    dronePoints = { A: null, B: null };
    droneImageObj = null;
    droneFileInput.value = '';
    droneAnalyzeBtn.disabled = true;
    droneInstruction.textContent = "1. Click the image to select the start point (A).";

    setStatus('Ready. Select points A and B on the map or enter coordinates.', 'info');
}

// === DRONE MODU LOGIC ===

function openDroneMode(file) {
    if (!file) {
        alert("Error: Could not read the file.");
        return;
    }

    const reader = new FileReader();
    reader.onerror = function() { alert("File read error."); };
    reader.onload = function(e) {
        const img = new Image();
        img.onerror = function() { alert("Image load error."); };
        img.onload = function() {
            droneImageObj = img;

            droneCanvas.width = img.width;
            droneCanvas.height = img.height;

            const container = document.getElementById('droneCanvasContainer');
            const containerH = container.clientHeight - 30;
            const containerW = container.clientWidth - 30;

            const imgAspect = img.width / img.height;
            const containerAspect = containerW / containerH;

            if (imgAspect > containerAspect) {
                droneCanvas.style.width = '100%';
                droneCanvas.style.height = 'auto';
            } else {
                droneCanvas.style.height = '100%';
                droneCanvas.style.width = 'auto';
            }

            droneCtx = droneCanvas.getContext('2d', { alpha: false });
            droneCtx.imageSmoothingEnabled = true;
            droneCtx.imageSmoothingQuality = 'high';

            droneCtx.drawImage(img, 0, 0);

            document.getElementById('droneWorkspaceEmpty').style.display = 'none';
            container.style.display = 'flex';
            document.getElementById('droneAnalysisMeta').hidden = true;
            dronePoints = { A: null, B: null };
            droneInstruction.textContent = "1. Click the image to select the start point (A).";
            droneAnalyzeBtn.disabled = true;
        }
        img.src = e.target.result;
    }
    reader.readAsDataURL(file);
}

function closeDroneMode() {
    droneModeOverlay.style.display = 'none';
    document.getElementById('droneWorkspaceEmpty').style.display = 'flex';
    document.getElementById('droneCanvasContainer').style.display = 'none';
    droneFileInput.value = '';
}

function pixelToGps(px, py) {
    const nwLat = parseFloat(document.getElementById('droneNWLat').value);
    const nwLon = parseFloat(document.getElementById('droneNWLon').value);
    const seLat = parseFloat(document.getElementById('droneSELat').value);
    const seLon = parseFloat(document.getElementById('droneSELon').value);

    if (isNaN(nwLat) || isNaN(nwLon) || isNaN(seLat) || isNaN(seLon)) return null;

    const lat = nwLat - (py / droneCanvas.height) * (nwLat - seLat);
    const lon = nwLon + (px / droneCanvas.width) * (seLon - nwLon);
    return { lat, lon };
}

function redrawDroneCanvas() {
    if (!droneImageObj) return;

    droneCtx.clearRect(0, 0, droneCanvas.width, droneCanvas.height);
    droneCtx.drawImage(droneImageObj, 0, 0);

    if (dronePoints.A) drawDroneMarker(dronePoints.A.x, dronePoints.A.y, 'A', '#3b82f6');
    if (dronePoints.B) drawDroneMarker(dronePoints.B.x, dronePoints.B.y, 'B', '#ef4444');
}

function drawDroneMarker(x, y, label, color) {
    droneCtx.beginPath();
    droneCtx.arc(x, y, 15, 0, 2 * Math.PI);
    droneCtx.fillStyle = color;
    droneCtx.fill();
    droneCtx.lineWidth = 3;
    droneCtx.strokeStyle = '#ffffff';
    droneCtx.stroke();

    droneCtx.fillStyle = '#ffffff';
    droneCtx.font = 'bold 16px Arial';
    droneCtx.textAlign = 'center';
    droneCtx.textBaseline = 'middle';
    droneCtx.fillText(label, x, y + 1);
}

droneCanvas.addEventListener('click', (e) => {
    if (isDroneAnalyzing) return;

    const rect = droneCanvas.getBoundingClientRect();
    const scaleX = droneCanvas.width / rect.width;
    const scaleY = droneCanvas.height / rect.height;

    const x = Math.round((e.clientX - rect.left) * scaleX);
    const y = Math.round((e.clientY - rect.top) * scaleY);

    if (!dronePoints.A) {
        dronePoints.A = { x, y };
        droneInstruction.textContent = "2. Select the destination point (B).";
    } else if (!dronePoints.B) {
        dronePoints.B = { x, y };
        droneInstruction.textContent = "3. Points selected. You can start the analysis.";
        droneAnalyzeBtn.disabled = false;
    } else {
        dronePoints.A = { x, y };
        dronePoints.B = null;
        droneInstruction.textContent = "2. Select the destination point (B).";
        droneAnalyzeBtn.disabled = true;
    }

    redrawDroneCanvas();
});

async function runDroneAnalysis() {
    if (!droneImageObj || !dronePoints.A || !dronePoints.B) return;

    const nwLat = parseFloat(document.getElementById('droneNWLat').value);
    const nwLon = parseFloat(document.getElementById('droneNWLon').value);
    const seLat = parseFloat(document.getElementById('droneSELat').value);
    const seLon = parseFloat(document.getElementById('droneSELon').value);

    if (isNaN(nwLat) || isNaN(nwLon) || isNaN(seLat) || isNaN(seLon)) {
        droneInstruction.textContent = "Error: Enter the GPS bounds (NW and SE corners).";
        return;
    }

    const gpsA = pixelToGps(dronePoints.A.x, dronePoints.A.y);
    const gpsB = pixelToGps(dronePoints.B.x, dronePoints.B.y);

    if (!gpsA || !gpsB) {
        droneInstruction.textContent = "Error: Could not convert GPS coordinates.";
        return;
    }

    isDroneAnalyzing = true;
    droneAnalyzeBtn.disabled = true;
    droneCloseBtn.disabled = true;
    droneInstruction.textContent = "SAHI analysis + route calculation… (using the real street network)";

    try {
        droneCanvas.toBlob(async (blob) => {
            try {
                const formData = new FormData();
                formData.append('resim', blob, 'drone_image.jpg');
                formData.append('nw_lat', nwLat);
                formData.append('nw_lon', nwLon);
                formData.append('se_lat', seLat);
                formData.append('se_lon', seLon);
                formData.append('start_lat', gpsA.lat);
                formData.append('start_lon', gpsA.lon);
                formData.append('end_lat', gpsB.lat);
                formData.append('end_lon', gpsB.lon);
                formData.append('routing_mode', routingMode.value);

                // Kullanicinin manuel ekledigi enkazlari da drone rotasina gonder
                const activeManualDebris = reportedDebrisMarkers.map(m => {
                    return {
                        lat: m.getLatLng().lat,
                        lon: m.getLatLng().lng,
                        radius: m.options.yikilma_orani === 'hafif' ? 15 : (m.options.yikilma_orani === 'orta' ? 25 : 40)
                    };
                });
                formData.append('manuel_enkazlar', JSON.stringify(activeManualDebris));

                const response = await fetch(`${API_URL}/api/drone-analiz`, {
                    method: 'POST',
                    body: formData
                });

                const res = await response.json();
                if (!response.ok) {
                    throw new Error(res.detail || res.mesaj || `HTTP Error: ${response.status}`);
                }

                if (res.durum === 'basarili') {
                    droneInstruction.textContent = `${res.tespit_sayisi} AI findings · field verification required. ${summarizeRouteMetrics(res.primary_metrics)}.`;
                    document.getElementById('droneAnalysisId').textContent = `Analysis ID: ${res.analysis_id}`;
                    document.getElementById('droneAnalysisMeta').hidden = false;
                    drawDroneResults(res.engeller, res.rota, res.rota_alt);
                } else {
                    droneInstruction.textContent = `Error: ${res.mesaj}`;
                }
            } catch (innerErr) {
                console.error("Drone analiz hatasi (ic):", innerErr);
                droneInstruction.textContent = `Failed: ${innerErr.message}`;
            } finally {
                isDroneAnalyzing = false;
                droneCloseBtn.disabled = false;
            }
        }, 'image/jpeg', 0.95);

    } catch (e) {
        console.error("Drone analiz hatasi:", e);
        droneInstruction.textContent = "Failed: Server connection error.";
        isDroneAnalyzing = false;
        droneCloseBtn.disabled = false;
    }
}

function drawDroneResults(engeller, rota, rotaAlt) {
    redrawDroneCanvas();

    engeller.forEach(enc => {
        const halfW = enc.w / 2;
        const halfH = enc.h / 2;
        const x = enc.x - halfW;
        const y = enc.y - halfH;

        droneCtx.lineWidth = 3;
        droneCtx.strokeStyle = enc.risk_score >= 0.9 ? '#ef4444' :
                              enc.risk_score >= 0.5 ? '#f97316' : '#22c55e';

        droneCtx.fillStyle = enc.risk_score >= 0.9 ? 'rgba(239, 68, 68, 0.25)' :
                            enc.risk_score >= 0.5 ? 'rgba(249, 115, 22, 0.25)' : 'rgba(34, 197, 94, 0.25)';

        droneCtx.beginPath();
        droneCtx.rect(x, y, enc.w, enc.h);
        droneCtx.fill();
        droneCtx.stroke();

        droneCtx.fillStyle = '#fff';
        droneCtx.font = 'bold 12px Arial';
        droneCtx.fillText(`${enc.sinif} %${Math.round(enc.confidence * 100)}`, x + 4, y - 4);
    });

    if (rotaAlt && rotaAlt.length > 1) {
        droneCtx.beginPath();
        droneCtx.moveTo(rotaAlt[0][0], rotaAlt[0][1]);
        for (let i = 1; i < rotaAlt.length; i++) {
            droneCtx.lineTo(rotaAlt[i][0], rotaAlt[i][1]);
        }
        droneCtx.strokeStyle = '#ffe500';
        droneCtx.lineWidth = 5;
        droneCtx.setLineDash([12, 8]);
        droneCtx.lineCap = 'round';
        droneCtx.lineJoin = 'round';
        droneCtx.stroke();
        droneCtx.setLineDash([]);
    }

    if (rota && rota.length > 1) {
        droneCtx.beginPath();
        droneCtx.moveTo(rota[0][0], rota[0][1]);
        for (let i = 1; i < rota.length; i++) {
            droneCtx.lineTo(rota[i][0], rota[i][1]);
        }
        droneCtx.strokeStyle = 'rgba(0, 230, 118, 0.35)';
        droneCtx.lineWidth = 14;
        droneCtx.lineCap = 'round';
        droneCtx.lineJoin = 'round';
        droneCtx.stroke();

        droneCtx.beginPath();
        droneCtx.moveTo(rota[0][0], rota[0][1]);
        for (let i = 1; i < rota.length; i++) {
            droneCtx.lineTo(rota[i][0], rota[i][1]);
        }
        droneCtx.strokeStyle = '#00e676';
        droneCtx.lineWidth = 5;
        droneCtx.stroke();

        droneCtx.strokeStyle = '#fef08a';
        droneCtx.lineWidth = 1.5;
        droneCtx.stroke();
    }

    if (dronePoints.A) drawDroneMarker(dronePoints.A.x, dronePoints.A.y, 'A', '#3b82f6');
    if (dronePoints.B) drawDroneMarker(dronePoints.B.x, dronePoints.B.y, 'B', '#ef4444');
}

// === BOOT ===
document.addEventListener('DOMContentLoaded', () => {
    initMap();
    loadEmergencyLocations();
    document.getElementById('refreshEmergencyLocations').addEventListener('click', () => loadEmergencyLocations(true));
    document.getElementById('emergencyLocationFilters').addEventListener('click', (event) => {
        const button = event.target.closest('[data-location-type]');
        if (!button) return;
        const type = button.dataset.locationType;
        const isVisible = button.getAttribute('aria-pressed') === 'true';
        button.setAttribute('aria-pressed', String(!isVisible));
        if (isVisible) visibleEmergencyTypes.delete(type);
        else visibleEmergencyTypes.add(type);
        applyEmergencyLocationFilters();
    });

    document.getElementById('openDashboardBtn').addEventListener('click', () => {
        openModal(dashboardModal);
        loadDashboardData();
    });
    document.getElementById('dashboardClose').addEventListener('click', () => closeModal(dashboardModal));
    dashboardModal.addEventListener('click', (event) => {
        if (event.target === dashboardModal) closeModal(dashboardModal);
    });
    dashboardFilters.addEventListener('submit', (event) => {
        event.preventDefault();
        loadDashboardData();
    });
    document.getElementById('dashboardRefresh').addEventListener('click', loadDashboardData);
    document.getElementById('dashboardAiReviews').addEventListener('click', async (event) => {
        const button = event.target.closest('[data-review][data-analysis-id]');
        if (!button) return;
        const analysisId = button.dataset.analysisId;
        const note = document.querySelector(`[data-analysis-note="${CSS.escape(analysisId)}"]`)?.value || '';
        button.disabled = true;
        try {
            const response = await fetch(`${API_URL}/api/analyses/${encodeURIComponent(analysisId)}/review`, {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ status: button.dataset.review, note })
            });
            const result = await response.json();
            if (!response.ok) throw new Error(result.detail || `HTTP ${response.status}`);
            setStatus('Review saved. Physical field verification is still required.', 'success');
            loadDashboardData();
        } catch (error) {
            setStatus(error.message || 'Could not save the AI review.', 'error');
        } finally {
            button.disabled = false;
        }
    });

    loadExistingDebrisReports();
    connectRealtimeUpdates();

    // Ana analiz ve sifirlama butonlari
    analizBtn.addEventListener('click', otonomAnalizEt);
    resetBtn.addEventListener('click', resetAll);

    // Theme toggle
    themeToggleBtn.addEventListener('click', toggleTheme);

    // Hava Durumu
    weatherBtn.addEventListener('click', () => {
        const isActive = weatherPanel.classList.contains('active');
        if (isActive) {
            weatherPanel.classList.remove('active');
        } else {
            weatherPanel.classList.add('active');
            fetchWeather();
        }
    });
    weatherPanelClose.addEventListener('click', () => {
        weatherPanel.classList.remove('active');
    });

    // Deprem modal
    earthquakeBtn.addEventListener('click', () => {
        openModal(earthquakeModal);
        fetchEarthquakes();
    });
    earthquakeModalClose.addEventListener('click', () => closeModal(earthquakeModal));
    earthquakeModal.addEventListener('click', (e) => {
        if (e.target === earthquakeModal) closeModal(earthquakeModal);
    });

    // Acil durum modal
    emergencyBtn.addEventListener('click', () => {
        openModal(emergencyModal);
        renderEmergencyNumbers();
    });
    emergencyModalClose.addEventListener('click', () => closeModal(emergencyModal));
    emergencyModal.addEventListener('click', (e) => {
        if (e.target === emergencyModal) closeModal(emergencyModal);
    });

    // Enkaz Bildir
    reportDebrisBtn.addEventListener('click', startDebrisReportMode);
    debrisReportClose.addEventListener('click', cancelDebrisReportMode);
    debrisReportModal.addEventListener('click', (e) => {
        if (e.target === debrisReportModal) cancelDebrisReportMode();
    });
    debrisReportForm.addEventListener('submit', submitDebrisReport);

    // Drone Modu Eventleri
    const openDroneWorkspaceBtn = document.getElementById('openDroneWorkspaceBtn');
    if (openDroneWorkspaceBtn) {
        openDroneWorkspaceBtn.addEventListener('click', (e) => {
            e.preventDefault();
            droneModeOverlay.style.display = 'flex';
            document.getElementById('droneWorkspaceEmpty').style.display = 'flex';
            document.getElementById('droneCanvasContainer').style.display = 'none';
            droneFileInput.value = '';

            if (map) {
                const bounds = map.getBounds();
                const nw = bounds.getNorthWest();
                const se = bounds.getSouthEast();
                document.getElementById('droneNWLat').value = nw.lat.toFixed(5);
                document.getElementById('droneNWLon').value = nw.lng.toFixed(5);
                document.getElementById('droneSELat').value = se.lat.toFixed(5);
                document.getElementById('droneSELon').value = se.lng.toFixed(5);
            }
        });
    }

    droneFileInput.addEventListener('change', (e) => {
        if (e.target.files && e.target.files.length > 0) {
            openDroneMode(e.target.files[0]);
        }
    });

    droneCloseBtn.addEventListener('click', closeDroneMode);
    droneAnalyzeBtn.addEventListener('click', runDroneAnalysis);

    // Koordinat uygulama butonlari
    applyABtn.addEventListener('click', () => applyCoordinate('A'));
    applyBBtn.addEventListener('click', () => applyCoordinate('B'));

    // Panel Kucult / Buyut
    hidePanelBtn.addEventListener('click', () => {
        controlPanel.classList.add('hidden');
        showPanelBtn.style.display = 'block';
    });

    showPanelBtn.addEventListener('click', () => {
        controlPanel.classList.remove('hidden');
        showPanelBtn.style.display = 'none';
    });

    // Enter tusu ile koordinat uygulama
    coordALat.addEventListener('keydown', (e) => { if (e.key === 'Enter') applyCoordinate('A'); });
    coordALon.addEventListener('keydown', (e) => { if (e.key === 'Enter') applyCoordinate('A'); });
    coordBLat.addEventListener('keydown', (e) => { if (e.key === 'Enter') applyCoordinate('B'); });
    coordBLon.addEventListener('keydown', (e) => { if (e.key === 'Enter') applyCoordinate('B'); });

    // Tarayici internete baglandiginda otomatik tetikle
    window.addEventListener('online', () => {
        console.log('🌐 Internet connection detected. Syncing offline data…');
        updateOfflineStatus();
        syncOfflineDebrisReports();
    });
    window.addEventListener('offline', updateOfflineStatus);
    registerOfflineAppShell();
});

// ============================================
// OFFLINE → ONLINE OTOMATİK SENKRONİZASYON
// ============================================
async function syncOfflineDebrisReports() {
    const saved = JSON.parse(localStorage.getItem('enkaz_bildirimleri') || '[]');
    const offlineReports = saved.filter(item => item.id && item.id.startsWith('local_'));

    if (offlineReports.length === 0) return; // Bekleyen offline veri yok

    console.log(`📤 ${offlineReports.length} adet offline bildirim sunucuya aktarılıyor...`);
    let syncCount = 0;

    for (const report of offlineReports) {
        try {
            const formData = new FormData();
            formData.append('report_type', report.type || 'building_damage');
            formData.append('severity', report.severity || 'medium');
            formData.append('latitude', String(report.lat));
            formData.append('longitude', String(report.lng));
            formData.append('description', report.aciklama || 'Offline incident report');
            if (report.isim) formData.append('reporter_name', report.isim);
            if (report.yikilma_orani) formData.append('collapse_rate', report.yikilma_orani);
            if (report.kisi_sayisi !== null && report.kisi_sayisi !== undefined) formData.append('people_count', String(report.kisi_sayisi));
            if (report.saglik_durumu) formData.append('health_status', report.saglik_durumu);
            if (report.iletisim) formData.append('contact', report.iletisim);

            const response = await fetch(`${API_URL}/api/reports`, { method: 'POST', body: formData });
            const result = await response.json();
            if (!response.ok) throw new Error(result.detail || `HTTP ${response.status}`);

            const markerIndex = reportedDebrisMarkers.findIndex(marker => marker.options.debrisId === report.id);
            if (markerIndex >= 0) {
                map.removeLayer(reportedDebrisMarkers[markerIndex]);
                reportedDebrisMarkers.splice(markerIndex, 1);
            }
            addDebrisMarkerToMap({
                ...report, id: result.id, type: result.type, severity: result.severity,
                status: result.status, backendReport: true
            });
            const updatedSaved = JSON.parse(localStorage.getItem('enkaz_bildirimleri') || '[]');
            localStorage.setItem('enkaz_bildirimleri', JSON.stringify(updatedSaved.filter(item => item.id !== report.id)));
            syncCount++;
        } catch (err) {
            // Keep the local item so the next online event can retry it.
            console.warn(`⚠️ Offline rapor senkronize edilemedi (${report.id}):`, err.message);
        }
    }

    if (syncCount > 0) {
        console.log(`✅ ${syncCount} offline bildiri başarıyla buluta yüklendi.`);
        setStatus(`${syncCount} pending reports synced to the server.`, 'success');
        recordSuccessfulSync();
    }
    updateOfflineStatus();
}

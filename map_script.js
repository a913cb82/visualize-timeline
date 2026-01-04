/**
 * --- Timeline Visualize - Map Logic ---
 */

// --- App State ---
const State = {
    map: null,
    allFeatures: [],
    currentStartMarker: null,
    currentEndMarker: null,
    segmentLayerGroup: null,
    dateSlider: null,
    minTimestamp: 0,
    maxTimestamp: 0,
    rAFHandle: null,
    isAdjustmentLoopRunning: false,
    activeHandleIndex: null,
    rangeMinDisplay: null,
    rangeMaxDisplay: null,
};

// --- Constants ---
const GEOJSON_URL = 'timeline_data.geojson';

// --- Adjustment Tuning Parameters ---
const ADJUST_FRACTION = 0.05;
const TARGET_START_HANDLE_POS = 0.20;
const TARGET_END_HANDLE_POS = 0.80;
const TARGET_HANDLE_SEPARATION_POS = TARGET_END_HANDLE_POS - TARGET_START_HANDLE_POS;
const MIN_HANDLE_SEPARATION_FACTOR = 0.001;
const STOP_ADJUST_THRESHOLD_MS = 10;
const MIN_RANGE_WIDTH_MS = 1 * 24 * 60 * 60 * 1000;
const ZOOM_OUT_EDGE_THRESHOLD = 0.05;
const ZOOM_OUT_SPEED_BOOST = 3.0;

// --- Segment Style Parameters ---
const SPEED_THRESHOLD_MPS = 44.704;       // Approx 100 mph in m/s
const DISTANCE_THRESHOLD_METERS = 16093.4; // Approx 10 miles in meters
const NORMAL_SEGMENT_WEIGHT = 3;
const FAST_SEGMENT_WEIGHT = 1;
const SEGMENT_COLOR = 'blue';
const SEGMENT_OPACITY = 0.85;

// --- Performance Tuning ---
const UPDATE_MAP_DEBOUNCE_MS = 250;

// --- Initialization ---

function initMap() {
    console.log("Initializing map...");
    State.map = L.map('map', {
        preferCanvas: true
    }).setView([48.8566, 2.3522], 5);

    const baseMaps = createBaseMaps();
    L.control.layers(baseMaps, null, { collapsed: true, position: 'topright' }).addTo(State.map);

    // Set default layer
    baseMaps["Esri NatGeo"].addTo(State.map);

    State.rangeMinDisplay = document.getElementById('range-min-display');
    State.rangeMaxDisplay = document.getElementById('range-max-display');
    State.segmentLayerGroup = L.featureGroup().addTo(State.map);

    console.log("Map initialized.");
}

function createBaseMaps() {
    const osm = L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
        attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
        maxZoom: 19
    });

    const esriNatGeo = L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/NatGeo_World_Map/MapServer/tile/{z}/{y}/{x}', {
        attribution: 'Tiles © Esri — Source: National Geographic, Esri, DeLorme, HERE, UNEP-WCMC, USGS, NASA, ESA, METI, NRCAN, GEBCO, NOAA, iPC',
        maxZoom: 16
    });

    const baseMaps = {
        "Esri NatGeo": esriNatGeo,
        "OpenStreetMap": osm
    };

    if (typeof CONFIG !== 'undefined') {
        if (CONFIG.STADIA_API_KEY) {
            baseMaps["Stadia Stamen Terrain"] = L.tileLayer(`https://tiles.stadiamaps.com/tiles/stamen_terrain/{z}/{x}/{y}.png?api_key=${CONFIG.STADIA_API_KEY}`, {
                attribution: '© Stadia Maps, © OpenMapTiles © OpenStreetMap contributors',
                maxZoom: 18
            });
        }

        if (CONFIG.GOOGLE_MAPS_API_KEY) {
            const googleAttr = { attribution: 'Google Maps' };
            baseMaps["Google Maps"] = L.tileLayer(`https://mt1.google.com/vt/lyrs=m&x={x}&y={y}&z={z}&key=${CONFIG.GOOGLE_MAPS_API_KEY}`, googleAttr);
            baseMaps["Google Terrain"] = L.tileLayer(`https://mt1.google.com/vt/lyrs=p&x={x}&y={y}&z={z}&key=${CONFIG.GOOGLE_MAPS_API_KEY}`, googleAttr);
            baseMaps["Google Hybrid"] = L.tileLayer(`https://mt1.google.com/vt/lyrs=y&x={x}&y={y}&z={z}&key=${CONFIG.GOOGLE_MAPS_API_KEY}`, googleAttr);
        }
    }

    return baseMaps;
}

// --- Data Handling ---

async function loadData() {
    const statusDiv = document.getElementById('filterStatus');
    console.log(`Fetching data from ${GEOJSON_URL}...`);

    try {
        const response = await fetch(GEOJSON_URL);
        if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);
        const geojsonData = await response.json();

        if (!geojsonData?.features?.length) {
            statusDiv.textContent = "No timeline data found.";
            return;
        }

        State.allFeatures = geojsonData.features
            .filter(f => f?.properties?.timestamp && f?.geometry?.coordinates?.length === 2)
            .map(f => {
                const dateObj = new Date(f.properties.timestamp);
                return {
                    ...f,
                    properties: {
                        ...f.properties,
                        timestampMs: dateObj.getTime(),
                        dateObj: dateObj,
                        lat: f.geometry.coordinates[1],
                        lon: f.geometry.coordinates[0],
                        segmentSpeed: 0,
                        segmentWeight: NORMAL_SEGMENT_WEIGHT
                    }
                };
            });

        if (!State.allFeatures.length) {
             statusDiv.textContent = "No valid data points after filtering.";
             return;
        }

        State.allFeatures.sort((a, b) => a.properties.timestampMs - b.properties.timestampMs);

        State.minTimestamp = State.allFeatures[0].properties.timestampMs;
        State.maxTimestamp = State.allFeatures[State.allFeatures.length - 1].properties.timestampMs;

        if (State.minTimestamp === State.maxTimestamp) {
             State.maxTimestamp += MIN_RANGE_WIDTH_MS;
        }

        calculateSegmentStyles();
        setupDateSlider();

    } catch (error) {
        console.error("Error loading GeoJSON:", error);
        statusDiv.textContent = `Error loading data: ${error.message}`;
    }
}

function calculateSegmentStyles() {
    if (State.allFeatures.length < 2) return;
    console.time("calculateSegmentStyles");

    for (let i = 0; i < State.allFeatures.length - 1; i++) {
        const p1 = State.allFeatures[i];
        const p2 = State.allFeatures[i + 1];

        const latLng1 = L.latLng(p1.properties.lat, p1.properties.lon);
        const latLng2 = L.latLng(p2.properties.lat, p2.properties.lon);

        const distance = latLng1.distanceTo(latLng2);
        const timeDiffMs = p2.properties.timestampMs - p1.properties.timestampMs;

        let speed = 0;
        if (timeDiffMs > 1) {
            speed = distance / (timeDiffMs / 1000.0);
        }

        const isFast = (speed >= SPEED_THRESHOLD_MPS);
        const isLongDistance = (distance >= DISTANCE_THRESHOLD_METERS);

        p1.properties.segmentSpeed = speed;
        p1.properties.segmentWeight = (isFast || isLongDistance) ? FAST_SEGMENT_WEIGHT : NORMAL_SEGMENT_WEIGHT;
    }

    // Last point
    const last = State.allFeatures[State.allFeatures.length - 1];
    last.properties.segmentSpeed = 0;
    last.properties.segmentWeight = NORMAL_SEGMENT_WEIGHT;

    console.timeEnd("calculateSegmentStyles");
}

// --- Slider & UI Logic ---

function setupDateSlider() {
    const sliderElement = document.getElementById('date-slider');
    const startLabel = document.getElementById('slider-start-date');
    const endLabel = document.getElementById('slider-end-date');

    if (State.dateSlider) {
        stopAdjustmentLoop();
        State.dateSlider.destroy();
    }

    try {
        State.dateSlider = noUiSlider.create(sliderElement, {
            start: [State.minTimestamp, State.maxTimestamp],
            connect: true,
            range: { 'min': State.minTimestamp, 'max': State.maxTimestamp },
            tooltips: false,
            format: { to: v => Math.round(v), from: v => Number(v) },
            behaviour: 'drag'
        });

        State.dateSlider.on('update', (values) => {
            startLabel.textContent = formatDateForDisplay(Number(values[0]));
            endLabel.textContent = formatDateForDisplay(Number(values[1]));
        });

        State.dateSlider.on('start', (v, h) => { State.activeHandleIndex = h; startAdjustmentLoop(); });

        const debouncedUpdate = debounce(updateMap, UPDATE_MAP_DEBOUNCE_MS);
        State.dateSlider.on('slide', (v, h) => {
            State.activeHandleIndex = h;
            startAdjustmentLoop();
            debouncedUpdate();
        });

        State.dateSlider.on('end', () => {
            State.activeHandleIndex = null;
            updateMap();
            startAdjustmentLoop();
        });

        updateRangeDisplay();
        updateMap();
    } catch (error) {
        console.error("Error creating slider:", error);
    }
}

function adjustRangeGradually() {
    if (!State.dateSlider) return false;

    const currentRange = State.dateSlider.options.range;
    const curMin = Number(currentRange.min);
    const curMax = Number(currentRange.max);
    const curWidth = curMax - curMin;
    if (curWidth <= 0) return false;

    const [handleMin, handleMax] = State.dateSlider.get().map(Number);
    const handleSeparation = handleMax - handleMin;
    const absWidth = State.maxTimestamp - State.minTimestamp;
    const minSep = absWidth > 0 ? absWidth * MIN_HANDLE_SEPARATION_FACTOR : MIN_RANGE_WIDTH_MS;

    if (handleSeparation < minSep) return true;

    const idealWidth = handleSeparation / TARGET_HANDLE_SEPARATION_POS;
    const targetMin = handleMin - TARGET_START_HANDLE_POS * idealWidth;
    const targetMax = targetMin + idealWidth;

    const deltaMin = targetMin - curMin;
    const deltaMax = targetMax - curMax;

    if (Math.abs(deltaMin) < STOP_ADJUST_THRESHOLD_MS && Math.abs(deltaMax) < STOP_ADJUST_THRESHOLD_MS) return false;

    const zoomIn = idealWidth < curWidth;
    const zoomOut = idealWidth > curWidth;

    let shouldUpdate = false;
    let fraction = ADJUST_FRACTION;

    if (zoomOut) {
        shouldUpdate = true;
        if (State.activeHandleIndex !== null) {
            const proximity = State.activeHandleIndex === 0 ?
                (handleMin - curMin) / curWidth :
                (curMax - handleMax) / curWidth;
            if (proximity >= 0 && proximity < ZOOM_OUT_EDGE_THRESHOLD) fraction *= ZOOM_OUT_SPEED_BOOST;
        }
    } else if (zoomIn && State.activeHandleIndex === null) {
        shouldUpdate = true;
    }

    if (shouldUpdate) {
        let newMin = Math.max(State.minTimestamp, curMin + deltaMin * fraction);
        let newMax = Math.min(State.maxTimestamp, curMax + deltaMax * fraction);

        if (newMax < newMin + MIN_RANGE_WIDTH_MS) {
             if (newMin === State.minTimestamp) newMax = Math.min(State.minTimestamp + MIN_RANGE_WIDTH_MS, State.maxTimestamp);
             else newMin = Math.max(newMax - MIN_RANGE_WIDTH_MS, State.minTimestamp);
        }

        if (Math.abs(newMin - curMin) >= 1 || Math.abs(newMax - curMax) >= 1) {
            State.dateSlider.updateOptions({ range: { min: Math.round(newMin), max: Math.round(newMax) } }, false);
            updateRangeDisplay();
        }
    }
    return true;
}

// --- Map Update ---

function updateMap() {
    const statusDiv = document.getElementById('filterStatus');
    if (!State.dateSlider || !State.segmentLayerGroup || !State.allFeatures.length) return;

    console.time("updateMap");
    const [start, end] = State.dateSlider.get().map(Number);

    const filtered = State.allFeatures.slice(
        findFirstIndex(start),
        findLastIndex(end) + 1
    );

    clearMapLayers();

    if (filtered.length > 0) {
        // Segments
        if (filtered.length > 1) {
            const polylines = [];
            for (let i = 0; i < filtered.length - 1; i++) {
                const p1 = filtered[i].properties;
                const p2 = filtered[i + 1].properties;
                if (p2.timestampMs > p1.timestampMs) {
                    polylines.push(L.polyline([[p1.lat, p1.lon], [p2.lat, p2.lon]], {
                        color: SEGMENT_COLOR,
                        weight: p1.segmentWeight,
                        opacity: SEGMENT_OPACITY
                    }));
                }
            }
            polylines.forEach(l => State.segmentLayerGroup.addLayer(l));
        }

        // Markers
        const startPt = filtered[0].properties;
        const endPt = filtered[filtered.length - 1].properties;

        State.currentStartMarker = createMarker(startPt, 'green', 'Start').addTo(State.map);

        if (filtered.length > 1 && startPt.timestampMs !== endPt.timestampMs) {
            State.currentEndMarker = createMarker(endPt, 'red', 'End').addTo(State.map);
        }

        fitMapBounds(filtered);
        statusDiv.textContent = `Showing ${filtered.length} points from ${formatDateForDisplay(start)} to ${formatDateForDisplay(end)}.`;
    } else {
        statusDiv.textContent = `No data found between ${formatDateForDisplay(start)} and ${formatDateForDisplay(end)}.`;
    }
    console.timeEnd("updateMap");
}

function createMarker(p, color, label) {
    const icon = L.icon({
        iconUrl: `https://raw.githubusercontent.com/pointhi/leaflet-color-markers/master/img/marker-icon-${color}.png`,
        shadowUrl: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.7.1/images/marker-shadow.png',
        iconSize: [25, 41], iconAnchor: [12, 41], popupAnchor: [1, -34], shadowSize: [41, 41]
    });
    const popup = `<b>${label}:</b><br>${formatDateForDisplay(p.dateObj)}<br>${p.dateObj.toLocaleTimeString()}`;
    return L.marker([p.lat, p.lon], { icon }).bindPopup(popup);
}

function fitMapBounds(features) {
    try {
        let bounds = null;
        if (State.segmentLayerGroup.getLayers().length > 0) {
            bounds = State.segmentLayerGroup.getBounds();
        } else if (State.currentStartMarker) {
            bounds = L.latLngBounds(State.currentStartMarker.getLatLng(), State.currentStartMarker.getLatLng());
        }

        if (bounds?.isValid()) {
            State.map.flyToBounds(bounds, { padding: [50, 50], maxZoom: 16, duration: 0.5 });
        }
    } catch (e) {
        console.warn("Could not fit bounds:", e);
    }
}

// --- Helpers ---

function clearMapLayers() {
    State.segmentLayerGroup?.clearLayers();
    if (State.currentStartMarker) State.map.removeLayer(State.currentStartMarker);
    if (State.currentEndMarker) State.map.removeLayer(State.currentEndMarker);
    State.currentStartMarker = null;
    State.currentEndMarker = null;
}

function formatDateForDisplay(val) {
    const d = new Date(Number(val));
    return isNaN(d.getTime()) ? "Invalid Date" : d.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
}

function updateRangeDisplay() {
    if (!State.dateSlider) return;
    const r = State.dateSlider.options.range;
    State.rangeMinDisplay.textContent = `Min: ${formatDateForDisplay(r.min)}`;
    State.rangeMaxDisplay.textContent = `Max: ${formatDateForDisplay(r.max)}`;
}

function findFirstIndex(ts) {
    let low = 0, high = State.allFeatures.length - 1, idx = State.allFeatures.length;
    while (low <= high) {
        const mid = Math.floor((low + high) / 2);
        if (State.allFeatures[mid].properties.timestampMs >= ts) { idx = mid; high = mid - 1; }
        else low = mid + 1;
    }
    return idx;
}

function findLastIndex(ts) {
    let low = 0, high = State.allFeatures.length - 1, idx = -1;
    while (low <= high) {
        const mid = Math.floor((low + high) / 2);
        if (State.allFeatures[mid].properties.timestampMs <= ts) { idx = mid; low = mid + 1; }
        else high = mid - 1;
    }
    return idx;
}

function debounce(f, ms) {
    let t;
    return (...args) => {
        clearTimeout(t);
        t = setTimeout(() => f(...args), ms);
    };
}

function startAdjustmentLoop() {
    if (!State.isAdjustmentLoopRunning && State.dateSlider) {
        State.isAdjustmentLoopRunning = true;
        State.rAFHandle = requestAnimationFrame(adjustmentLoop);
    }
}

function stopAdjustmentLoop() {
    if (State.rAFHandle) cancelAnimationFrame(State.rAFHandle);
    State.rAFHandle = null;
    State.isAdjustmentLoopRunning = false;
}

function adjustmentLoop() {
    if (!State.isAdjustmentLoopRunning) return;
    if (adjustRangeGradually() && State.isAdjustmentLoopRunning) {
        State.rAFHandle = requestAnimationFrame(adjustmentLoop);
    } else {
        stopAdjustmentLoop();
    }
}

// --- Entry Point ---
document.addEventListener('DOMContentLoaded', () => {
    initMap();
    loadData();
});
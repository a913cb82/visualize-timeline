// --- Global Variables ---
let map = null;
let allFeatures = [];
let currentStartMarker = null;
let currentEndMarker = null;
let segmentLayerGroup = null;
const GEOJSON_URL = 'timeline_data.geojson';
let dateSlider = null;
let minTimestamp = 0;
let maxTimestamp = 0;

// --- References for Range Display ---
let rangeMinDisplay = null;
let rangeMaxDisplay = null;

// --- Dynamic Range Adjustment State ---
let rAFHandle = null;
let isAdjustmentLoopRunning = false;
let activeHandleIndex = null;

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
let updateMapTimeout = null;

// --- Initialization ---
function initMap() {
    console.log("Initializing map with Canvas renderer...");
    map = L.map('map', {
        preferCanvas: true
    }).setView([48.8566, 2.3522], 5);

    const osm = L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', { attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors', maxZoom: 19 });
    const esriNatGeo = L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/NatGeo_World_Map/MapServer/tile/{z}/{y}/{x}', { attribution: 'Tiles © Esri — ...', maxZoom: 16 });
    esriNatGeo.addTo(map);
    const baseMaps = { "Esri NatGeo": esriNatGeo, "OpenStreetMap": osm };
    L.control.layers(baseMaps, null, { collapsed: true, position: 'topright' }).addTo(map);

    rangeMinDisplay = document.getElementById('range-min-display');
    rangeMaxDisplay = document.getElementById('range-max-display');
    segmentLayerGroup = L.featureGroup().addTo(map);

    console.log("Map initialized.");
}

// --- Data Handling ---
async function loadData() {
    console.log(`Fetching data from ${GEOJSON_URL}...`);
    const statusDiv = document.getElementById('filterStatus');
    try {
        const response = await fetch(GEOJSON_URL);
        if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);
        const geojsonData = await response.json();
        console.log(`Data loaded. Features: ${geojsonData.features.length}`);

        if (!geojsonData || !geojsonData.features || geojsonData.features.length === 0) {
            console.warn("GeoJSON data is empty or invalid.");
            statusDiv.textContent = "No timeline data found.";
            return;
        }

        // Process and sort features, initialize properties
        allFeatures = geojsonData.features
            .filter(feature =>
                feature?.properties?.timestamp &&
                feature?.geometry?.coordinates?.length === 2
            )
            .map(feature => {
                feature.properties.dateObj = new Date(feature.properties.timestamp);
                feature.properties.lat = feature.geometry.coordinates[1];
                feature.properties.lon = feature.geometry.coordinates[0];
                feature.properties.segmentSpeed = 0;
                feature.properties.segmentWeight = NORMAL_SEGMENT_WEIGHT;
                return feature;
            });

        if (allFeatures.length === 0) {
             statusDiv.textContent = "No valid data points after filtering.";
             console.warn("No valid features remained after initial filtering.");
             return;
        }

        allFeatures.sort((a, b) => a.properties.dateObj.getTime() - b.properties.dateObj.getTime());

        minTimestamp = allFeatures[0].properties.dateObj.getTime();
        maxTimestamp = allFeatures[allFeatures.length - 1].properties.dateObj.getTime();
        if (minTimestamp === maxTimestamp) {
            maxTimestamp += MIN_RANGE_WIDTH_MS;
        }

        // Calculate segment styles (speed, distance -> weight)
        calculateSegmentStyles(); // *** MODIFIED ***

        console.log(`Features processed. Absolute range: ${new Date(minTimestamp).toISOString()} to ${new Date(maxTimestamp).toISOString()}`);
        setupDateSlider();

    } catch (error) {
        console.error("Error loading/processing GeoJSON:", error);
        statusDiv.textContent = `Error loading data: ${error.message}`;
    }
}

// --- Calculate Segment Styles based on Speed OR Distance Threshold --- // *** MODIFIED ***
function calculateSegmentStyles() {
    if (allFeatures.length < 2) return;
    console.log(`Using Speed Threshold: ${SPEED_THRESHOLD_MPS.toFixed(2)} m/s OR Distance Threshold: ${(DISTANCE_THRESHOLD_METERS / 1000).toFixed(1)} km`);

    for (let i = 0; i < allFeatures.length - 1; i++) {
        const p1 = allFeatures[i];
        const p2 = allFeatures[i + 1];

        const latLng1 = L.latLng(p1.properties.lat, p1.properties.lon);
        const latLng2 = L.latLng(p2.properties.lat, p2.properties.lon);

        const distance = latLng1.distanceTo(latLng2); // meters
        const timeDiffMs = p2.properties.dateObj.getTime() - p1.properties.dateObj.getTime(); // milliseconds

        let speed = 0;
        let isFast = false;
        // Calculate speed only if time difference is meaningful
        if (timeDiffMs > 1) {
            speed = distance / (timeDiffMs / 1000.0); // meters per second
            isFast = (speed >= SPEED_THRESHOLD_MPS);
        }

        // Store speed on the starting point (mostly for potential debugging/info)
        p1.properties.segmentSpeed = speed;

        // *** NEW: Check distance threshold ***
        const isLongDistance = (distance >= DISTANCE_THRESHOLD_METERS);

        // Assign weight if EITHER condition is met
        if (isFast || isLongDistance) {
            p1.properties.segmentWeight = FAST_SEGMENT_WEIGHT;
        } else {
            p1.properties.segmentWeight = NORMAL_SEGMENT_WEIGHT;
        }
    }
     // Ensure the last point has default weight (it doesn't start a segment)
    allFeatures[allFeatures.length - 1].properties.segmentSpeed = 0;
    allFeatures[allFeatures.length - 1].properties.segmentWeight = NORMAL_SEGMENT_WEIGHT;

    console.log("Segment weights calculated using speed OR distance threshold.");
}


// --- Date Formatting Helper ---
function formatDateForDisplay(timestampOrDate) {
    const date = (timestampOrDate instanceof Date) ? timestampOrDate : new Date(timestampOrDate);
    return date.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
}

// --- Update Range Display UI ---
function updateRangeDisplay() {
    if (!dateSlider || !rangeMinDisplay || !rangeMaxDisplay) return;
    const currentRange = dateSlider.options.range;
    rangeMinDisplay.textContent = `Min: ${formatDateForDisplay(Number(currentRange.min))}`;
    rangeMaxDisplay.textContent = `Max: ${formatDateForDisplay(Number(currentRange.max))}`;
}


// --- Functions to Control the Slider Range Adjustment Loop ---
function stopAdjustmentLoop() { if (rAFHandle) cancelAnimationFrame(rAFHandle); rAFHandle = null; isAdjustmentLoopRunning = false; }
function startAdjustmentLoop() { if (!isAdjustmentLoopRunning && dateSlider) { isAdjustmentLoopRunning = true; rAFHandle = requestAnimationFrame(adjustmentLoop); } }
function adjustmentLoop() { if (!isAdjustmentLoopRunning) return; const keepLooping = adjustRangeGradually(); if (keepLooping && isAdjustmentLoopRunning) rAFHandle = requestAnimationFrame(adjustmentLoop); else { isAdjustmentLoopRunning = false; rAFHandle = null; } }
function adjustRangeGradually() { // Adjusts slider range
    // ... (This function remains unchanged) ...
    if (!dateSlider) return false;
    const sliderOptions = dateSlider.options;
    const currentRange = sliderOptions.range;
    const currentMin = Number(currentRange.min);
    const currentMax = Number(currentRange.max);
    const currentWidth = currentMax - currentMin;
    if (currentWidth <= 0) return false;
    const handleValues = dateSlider.get().map(Number);
    const startHandleValue = handleValues[0];
    const endHandleValue = handleValues[1];
    const handleSeparation = endHandleValue - startHandleValue;
    const minHandleSeparationMs = (maxTimestamp - minTimestamp) * MIN_HANDLE_SEPARATION_FACTOR;
    if (handleSeparation <= minHandleSeparationMs) return true;
    const idealWidth = handleSeparation / TARGET_HANDLE_SEPARATION_POS;
    let targetMin = startHandleValue - TARGET_START_HANDLE_POS * idealWidth;
    let targetMax = targetMin + idealWidth;
    const deltaMin = targetMin - currentMin;
    const deltaMax = targetMax - currentMax;
    if (Math.abs(deltaMin) < STOP_ADJUST_THRESHOLD_MS && Math.abs(deltaMax) < STOP_ADJUST_THRESHOLD_MS) return false;
    const zoomInNeeded = idealWidth < currentWidth;
    const zoomOutNeeded = idealWidth > currentWidth;
    let shouldApplyUpdate = false;
    let effectiveAdjustFraction = ADJUST_FRACTION;
    if (zoomOutNeeded) {
        shouldApplyUpdate = true;
        if (activeHandleIndex !== null) {
            let handleProximity = -1;
            if (activeHandleIndex === 0) handleProximity = (startHandleValue - currentMin) / currentWidth;
            else handleProximity = (currentMax - endHandleValue) / currentWidth;
            if (handleProximity >= 0 && handleProximity < ZOOM_OUT_EDGE_THRESHOLD) effectiveAdjustFraction *= ZOOM_OUT_SPEED_BOOST;
        }
    } else if (zoomInNeeded && activeHandleIndex === null) {
        shouldApplyUpdate = true;
    }
    if (shouldApplyUpdate) {
        const stepMin = deltaMin * effectiveAdjustFraction;
        const stepMax = deltaMax * effectiveAdjustFraction;
        let newMin = currentMin + stepMin;
        let newMax = currentMax + stepMax;
        newMin = Math.max(minTimestamp, newMin);
        newMax = Math.min(maxTimestamp, newMax);
        if (newMax < newMin + MIN_RANGE_WIDTH_MS) return true;
        const changeThreshold = 1;
        let updateNeeded = false;
        let rangeToUpdate = { min: currentMin, max: currentMax };
        if (Math.abs(newMin - currentMin) >= changeThreshold) { rangeToUpdate.min = Math.round(newMin); updateNeeded = true; }
        if (Math.abs(newMax - currentMax) >= changeThreshold) { rangeToUpdate.max = Math.round(newMax); updateNeeded = true; }
        if (updateNeeded) {
            try {
                dateSlider.updateOptions({ range: rangeToUpdate }, false);
                updateRangeDisplay();
            } catch(error) { console.error("Error updating slider options:", error); return false; }
        }
    }
    return true;
}

// --- Slider Setup ---
function setupDateSlider() { // Sets up noUiSlider and event listeners
    // ... (This function remains unchanged - includes debounced updateMap trigger) ...
    if (allFeatures.length === 0) return;
    const sliderElement = document.getElementById('date-slider');
    const startDateLabel = document.getElementById('slider-start-date');
    const endDateLabel = document.getElementById('slider-end-date');
    if (dateSlider && dateSlider.destroy) { stopAdjustmentLoop(); dateSlider.destroy(); dateSlider = null; }

    dateSlider = noUiSlider.create(sliderElement, {
        start: [minTimestamp, maxTimestamp], connect: true, range: { 'min': minTimestamp, 'max': maxTimestamp },
        tooltips: false, format: { to: value => Math.round(value), from: value => Number(value) }, behaviour: 'drag'
    });

    dateSlider.on('update', function (values, handle) {
        if (!dateSlider) return;
        startDateLabel.textContent = formatDateForDisplay(values[0]);
        endDateLabel.textContent = formatDateForDisplay(values[1]);
    });
    dateSlider.on('start', function (values, handle) { activeHandleIndex = handle; startAdjustmentLoop(); });
    dateSlider.on('slide', function (values, handle) { activeHandleIndex = handle; startAdjustmentLoop(); });

    const debouncedUpdate = debounce(updateMap, UPDATE_MAP_DEBOUNCE_MS);
    const finalUpdateHandler = (values, handle) => { if (!dateSlider) return; activeHandleIndex = null; debouncedUpdate(); startAdjustmentLoop(); };
    dateSlider.on('end', finalUpdateHandler);
    dateSlider.on('set', finalUpdateHandler);

    console.log("Date slider initialized.");
    updateRangeDisplay();
    updateMap(); // Initial map draw
}

// --- Debounce Function ---
function debounce(func, wait) { // Utility for delaying function execution
    // ... (This function remains unchanged) ...
    let timeout;
    return function executedFunction(...args) {
        const later = () => { clearTimeout(timeout); func(...args); };
        clearTimeout(timeout);
        timeout = setTimeout(later, wait);
    };
}

// --- Map Update Logic ---
function updateMap() { // Filters data and redraws map layers
    // ... (This function remains unchanged - uses pre-calculated segmentWeight) ...
    console.log("updateMap called");
    const statusDiv = document.getElementById('filterStatus');
    if (!dateSlider || !segmentLayerGroup) { statusDiv.textContent = "Waiting..."; return; }

    const sliderValues = dateSlider.get();
    const startTimestamp = sliderValues[0]; const endTimestamp = sliderValues[1];
    const startDateStr = formatDateForDisplay(startTimestamp); const endDateStr = formatDateForDisplay(endTimestamp);
    statusDiv.textContent = `Filtering data...`;

    const filteredFeatures = allFeatures.filter(feature => { const ft = feature.properties.dateObj.getTime(); return ft >= startTimestamp && ft <= endTimestamp; });

    clearMapLayers();

    if (filteredFeatures.length > 0) {
        if (filteredFeatures.length > 1) {
            const layersToAdd = [];
            for (let i = 0; i < filteredFeatures.length - 1; i++) {
                const p1 = filteredFeatures[i]; const p2 = filteredFeatures[i + 1];
                const weight = p1.properties.segmentWeight; // Use pre-calculated weight
                if (p2.properties.dateObj.getTime() > p1.properties.dateObj.getTime()) {
                    const segmentCoords = [[p1.properties.lat, p1.properties.lon], [p2.properties.lat, p2.properties.lon]];
                    layersToAdd.push(L.polyline(segmentCoords, { color: SEGMENT_COLOR, weight: weight, opacity: SEGMENT_OPACITY }));
                }
            }
            layersToAdd.forEach(layer => segmentLayerGroup.addLayer(layer));
        }

        const startPoint = filteredFeatures[0]; const endPoint = filteredFeatures[filteredFeatures.length - 1];
        const greenIcon = L.icon({ iconUrl: 'https://raw.githubusercontent.com/pointhi/leaflet-color-markers/master/img/marker-icon-green.png', shadowUrl: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.7.1/images/marker-shadow.png', iconSize: [25, 41], iconAnchor: [12, 41], popupAnchor: [1, -34], shadowSize: [41, 41] });
        const redIcon = L.icon({ iconUrl: 'https://raw.githubusercontent.com/pointhi/leaflet-color-markers/master/img/marker-icon-red.png', shadowUrl: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.7.1/images/marker-shadow.png', iconSize: [25, 41], iconAnchor: [12, 41], popupAnchor: [1, -34], shadowSize: [41, 41] });

        currentStartMarker = L.marker([startPoint.properties.lat, startPoint.properties.lon], { icon: greenIcon }).addTo(map);

        if (filteredFeatures.length > 1 && startPoint.properties.dateObj.getTime() !== endPoint.properties.dateObj.getTime()) {
            currentEndMarker = L.marker([endPoint.properties.lat, endPoint.properties.lon], { icon: redIcon })
                .bindPopup(`<b>End:</b><br>${formatDateForDisplay(endPoint.properties.dateObj)}<br>${endPoint.properties.dateObj.toLocaleTimeString()}`).addTo(map);
            currentStartMarker.bindPopup(`<b>Start:</b><br>${formatDateForDisplay(startPoint.properties.dateObj)}<br>${startPoint.properties.dateObj.toLocaleTimeString()}`);
        } else if (filteredFeatures.length === 1) {
            currentStartMarker.bindPopup(`<b>Single Point:</b><br>${formatDateForDisplay(startPoint.properties.dateObj)}<br>${startPoint.properties.dateObj.toLocaleTimeString()}`);
        } else {
             currentStartMarker.bindPopup(`<b>Start/End:</b><br>${formatDateForDisplay(startPoint.properties.dateObj)}<br>${startPoint.properties.dateObj.toLocaleTimeString()}`);
        }

        try {
             let boundsToFit = null;
             if (segmentLayerGroup.getLayers().length > 0) boundsToFit = segmentLayerGroup.getBounds();
             else if (currentStartMarker) boundsToFit = L.latLngBounds(currentStartMarker.getLatLng(), currentStartMarker.getLatLng()).pad(0.1);
             if (boundsToFit?.isValid()) map.flyToBounds(boundsToFit, { padding: [50, 50] });
         } catch (e) { console.warn("Could not fit bounds:", e); }

        statusDiv.textContent = `Showing ${filteredFeatures.length} points from ${startDateStr} to ${endDateStr}.`;
    } else {
        statusDiv.textContent = `No data found between ${startDateStr} and ${endDateStr}.`;
    }
}


// --- Clear Map Layers ---
function clearMapLayers() { // Removes segments and markers
    // ... (This function remains unchanged) ...
    segmentLayerGroup?.clearLayers();
    if (currentStartMarker) { map.removeLayer(currentStartMarker); currentStartMarker = null; }
    if (currentEndMarker) { map.removeLayer(currentEndMarker); currentEndMarker = null; }
}


// --- Script Execution ---
document.addEventListener('DOMContentLoaded', () => {
    initMap();
    loadData();
});
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
        preferCanvas: true // Keep Canvas renderer, it's generally better for many features than SVG
    }).setView([48.8566, 2.3522], 5);

    const osm = L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', { attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors', maxZoom: 19 });
    const esriNatGeo = L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/NatGeo_World_Map/MapServer/tile/{z}/{y}/{x}', { attribution: 'Tiles © Esri — Source: National Geographic, Esri, DeLorme, HERE, UNEP-WCMC, USGS, NASA, ESA, METI, NRCAN, GEBCO, NOAA, iPC', maxZoom: 16 });
    esriNatGeo.addTo(map);

    const baseMaps = {
        "Esri NatGeo": esriNatGeo,
        "OpenStreetMap": osm
    };

    // --- Conditional Layers based on API Keys ---
    if (typeof CONFIG !== 'undefined') {
        if (CONFIG.STADIA_API_KEY) {
            const stadiaTilesUrl = `https://tiles.stadiamaps.com/tiles/stamen_terrain/{z}/{x}/{y}.png?api_key=${CONFIG.STADIA_API_KEY}`;
            baseMaps["Stadia Stamen Terrain"] = L.tileLayer(stadiaTilesUrl, {
                attribution: '© Stadia Maps, © OpenMapTiles © OpenStreetMap contributors',
                maxZoom: 18
            });
        }

        if (CONFIG.GOOGLE_MAPS_API_KEY) {
            const googleRoads = L.tileLayer(`https://mt1.google.com/vt/lyrs=m&x={x}&y={y}&z={z}&key=${CONFIG.GOOGLE_MAPS_API_KEY}`, { attribution: 'Google Maps' });
            const googleTerrain = L.tileLayer(`https://mt1.google.com/vt/lyrs=p&x={x}&y={y}&z={z}&key=${CONFIG.GOOGLE_MAPS_API_KEY}`, { attribution: 'Google Maps' });
            const googleHybrid = L.tileLayer(`https://mt1.google.com/vt/lyrs=y&x={x}&y={y}&z={z}&key=${CONFIG.GOOGLE_MAPS_API_KEY}`, { attribution: 'Google Maps' });

            baseMaps["Google Maps"] = googleRoads;
            baseMaps["Google Terrain"] = googleTerrain;
            baseMaps["Google Hybrid"] = googleHybrid;
        }
    }

    L.control.layers(baseMaps, null, { collapsed: true, position: 'topright' }).addTo(map);

    rangeMinDisplay = document.getElementById('range-min-display');
    rangeMaxDisplay = document.getElementById('range-max-display');
    segmentLayerGroup = L.featureGroup().addTo(map); // FeatureGroup is fine for this

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

        allFeatures = geojsonData.features
            .filter(feature =>
                feature?.properties?.timestamp &&
                feature?.geometry?.coordinates?.length === 2
            )
            .map(feature => {
                const dateObj = new Date(feature.properties.timestamp);
                feature.properties.timestampMs = dateObj.getTime(); // Use number timestamp
                feature.properties.dateObj = dateObj;
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

        allFeatures.sort((a, b) => a.properties.timestampMs - b.properties.timestampMs);

        minTimestamp = allFeatures[0].properties.timestampMs;
        maxTimestamp = allFeatures[allFeatures.length - 1].properties.timestampMs;

        if (minTimestamp === maxTimestamp && allFeatures.length > 0) {
             maxTimestamp += MIN_RANGE_WIDTH_MS;
        } else if (minTimestamp > maxTimestamp) {
            console.error("Error: minTimestamp > maxTimestamp after sorting.");
            statusDiv.textContent = "Error processing data timestamps.";
            return;
        }

        calculateSegmentStyles(); // Calculate weights once

        console.log(`Features processed. Absolute range: ${new Date(minTimestamp).toISOString()} to ${new Date(maxTimestamp).toISOString()}`);
        setupDateSlider();

    } catch (error) {
        console.error("Error loading/processing GeoJSON:", error);
        statusDiv.textContent = `Error loading data: ${error.message}`;
    }
}

// --- Calculate Segment Styles based on Speed OR Distance Threshold ---
function calculateSegmentStyles() {
    if (allFeatures.length < 2) return;
    console.log(`Calculating segment styles...`);
    console.time("calculateSegmentStyles"); // Time the whole calculation

    for (let i = 0; i < allFeatures.length - 1; i++) {
        const p1 = allFeatures[i];
        const p2 = allFeatures[i + 1];

        const latLng1 = L.latLng(p1.properties.lat, p1.properties.lon);
        const latLng2 = L.latLng(p2.properties.lat, p2.properties.lon);

        const distance = latLng1.distanceTo(latLng2);
        const timeDiffMs = p2.properties.timestampMs - p1.properties.timestampMs;

        let speed = 0;
        let isFast = false;
        if (timeDiffMs > 1) {
            speed = distance / (timeDiffMs / 1000.0);
            isFast = (speed >= SPEED_THRESHOLD_MPS);
        }
        p1.properties.segmentSpeed = speed;
        const isLongDistance = (distance >= DISTANCE_THRESHOLD_METERS);

        p1.properties.segmentWeight = (isFast || isLongDistance) ? FAST_SEGMENT_WEIGHT : NORMAL_SEGMENT_WEIGHT;
    }
    if (allFeatures.length > 0) {
        allFeatures[allFeatures.length - 1].properties.segmentSpeed = 0;
        allFeatures[allFeatures.length - 1].properties.segmentWeight = NORMAL_SEGMENT_WEIGHT;
    }

    console.timeEnd("calculateSegmentStyles");
    console.log("Segment weights calculated.");
}


// --- Date Formatting Helper ---
function formatDateForDisplay(timestampOrDate) {
    const date = (timestampOrDate instanceof Date) ? timestampOrDate : new Date(Number(timestampOrDate));
    if (isNaN(date.getTime())) return "Invalid Date";
    return date.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
}

// --- Update Range Display UI ---
function updateRangeDisplay() {
    if (!dateSlider || !rangeMinDisplay || !rangeMaxDisplay) return;
    try {
        const currentRange = dateSlider.options.range;
        rangeMinDisplay.textContent = `Min: ${formatDateForDisplay(Number(currentRange.min))}`;
        rangeMaxDisplay.textContent = `Max: ${formatDateForDisplay(Number(currentRange.max))}`;
    } catch (error) {
        console.error("Error updating range display:", error);
        rangeMinDisplay.textContent = "Min: Error";
        rangeMaxDisplay.textContent = "Max: Error";
    }
}


// --- Functions to Control the Slider Range Adjustment Loop ---
// (These functions remain unchanged)
function stopAdjustmentLoop() { if (rAFHandle) cancelAnimationFrame(rAFHandle); rAFHandle = null; isAdjustmentLoopRunning = false; }
function startAdjustmentLoop() { if (!isAdjustmentLoopRunning && dateSlider) { isAdjustmentLoopRunning = true; rAFHandle = requestAnimationFrame(adjustmentLoop); } }
function adjustmentLoop() { if (!isAdjustmentLoopRunning) return; const keepLooping = adjustRangeGradually(); if (keepLooping && isAdjustmentLoopRunning) rAFHandle = requestAnimationFrame(adjustmentLoop); else { isAdjustmentLoopRunning = false; rAFHandle = null; } }
function adjustRangeGradually() {
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
    const absoluteRangeWidth = maxTimestamp - minTimestamp;
    const minHandleSeparationMs = absoluteRangeWidth > 0 ? absoluteRangeWidth * MIN_HANDLE_SEPARATION_FACTOR : MIN_RANGE_WIDTH_MS;
    if (handleSeparation < minHandleSeparationMs) return true;
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
        if (newMax < newMin + MIN_RANGE_WIDTH_MS) {
             if (newMin === minTimestamp) newMax = Math.min(minTimestamp + MIN_RANGE_WIDTH_MS, maxTimestamp);
             else newMin = Math.max(newMax - MIN_RANGE_WIDTH_MS, minTimestamp);
             if (newMax < newMin + MIN_RANGE_WIDTH_MS) return true;
        }
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
// (This function remains largely unchanged, ensure error handling is robust)
function setupDateSlider() {
    if (allFeatures.length === 0) { console.log("No features, cannot setup slider."); return; }
    if (typeof minTimestamp !== 'number' || typeof maxTimestamp !== 'number' || isNaN(minTimestamp) || isNaN(maxTimestamp)) { console.error("Invalid min/max timestamps for slider setup."); document.getElementById('filterStatus').textContent = "Error: Invalid date range."; return; }
    if (minTimestamp >= maxTimestamp) {
        console.error(`Slider setup error: minTimestamp (${minTimestamp}) must be less than maxTimestamp (${maxTimestamp}).`);
         if (minTimestamp === maxTimestamp) { maxTimestamp = minTimestamp + MIN_RANGE_WIDTH_MS; console.warn("Corrected equal min/max timestamps for slider."); }
         else { document.getElementById('filterStatus').textContent = "Error: Invalid date range."; return; }
    }

    const sliderElement = document.getElementById('date-slider');
    const startDateLabel = document.getElementById('slider-start-date');
    const endDateLabel = document.getElementById('slider-end-date');
    if (!sliderElement || !startDateLabel || !endDateLabel) { console.error("Slider UI elements not found."); return; }

    if (dateSlider && typeof dateSlider.destroy === 'function') { stopAdjustmentLoop(); dateSlider.destroy(); dateSlider = null; console.log("Existing slider destroyed."); }

    console.log(`Setting up slider with range: ${minTimestamp} to ${maxTimestamp}`);
    try {
        dateSlider = noUiSlider.create(sliderElement, {
            start: [minTimestamp, maxTimestamp], connect: true, range: { 'min': minTimestamp, 'max': maxTimestamp },
            tooltips: false, format: { to: value => Math.round(value), from: value => Number(value) }, behaviour: 'drag'
        });

        dateSlider.on('update', function (values, handle) { if (!dateSlider) return; startDateLabel.textContent = formatDateForDisplay(Number(values[0])); endDateLabel.textContent = formatDateForDisplay(Number(values[1])); });
        dateSlider.on('start', function (values, handle) { activeHandleIndex = handle; startAdjustmentLoop(); });

        const debouncedUpdate = debounce(updateMap, UPDATE_MAP_DEBOUNCE_MS);
        dateSlider.on('slide', function (values, handle) { activeHandleIndex = handle; startAdjustmentLoop(); debouncedUpdate(); });

        const finalUpdateHandler = (values, handle) => { if (!dateSlider) return; activeHandleIndex = null; updateMap(); startAdjustmentLoop(); };
        dateSlider.on('end', finalUpdateHandler);
        dateSlider.on('set', finalUpdateHandler);

        console.log("Date slider initialized successfully.");
        updateRangeDisplay();
        updateMap();
    } catch (error) { console.error("Error creating noUiSlider:", error); statusDiv.textContent = "Error initializing slider."; }
}

// --- Debounce Function ---
function debounce(func, wait) {
    let timeout;
    return function executedFunction(...args) {
        const later = () => { clearTimeout(timeout); func(...args); };
        clearTimeout(timeout);
        timeout = setTimeout(later, wait);
    };
}

// --- Binary Search Helper Functions ---
// (These functions remain unchanged)
function findFirstFeatureIndex(targetTimestamp) {
    let low = 0; let high = allFeatures.length - 1; let index = allFeatures.length;
    while (low <= high) { const mid = Math.floor((low + high) / 2); if (allFeatures[mid].properties.timestampMs >= targetTimestamp) { index = mid; high = mid - 1; } else { low = mid + 1; } }
    return index;
}
function findLastFeatureIndex(targetTimestamp) {
    let low = 0; let high = allFeatures.length - 1; let index = -1;
    while (low <= high) { const mid = Math.floor((low + high) / 2); if (allFeatures[mid].properties.timestampMs <= targetTimestamp) { index = mid; low = mid + 1; } else { high = mid - 1; } }
    return index;
}


// --- Map Update Logic ---
function updateMap() {
    console.time("updateMap Total"); // Time the entire function
    const statusDiv = document.getElementById('filterStatus');
    if (!dateSlider || !segmentLayerGroup || allFeatures.length === 0) {
        statusDiv.textContent = "Waiting for data/slider...";
        clearMapLayers();
        console.timeEnd("updateMap Total");
        return;
    }

    console.time("updateMap: Get Slider Values");
    const sliderValues = dateSlider.get().map(Number);
    const startTimestamp = sliderValues[0];
    const endTimestamp = sliderValues[1];
    const startDateStr = formatDateForDisplay(startTimestamp);
    const endDateStr = formatDateForDisplay(endTimestamp);
    statusDiv.textContent = `Filtering data...`;
    console.timeEnd("updateMap: Get Slider Values");


    console.time("updateMap: Filter Features (Binary Search + Slice)");
    const startIndex = findFirstFeatureIndex(startTimestamp);
    const endIndex = findLastFeatureIndex(endTimestamp);
    let filteredFeatures = [];
    if (startIndex <= endIndex && startIndex < allFeatures.length && endIndex >= 0) {
        filteredFeatures = allFeatures.slice(startIndex, endIndex + 1);
    }
    console.timeEnd("updateMap: Filter Features (Binary Search + Slice)");
    console.log(`updateMap: Found ${filteredFeatures.length} features between index ${startIndex} and ${endIndex}.`); // Log count immediately

    console.time("updateMap: Clear Layers");
    clearMapLayers();
    console.timeEnd("updateMap: Clear Layers");

    if (filteredFeatures.length > 0) {

        console.time("updateMap: Create/Add Segments");
        if (filteredFeatures.length > 1) {
            const layersToAdd = []; // Create temp array first
            for (let i = 0; i < filteredFeatures.length - 1; i++) {
                const p1 = filteredFeatures[i];
                const p2 = filteredFeatures[i + 1];
                const weight = p1.properties.segmentWeight; // Use pre-calculated weight

                 if (p2.properties.timestampMs > p1.properties.timestampMs) {
                    const segmentCoords = [ [p1.properties.lat, p1.properties.lon], [p2.properties.lat, p2.properties.lon] ];
                    // --- Create Polyline Object ---
                    // console.time("updateMap: Create Single Polyline"); // Too verbose usually
                    const polyline = L.polyline(segmentCoords, { color: SEGMENT_COLOR, weight: weight, opacity: SEGMENT_OPACITY });
                    // console.timeEnd("updateMap: Create Single Polyline");
                    layersToAdd.push(polyline);
                 }
            }
            // --- Add Layers in Bulk (might be slightly faster for FeatureGroup) ---
            console.time("updateMap: Add Polylines to Group");
            layersToAdd.forEach(layer => segmentLayerGroup.addLayer(layer));
            // Alternative (try if above is slow, though often similar for FeatureGroup):
            // segmentLayerGroup.addLayer(L.layerGroup(layersToAdd));
            console.timeEnd("updateMap: Add Polylines to Group");
        }
        console.timeEnd("updateMap: Create/Add Segments"); // Ends timing for segment processing


        console.time("updateMap: Create Markers & Popups");
        const startPoint = filteredFeatures[0];
        const endPoint = filteredFeatures[filteredFeatures.length - 1];
        const greenIcon = L.icon({ iconUrl: 'https://raw.githubusercontent.com/pointhi/leaflet-color-markers/master/img/marker-icon-green.png', shadowUrl: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.7.1/images/marker-shadow.png', iconSize: [25, 41], iconAnchor: [12, 41], popupAnchor: [1, -34], shadowSize: [41, 41] });
        const redIcon = L.icon({ iconUrl: 'https://raw.githubusercontent.com/pointhi/leaflet-color-markers/master/img/marker-icon-red.png', shadowUrl: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.7.1/images/marker-shadow.png', iconSize: [25, 41], iconAnchor: [12, 41], popupAnchor: [1, -34], shadowSize: [41, 41] });

        currentStartMarker = L.marker([startPoint.properties.lat, startPoint.properties.lon], { icon: greenIcon }).addTo(map);
        const startPopupContent = `<b>Start:</b><br>${formatDateForDisplay(startPoint.properties.dateObj)}<br>${startPoint.properties.dateObj.toLocaleTimeString()}`;

        if (filteredFeatures.length > 1 && startPoint.properties.timestampMs !== endPoint.properties.timestampMs) {
             const endPopupContent = `<b>End:</b><br>${formatDateForDisplay(endPoint.properties.dateObj)}<br>${endPoint.properties.dateObj.toLocaleTimeString()}`;
             currentEndMarker = L.marker([endPoint.properties.lat, endPoint.properties.lon], { icon: redIcon })
                .bindPopup(endPopupContent)
                .addTo(map);
            currentStartMarker.bindPopup(startPopupContent);
        } else if (filteredFeatures.length === 1) {
            currentStartMarker.bindPopup(`<b>Single Point:</b><br>${formatDateForDisplay(startPoint.properties.dateObj)}<br>${startPoint.properties.dateObj.toLocaleTimeString()}`);
        } else {
             currentStartMarker.bindPopup(`<b>Start/End:</b><br>${formatDateForDisplay(startPoint.properties.dateObj)}<br>${startPoint.properties.dateObj.toLocaleTimeString()}`);
        }
        console.timeEnd("updateMap: Create Markers & Popups");


        console.time("updateMap: Fit Bounds");
        try {
             let boundsToFit = null;
             console.time("updateMap: Calculate Bounds");
             if (segmentLayerGroup && segmentLayerGroup.getLayers().length > 0) {
                 boundsToFit = segmentLayerGroup.getBounds();
             } else if (currentStartMarker) {
                 boundsToFit = L.latLngBounds(currentStartMarker.getLatLng(), currentStartMarker.getLatLng());
             }
             console.timeEnd("updateMap: Calculate Bounds");

             if (boundsToFit && boundsToFit.isValid()) {
                 console.time("updateMap: Fly To Bounds Call");
                 map.flyToBounds(boundsToFit, { padding: [50, 50], maxZoom: 16, duration: 0.5 }); // Optional: slightly reduce animation duration
                 console.timeEnd("updateMap: Fly To Bounds Call");
             } else if (filteredFeatures.length > 0) {
                  map.setView([startPoint.properties.lat, startPoint.properties.lon], map.getZoom());
             }
         } catch (e) {
             console.warn("Could not calculate or fit bounds:", e);
             if (startPoint) map.setView([startPoint.properties.lat, startPoint.properties.lon], map.getZoom());
         }
        console.timeEnd("updateMap: Fit Bounds");

        statusDiv.textContent = `Showing ${filteredFeatures.length} points from ${startDateStr} to ${endDateStr}.`;

    } else {
        // No features found
        statusDiv.textContent = `No data found between ${startDateStr} and ${endDateStr}.`;
    }
    console.timeEnd("updateMap Total"); // End timing the entire function
}


// --- Clear Map Layers ---
function clearMapLayers() {
    // This is usually fast, but let's time it just in case
    // console.time("clearMapLayers Internal");
    segmentLayerGroup?.clearLayers();
    if (currentStartMarker) { map.removeLayer(currentStartMarker); currentStartMarker = null; }
    if (currentEndMarker) { map.removeLayer(currentEndMarker); currentEndMarker = null; }
    // console.timeEnd("clearMapLayers Internal");
}


// --- Script Execution ---
document.addEventListener('DOMContentLoaded', () => {
    initMap();
    loadData();
});
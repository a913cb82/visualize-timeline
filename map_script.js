// --- Global Variables ---
let map = null;                     // Leaflet map instance
let allFeatures = [];               // Array to hold all GeoJSON features after loading
let currentPolylineLayer = null;    // Reference to the currently displayed polyline
let currentStartMarker = null;      // Reference to the start marker
let currentEndMarker = null;        // Reference to the end marker
const GEOJSON_URL = 'timeline_data.geojson'; // Path to your data file

// --- Initialization ---

function initMap() {
    console.log("Initializing map...");
    // Create map instance centered on a default location
    map = L.map('map').setView([48.8566, 2.3522], 5); // Paris, zoom level 5

    // --- Define Base Layers ---
    const osm = L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
        attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
        maxZoom: 19 // Standard max zoom for OSM
    });

    const esriNatGeo = L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/NatGeo_World_Map/MapServer/tile/{z}/{y}/{x}', {
        attribution: 'Tiles © Esri — National Geographic, Esri, DeLorme, NAVTEQ, UNEP-WCMC, USGS, NASA, ESA, METI, NRCAN, GEBCO, NOAA, iPC',
        maxZoom: 16 // Esri NatGeo often has a lower max practical zoom
    });

    // --- Add Default Base Layer ---
    esriNatGeo.addTo(map); // Add Esri NatGeo as the default

    // --- Create Layer Control ---
    const baseMaps = {
        "Esri NatGeo": esriNatGeo,
        "OpenStreetMap": osm
        // Add other base maps here if desired (e.g., Satellite)
        // "Esri Satellite": L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', { attribution: 'Tiles © Esri — Source: Esri, i-cubed, USDA, USGS, AEX, GeoEye, Getmapping, Aerogrid, IGN, IGP, UPR-EGP, and the GIS User Community' })
    };

    // Add layer control to the map
    L.control.layers(baseMaps, null, { collapsed: false }).addTo(map); // Add base layer switcher, not collapsed initially

    console.log("Map initialized with Esri NatGeo and layer control.");
}

// --- Data Handling --- (Keep the rest of the file the same) ---

async function loadData() {
    console.log(`Fetching data from ${GEOJSON_URL}...`);
    const statusDiv = document.getElementById('filterStatus');
    try {
        const response = await fetch(GEOJSON_URL);
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        const geojsonData = await response.json();
        console.log(`Data loaded successfully. Features found: ${geojsonData.features.length}`);

        if (!geojsonData || !geojsonData.features || geojsonData.features.length === 0) {
            console.warn("GeoJSON data is empty or invalid.");
            statusDiv.textContent = "No timeline data found.";
            return;
        }

        // Store features and parse dates
        allFeatures = geojsonData.features.map(feature => {
            // Add a JavaScript Date object for easier filtering
            feature.properties.dateObj = new Date(feature.properties.timestamp);
            // Add lat/lon properties directly for easier access by Leaflet Polyline
            feature.properties.lat = feature.geometry.coordinates[1];
            feature.properties.lon = feature.geometry.coordinates[0];
            return feature;
        });

        // Sort features by date (important for drawing lines correctly)
        allFeatures.sort((a, b) => a.properties.dateObj - b.properties.dateObj);

        console.log("Features processed and sorted.");

        // Set date picker defaults and limits
        setupDatePickers();

        // Initial map update to show all data (or default range)
        updateMap();

    } catch (error) {
        console.error("Error loading or processing GeoJSON:", error);
        statusDiv.textContent = `Error loading data: ${error.message}`;
    }
}

function setupDatePickers() {
    if (allFeatures.length === 0) return;

    const startDateInput = document.getElementById('startDate');
    const endDateInput = document.getElementById('endDate');

    // Find min and max dates from the data
    let minDate = allFeatures[0].properties.dateObj;
    let maxDate = allFeatures[allFeatures.length - 1].properties.dateObj;

    // Format dates as YYYY-MM-DD for input value/min/max
    const formatDate = (date) => date.toISOString().split('T')[0];

    const minDateStr = formatDate(minDate);
    const maxDateStr = formatDate(maxDate);

    console.log(`Data date range: ${minDateStr} to ${maxDateStr}`);

    startDateInput.min = minDateStr;
    startDateInput.max = maxDateStr;
    startDateInput.value = minDateStr; // Default to start date

    endDateInput.min = minDateStr;
    endDateInput.max = maxDateStr;
    endDateInput.value = maxDateStr;   // Default to end date

    // Add event listener to the button
    const filterButton = document.getElementById('filterButton');
    filterButton.addEventListener('click', updateMap);

     // Optional: Trigger update on date change directly
     // startDateInput.addEventListener('change', updateMap);
     // endDateInput.addEventListener('change', updateMap);

    console.log("Date pickers configured.");
}

// --- Map Update Logic ---

function updateMap() {
    console.log("Updating map based on date filter...");
    const startDateInput = document.getElementById('startDate');
    const endDateInput = document.getElementById('endDate');
    const statusDiv = document.getElementById('filterStatus');

    if (!startDateInput.value || !endDateInput.value) {
        statusDiv.textContent = "Please select start and end dates.";
        return;
    }

    // --- Get and Parse Selected Dates ---
    const startDateStr = startDateInput.value;
    const endDateStr = endDateInput.value;

    const startDate = new Date(Date.UTC(
        parseInt(startDateStr.substring(0, 4)),
        parseInt(startDateStr.substring(5, 7)) - 1,
        parseInt(startDateStr.substring(8, 10)),
        0, 0, 0, 0
    ));

    const endDate = new Date(Date.UTC(
        parseInt(endDateStr.substring(0, 4)),
        parseInt(endDateStr.substring(5, 7)) - 1,
        parseInt(endDateStr.substring(8, 10)),
        23, 59, 59, 999
    ));


    if (startDate > endDate) {
        statusDiv.textContent = "Error: Start date cannot be after end date.";
        clearMapLayers();
        return;
    }

    console.log(`Filtering between ${startDate.toISOString()} and ${endDate.toISOString()}`);
    statusDiv.textContent = `Filtering data...`;

    // --- Filter Data ---
    const filteredFeatures = allFeatures.filter(feature => {
        const featureDate = feature.properties.dateObj;
        return featureDate >= startDate && featureDate <= endDate;
    });

    console.log(`Found ${filteredFeatures.length} features in the selected range.`);

    // --- Clear Existing Layers ---
    clearMapLayers();

    // --- Draw New Layers ---
    if (filteredFeatures.length > 0) {
        const coordinates = filteredFeatures.map(feature => [
            feature.properties.lat,
            feature.properties.lon
        ]);

        currentPolylineLayer = L.polyline(coordinates, {
            color: 'blue',
            weight: 3,
            opacity: 0.8
        }).addTo(map);
        console.log("Polyline drawn.");

        const startPoint = filteredFeatures[0];
        const endPoint = filteredFeatures[filteredFeatures.length - 1];

        currentStartMarker = L.marker([startPoint.properties.lat, startPoint.properties.lon], {
            icon: L.icon({ iconUrl: 'https://raw.githubusercontent.com/pointhi/leaflet-color-markers/master/img/marker-icon-green.png', shadowUrl: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.7.1/images/marker-shadow.png', iconSize: [25, 41], iconAnchor: [12, 41], popupAnchor: [1, -34], shadowSize: [41, 41] })
        }).bindPopup(`<b>Start:</b><br>${startPoint.properties.dateObj.toLocaleString()}`).addTo(map);

        if (filteredFeatures.length > 1) {
            currentEndMarker = L.marker([endPoint.properties.lat, endPoint.properties.lon], {
                 icon: L.icon({ iconUrl: 'https://raw.githubusercontent.com/pointhi/leaflet-color-markers/master/img/marker-icon-red.png', shadowUrl: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.7.1/images/marker-shadow.png', iconSize: [25, 41], iconAnchor: [12, 41], popupAnchor: [1, -34], shadowSize: [41, 41] })
            }).bindPopup(`<b>End:</b><br>${endPoint.properties.dateObj.toLocaleString()}`).addTo(map);
        }
        console.log("Start/End markers added.");

        try {
             map.flyToBounds(currentPolylineLayer.getBounds(), { padding: [30, 30] });
             console.log("Map bounds adjusted.");
        } catch (e) {
            console.warn("Could not fit bounds:", e);
        }


        statusDiv.textContent = `Showing ${filteredFeatures.length} points from ${startDateStr} to ${endDateStr}.`;

    } else {
        statusDiv.textContent = `No data found between ${startDateStr} and ${endDateStr}.`;
    }
}

function clearMapLayers() {
    if (currentPolylineLayer && map.hasLayer(currentPolylineLayer)) {
        map.removeLayer(currentPolylineLayer);
        currentPolylineLayer = null;
    }
    if (currentStartMarker && map.hasLayer(currentStartMarker)) {
        map.removeLayer(currentStartMarker);
        currentStartMarker = null;
    }
    if (currentEndMarker && map.hasLayer(currentEndMarker)) {
        map.removeLayer(currentEndMarker);
        currentEndMarker = null;
    }
}


// --- Script Execution ---

document.addEventListener('DOMContentLoaded', () => {
    initMap();
    loadData();
});
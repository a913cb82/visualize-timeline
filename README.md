# Google Timeline Visualizer

A high-performance, interactive web-based tool to visualize and explore your Google Location History (Timeline). This project processes your raw Google Timeline JSON data, simplifies it for performance, and provides a smooth map interface with dynamic date filtering.

## Features

- **Smooth Visualization**: Processes thousands of points and renders them efficiently using Leaflet's Canvas renderer.
- **Smart Simplification**: Automatically removes redundant points (close in time and distance) to keep the map responsive and output files compact.
- **Interactive Timeline**: A custom-styled date slider allows you to filter your history by date with dynamic range adjustment.
- **Multi-Layer Maps**: Supports OpenStreetMap, Esri NatGeo, and optionally Google Maps or Stadia Stamen Terrain.
- **CLI Progress**: Real-time progress bars during data processing using tqdm.
- **Environment Configuration**: Simple setup for optional API keys using environment variables.

## Setup and Usage

### 1. Export your Location History

You need a `Timeline.json` exported from Google. This data lives on your Android device (Google no longer keeps Timeline in the cloud for new accounts), so the export must come from the phone that holds it:

1. On your Android phone, make sure Location History is on: **Settings → Location → Location Services → Timeline** (on some versions: **Google Maps → Settings → Personal content → Location History**).
2. Open **Timeline** and choose **Export Timeline Data** (usually under the overflow **⋮** menu).
3. Transfer the exported `Timeline.json` to your computer and place it in the root of this project directory.

> Older, account-based history can alternatively be requested through **Google Takeout** (takeout.google.com) with the *Location History (Timeline)* item selected; export and place the resulting JSON as `Timeline.json`.

### 2. Environment Setup
Create a virtual environment and install the required dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate  # On Windows use: .venv\Scripts\activate
pip install -r requirements.txt
```

### 3. Configuration (Optional)
If you wish to use Google Maps or Stadia layers, create a `.env` file from the example:

```bash
cp .env.example .env
# Edit .env and add your API keys
```

### 4. Process the Data
Run the converter script to generate the GeoJSON data and frontend configuration:

```bash
python timeline_to_geojson.py
```

### 5. View the Map
Start a local web server and open the application:

```bash
python3 -m http.server 8000
```
Navigate to `http://localhost:8000` in your web browser.

## Dependencies

- **Python**: pandas, geopy, python-dotenv, tqdm.
- **Frontend**: Leaflet.js, noUiSlider.

## Privacy
All processing is performed locally on your machine. Your location history data is never uploaded or shared with any external services by this tool.

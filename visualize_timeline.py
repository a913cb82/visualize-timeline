import pandas as pd
from datetime import datetime, timezone, timedelta
import sys
import os
import warnings
import json # To safely embed Python variables in JavaScript

# --- Geospatial/Interactive Map Libraries ---
try:
    import geopandas as gpd
    from shapely.geometry import Point, LineString
    import folium
    # from folium.elements import JSCSSMixin # Not strictly needed for this approach
    # from jinja2 import Template # Not strictly needed for direct string embedding
except ImportError as e:
    print(f"ERROR: Required libraries for interactive mapping are missing: {e}")
    print("Please install geopandas and folium: pip install geopandas folium")
    sys.exit(1)

# --- Constants ---
API_KEY_FILENAME = "stadia_api_key.txt"

# --- Fading/Slider Configuration ---
DEFAULT_HALF_LIFE_DAYS = 7.0   # Default fade half-life in days
MIN_HALF_LIFE_DAYS = 0.1     # Minimum value for the slider (set > 0 to avoid division by zero issues in formula)
MAX_HALF_LIFE_DAYS = 90.0    # Maximum value for the slider
SLIDER_STEP = 0.1            # Granularity of the slider
MIN_LINE_OPACITY = 0.1       # Base opacity for segments even after infinite time
INITIAL_OPACITY_SKIP_THRESHOLD = 0.02 # Don't draw segments initially fainter than this
DYNAMIC_OPACITY_OFF_THRESHOLD = 0.01  # Turn segments "off" (opacity 0) if they dynamically fall below this
SLIDER_Z_INDEX = 2000         # z-index for the slider (higher than default Leaflet controls)

# --- Functions (load_csv, filter_locations) ---
def load_photo_locations_from_csv(file_path: str) -> pd.DataFrame | None:
    """Loads photo location data from CSV, parses timestamps (UTC), sorts by timestamp."""
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Error: CSV file not found at {file_path}")
    print(f"Attempting to load CSV data from: {file_path}")
    try:
        df = pd.read_csv(file_path)
        print(f"CSV loaded successfully. Found {len(df)} rows.")
        required_columns = ['filepath', 'timestamp_str', 'latitude', 'longitude']
        if not all(col in df.columns for col in required_columns):
            missing = [col for col in required_columns if col not in df.columns]
            print(f"Error: CSV file is missing required columns: {missing}")
            return None
        print("Parsing timestamps and converting coordinates...")
        try:
            # Try multiple formats, prioritizing the original one
            df['timestamp'] = pd.to_datetime(df['timestamp_str'], format='%Y-%m-%d %H:%M:%S', errors='coerce')
            # If coerce resulted in NaT, try ISO 8601 format (common alternative)
            if df['timestamp'].isnull().any():
                 print("Trying alternative timestamp format (ISO 8601)...")
                 # Fill NaNs using the alternative format attempt on the original string column
                 df['timestamp'] = df['timestamp'].fillna(pd.to_datetime(df.loc[df['timestamp'].isnull(), 'timestamp_str'], errors='coerce'))

            # Ensure timezone is UTC if not already present
            if df['timestamp'].dt.tz is None:
                # Be careful assuming local timezone if none provided. UTC is safer.
                print("Localizing timestamps to UTC (assuming original times were UTC)...")
                # Use errors='raise' to catch ambiguous or non-existent times if trying tz_localize('local')
                # Forcing UTC is generally safer unless source timezone is known.
                df['timestamp'] = df['timestamp'].dt.tz_localize(timezone.utc, ambiguous='infer', nonexistent='shift_forward')

            else:
                print("Converting timestamps to UTC...")
                df['timestamp'] = df['timestamp'].dt.tz_convert(timezone.utc)

        except Exception as e:
             # Catch specific errors if possible, e.g., pytz.exceptions.AmbiguousTimeError
             print(f"Error parsing or localizing timestamps: {e}. Ensure format is 'YYYY-MM-DD HH:MM:S' or ISO 8601.")
             return None

        df['latitude'] = pd.to_numeric(df['latitude'], errors='coerce')
        df['longitude'] = pd.to_numeric(df['longitude'], errors='coerce')

        original_count = len(df)
        df.dropna(subset=['timestamp', 'latitude', 'longitude'], inplace=True)
        removed_count = original_count - len(df)
        if removed_count > 0:
            print(f"Warning: Removed {removed_count} rows due to invalid/missing timestamp or coordinates.")

        if df.empty:
            print("No valid data remaining after cleaning.")
            return None

        df.sort_values(by='timestamp', inplace=True)
        print(f"Data prepared and sorted. {len(df)} valid photo location entries.")
        return df
    except pd.errors.EmptyDataError:
        print(f"Error: The CSV file '{file_path}' is empty.")
        return None
    except Exception as e:
        print(f"An unexpected error occurred while reading or processing the CSV: {e}")
        raise

def filter_locations_dataframe(df: pd.DataFrame,
                               start_date: datetime,
                               end_date: datetime) -> pd.DataFrame:
    """Filters DataFrame by date range (inclusive). Dates should be timezone-aware (UTC recommended)."""
    if df['timestamp'].dt.tz is None: raise ValueError("DataFrame timestamps must be timezone-aware for filtering.")
    if start_date.tzinfo is None or end_date.tzinfo is None: raise ValueError("Start and end dates must be timezone-aware for filtering.")
    start_date_utc = start_date.astimezone(timezone.utc)
    end_date_utc = end_date.astimezone(timezone.utc)
    print(f"Filtering locations between {start_date_utc.strftime('%Y-%m-%d %H:%M:%S %Z')} and {end_date_utc.strftime('%Y-%m-%d %H:%M:%S %Z')}")
    # Ensure the end_date includes the whole day
    # If end_date has 00:00:00 time, make it end of day
    if end_date_utc.time() == datetime.min.time():
         end_date_utc = end_date_utc.replace(hour=23, minute=59, second=59, microsecond=999999)
         print(f"Adjusted end date for filtering to include the full day: {end_date_utc.strftime('%Y-%m-%d %H:%M:%S %Z')}")
    filtered_df = df[(df['timestamp'] >= start_date_utc) & (df['timestamp'] <= end_date_utc)].copy()
    print(f"Found {len(filtered_df)} photo locations within the specified date range.")
    return filtered_df


# --- Interactive Plotting Function (Revised for Slider Control Visibility & NameError Fix) ---
def create_interactive_map(
    gdf_wgs84: gpd.GeoDataFrame,
    output_html_path: str,
    stadia_api_key: str | None,
    add_start_end_markers: bool = True,
    add_all_points: bool = False,
    # --- Pass slider config ---
    default_half_life: float = DEFAULT_HALF_LIFE_DAYS,
    min_half_life: float = MIN_HALF_LIFE_DAYS,
    max_half_life: float = MAX_HALF_LIFE_DAYS,
    slider_step: float = SLIDER_STEP,
    min_opacity: float = MIN_LINE_OPACITY,
    initial_skip_threshold: float = INITIAL_OPACITY_SKIP_THRESHOLD,
    dynamic_off_threshold: float = DYNAMIC_OPACITY_OFF_THRESHOLD,
    slider_z_index: int = SLIDER_Z_INDEX
):
    """
    Creates an interactive HTML map with a slider to control line segment fading half-life.

    Args:
        gdf_wgs84: GeoDataFrame with location data (EPSG:4326, time-sorted).
        output_html_path: Path to save the interactive HTML map.
        stadia_api_key: The Stadia Maps API key (if available, else None).
        add_start_end_markers: Add markers for the first and last points.
        add_all_points: Add circle markers for every point.
        default_half_life: Initial half-life setting for the slider (days).
        min_half_life: Minimum slider value (days).
        max_half_life: Maximum slider value (days).
        slider_step: Step increment for the slider.
        min_opacity: Minimum opacity floor for segments.
        initial_skip_threshold: Opacity below which segments are not drawn initially.
        dynamic_off_threshold: Opacity below which segments are turned off dynamically by slider.
        slider_z_index: CSS z-index for the slider control.
    """
    if gdf_wgs84.empty: print("GeoDataFrame is empty. Cannot create map."); return
    if gdf_wgs84.crs != "EPSG:4326":
        try: gdf_wgs84 = gdf_wgs84.to_crs("EPSG:4326")
        except Exception as e: print(f"Error converting GDF to EPSG:4326: {e}. Cannot create map."); return
    gdf_wgs84 = gdf_wgs84.sort_values(by='timestamp')

    print(f"\n--- Generating Interactive Map: {os.path.basename(output_html_path)} ---")

    # --- Determine Map Center ---
    try:
        # Use union_all() instead of deprecated unary_union
        if len(gdf_wgs84) > 1: center_lat, center_lon = gdf_wgs84.union_all().centroid.y, gdf_wgs84.union_all().centroid.x
        elif len(gdf_wgs84) == 1: center_lat, center_lon = gdf_wgs84.iloc[0].geometry.y, gdf_wgs84.iloc[0].geometry.x
        else: center_lat, center_lon = 0, 0
        start_location = [center_lat, center_lon]
    except Exception as e: print(f"Warning: Could not calculate center, using default. Error: {e}"); start_location = [0, 0]

    # --- Initialize Folium Map ---
    m = folium.Map(location=start_location, zoom_start=10, tiles="OpenStreetMap")
    map_id = m.get_name() # Get the unique ID Folium assigns to the map div

    # --- Add Basemap Layers ---
    folium.TileLayer('OpenStreetMap', name='OpenStreetMap', attr='© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors', overlay=False, control=True, show=True).add_to(m)
    print("Added OpenStreetMap basemap.")
    otm_attribution = 'Map data: © <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors, <a href="http://viewfinderpanoramas.org">SRTM</a> | Map style: © <a href="https://opentopomap.org">OpenTopoMap</a> (<a href="https://creativecommons.org/licenses/by-sa/3.0/">CC-BY-SA</a>)'
    folium.TileLayer(tiles='https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png', attr=otm_attribution, name='OpenTopoMap', overlay=False, control=True, show=False).add_to(m)
    print("Added OpenTopoMap basemap.")
    if stadia_api_key:
        print("Attempting to add Stadia Stamen Terrain basemap...")
        stadia_attribution = "© Stadia Maps, © OpenMapTiles © OpenStreetMap contributors"
        stadia_tiles_url = f"https://tiles.stadiamaps.com/tiles/stamen_terrain/{{z}}/{{x}}/{{y}}.png?api_key={stadia_api_key}"
        try: folium.TileLayer(tiles=stadia_tiles_url, attr=stadia_attribution, name='Stadia Stamen Terrain', overlay=False, control=True, show=False).add_to(m); print("Stadia Stamen Terrain added as an option.")
        except Exception as e_stadia: print(f"*** Warning: Could not add Stadia Stamen Terrain layer: {e_stadia}")
    else: print("No Stadia API key provided. Skipping Stadia basemap.")


    # --- Add Data: Fading Line Segments ---
    fading_segment_group = None
    drawn_segments = 0
    skipped_segments = 0
    if len(gdf_wgs84) >= 2:
        print("Adding path as fading PolyLine segments with interactive control...")
        fading_segment_group = folium.FeatureGroup(name="Photo Path (Fading)", control=True).add_to(m) # Assign name for Layer Control

        end_timestamp = gdf_wgs84['timestamp'].iloc[-1]
        SECONDS_PER_DAY = 24 * 3600

        for i in range(len(gdf_wgs84) - 1):
            point1 = gdf_wgs84.iloc[i]
            point2 = gdf_wgs84.iloc[i+1]
            segment_coords = [[point1.geometry.y, point1.geometry.x], [point2.geometry.y, point2.geometry.x]]
            segment_start_time = point1['timestamp']

            delta_t_seconds = (end_timestamp - segment_start_time).total_seconds()
            delta_t_days = max(0, delta_t_seconds / SECONDS_PER_DAY) # Ensure non-negative

            initial_opacity = min_opacity
            if default_half_life > 0:
                 # Exponential decay formula based on half-life
                 decay_factor = 0.5 ** (delta_t_days / default_half_life)
                 initial_opacity = min_opacity + (1.0 - min_opacity) * decay_factor
                 initial_opacity = max(0.0, min(1.0, initial_opacity)) # Clamp [0, 1]

            # Optimization: Skip drawing if initially too faint
            if initial_opacity < initial_skip_threshold:
                skipped_segments += 1
                continue

            # Store delta_t_days in options for JS access
            segment_options = {'delta_t_days': delta_t_days}

            folium.PolyLine(
                locations=segment_coords,
                color='blue',
                weight=3,
                opacity=initial_opacity, # Set initial opacity
                options=segment_options  # Embed delta_t_days here
            ).add_to(fading_segment_group) # Add segment to the named group
            drawn_segments += 1

        print(f"Added {drawn_segments} fading line segments (skipped {skipped_segments} initially faint segments).")

    elif len(gdf_wgs84) == 1:
         print("Only one point found. Adding a marker instead of a line.")
         add_all_points = True; add_start_end_markers = False

    # --- Add Markers ---
    marker_group = folium.FeatureGroup(name="Start/End Points", show=True).add_to(m)
    if add_start_end_markers and len(gdf_wgs84) >= 1:
        start_point = gdf_wgs84.iloc[0]; end_point = gdf_wgs84.iloc[-1]
        start_popup_html = f"<b>Start Point</b><br>Timestamp: {start_point['timestamp'].strftime('%Y-%m-%d %H:%M:%S %Z')}<br>Coords: ({start_point.geometry.y:.6f}, {start_point.geometry.x:.6f})<br>File: {os.path.basename(start_point['filepath'])}"
        folium.Marker(location=[start_point.geometry.y, start_point.geometry.x], popup=folium.Popup(start_popup_html, max_width=300), tooltip="Click for Start Info", icon=folium.Icon(color='green', icon='play')).add_to(marker_group)
        if len(gdf_wgs84) > 1:
            end_popup_html = f"<b>End Point</b><br>Timestamp: {end_point['timestamp'].strftime('%Y-%m-%d %H:%M:%S %Z')}<br>Coords: ({end_point.geometry.y:.6f}, {end_point.geometry.x:.6f})<br>File: {os.path.basename(end_point['filepath'])}"
            folium.Marker(location=[end_point.geometry.y, end_point.geometry.x], popup=folium.Popup(end_popup_html, max_width=300), tooltip="Click for End Info", icon=folium.Icon(color='red', icon='stop')).add_to(marker_group)
        print("Start/End markers added.")

    if add_all_points:
        point_group = folium.FeatureGroup(name="All Points", show=False).add_to(m)
        print("Adding markers for all points...")
        for idx, row in gdf_wgs84.iterrows():
            point_popup_html = f"Timestamp: {row['timestamp'].strftime('%Y-%m-%d %H:%M:%S %Z')}<br>Coords: ({row.geometry.y:.6f}, {row.geometry.x:.6f})<br>File: {os.path.basename(row['filepath'])}"
            folium.CircleMarker(location=[row.geometry.y, row.geometry.x], radius=3, color='purple', fill=True, fill_color='purple', fill_opacity=0.6, popup=folium.Popup(point_popup_html, max_width=300), tooltip=row['timestamp'].strftime('%H:%M:%S')).add_to(point_group)
        print(f"Added {len(gdf_wgs84)} point markers (grouped).")


    # --- Add Slider Control HTML & JS ---
    if fading_segment_group and drawn_segments > 0:
        # Use the configured z-index
        slider_html = f"""
         <div id="slider-control" style="position: fixed; top: 10px; right: 10px; z-index: {slider_z_index}; background-color: white; padding: 8px; border-radius: 5px; border: 1px solid grey; font-family: sans-serif; font-size: 0.9em; box-shadow: 0 1px 5px rgba(0,0,0,0.65);">
           <label for="halfLifeSlider">Fade Half-life (days):</label>
           <input type="range" id="halfLifeSlider" name="halfLifeSlider"
                  min="{min_half_life}" max="{max_half_life}" value="{default_half_life}" step="{slider_step}"
                  style="width: 150px; vertical-align: middle;">
           <span id="halfLifeValue">{default_half_life}</span> days
         </div>
        """

        # Refined JS with fix for f-string interpolation of JS variables
        js_code = f"""
        // Helper function to find the FeatureGroup containing segments
        function findFadingSegmentsLayer(mapInstance) {{
            let foundLayer = null;
            mapInstance.eachLayer(function(layer) {{
                if (layer instanceof L.FeatureGroup) {{
                    let hasSegmentOption = false;
                    let childrenChecked = 0;
                    layer.eachLayer(function(child) {{
                        if (child.options && child.options.hasOwnProperty('delta_t_days')) {{
                            hasSegmentOption = true;
                            return false; // Stop inner loop
                        }}
                        childrenChecked++;
                        if (childrenChecked > 5) {{ return false; }}
                    }});
                    if (hasSegmentOption) {{
                        foundLayer = layer;
                        return false; // Stop outer loop
                    }}
                }}
            }});
            if (!foundLayer) {{ console.warn("Could not definitively find fading segments layer group by child options."); }}
            return foundLayer;
        }}

        // Main function to update segment styles based on slider
        function updateSegmentOpacities(mapInstance, halfLifeDays) {{
            const minOpacity = {json.dumps(min_opacity)};
            const dynamicOffThreshold = {json.dumps(dynamic_off_threshold)};
            const targetLayer = findFadingSegmentsLayer(mapInstance);

            if (!targetLayer) {{
                console.error("Fading segments layer group not found. Cannot update opacities.");
                return;
            }}

            targetLayer.eachLayer(function(segment) {{
                if (segment.options && segment.options.hasOwnProperty('delta_t_days')) {{
                    const delta_t = segment.options.delta_t_days;
                    let opacity = minOpacity;

                    if (halfLifeDays > 0) {{
                        const decayFactor = Math.pow(0.5, delta_t / halfLifeDays);
                        opacity = minOpacity + (1.0 - minOpacity) * decayFactor;
                    }} else {{
                         opacity = (delta_t < 0.01) ? 1.0 : minOpacity;
                    }}

                    opacity = Math.max(0.0, Math.min(1.0, opacity));

                    let finalOpacity = (opacity < dynamicOffThreshold) ? 0 : opacity;

                    if (segment.setStyle) {{
                         segment.setStyle({{ opacity: finalOpacity }}); // Use finalOpacity
                    }} else {{
                         console.warn("Segment layer does not have setStyle method?", segment);
                    }}
                }}
            }});
        }}

        // Wait for the DOM and Leaflet map to be ready
        document.addEventListener('DOMContentLoaded', (event) => {{
            const mapDiv = document.getElementById("{map_id}");
            let leafletMapInstance = null;

            let checkAttempts = 0; // Define JS variable
            const maxCheckAttempts = 10; // Define JS variable
            const checkInterval = 200; // Define JS variable

            function findMapInstance() {{
                 if (mapDiv && mapDiv.leaflet_map) {{
                      leafletMapInstance = mapDiv.leaflet_map;
                      console.log("Found Leaflet map instance:", leafletMapInstance);
                      initializeSlider();
                 }} else {{
                      checkAttempts++;
                      if (checkAttempts < maxCheckAttempts) {{
                           // *** Fix: Use double curly braces to escape JS template literal for Python f-string ***
                           console.log(`Map instance not ready, attempt ${{checkAttempts}}/${{maxCheckAttempts}}. Retrying in ${{checkInterval}}ms...`);
                           setTimeout(findMapInstance, checkInterval);
                      }} else {{
                           console.error(`Could not find Leaflet map instance for map ID '{map_id}' after ${{maxCheckAttempts}} attempts.`);
                      }}
                 }}
            }}

            function initializeSlider() {{
                if (!leafletMapInstance) return;

                const slider = document.getElementById('halfLifeSlider');
                const valueDisplay = document.getElementById('halfLifeValue');

                if (slider && valueDisplay) {{
                    console.log("Initializing slider control.");
                    slider.addEventListener('input', function() {{
                        const currentHalfLife = parseFloat(this.value);
                        const safeHalfLife = Math.max({json.dumps(min_half_life)}, currentHalfLife);
                        valueDisplay.textContent = safeHalfLife.toFixed(1);
                        updateSegmentOpacities(leafletMapInstance, safeHalfLife);
                    }});

                    const initialHalfLife = parseFloat(slider.value);
                    updateSegmentOpacities(leafletMapInstance, initialHalfLife);
                    console.log("Initial opacity update called.");
                }} else {{
                    console.error("Slider control elements (slider or value display) not found in the DOM.");
                }}
            }}

            // Start checking for the map instance
            findMapInstance();
        }});
        """

        # Embed the HTML and JS into the map
        m.get_root().html.add_child(folium.Element(slider_html))
        m.get_root().script.add_child(folium.Element(js_code))
        print(f"Added interactive slider control (z-index: {slider_z_index}).")
    else:
        print("No segments drawn, skipping slider control.")

    # --- Fit map bounds ---
    if not gdf_wgs84.empty:
        try:
            bounds = [[gdf_wgs84.total_bounds[1], gdf_wgs84.total_bounds[0]], [gdf_wgs84.total_bounds[3], gdf_wgs84.total_bounds[2]]]
            m.fit_bounds(bounds, padding=(0.05, 0.05))
            print("Map bounds fitted to data.")
        except Exception as e: print(f"Warning: Could not fit map bounds. Error: {e}")

    # --- Add Layer Control (Add LAST) ---
    folium.LayerControl().add_to(m)
    print("Layer control added.")

    # --- Save Map ---
    print(f"Saving interactive map to: {output_html_path}...")
    try:
        m.save(output_html_path)
        print(f"Map saved successfully: {output_html_path}")
        print("Open this HTML file in your web browser.")
    except Exception as e:
        print(f"Error saving map HTML: {e}")


# --- Main Execution Block ---
if __name__ == "__main__":

    # --- Configuration ---
    csv_file_path = './local_photo_locations.csv'
    start_date_str = None
    end_date_str = None
    output_map_filename = 'interactive_photo_path_map_slider.html'

    # --- API Key Handling ---
    stadia_api_key_from_file = None
    if os.path.exists(API_KEY_FILENAME):
        print(f"Attempting to read Stadia API key from '{API_KEY_FILENAME}'...")
        try:
            with open(API_KEY_FILENAME, 'r') as f: stadia_api_key_from_file = f.read().strip()
            if not stadia_api_key_from_file: print(f"API key file '{API_KEY_FILENAME}' found but is empty."); stadia_api_key_from_file = None
            else: print("API key read successfully.")
        except Exception as e: print(f"Warning: Error reading API key file '{API_KEY_FILENAME}': {e}."); stadia_api_key_from_file = None
    else: print(f"API key file '{API_KEY_FILENAME}' not found. Stadia layer will not be available.")

    # --- File Check & Date Parsing ---
    if csv_file_path == './local_photo_locations.csv' and not os.path.exists(csv_file_path):
        print(f"INFO: Default CSV path '{csv_file_path}' not found.")
        # Optionally create dummy file here if needed for testing
        if not os.path.exists(csv_file_path):
             try:
                  dummy_data = {
                      'filepath': ['dummy/path/img1.jpg', 'dummy/path/img2.jpg', 'dummy/path/img3.jpg'],
                      'timestamp_str': ['2023-10-26 10:00:00', '2023-10-26 10:05:00', '2023-10-26 10:10:00'],
                      'latitude': [40.7128, 40.7130, 40.7135], # Example NYC coords
                      'longitude': [-74.0060, -74.0055, -74.0045]
                  }
                  pd.DataFrame(dummy_data).to_csv(csv_file_path, index=False)
                  print(f"Created dummy CSV file at '{csv_file_path}'. Please replace with your actual data.")
             except Exception as e:
                  print(f"ERROR: Could not create dummy CSV file: {e}")
                  # Don't exit, maybe user provides different path later? Or handle based on requirements.

    elif not os.path.exists(csv_file_path):
        print(f"ERROR: The specified CSV file path does not exist: {csv_file_path}"); sys.exit(1)

    start_datetime_utc = None; end_datetime_utc = None; perform_filtering = False
    if start_date_str and end_date_str:
        print("Date range provided, attempting to parse...")
        try:
            start_dt = datetime.strptime(start_date_str, '%Y-%m-%d'); end_dt = datetime.strptime(end_date_str, '%Y-%m-%d')
            start_datetime_utc = start_dt.replace(tzinfo=timezone.utc); end_datetime_utc = end_dt.replace(hour=23, minute=59, second=59, microsecond=999999, tzinfo=timezone.utc)
            perform_filtering = True; print("Date range parsed successfully (interpreted as UTC days).")
        except ValueError: print(f"Error: Invalid date format (use YYYY-MM-DD). Disabling date filtering."); perform_filtering = False
    else: print("No date range specified. Processing all data.")

    # --- Load & Filter Data ---
    try:
        all_photos_df = load_photo_locations_from_csv(csv_file_path)
        if all_photos_df is None or all_photos_df.empty: print("No valid photo location data loaded. Exiting."); sys.exit(0)

        photos_to_plot_df = None
        if perform_filtering:
            if start_datetime_utc and end_datetime_utc: photos_to_plot_df = filter_locations_dataframe(all_photos_df, start_datetime_utc, end_datetime_utc)
            else: print("Filtering requested but date parsing failed. Processing all data."); photos_to_plot_df = all_photos_df
        else: photos_to_plot_df = all_photos_df; print(f"Proceeding with all {len(photos_to_plot_df)} loaded locations.")

        if photos_to_plot_df is None or photos_to_plot_df.empty: print("No photos found for the specified criteria. No map generated.")
        else:
            # --- Create GeoDataFrame ---
            print("\nCreating GeoDataFrame from photo locations...")
            try:
                gdf_wgs84_to_plot = gpd.GeoDataFrame(
                    photos_to_plot_df,
                    geometry=gpd.points_from_xy(photos_to_plot_df['longitude'], photos_to_plot_df['latitude']),
                    crs="EPSG:4326"
                )
                gdf_wgs84_to_plot.sort_values(by='timestamp', inplace=True)
                print("GeoDataFrame created successfully and sorted by timestamp.")

                # --- Generate Interactive Map ---
                create_interactive_map(
                    gdf_wgs84=gdf_wgs84_to_plot,
                    output_html_path=output_map_filename,
                    stadia_api_key=stadia_api_key_from_file,
                    add_start_end_markers=True,
                    add_all_points=False,
                    # Use configured values:
                    default_half_life=DEFAULT_HALF_LIFE_DAYS,
                    min_half_life=MIN_HALF_LIFE_DAYS,
                    max_half_life=MAX_HALF_LIFE_DAYS,
                    slider_step=SLIDER_STEP,
                    min_opacity=MIN_LINE_OPACITY,
                    initial_skip_threshold=INITIAL_OPACITY_SKIP_THRESHOLD,
                    dynamic_off_threshold=DYNAMIC_OPACITY_OFF_THRESHOLD,
                    slider_z_index=SLIDER_Z_INDEX # Pass z-index
                )
            # Catch exceptions during map creation more specifically if needed
            except Exception as e_map: print(f"Error creating GeoDataFrame or Map: {e_map}"); import traceback; traceback.print_exc()

    except FileNotFoundError as e: print(f"\nFatal Error: {e}")
    except Exception as e: print(f"\nAn unexpected fatal error occurred in the main block: {e}"); import traceback; traceback.print_exc()
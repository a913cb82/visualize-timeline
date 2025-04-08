import pandas as pd
from datetime import datetime, timezone, timedelta
import sys
import os
import warnings

# --- Geospatial/Interactive Map Libraries ---
try:
    import geopandas as gpd
    from shapely.geometry import Point, LineString
    import folium
    import xyzservices.providers as xyz # Import xyzservices
except ImportError as e:
    print(f"ERROR: Required libraries for interactive mapping are missing: {e}")
    print("Please install geopandas, folium, and xyzservices: pip install geopandas folium xyzservices")
    sys.exit(1)

# --- Constants ---
API_KEY_FILENAME = "stadia_api_key.txt"
GOOGLE_MAPS_API_KEY_FILENAME = "google_maps_api_key.txt"  # New constant for Google Maps API key file
# No fading/slider constants needed

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
            df['timestamp'] = pd.to_datetime(df['timestamp_str'], format='%Y-%m-%d %H:%M:%S', errors='coerce')
            if df['timestamp'].isnull().any():
                 print("Trying alternative timestamp format (ISO 8601)...")
                 df['timestamp'] = df['timestamp'].fillna(pd.to_datetime(df.loc[df['timestamp'].isnull(), 'timestamp_str'], errors='coerce'))
            if df['timestamp'].dt.tz is None:
                print("Localizing timestamps to UTC (assuming original times were UTC)...")
                df['timestamp'] = df['timestamp'].dt.tz_localize(timezone.utc, ambiguous='infer', nonexistent='shift_forward')
            else:
                print("Converting timestamps to UTC...")
                df['timestamp'] = df['timestamp'].dt.tz_convert(timezone.utc)
        except Exception as e:
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
    if end_date_utc.time() == datetime.min.time():
         end_date_utc = end_date_utc.replace(hour=23, minute=59, second=59, microsecond=999999)
         print(f"Adjusted end date for filtering to include the full day: {end_date_utc.strftime('%Y-%m-%d %H:%M:%S %Z')}")
    filtered_df = df[(df['timestamp'] >= start_date_utc) & (df['timestamp'] <= end_date_utc)].copy()
    print(f"Found {len(filtered_df)} photo locations within the specified date range.")
    return filtered_df


# --- Interactive Plotting Function (Fully Simplified: No Fading/Slider) ---
def create_interactive_map(
    gdf_wgs84: gpd.GeoDataFrame,
    output_html_path: str,
    stadia_api_key: str | None,
    add_start_end_markers: bool = True,
    add_all_points: bool = False,
    line_color: str = 'blue',
    line_weight: int = 3,
    google_maps_api_key: str | None = None # Add google maps api key
):
    """
    Creates a simple interactive HTML map with a solid path line.
    Removed all fading opacity and slider controls.
    """
    if gdf_wgs84.empty: print("GeoDataFrame is empty. Cannot create map."); return
    if gdf_wgs84.crs != "EPSG:4326":
        try: gdf_wgs84 = gdf_wgs84.to_crs("EPSG:4326")
        except Exception as e: print(f"Error converting GDF to EPSG:4326: {e}. Cannot create map."); return
    gdf_wgs84 = gdf_wgs84.sort_values(by='timestamp') # Keep sorted for start/end markers

    print(f"\n--- Generating Interactive Map (SIMPLE): {os.path.basename(output_html_path)} ---")

    # --- Determine Map Center ---
    try:
        if len(gdf_wgs84) > 1: center_lat, center_lon = gdf_wgs84.union_all().centroid.y, gdf_wgs84.union_all().centroid.x
        elif len(gdf_wgs84) == 1: center_lat, center_lon = gdf_wgs84.iloc[0].geometry.y, gdf_wgs84.iloc[0].geometry.x
        else: center_lat, center_lon = 0, 0
        start_location = [center_lat, center_lon]
    except Exception as e: print(f"Warning: Could not calculate center, using default. Error: {e}"); start_location = [0, 0]

    # --- Initialize Folium Map ---
    m = folium.Map(location=start_location, zoom_start=10, tiles=None)

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

    # --- Add Google Maps Tile Layer ---
    if google_maps_api_key:
        print("Attempting to add Google Maps tile layer...")
        try:
            # Google Maps Standard
            google_maps_tile_url = f"https://mt1.google.com/vt/lyrs=m&x={{x}}&y={{y}}&z={{z}}&key={google_maps_api_key}"
            folium.TileLayer(
                tiles=google_maps_tile_url,
                attr="Google Maps",
                name="Google Maps",
                overlay=False,
                control=True,
                show=False,
            ).add_to(m)
            print("Google Maps tile layer added as an option.")
            # Google Maps Terrain
            google_maps_terrain_tile_url = f"https://mt1.google.com/vt/lyrs=p&x={{x}}&y={{y}}&z={{z}}&key={google_maps_api_key}"
            folium.TileLayer(
                tiles=google_maps_terrain_tile_url,
                attr="Google Maps Terrain",
                name="Google Maps Terrain",
                overlay=False,
                control=True,
                show=False,
            ).add_to(m)
            print("Google Maps Terrain tile layer added as an option.")
            # Google Maps Hybrid
            google_maps_hybrid_tile_url = f"https://mt1.google.com/vt/lyrs=y&x={{x}}&y={{y}}&z={{z}}&key={google_maps_api_key}"
            folium.TileLayer(
                tiles=google_maps_hybrid_tile_url,
                attr="Google Maps Hybrid",
                name="Google Maps Hybrid",
                overlay=False,
                control=True,
                show=False,
            ).add_to(m)
            print("Google Maps Hybrid tile layer added as an option.")
        except Exception as e_google:
            print(f"*** Warning: Could not add Google Maps tile layer: {e_google}")
    else:
        print("No Google Maps API key provided. Skipping Google Maps basemap.")

    # --- Add Free Satellite Layer ---
    folium.TileLayer(
        tiles=xyz.Esri.WorldImagery.build_url(),
        attr="Esri World Imagery",
        name="Esri World Imagery",
        overlay=False,
        control=True,
        show=False,
    ).add_to(m)
    print("Added Esri World Imagery satellite layer.")

    esri_ref_url = 'https://services.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}'
    folium.TileLayer(
        tiles=esri_ref_url,
        attr='Esri & contributors',
        name='Labels & Boundaries (Esri)', # Give it a descriptive name
        overlay=True,
        control=True,
        show=False,
    ).add_to(m)

    # --- Add Data: Single Solid Polyline ---
    if len(gdf_wgs84) >= 2:
        print("Adding path as a single solid PolyLine...")
        # Create coordinate pairs: [[lat1, lon1], [lat2, lon2], ...]
        locations = gdf_wgs84.geometry.apply(lambda point: [point.y, point.x]).tolist()

        # Add the PolyLine to the map - *fully opaque*
        folium.PolyLine(
            locations=locations,
            color=line_color,
            weight=line_weight,
            opacity=1.0, # Set opacity to 1.0 for fully opaque
            tooltip=f"Photo Path ({len(locations)} points)" # Simple tooltip
        ).add_to(m) # Add directly to map, no separate group needed for line
        print("PolyLine added.")
    elif len(gdf_wgs84) == 1:
         print("Only one point found. Adding a marker instead of a line.")
         add_all_points = True # Force showing the single point if option exists
         add_start_end_markers = False # No start/end distinction needed

    # --- Add Markers ---
    marker_group = folium.FeatureGroup(name="Start/End Points", show=True).add_to(m)
    if add_start_end_markers and len(gdf_wgs84) >= 1:
        start_point = gdf_wgs84.iloc[0]; end_point = gdf_wgs84.iloc[-1]
        start_popup_html = f"<b>Start Point</b><br>TS: {start_point['timestamp'].strftime('%Y-%m-%d %H:%M:%S %Z')}<br>File: {os.path.basename(start_point['filepath'])}"
        folium.Marker(location=[start_point.geometry.y, start_point.geometry.x], popup=folium.Popup(start_popup_html, max_width=300), tooltip="Start", icon=folium.Icon(color='green', icon='play')).add_to(marker_group)
        if len(gdf_wgs84) > 1:
            end_popup_html = f"<b>End Point</b><br>TS: {end_point['timestamp'].strftime('%Y-%m-%d %H:%M:%S %Z')}<br>File: {os.path.basename(end_point['filepath'])}"
            folium.Marker(location=[end_point.geometry.y, end_point.geometry.x], popup=folium.Popup(end_popup_html, max_width=300), tooltip="End", icon=folium.Icon(color='red', icon='stop')).add_to(marker_group)
        print("Start/End markers added.")

    if add_all_points:
        point_group = folium.FeatureGroup(name="All Points", show=False).add_to(m)
        print("Adding markers for all points...")
        for idx, row in gdf_wgs84.iterrows():
            point_popup_html = f"TS: {row['timestamp'].strftime('%H:%M:%S %Z')}<br>File: {os.path.basename(row['filepath'])}"
            folium.CircleMarker(location=[row.geometry.y, row.geometry.x], radius=3, color='purple', fill=True, fill_color='purple', fill_opacity=0.6, popup=folium.Popup(point_popup_html, max_width=250), tooltip=row['timestamp'].strftime('%H:%M:%S')).add_to(point_group)
        print(f"Added {len(gdf_wgs84)} point markers (grouped).")

    # --- Removed Slider/Opacity JS ---

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

def read_api_key_from_file(filename: str) -> str | None:
    """Reads an API key from a file."""
    if os.path.exists(filename):
        print(f"Attempting to read API key from '{filename}'...")
        try:
            with open(filename, 'r') as f:
                api_key = f.read().strip()
            if not api_key:
                print(f"API key file '{filename}' found but is empty.")
                return None
            else:
                print("API key read successfully.")
                return api_key
        except Exception as e:
            print(f"Warning: Error reading API key file '{filename}': {e}.")
            return None
    else:
        print(f"API key file '{filename}' not found.")
        return None

# --- Main Execution Block ---
if __name__ == "__main__":

    # --- Configuration ---
    csv_file_path = './local_photo_locations.csv'
    start_date_str = None
    end_date_str = None
    output_map_filename = 'interactive_photo_path_map_simple_opaque.html' # New simplified name

    # --- API Key Handling ---
    stadia_api_key_from_file = read_api_key_from_file(API_KEY_FILENAME)
    if not stadia_api_key_from_file:
        print("Stadia layer will not be available.")

    # --- Google Maps API Key Handling ---
    google_maps_api_key_from_file = read_api_key_from_file(GOOGLE_MAPS_API_KEY_FILENAME)
    if not google_maps_api_key_from_file:
        print("No Google Maps API key found. Google Maps layer will not be available.")

    # --- File Check & Date Parsing ---
    if csv_file_path == './local_photo_locations.csv' and not os.path.exists(csv_file_path):
        print(f"INFO: Default CSV path '{csv_file_path}' not found.")
        if not os.path.exists(csv_file_path):
             try:
                  dummy_data = {
                      'filepath': ['dummy/path/img1.jpg', 'dummy/path/img2.jpg', 'dummy/path/img3.jpg'],
                      'timestamp_str': ['2023-10-26 10:00:00', '2023-10-26 10:05:00', '2023-10-26 10:10:00'],
                      'latitude': [40.7128, 40.7130, 40.7135],
                      'longitude': [-74.0060, -74.0055, -74.0045]
                  }
                  pd.DataFrame(dummy_data).to_csv(csv_file_path, index=False)
                  print(f"Created dummy CSV file at '{csv_file_path}'. Please replace with your actual data.")
             except Exception as e: print(f"ERROR: Could not create dummy CSV file: {e}")
    elif not os.path.exists(csv_file_path): print(f"ERROR: The specified CSV file path does not exist: {csv_file_path}"); sys.exit(1)

    start_datetime_utc = None; end_datetime_utc = None; perform_filtering = False
    if start_date_str and end_date_str:
        print("Date range provided, attempting to parse...")
        try:
            start_dt = datetime.strptime(start_date_str, '%Y-%m-%d'); end_dt = datetime.strptime(end_date_str, '%Y-%m-%d')
            start_datetime_utc = start_dt.replace(tzinfo=timezone.utc); end_datetime_utc = end_dt.replace(hour=23, minute=59, second=59, microsecond=999999, tzinfo=timezone.utc)
            perform_filtering = True; print("Date range parsed successfully.")
        except ValueError: print(f"Error: Invalid date format. Disabling date filtering."); perform_filtering = False
    else: print("No date range specified. Processing all data.")

    # --- Load & Filter Data ---
    try:
        all_photos_df = load_photo_locations_from_csv(csv_file_path)
        if all_photos_df is None or all_photos_df.empty: print("No valid photo location data loaded. Exiting."); sys.exit(0)
        photos_to_plot_df = all_photos_df if not perform_filtering or not start_datetime_utc else filter_locations_dataframe(all_photos_df, start_datetime_utc, end_datetime_utc)
        if photos_to_plot_df is None or photos_to_plot_df.empty: print("No photos found for the specified criteria. No map generated.")
        else:
            # --- Create GeoDataFrame ---
            print("\nCreating GeoDataFrame...")
            try:
                gdf_wgs84_to_plot = gpd.GeoDataFrame(photos_to_plot_df, geometry=gpd.points_from_xy(photos_to_plot_df['longitude'], photos_to_plot_df['latitude']), crs="EPSG:4326")
                gdf_wgs84_to_plot.sort_values(by='timestamp', inplace=True)
                print("GeoDataFrame created successfully.")

                # --- Generate Interactive Map (SIMPLE VERSION) ---
                create_interactive_map(
                    gdf_wgs84=gdf_wgs84_to_plot,
                    output_html_path=output_map_filename,
                    stadia_api_key=stadia_api_key_from_file,
                    add_start_end_markers=True,
                    add_all_points=False,
                    google_maps_api_key=google_maps_api_key_from_file # Pass the google maps api key
                )
            except Exception as e_map: print(f"Error creating GeoDataFrame or Map: {e_map}"); import traceback; traceback.print_exc()
    except FileNotFoundError as e: print(f"\nFatal Error: {e}")
    except Exception as e: print(f"\nAn unexpected fatal error occurred: {e}"); import traceback; traceback.print_exc()

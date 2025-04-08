import pandas as pd
from datetime import datetime, timezone, timedelta # Added timedelta
import sys
import os
import warnings
import json

# --- Geospatial/Interactive Map Libraries ---
try:
    import geopandas as gpd
    from shapely.geometry import Point
    import folium
    import xyzservices.providers as xyz
except ImportError as e:
    print(f"ERROR: Required libraries for interactive mapping are missing: {e}")
    print("Please install geopandas, folium, and xyzservices: pip install geopandas folium xyzservices")
    sys.exit(1)

# --- Constants ---
TIMELINE_JSON_FILENAME = "Timeline.json"
GOOGLE_MAPS_API_KEY_FILENAME = "google_maps_api_key.txt"
API_KEY_FILENAME = "stadia_api_key.txt"
DATE_FORMAT = "%Y-%m-%d" # Define standard date format for input

# --- Functions ---

def parse_point_string(point_str: str) -> tuple[float | None, float | None]:
    """Parses 'lat°, lon°' string into float coordinates."""
    try:
        lat_str, lon_str = point_str.split('°,')
        lat = float(lat_str.strip())
        lon = float(lon_str.replace('°', '').strip())
        if -90 <= lat <= 90 and -180 <= lon <= 180:
            return lat, lon
        else:
            # Reduced verbosity for large files
            # print(f"Warning: Parsed coordinates out of bounds: lat={lat}, lon={lon}. Skipping.")
            return None, None
    except Exception:
        # Reduced verbosity
        # print(f"Warning: Could not parse point string '{point_str}': {e}. Skipping.")
        return None, None

def load_timeline_locations_from_json(file_path: str) -> pd.DataFrame | None:
    """Loads location data from Google Timeline JSON, extracts path points, and sorts by timestamp."""
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Error: Timeline JSON file not found at {file_path}")

    print(f"Loading Timeline JSON data from: {file_path}")
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            # Use streaming for potentially large files (more memory efficient)
            # Note: This requires a JSON library that supports streaming or chunking
            # For simplicity with standard json, we load all at once, but be mindful of memory
            data = json.load(f)
        print("Timeline JSON loaded successfully.")

        if 'semanticSegments' not in data or not isinstance(data['semanticSegments'], list):
            print("Error: 'semanticSegments' key not found or invalid format in the JSON file.")
            return None

        extracted_points = []
        total_segments = len(data['semanticSegments'])
        print(f"Processing {total_segments} semantic segments...")
        processed_count = 0

        for segment in data['semanticSegments']:
             processed_count += 1
             if processed_count % 5000 == 0: # Progress indicator every 5000 segments
                  print(f"  Processed {processed_count}/{total_segments} segments...")

             # Ensure segment is a dict and has timelinePath list
             if isinstance(segment, dict) and 'timelinePath' in segment and isinstance(segment['timelinePath'], list):
                 for point_data in segment['timelinePath']:
                     if isinstance(point_data, dict) and 'point' in point_data and 'time' in point_data:
                         lat, lon = parse_point_string(point_data['point'])
                         timestamp_str = point_data['time']

                         if lat is not None and lon is not None and timestamp_str:
                             extracted_points.append({
                                 'latitude': lat,
                                 'longitude': lon,
                                 'timestamp_str': timestamp_str
                             })

        print(f"Finished processing segments. Found {len(extracted_points)} raw points in timeline paths.")

        if not extracted_points:
            print("No valid timeline path points found in the JSON file.")
            return None

        df = pd.DataFrame(extracted_points)

        print("Parsing timestamps and converting to UTC...")
        try:
            df['timestamp'] = pd.to_datetime(df['timestamp_str'], errors='coerce', utc=True)
        except Exception as e:
            print(f"Error parsing timestamps from timeline JSON: {e}")
            return None # Cannot proceed without valid timestamps

        # Drop rows where timestamp parsing failed or coords are missing
        original_count = len(df)
        df.dropna(subset=['timestamp', 'latitude', 'longitude'], inplace=True)
        removed_count = original_count - len(df)
        if removed_count > 0:
            print(f"Warning: Removed {removed_count} rows due to invalid/missing timestamp or coordinates after parsing.")

        if df.empty:
            print("No valid data remaining after cleaning.")
            return None

        # Remove potential duplicates based on exact timestamp and coords BEFORE sorting
        original_count = len(df)
        df.drop_duplicates(subset=['timestamp', 'latitude', 'longitude'], keep='first', inplace=True)
        removed_duplicates = original_count - len(df)
        if removed_duplicates > 0:
             print(f"Removed {removed_duplicates} duplicate entries (same time and location).")

        # --- CRITICAL SORT ---
        # Sort primarily by timestamp, secondarily by lat/lon to ensure deterministic order for duplicates at the same microsecond (unlikely but possible)
        df.sort_values(by=['timestamp', 'latitude', 'longitude'], inplace=True)
        print(f"Timeline data prepared and definitively sorted. {len(df)} valid location entries.")
        return df[['timestamp', 'latitude', 'longitude']] # Keep only necessary columns

    except json.JSONDecodeError as e:
        print(f"Error: Failed to decode JSON file '{file_path}'. It might be corrupted. Error: {e}")
        return None
    except MemoryError:
        print(f"Error: Ran out of memory trying to load '{file_path}'. The file might be too large for available RAM.")
        return None
    except Exception as e:
        print(f"An unexpected error occurred while reading or processing the Timeline JSON: {e}")
        raise


def filter_locations_dataframe(df: pd.DataFrame, start_date_utc: datetime, end_date_utc: datetime) -> pd.DataFrame:
    """Filters DataFrame by date range (inclusive, UTC)."""
    # Ensure DataFrame timestamps are UTC (should be from loader)
    if df['timestamp'].dt.tz is None or df['timestamp'].dt.tz.utcoffset(None) != timedelta(0):
         print("Warning: Timestamps in DataFrame are not UTC. Attempting conversion...")
         try:
            df['timestamp'] = df['timestamp'].dt.tz_convert(timezone.utc)
         except Exception as e:
            raise ValueError(f"DataFrame timestamps must be UTC for filtering. Conversion failed: {e}")

    # Ensure filter dates are UTC (should be from main logic)
    if start_date_utc.tzinfo is None or start_date_utc.tzinfo.utcoffset(None) != timedelta(0):
         raise ValueError("Start date must be timezone-aware UTC for filtering.")
    if end_date_utc.tzinfo is None or end_date_utc.tzinfo.utcoffset(None) != timedelta(0):
         raise ValueError("End date must be timezone-aware UTC for filtering.")


    print(f"Filtering locations between {start_date_utc.strftime('%Y-%m-%d %H:%M:%S %Z')} and {end_date_utc.strftime('%Y-%m-%d %H:%M:%S %Z')}")

    # Filtering automatically preserves the existing sort order
    filtered_df = df[(df['timestamp'] >= start_date_utc) & (df['timestamp'] <= end_date_utc)].copy()

    print(f"Found {len(filtered_df)} locations within the specified date range.")
    return filtered_df


def create_interactive_map(
    gdf_wgs84: gpd.GeoDataFrame,
    output_html_path: str,
    stadia_api_key: str | None,
    add_start_end_markers: bool = True,
    add_all_points: bool = False,
    line_color: str = 'blue',
    line_weight: int = 3,
    google_maps_api_key: str | None = None,
):
    """Creates an interactive HTML map with a chronologically sorted path line."""
    if gdf_wgs84.empty:
        print("GeoDataFrame is empty. Cannot create map.")
        return

    if 'timestamp' not in gdf_wgs84.columns:
        print("Error: GeoDataFrame is missing the required 'timestamp' column.")
        return

    if gdf_wgs84.crs != "EPSG:4326":
        print("Warning: GeoDataFrame CRS is not EPSG:4326. Attempting conversion...")
        try:
            gdf_wgs84 = gdf_wgs84.to_crs("EPSG:4326")
            print("CRS converted successfully.")
        except Exception as e:
            print(f"Error converting GDF to EPSG:4326: {e}. Cannot create map.")
            return

    # --- ENSURE FINAL SORT before extracting coordinates for the line ---
    # Although data should arrive sorted, this is a final safeguard.
    print("Ensuring final sort of GeoDataFrame by timestamp before plotting...")
    gdf_wgs84 = gdf_wgs84.sort_values(by='timestamp')
    print("GeoDataFrame sorted.")

    print(f"\n--- Generating Interactive Map: {os.path.basename(output_html_path)} ---")

    # --- Determine Map Center ---
    center_lat, center_lon = 0, 0 # Default
    try:
        if len(gdf_wgs84) >= 1:
            bounds = gdf_wgs84.total_bounds # minx, miny, maxx, maxy
            # Check for valid bounds (sometimes can be NaN if data is bad)
            if not pd.isna(bounds).any() and bounds[0] <= bounds[2] and bounds[1] <= bounds[3]:
                 center_lon = (bounds[0] + bounds[2]) / 2
                 center_lat = (bounds[1] + bounds[3]) / 2
            elif len(gdf_wgs84) == 1: # Fallback for single point if bounds invalid
                 center_lat = gdf_wgs84.iloc[0].geometry.y
                 center_lon = gdf_wgs84.iloc[0].geometry.x
                 print("Warning: Could not calculate valid bounds, centering on the single point.")
            else: # Fallback if bounds are invalid for multiple points
                 # Calculate centroid of the first few points as a guess
                 centroid = gdf_wgs84.head(10).unary_union.centroid
                 center_lat, center_lon = centroid.y, centroid.x
                 print("Warning: Could not calculate valid bounds from all points, centering on centroid of first 10 points.")
        start_location = [center_lat, center_lon]
    except Exception as e:
        print(f"Warning: Could not calculate center, using default [0, 0]. Error: {e}")
        start_location = [center_lat, center_lon] # Use default 0,0

    # --- Initialize Folium Map ---
    m = folium.Map(location=start_location, zoom_start=10, tiles=None)

    # --- Add Basemap Layers (Code unchanged) ---
    folium.TileLayer('OpenStreetMap', name='OpenStreetMap', attr='© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors', overlay=False, control=True, show=True).add_to(m)
    print("Added OpenStreetMap basemap.")
    otm_attribution = 'Map data: © <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors, <a href="http://viewfinderpanoramas.org">SRTM</a> | Map style: © <a href="https://opentopomap.org">OpenTopoMap</a> (<a href="https://creativecommons.org/licenses/by-sa/3.0/">CC-BY-SA</a>)'
    folium.TileLayer(tiles='https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png', attr=otm_attribution, name='OpenTopoMap', overlay=False, control=True, show=False).add_to(m)
    print("Added OpenTopoMap basemap.")
    if stadia_api_key:
        print("Attempting to add Stadia Stamen Terrain basemap...")
        stadia_attribution = "© Stadia Maps, © OpenMapTiles © OpenStreetMap contributors"
        stadia_tiles_url = f"https://tiles.stadiamaps.com/tiles/stamen_terrain/{{z}}/{{x}}/{{y}}.png?api_key={stadia_api_key}"
        try:
            folium.TileLayer(tiles=stadia_tiles_url, attr=stadia_attribution, name='Stadia Stamen Terrain', overlay=False, control=True, show=False).add_to(m)
            print("Stadia Stamen Terrain added as an option.")
        except Exception as e_stadia:
            print(f"*** Warning: Could not add Stadia Stamen Terrain layer: {e_stadia}")
    else:
        print("No Stadia API key provided. Skipping Stadia basemap.")
    if google_maps_api_key:
        print("Attempting to add Google Maps tile layer...")
        try:
            google_maps_tile_url = f"https://mt1.google.com/vt/lyrs=m&x={{x}}&y={{y}}&z={{z}}&key={google_maps_api_key}"
            folium.TileLayer(tiles=google_maps_tile_url, attr="Google Maps", name="Google Maps", overlay=False, control=True, show=False).add_to(m)
            print("Google Maps tile layer added as an option.")
            google_maps_terrain_tile_url = f"https://mt1.google.com/vt/lyrs=p&x={{x}}&y={{y}}&z={{z}}&key={google_maps_api_key}"
            folium.TileLayer(tiles=google_maps_terrain_tile_url, attr="Google Maps Terrain", name="Google Maps Terrain", overlay=False, control=True, show=False).add_to(m)
            print("Google Maps Terrain tile layer added as an option.")
            google_maps_hybrid_tile_url = f"https://mt1.google.com/vt/lyrs=y&x={{x}}&y={{y}}&z={{z}}&key={google_maps_api_key}"
            folium.TileLayer(tiles=google_maps_hybrid_tile_url, attr="Google Maps Hybrid", name="Google Maps Hybrid", overlay=False, control=True, show=False).add_to(m)
            print("Google Maps Hybrid tile layer added as an option.")
        except Exception as e_google:
            print(f"*** Warning: Could not add Google Maps tile layer: {e_google}")
    else:
        print("No Google Maps API key provided. Skipping Google Maps basemap.")
    folium.TileLayer(tiles=xyz.Esri.NatGeoWorldMap.build_url(), attr="Esri NatGeo", name="Esri NatGeo", overlay=False, control=True, show=False).add_to(m)
    print("Added Esri NatGeo layer.")
    folium.TileLayer(tiles=xyz.Esri.WorldImagery.build_url(), attr="Esri World Imagery", name="Esri World Imagery", overlay=False, control=True, show=False).add_to(m)
    print("Added Esri World Imagery satellite layer.")
    esri_ref_url = 'https://services.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}'
    folium.TileLayer(tiles=esri_ref_url, attr='Esri & contributors', name='Labels & Boundaries (Esri)', overlay=True, control=True, show=False).add_to(m)
    # --- End Basemaps ---


    # --- Add Data: Single Solid Polyline ---
    if len(gdf_wgs84) >= 2:
        print("Adding path as a single solid PolyLine (using sorted data)...")
        # Extract coordinates *after* the final sort
        locations = gdf_wgs84.geometry.apply(lambda point: [point.y, point.x]).tolist()
        folium.PolyLine(
             locations=locations,
             color=line_color,
             weight=line_weight,
             opacity=0.8,
             tooltip=f"Timeline Path ({len(locations)} points)"
        ).add_to(m)
        print("PolyLine added.")
    elif len(gdf_wgs84) == 1:
        print("Only one point found. Adding a marker instead of a line.")
        add_all_points = True
        add_start_end_markers = False

    # --- Add Markers (Code mostly unchanged, relies on sorted GDF) ---
    if add_start_end_markers and len(gdf_wgs84) >= 1:
        marker_group = folium.FeatureGroup(name="Start/End Points", show=True).add_to(m)
        start_point = gdf_wgs84.iloc[0]
        end_point = gdf_wgs84.iloc[-1]
        start_popup_html = f"<b>Start Point</b><br>TS: {start_point['timestamp'].strftime('%Y-%m-%d %H:%M:%S %Z')}<br>Lat: {start_point.geometry.y:.6f}<br>Lon: {start_point.geometry.x:.6f}"
        folium.Marker(location=[start_point.geometry.y, start_point.geometry.x], popup=folium.Popup(start_popup_html, max_width=300), tooltip="Start", icon=folium.Icon(color='green', icon='play')).add_to(marker_group)
        if len(gdf_wgs84) > 1:
            end_popup_html = f"<b>End Point</b><br>TS: {end_point['timestamp'].strftime('%Y-%m-%d %H:%M:%S %Z')}<br>Lat: {end_point.geometry.y:.6f}<br>Lon: {end_point.geometry.x:.6f}"
            folium.Marker(location=[end_point.geometry.y, end_point.geometry.x], popup=folium.Popup(end_popup_html, max_width=300), tooltip="End", icon=folium.Icon(color='red', icon='stop')).add_to(marker_group)
        print("Start/End markers added.")

    if add_all_points:
        point_group = folium.FeatureGroup(name="All Points", show=False).add_to(m)
        print("Adding markers for all points (initially hidden)...")
        # Iterating over the already sorted GeoDataFrame
        for idx, row in gdf_wgs84.iterrows():
            point_popup_html = f"TS: {row['timestamp'].strftime('%Y-%m-%d %H:%M:%S %Z')}<br>Lat: {row.geometry.y:.6f}<br>Lon: {row.geometry.x:.6f}"
            folium.CircleMarker(
                location=[row.geometry.y, row.geometry.x],
                radius=3, color='purple', fill=True, fill_color='purple', fill_opacity=0.6,
                popup=folium.Popup(point_popup_html, max_width=250),
                tooltip=row['timestamp'].strftime('%Y-%m-%d %H:%M:%S %Z')
            ).add_to(point_group)
        print(f"Added {len(gdf_wgs84)} point markers (grouped).")

    # --- Fit map bounds ---
    if not gdf_wgs84.empty and len(gdf_wgs84) > 0: # Check length again after potential filtering
        try:
            bounds_coords = [[gdf_wgs84.total_bounds[1], gdf_wgs84.total_bounds[0]], [gdf_wgs84.total_bounds[3], gdf_wgs84.total_bounds[2]]]
            # Check if bounds are valid numbers before fitting
            if all(isinstance(coord, (int, float)) for sublist in bounds_coords for coord in sublist):
                 m.fit_bounds(bounds_coords, padding=(0.01, 0.01))
                 print("Map bounds fitted to data.")
            else:
                 print("Warning: Invalid bounds calculated, skipping map fitting.")
        except IndexError:
             print("Warning: Could not calculate bounds (IndexError), skipping map fitting.")
        except Exception as e:
            print(f"Warning: Could not fit map bounds. Error: {e}")

    # --- Add Layer Control ---
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
    # (Function unchanged)
    if os.path.exists(filename):
        # print(f"Attempting to read API key from '{filename}'...") # Less verbose
        try:
            with open(filename, 'r') as f:
                api_key = f.read().strip()
            if not api_key:
                print(f"API key file '{filename}' found but is empty.")
                return None
            else:
                # print("API key read successfully.") # Less verbose
                return api_key
        except Exception as e:
            print(f"Warning: Error reading API key file '{filename}': {e}.")
            return None
    else:
        # print(f"API key file '{filename}' not found.") # Less verbose
        return None

def get_date_input(prompt_message: str) -> datetime | None:
    """Prompts user for a date and validates it."""
    while True:
        date_str = input(f"{prompt_message} (YYYY-MM-DD, or press Enter to skip): ").strip()
        if not date_str:
            return None # User skipped
        try:
            # Parse as naive datetime first
            dt_naive = datetime.strptime(date_str, DATE_FORMAT)
            # Assume local timezone and convert to aware datetime - IMPORTANT for consistent UTC comparison later
            # This uses the system's local timezone setting.
            dt_local_aware = dt_naive.astimezone()
            print(f"Parsed '{date_str}' as {dt_local_aware.tzinfo} timezone.")
            return dt_local_aware
        except ValueError:
            print(f"Error: Invalid date format. Please use YYYY-MM-DD.")
        except Exception as e:
             print(f"An unexpected error occurred during date input: {e}")
             # Optionally return None or re-prompt depending on desired strictness


# --- Main Execution Block ---
if __name__ == "__main__":
    print("--- Timeline Map Generator ---")

    # --- Configuration ---
    timeline_json_path = TIMELINE_JSON_FILENAME
    output_map_filename = 'interactive_timeline_path_map.html'
    add_individual_points_to_map = False # Usually False for large timelines

    # --- API Key Handling ---
    print("\nChecking for API keys...")
    stadia_api_key_from_file = read_api_key_from_file(API_KEY_FILENAME)
    if stadia_api_key_from_file:
        print(" - Stadia API key found.")
    else:
        print(" - Stadia API key not found or empty. Stadia layer unavailable.")

    google_maps_api_key_from_file = read_api_key_from_file(GOOGLE_MAPS_API_KEY_FILENAME)
    if google_maps_api_key_from_file:
        print(" - Google Maps API key found.")
    else:
        print(" - Google Maps API key not found or empty. Google Maps layers unavailable.")

    # --- File Check ---
    if not os.path.exists(timeline_json_path):
        print(f"\nERROR: The required Timeline JSON file was not found at: {timeline_json_path}")
        print("Please download your Location History as JSON from Google Takeout.")
        print(f"Ensure the file is named '{TIMELINE_JSON_FILENAME}' and placed in the same directory as this script.")
        sys.exit(1)
    print(f"\nFound Timeline file: {timeline_json_path}")

    # --- Get Date Range from User ---
    print("\nEnter Date Range (optional):")
    start_dt_local = get_date_input("Start date")
    end_dt_local = get_date_input("End date")

    start_datetime_utc = None
    end_datetime_utc = None
    perform_filtering = False

    if start_dt_local and end_dt_local:
        if end_dt_local < start_dt_local:
            print("Warning: End date is before start date. Swapping them.")
            start_dt_local, end_dt_local = end_dt_local, start_dt_local

        # Convert local aware datetimes to UTC
        # Start of the day for start_date
        start_datetime_utc = start_dt_local.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        # End of the day for end_date
        end_datetime_utc = end_dt_local.astimezone(timezone.utc).replace(hour=23, minute=59, second=59, microsecond=999999)

        print(f"\nFiltering data from {start_datetime_utc.strftime(DATE_FORMAT + ' %H:%M:%S %Z')} to {end_datetime_utc.strftime(DATE_FORMAT + ' %H:%M:%S %Z')}")
        perform_filtering = True
    elif start_dt_local or end_dt_local:
         print("\nWarning: Only one date provided. Filtering requires both start and end dates. Processing all data.")
         perform_filtering = False
    else:
        print("\nNo date range specified. Processing all data from the timeline.")
        perform_filtering = False

    # --- Load & Filter Data ---
    try:
        all_locations_df = load_timeline_locations_from_json(timeline_json_path)

        if all_locations_df is None or all_locations_df.empty:
            print("\nNo valid location data loaded from Timeline JSON. Exiting.")
            sys.exit(0) # Use exit code 0 for graceful exit on no data

        # Filter if dates were provided and parsed correctly
        if perform_filtering and start_datetime_utc and end_datetime_utc:
             locations_to_plot_df = filter_locations_dataframe(all_locations_df, start_datetime_utc, end_datetime_utc)
        else:
             locations_to_plot_df = all_locations_df # Use all data if no filter

        if locations_to_plot_df is None or locations_to_plot_df.empty:
            print("\nNo locations found for the specified criteria (or file was empty after cleaning). No map generated.")
            sys.exit(0)
        else:
            # --- Create GeoDataFrame ---
            print("\nCreating GeoDataFrame from filtered/loaded data...")
            try:
                if not {'latitude', 'longitude', 'timestamp'}.issubset(locations_to_plot_df.columns):
                     raise ValueError("DataFrame is missing required columns: latitude, longitude, timestamp")

                gdf_wgs84_to_plot = gpd.GeoDataFrame(
                    locations_to_plot_df,
                    geometry=gpd.points_from_xy(locations_to_plot_df['longitude'], locations_to_plot_df['latitude']),
                    crs="EPSG:4326" # Source data is WGS84
                )
                # Note: Sorting now happens reliably inside create_interactive_map
                print(f"GeoDataFrame created with {len(gdf_wgs84_to_plot)} points.")

                # --- Generate Interactive Map ---
                create_interactive_map(
                    gdf_wgs84=gdf_wgs84_to_plot, # Pass the potentially filtered data
                    output_html_path=output_map_filename,
                    stadia_api_key=stadia_api_key_from_file,
                    add_start_end_markers=True,
                    add_all_points=add_individual_points_to_map,
                    google_maps_api_key=google_maps_api_key_from_file
                )
            except gpd.pd.errors.EmptyDataError: # Specific error from geopandas
                print("Error: Cannot create GeoDataFrame from empty data after filtering.")
            except Exception as e_map:
                print(f"\nError creating GeoDataFrame or Map: {e_map}")
                import traceback
                traceback.print_exc()

    except FileNotFoundError as e:
        # Already handled above, but catch again just in case
        print(f"\nFatal Error: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\nAn unexpected fatal error occurred during loading or processing: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

    print("\n--- Script Finished ---")
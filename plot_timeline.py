import pandas as pd
from datetime import datetime, timezone, timedelta
import sys
import os
import warnings

# --- Plotting/Geospatial Libraries ---
try:
    import geopandas as gpd
    import matplotlib.pyplot as plt
    # import matplotlib # Optional: for matplotlib.use('Agg')
    # matplotlib.use('Agg') # Use if running without a display
    import contextily as ctx
    from shapely.geometry import LineString
    import requests # Potentially needed by contextily or for custom requests later
    import aiohttp # Potentially needed by contextily async operations
    import pyproj # Kept for consistency, though gpd handles projections
except ImportError:
    print("ERROR: Required libraries...") # Keep full error message
    sys.exit(1)

# warnings.filterwarnings("ignore", category=FutureWarning)

# --- Constants ---
API_KEY_FILENAME = "stadia_api_key.txt" # File to store the Stadia Maps API key

# --- Functions (load_csv, filter_locations) ---
# (These functions remain unchanged from the previous working version)
def load_photo_locations_from_csv(file_path: str) -> pd.DataFrame | None:
    """Loads photo location data from CSV, parses timestamps (UTC), sorts by timestamp."""
    # ... (Existing code) ...
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
            df['timestamp'] = df['timestamp'].dt.tz_localize(timezone.utc)
        except Exception as e:
             print(f"Error parsing timestamps: {e}. Ensure format is 'YYYY-MM-DD HH:MM:SS'.")
             return None
        df['latitude'] = pd.to_numeric(df['latitude'], errors='coerce')
        df['longitude'] = pd.to_numeric(df['longitude'], errors='coerce')
        original_count = len(df)
        df.dropna(subset=['timestamp', 'latitude', 'longitude'], inplace=True)
        removed_count = original_count - len(df)
        if removed_count > 0:
            print(f"Warning: Removed {removed_count} rows due to invalid timestamp or coordinates.")
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
    """Filters DataFrame by date range."""
    # ... (Existing code) ...
    start_date_utc = start_date.astimezone(timezone.utc)
    end_date_utc = end_date.astimezone(timezone.utc)
    print(f"Filtering locations between {start_date_utc.strftime('%Y-%m-%d %H:%M:%S %Z')} and {end_date_utc.strftime('%Y-%m-%d %H:%M:%S %Z')}")
    filtered_df = df[(df['timestamp'] >= start_date_utc) & (df['timestamp'] <= end_date_utc)].copy()
    print(f"Found {len(filtered_df)} photo locations within the specified date range.")
    return filtered_df

# --- Plotting Function (Modified to accept explicit zoom) ---
def plot_locations_on_map(
    gdf_wgs84: gpd.GeoDataFrame, # Pass GeoDataFrame directly
    output_image_path: str,
    use_stadia: bool,
    stadia_api_key: str | None,
    zoom_level: int, # Explicit zoom level 'z'
    fixed_bounds_proj: tuple | None = None, # Optional: Pre-calculated bounds in EPSG:3857
    padding_factor: float = 0.1, # Used only if fixed_bounds_proj is None
    outline_width_factor: float = 2.0
    ):
    """
    Plots locations on map using a *fixed zoom level*. Calculates bounds if not provided.
    Uses user-chosen basemap. Saves square image. Plots line with outline.

    Args:
        gdf_wgs84: GeoDataFrame with location data (must be in EPSG:4326).
        output_image_path: Path to save the map image.
        use_stadia: Boolean indicating if Stadia Stamen Terrain should be attempted.
        stadia_api_key: The Stadia Maps API key (required if use_stadia is True).
        zoom_level: The explicit tile zoom level (z) to force contextily to use.
        fixed_bounds_proj: Optional tuple (minx, miny, maxx, maxy) in EPSG:3857.
                           If provided, these bounds are used directly.
                           If None, bounds are calculated from gdf_wgs84 + padding_factor.
        padding_factor: Used ONLY if fixed_bounds_proj is None.
        outline_width_factor: Multiplier for inner line width to get outline width.
    """
    if gdf_wgs84.empty: print("GeoDataFrame is empty. No locations to plot."); return
    plot_as_points = len(gdf_wgs84) < 2
    web_mercator_crs = "EPSG:3857"
    print(f"\n--- Generating Map: {os.path.basename(output_image_path)} (Zoom Level: {zoom_level}) ---")

    # --- Determine Line Geometry (needed for bounds if fixed_bounds not provided) ---
    gdf_line_wgs84 = None
    data_geom_for_bounds_wgs84 = gdf_wgs84 # Default to points for bounds calculation
    if not plot_as_points:
        try:
            coordinates = list(zip(gdf_wgs84.geometry.x, gdf_wgs84.geometry.y)); line = LineString(coordinates)
            gdf_line_wgs84 = gpd.GeoDataFrame([1], geometry=[line], crs=gdf_wgs84.crs)
            data_geom_for_bounds_wgs84 = gdf_line_wgs84 # Use line if possible
        except Exception as e: print(f"Error creating LineString: {e}. Plotting points."); plot_as_points = True

    # --- Calculate or Use Fixed Bounds ---
    if fixed_bounds_proj:
        print("Using pre-calculated fixed map bounds.")
        padded_minx_m, padded_miny_m, padded_maxx_m, padded_maxy_m = fixed_bounds_proj
    else:
        # Calculate bounds if not provided (using padding_factor)
        print(f"Projecting geometry to {web_mercator_crs} for bounds calculation...")
        try: data_geom_proj = data_geom_for_bounds_wgs84.to_crs(web_mercator_crs)
        except Exception as e: print(f"Error projecting geometry: {e}"); return

        print("Calculating padded, square map extent in projected coordinates...")
        minx_m, miny_m, maxx_m, maxy_m = data_geom_proj.total_bounds; x_range_m = maxx_m - minx_m if maxx_m > minx_m else 100.0; y_range_m = maxy_m - miny_m if maxy_m > miny_m else 100.0
        x_padding_m = x_range_m * padding_factor; y_padding_m = y_range_m * padding_factor
        padded_minx_m = minx_m - x_padding_m; padded_maxx_m = maxx_m + x_padding_m; padded_miny_m = miny_m - y_padding_m; padded_maxy_m = maxy_m + y_padding_m
        padded_x_range_m = padded_maxx_m - padded_minx_m; padded_y_range_m = padded_maxy_m - padded_miny_m
        if padded_x_range_m > padded_y_range_m: diff_m = padded_x_range_m - padded_y_range_m; padded_miny_m -= diff_m / 2; padded_maxy_m += diff_m / 2
        elif padded_y_range_m > padded_x_range_m: diff_m = padded_y_range_m - padded_x_range_m; padded_minx_m -= diff_m / 2; padded_maxx_m += diff_m / 2

    print(f"Map bounds set (meters): X=[{padded_minx_m:.1f}, {padded_maxx_m:.1f}], Y=[{padded_miny_m:.1f}, {padded_maxy_m:.1f}]")

    # --- Plotting Setup ---
    print("Starting plot generation...")
    fig, ax = plt.subplots(1, 1, figsize=(10, 10))
    # Set the final calculated/provided bounds
    ax.set_xlim(padded_minx_m, padded_maxx_m)
    ax.set_ylim(padded_miny_m, padded_maxy_m)

    # --- Plot Data (Points or Line with Outline) ---
    if plot_as_points:
        print("Plotting individual points (projected).")
        gdf_wgs84.to_crs(web_mercator_crs).plot(ax=ax, marker='o', color='blue', markersize=15, alpha=0.6) # Slightly smaller points maybe
    else:
        print("Plotting path as a line (projected, with outline)...")
        inner_line_width = 2.0; outline_line_width = inner_line_width * outline_width_factor
        # Plot outline then inner line
        gdf_line_wgs84.to_crs(web_mercator_crs).plot(ax=ax, color='white', linewidth=outline_line_width, alpha=0.8)
        gdf_line_wgs84.to_crs(web_mercator_crs).plot(ax=ax, color='black', linewidth=inner_line_width, alpha=1.0)

    # --- Add Basemap ---
    basemap_added = False; stamen_terrain_url_with_key = None; stamen_attribution = None
    if use_stadia and stadia_api_key:
        try:
            stadia_stamen_terrain_base_template = "https://tiles.stadiamaps.com/tiles/stamen_terrain/{z}/{x}/{y}.png"
            # print("Using hardcoded Stadia Stamen Terrain URL template.") # Less verbose
            stamen_attribution = ctx.providers.Stadia.StamenTerrain.attribution
            stamen_terrain_url_with_key = f"{stadia_stamen_terrain_base_template}?api_key={stadia_api_key}"
        except AttributeError: print("*** Warning: Could not find Stadia provider definition in contextily.") ; stamen_terrain_url_with_key = None
        except Exception as e_url: print(f"*** Warning: Failed during Stadia URL setup: {e_url}"); stamen_terrain_url_with_key = None

        if stamen_terrain_url_with_key and stamen_attribution:
            try:
                print(f"Attempting to add Stadia Stamen Terrain basemap (Zoom: {zoom_level})...")
                with warnings.catch_warnings(): warnings.simplefilter("ignore", UserWarning)
                ctx.add_basemap( ax, crs=web_mercator_crs,
                                 source=stamen_terrain_url_with_key,
                                 zoom=zoom_level, # <<< FORCE ZOOM LEVEL
                                 attribution=stamen_attribution, attribution_size=5)
                print("Stadia Stamen Terrain basemap added."); basemap_added = True
            except Exception as e_stamen:
                print(f"*** Warning: Could not add Stadia Stamen Terrain basemap. Error: {e_stamen}")
                if "401" in str(e_stamen): print("--> Authorization Failed (401). Check API key.") # etc...
    # Fallback if needed
    if not basemap_added:
        print(f"Using fallback basemap (OpenTopoMap, Zoom: {zoom_level})...")
        try:
            ctx.add_basemap(ax, crs=web_mercator_crs,
                            source=ctx.providers.OpenTopoMap,
                            zoom=zoom_level, # <<< FORCE ZOOM LEVEL
                            attribution_size=5)
            print("OpenTopoMap basemap added."); basemap_added = True
        except Exception as e_otm: print(f"*** Warning: Could not add fallback OpenTopoMap basemap. Error: {e_otm}")

    # --- Finalize & Save ---
    ax.set_axis_off(); plt.subplots_adjust(top=1, bottom=0, right=1, left=0, hspace=0, wspace=0); plt.margins(0, 0)
    print(f"Saving map to: {output_image_path}...")
    try: plt.savefig(output_image_path, dpi=300, bbox_inches='tight', pad_inches=0); print(f"Map saved successfully: {output_image_path}")
    except Exception as e: print(f"Error saving map image: {e}")
    plt.close(fig)

# --- Main Execution Block ---
if __name__ == "__main__":

    # --- Configuration ---
    csv_file_path = './local_photo_locations.csv'
    start_date_str = None; end_date_str = None
    output_map_base_filename = 'photo_path_map_z' # Base name includes 'z' now
    # Define EXPLICIT zoom levels 'z' to generate
    # Adjust these based on your data's typical geographic spread
    # (e.g., city walk vs cross-country drive)
    ZOOM_LEVELS = [1,2,3,4,5,6,7] # Example zoom levels
    # Define a FIXED padding factor to calculate the consistent geographic extent
    EXTENT_PADDING_FACTOR = 0.2 # Use 10% padding around data for the view extent
    # --- End Configuration ---

    # --- User Input & API Key Handling ---
    # ... (Keep this section unchanged) ...
    use_stadia_input = input(f"Use Stadia Stamen Terrain basemap (requires API key in '{API_KEY_FILENAME}')? [y/N]: ")
    use_stadia = use_stadia_input.lower().startswith('y')
    stadia_api_key_from_file = None
    if use_stadia:
        print(f"Attempting to read Stadia API key from '{API_KEY_FILENAME}'...")
        try:
            with open(API_KEY_FILENAME, 'r') as f: stadia_api_key_from_file = f.read().strip()
            if not stadia_api_key_from_file: print(f"*** Warning: API key file '{API_KEY_FILENAME}' found but is empty. Using fallback."); use_stadia = False
            else: print("API key successfully read.")
        except FileNotFoundError: print(f"*** Warning: API key file '{API_KEY_FILENAME}' not found. Using fallback."); use_stadia = False
    else: print("Stadia Stamen Terrain not selected. Using fallback.")


    # --- File Check & Date Parsing ---
    # ... (Keep this section unchanged) ...
    if csv_file_path == './local_photo_locations.csv' and not os.path.exists(csv_file_path): print(f"INFO: Using default CSV path '{csv_file_path}'. Ensure file exists.")
    elif not os.path.exists(csv_file_path): print(f"ERROR: The specified CSV file path does not exist: {csv_file_path}"); sys.exit(1)
    start_datetime_utc = None; end_datetime_utc = None; perform_filtering = False
    if start_date_str and end_date_str:
        print("Date range provided, attempting to parse...")
        try:
            start_datetime_utc = datetime.strptime(start_date_str, '%Y-%m-%d').replace(tzinfo=timezone.utc)
            end_datetime_utc = datetime.strptime(end_date_str, '%Y-%m-%d').replace(hour=23, minute=59, second=59, microsecond=999999, tzinfo=timezone.utc)
            perform_filtering = True; print("Date range parsed successfully.")
        except ValueError: print(f"Error: Invalid date format. Disabling date filtering."); perform_filtering = False
    else: print("No date range specified. Processing all data.")

    # --- Load & Filter Data ---
    try:
        all_photos_df = load_photo_locations_from_csv(csv_file_path)
        if all_photos_df is None or all_photos_df.empty:
            print("No valid photo location data loaded after cleaning/parsing. Exiting.")
            sys.exit(0)

        if perform_filtering:
            photos_to_plot_df = filter_locations_dataframe(all_photos_df, start_datetime_utc, end_datetime_utc)
        else:
            photos_to_plot_df = all_photos_df
            print(f"Proceeding with all {len(photos_to_plot_df)} loaded locations.")

        if photos_to_plot_df.empty:
             print("No photos found in the specified date range (if any). No maps generated.")
        else:
            # --- Calculate Fixed Bounds ONCE ---
            print("\nCalculating fixed geographic extent for all zoom levels...")
            gdf_wgs84_to_plot = gpd.GeoDataFrame(
                photos_to_plot_df, # Use the (potentially filtered) data
                geometry=gpd.points_from_xy(photos_to_plot_df['longitude'], photos_to_plot_df['latitude']),
                crs="EPSG:4326"
            )
            # Determine geometry for bounds (line preferred)
            gdf_line_wgs84_bounds = None; plot_as_points_bounds = len(gdf_wgs84_to_plot) < 2
            data_geom_for_bounds_wgs84_plot = gdf_wgs84_to_plot
            if not plot_as_points_bounds:
                 try:
                      coordinates = list(zip(gdf_wgs84_to_plot.geometry.x, gdf_wgs84_to_plot.geometry.y)); line = LineString(coordinates)
                      gdf_line_wgs84_bounds = gpd.GeoDataFrame([1], geometry=[line], crs=gdf_wgs84_to_plot.crs)
                      data_geom_for_bounds_wgs84_plot = gdf_line_wgs84_bounds
                 except Exception: plot_as_points_bounds = True # Fallback if line fails

            # Project and calculate padded, square bounds in meters
            data_geom_proj_bounds = data_geom_for_bounds_wgs84_plot.to_crs("EPSG:3857")
            minx_m, miny_m, maxx_m, maxy_m = data_geom_proj_bounds.total_bounds
            x_range_m = maxx_m - minx_m if maxx_m > minx_m else 100.0; y_range_m = maxy_m - miny_m if maxy_m > miny_m else 100.0
            x_padding_m = x_range_m * EXTENT_PADDING_FACTOR; y_padding_m = y_range_m * EXTENT_PADDING_FACTOR # Use fixed padding
            padded_minx_m = minx_m - x_padding_m; padded_maxx_m = maxx_m + x_padding_m
            padded_miny_m = miny_m - y_padding_m; padded_maxy_m = maxy_m + y_padding_m
            padded_x_range_m = padded_maxx_m - padded_minx_m; padded_y_range_m = padded_maxy_m - padded_miny_m
            if padded_x_range_m > padded_y_range_m: diff_m = padded_x_range_m - padded_y_range_m; padded_miny_m -= diff_m / 2; padded_maxy_m += diff_m / 2
            elif padded_y_range_m > padded_x_range_m: diff_m = padded_y_range_m - padded_x_range_m; padded_minx_m -= diff_m / 2; padded_maxx_m += diff_m / 2
            fixed_plot_bounds_proj = (padded_minx_m, padded_miny_m, padded_maxx_m, padded_maxy_m)
            print(f"Fixed extent calculated (meters): X=[{padded_minx_m:.1f}, {padded_maxx_m:.1f}], Y=[{padded_miny_m:.1f}, {padded_maxy_m:.1f}]")

            # --- Loop Through Explicit Zoom Levels and Plot ---
            print(f"\nGenerating maps for zoom levels: {ZOOM_LEVELS}...")
            for z in ZOOM_LEVELS:
                 output_filename = f"{output_map_base_filename}_z{z}.png" # Filename includes z level

                 plot_locations_on_map(
                     gdf_wgs84_to_plot, # Pass the GeoDataFrame of data to plot
                     output_filename,
                     use_stadia=use_stadia,
                     stadia_api_key=stadia_api_key_from_file,
                     zoom_level=z, # <<< Pass the explicit zoom level
                     fixed_bounds_proj=fixed_plot_bounds_proj # <<< Pass the pre-calculated bounds
                     # padding_factor is now ignored by the function when fixed_bounds_proj is set
                 )
            print("\nFinished generating all maps.")

    except FileNotFoundError as e: print(f"\nFatal Error: {e}")
    except Exception as e: print(f"\nAn unexpected fatal error occurred: {e}"); import traceback; traceback.print_exc()
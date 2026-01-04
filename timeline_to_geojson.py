import pandas as pd
from datetime import datetime, timezone, timedelta
import sys
import os
import warnings
import json
from geopy.distance import geodesic # Import for distance calculation
from dotenv import load_dotenv
from tqdm import tqdm

# --- Constants ---
TIMELINE_JSON_FILENAME = "Timeline.json"
GEOJSON_OUTPUT_FILENAME = "timeline_data.geojson"
CONFIG_JS_FILENAME = "config.js"

# --- Filtering Thresholds ---
MAX_SPEED_KMH = 200               # Maximum realistic speed between points
GLITCH_LOOKAHEAD_DAYS = 1         # Temporal window to find a recovery point
EXCURSION_MIN_DIST_KM = 100       # Minimum distance for an excursion to be suspicious
EXCURSION_MAX_SPEED_KMH = 100     # "High-ish" speed threshold for excursions
EXCURSION_MAX_RADIUS_KM = 2       # Max internal spread for a cluster to be "tight"

# --- Simplification Thresholds ---
MIN_TIME_DIFFERENCE_SECONDS = 3600  # Ignore points closer than 3600 seconds AND...
MIN_DISTANCE_METERS = 50          # ... closer than 50 meters to the previous kept point

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
            return None, None
    except Exception:
        return None, None

def load_timeline_locations_from_json(file_path: str) -> pd.DataFrame | None:
    """Loads location data from Google Timeline JSON, extracts path points, and sorts by timestamp."""
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Error: Timeline JSON file not found at {file_path}")

    print(f"Loading Timeline JSON data from: {file_path}")
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        print("Timeline JSON loaded successfully.")

        if 'semanticSegments' not in data or not isinstance(data['semanticSegments'], list):
            print("Error: 'semanticSegments' key not found or invalid format in the JSON file.")
            return None

        extracted_points = []
        for segment in tqdm(data['semanticSegments'], desc="Processing semantic segments", unit="seg"):
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
            return None

        original_count = len(df)
        df.dropna(subset=['timestamp', 'latitude', 'longitude'], inplace=True)
        removed_count = original_count - len(df)
        if removed_count > 0:
            print(f"Warning: Removed {removed_count} rows due to invalid/missing timestamp or coordinates after parsing.")

        if df.empty:
            print("No valid data remaining after cleaning.")
            return None

        # Sort primarily by timestamp
        df.sort_values(by='timestamp', inplace=True)
        df.reset_index(drop=True, inplace=True)

        original_count = len(df)
        # Drop exact duplicates
        df.drop_duplicates(subset=['timestamp', 'latitude', 'longitude'], keep='first', inplace=True)
        removed_duplicates = original_count - len(df)
        if removed_duplicates > 0:
             print(f"Removed {removed_duplicates} exact duplicate entries (same time and location).")

        return df[['timestamp', 'latitude', 'longitude', 'timestamp_str']]

    except json.JSONDecodeError as e:
        print(f"Error: Failed to decode JSON file '{file_path}'. It might be corrupted. Error: {e}")
        return None
    except MemoryError:
        print(f"Error: Ran out of memory trying to load '{file_path}'. The file might be too large.")
        return None
    except Exception as e:
        print(f"An unexpected error occurred while reading or processing the Timeline JSON: {e}")
        raise

def is_cluster_tight(df_subset: pd.DataFrame, max_radius_km: float) -> bool:
    """Checks if all points in a subset are within a certain radius of the first point."""
    if df_subset.empty:
        return True
    
    p0_coords = (df_subset.iloc[0]['latitude'], df_subset.iloc[0]['longitude'])
    for idx, row in df_subset.iterrows():
        try:
            if geodesic(p0_coords, (row['latitude'], row['longitude'])).km > max_radius_km:
                return False
        except ValueError:
            continue
    return True

def filter_glitches(df: pd.DataFrame, max_speed_kmh: int, lookahead_days: int) -> pd.DataFrame:
    """
    Filters out 'teleportation' glitches using a temporal forward-looking anchor algorithm.
    Handles impossible speeds and tight-cluster distant excursions.
    """
    if df.empty or len(df) < 2:
        return df

    print(f"\nFiltering glitches: Removing points requiring speed > {max_speed_kmh} km/h or tight distant excursions...")
    
    keep_indices = [0]
    anchor_idx = 0
    total = len(df)
    lookahead_delta = timedelta(days=lookahead_days)
    
    i = 1
    pbar = tqdm(total=total, desc="Analyzing trajectory", unit="pt")
    pbar.update(1)

    while i < total:
        p_anchor = df.iloc[anchor_idx]
        p_curr = df.iloc[i]
        
        coords_a = (p_anchor['latitude'], p_anchor['longitude'])
        coords_c = (p_curr['latitude'], p_curr['longitude'])
        
        try:
            dist_km = geodesic(coords_a, coords_c).km
        except ValueError:
            i += 1
            pbar.update(1)
            continue

        time_diff_h = (p_curr['timestamp'] - p_anchor['timestamp']).total_seconds() / 3600.0
        speed_kmh = dist_km / time_diff_h if time_diff_h > 0 else 0
        
        # Decide if we should look ahead for recovery
        impossible_speed = speed_kmh > max_speed_kmh
        suspicious_excursion = (dist_km > EXCURSION_MIN_DIST_KM and speed_kmh > EXCURSION_MAX_SPEED_KMH)

        if not impossible_speed and not suspicious_excursion:
            # Point is sane, accept it as the new anchor
            keep_indices.append(i)
            anchor_idx = i
            i += 1
            pbar.update(1)
        else:
            # Potential glitch found. Look ahead temporally for recovery to vicinity of anchor.
            found_recovery = False
            lookahead_limit_time = p_curr['timestamp'] + lookahead_delta
            
            for j in range(i + 1, total):
                p_future = df.iloc[j]
                if p_future['timestamp'] > lookahead_limit_time:
                    break
                
                coords_f = (p_future['latitude'], p_future['longitude'])
                try:
                    dist_f = geodesic(coords_a, coords_f).km
                except ValueError:
                    continue

                t_f = (p_future['timestamp'] - p_anchor['timestamp']).total_seconds() / 3600.0
                v_f = dist_f / t_f if t_f > 0 else 0
                
                if v_f <= max_speed_kmh:
                    # Found a recovery point within the time window.
                    # Now decide whether to discard the skipped points.
                    should_discard = False
                    
                    if impossible_speed:
                        # Fallback logic: if it was physically impossible, discard it.
                        should_discard = True
                    else:
                        # Excursion logic: only discard if the cluster is tight.
                        # If it's a spread-out excursion, assume it might be a flight.
                        if is_cluster_tight(df.iloc[i:j], EXCURSION_MAX_RADIUS_KM):
                            should_discard = True
                    
                    if should_discard:
                        pbar.update(j - i)
                        i = j
                        found_recovery = True
                        break
                    else:
                        # It was a dispersed excursion (not a tight glitch). 
                        # Stop lookahead and accept the first point of the excursion.
                        break
            
            if not found_recovery:
                # No recovery found or excursion was accepted.
                keep_indices.append(i)
                anchor_idx = i
                i += 1
                pbar.update(1)
                
    pbar.close()
    
    filtered_df = df.iloc[keep_indices].reset_index(drop=True)
    removed = total - len(filtered_df)
    print(f"Glitch filtering complete. Kept {len(filtered_df)} points (removed {removed}).")
    return filtered_df

def simplify_locations(df: pd.DataFrame, time_threshold_sec: int, dist_threshold_m: int) -> pd.DataFrame:
    """Simplifies the DataFrame by removing points too close in time and space."""
    if df.empty:
        return df

    print(f"\nSimplifying locations: Removing points within {time_threshold_sec}s AND {dist_threshold_m}m of previous point...")
    if 'timestamp' not in df.columns or 'latitude' not in df.columns or 'longitude' not in df.columns:
         print("Error: DataFrame missing required columns for simplification.")
         return df

    # Ensure data is sorted by time
    df_sorted = df.sort_values(by='timestamp').reset_index(drop=True)

    keep_indices = [0] # Always keep the first point
    last_kept_idx = 0

    min_time_delta = timedelta(seconds=time_threshold_sec)

    total_points = len(df_sorted)
    for current_idx in tqdm(range(1, total_points), desc="Simplifying locations", unit="pt"):
        last_kept_point = df_sorted.iloc[last_kept_idx]
        current_point = df_sorted.iloc[current_idx]

        # Check time difference
        time_diff = current_point['timestamp'] - last_kept_point['timestamp']

        # If time difference is large enough, we definitely keep the point
        if time_diff >= min_time_delta:
            keep_indices.append(current_idx)
            last_kept_idx = current_idx
            continue

        # If time difference is small, check distance
        coords_last = (last_kept_point['latitude'], last_kept_point['longitude'])
        coords_current = (current_point['latitude'], current_point['longitude'])

        try:
             distance_m = geodesic(coords_last, coords_current).meters
        except ValueError as e:
             print(f"Warning: Skipping distance calculation due to error at index {current_idx}: {e}")
             continue

        # If BOTH time and distance are below threshold, discard the current point
        if distance_m < dist_threshold_m:
            continue

        # If time is close but distance is far enough, keep the point
        else:
            keep_indices.append(current_idx)
            last_kept_idx = current_idx

    simplified_df = df_sorted.iloc[keep_indices].reset_index(drop=True)
    removed_count = len(df_sorted) - len(simplified_df)
    print(f"Simplification complete. Kept {len(simplified_df)} points (removed {removed_count}).")

    return simplified_df

def convert_df_to_geojson(df: pd.DataFrame, output_file: str):
    """Converts processed (and potentially simplified) DataFrame to GeoJSON."""
    if df.empty:
        print("Warning: DataFrame is empty. No GeoJSON will be created.")
        return

    print(f"Converting DataFrame ({len(df)} points) to GeoJSON: {output_file}")

    geojson_feature_collection = {
        "type": "FeatureCollection",
        "features": []
    }

    for index, row in tqdm(df.iterrows(), total=len(df), desc="Creating GeoJSON", unit="pt"):
        # Ensure timestamp is valid before converting
        ts = row.get('timestamp')
        if pd.isna(ts):
            continue

        feature = {
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [row['longitude'], row['latitude']]
            },
            "properties": {
                "timestamp": ts.isoformat().replace('+00:00', 'Z'), # Standard ISO 8601 format with Z for UTC
                "timestamp_str": row.get('timestamp_str', '') # Include original if exists
            }
        }
        geojson_feature_collection['features'].append(feature)

    try:
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(geojson_feature_collection, f, ensure_ascii=False, separators=(',', ':'))
        print(f"GeoJSON file created successfully: {output_file}")
    except Exception as e:
        print(f"Error writing GeoJSON file: {e}")

def generate_config_js(output_path: str):
    """Generates config.js for the frontend from environment variables."""
    print("\nGenerating config.js...")
    load_dotenv()
    google_maps_api_key = os.getenv("GOOGLE_MAPS_API_KEY", "")
    stadia_api_key = os.getenv("STADIA_API_KEY", "")

    config_content = f"""
// Auto-generated config file
const CONFIG = {{
    GOOGLE_MAPS_API_KEY: "{google_maps_api_key}",
    STADIA_API_KEY: "{stadia_api_key}"
}};
"""
    try:
        with open(output_path, "w", encoding='utf-8') as f:
            f.write(config_content)
        print(f"Config file created successfully: {output_path}")
    except Exception as e:
        print(f"Error writing config file: {e}")

# --- Main Execution Block ---
if __name__ == "__main__":
    print("--- Timeline to GeoJSON Converter ---")

    if not os.path.exists(TIMELINE_JSON_FILENAME):
        print(f"\nERROR: Timeline JSON not found: {TIMELINE_JSON_FILENAME}")
        sys.exit(1)

    try:
        # 1. Load and perform initial cleaning
        all_locations_df = load_timeline_locations_from_json(TIMELINE_JSON_FILENAME)

        if all_locations_df is not None and not all_locations_df.empty:
            # 2. Filter out teleportation glitches
            filtered_df = filter_glitches(all_locations_df, MAX_SPEED_KMH, GLITCH_LOOKAHEAD_DAYS)
            
            # 3. Simplify the data
            simplified_df = simplify_locations(
                filtered_df,
                time_threshold_sec=MIN_TIME_DIFFERENCE_SECONDS,
                dist_threshold_m=MIN_DISTANCE_METERS
            )

            if not simplified_df.empty:
                # 4. Convert to GeoJSON
                convert_df_to_geojson(simplified_df, GEOJSON_OUTPUT_FILENAME)
            else:
                print("\nData became empty after processing.")
        else:
            print("\nNo valid location data loaded.")

        # 5. Always generate config.js
        generate_config_js(CONFIG_JS_FILENAME)

    except Exception as e:
        print(f"\nAn unexpected fatal error occurred: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

    print("\n--- Script Finished ---")

import pandas as pd
from datetime import datetime, timezone, timedelta
import sys
import os
import warnings
import json
from geopy.distance import geodesic # Import for distance calculation

# --- Constants ---
TIMELINE_JSON_FILENAME = "Timeline.json"
GEOJSON_OUTPUT_FILENAME = "timeline_data.geojson"

# --- Simplification Thresholds ---
# Adjust these values as needed
MIN_TIME_DIFFERENCE_SECONDS = 3600  # Ignore points closer than 3600 seconds AND...
MIN_DISTANCE_METERS = 50          # ... closer than 10 meters to the previous kept point

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
        total_segments = len(data['semanticSegments'])
        print(f"Processing {total_segments} semantic segments...")
        processed_count = 0

        for segment in data['semanticSegments']:
             processed_count += 1
             if processed_count % 10000 == 0:
                  print(f"  Processed {processed_count}/{total_segments} segments...")

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
            # Attempt to parse multiple formats robustly if needed, but start with standard
            df['timestamp'] = pd.to_datetime(df['timestamp_str'], errors='coerce', utc=True)
            # Example of handling multiple formats if necessary:
            # df['timestamp'] = pd.to_datetime(df['timestamp_str'], format='mixed', errors='coerce', utc=True)
        except Exception as e:
            print(f"Error parsing timestamps from timeline JSON: {e}")
            # Consider logging problematic timestamps here if debugging is needed
            return None

        original_count = len(df)
        df.dropna(subset=['timestamp', 'latitude', 'longitude'], inplace=True)
        removed_count = original_count - len(df)
        if removed_count > 0:
            print(f"Warning: Removed {removed_count} rows due to invalid/missing timestamp or coordinates after parsing.")

        if df.empty:
            print("No valid data remaining after cleaning.")
            return None

        # Sort before dropping duplicates to ensure consistency
        df.sort_values(by=['timestamp', 'latitude', 'longitude'], inplace=True)

        original_count = len(df)
        # Drop exact duplicates first
        df.drop_duplicates(subset=['timestamp', 'latitude', 'longitude'], keep='first', inplace=True)
        removed_duplicates = original_count - len(df)
        if removed_duplicates > 0:
             print(f"Removed {removed_duplicates} exact duplicate entries (same time and location).")

        # Ensure sorting after potential drops
        df.sort_values(by='timestamp', inplace=True)
        df.reset_index(drop=True, inplace=True) # Reset index after sorting/dropping

        print(f"Timeline data prepared and sorted. {len(df)} unique location entries before simplification.")
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

def simplify_locations(df: pd.DataFrame, time_threshold_sec: int, dist_threshold_m: int) -> pd.DataFrame:
    """Simplifies the DataFrame by removing points too close in time and space."""
    if df.empty:
        return df

    print(f"\nSimplifying locations: Removing points within {time_threshold_sec}s AND {dist_threshold_m}m of the previous kept point...")
    if 'timestamp' not in df.columns or 'latitude' not in df.columns or 'longitude' not in df.columns:
         print("Error: DataFrame missing required columns for simplification (timestamp, latitude, longitude).")
         return df # Return original df if columns are missing

    # Ensure data is sorted by time
    df_sorted = df.sort_values(by='timestamp').reset_index(drop=True)

    keep_indices = [0] # Always keep the first point
    last_kept_idx = 0

    min_time_delta = timedelta(seconds=time_threshold_sec)

    total_points = len(df_sorted)
    for current_idx in range(1, total_points):
        # Progress indicator
        if current_idx % 50000 == 0:
             print(f"  Simplification progress: {current_idx}/{total_points} points checked...")

        last_kept_point = df_sorted.iloc[last_kept_idx]
        current_point = df_sorted.iloc[current_idx]

        # Check time difference
        time_diff = current_point['timestamp'] - last_kept_point['timestamp']

        # If time difference is large enough, we definitely keep the point
        if time_diff >= min_time_delta:
            keep_indices.append(current_idx)
            last_kept_idx = current_idx
            continue # Move to the next point

        # If time difference is small, check distance
        coords_last = (last_kept_point['latitude'], last_kept_point['longitude'])
        coords_current = (current_point['latitude'], current_point['longitude'])

        try:
             # Calculate distance only if time difference is small
             distance_m = geodesic(coords_last, coords_current).meters
        except ValueError as e:
             # Handle potential errors from geopy (e.g., invalid coordinates somehow missed earlier)
             print(f"Warning: Skipping distance calculation due to error at index {current_idx}: {e}")
             # Decide whether to keep or discard based on time alone, or skip point entirely
             # Let's keep it to be safe if distance fails, as time diff is small
             # keep_indices.append(current_idx)
             # last_kept_idx = current_idx
             continue # Or discard if unsure: continue

        # If BOTH time and distance are below threshold, discard the current point (by NOT adding its index)
        if distance_m < dist_threshold_m:
            # print(f"  Discarding point {current_idx}: TimeDiff={time_diff}, Dist={distance_m:.1f}m") # Debugging
            continue # Skip to next point, effectively discarding this one

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

    for index, row in df.iterrows():
        # Ensure timestamp is valid before converting
        ts = row.get('timestamp')
        if pd.isna(ts):
            print(f"Warning: Skipping row {index} due to missing/invalid timestamp during GeoJSON conversion.")
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
            json.dump(geojson_feature_collection, f, ensure_ascii=False, separators=(',', ':')) # Use separators for smaller file size
        print(f"GeoJSON file created successfully: {output_file}")
    except Exception as e:
        print(f"Error writing GeoJSON file: {e}")


# --- Main Execution Block ---
if __name__ == "__main__":
    print("--- Timeline to GeoJSON Converter ---")

    timeline_json_path = TIMELINE_JSON_FILENAME
    geojson_output_path = GEOJSON_OUTPUT_FILENAME

    if not os.path.exists(timeline_json_path):
        print(f"\nERROR: Timeline JSON not found: {timeline_json_path}")
        sys.exit(1)
    print(f"\nFound Timeline file: {timeline_json_path}")

    try:
        # 1. Load and perform initial cleaning
        all_locations_df = load_timeline_locations_from_json(timeline_json_path)

        if all_locations_df is None or all_locations_df.empty:
            print("\nNo valid location data loaded. Exiting.")
            sys.exit(0)

        # 2. Simplify the data *** NEW STEP ***
        simplified_df = simplify_locations(
            all_locations_df,
            time_threshold_sec=MIN_TIME_DIFFERENCE_SECONDS,
            dist_threshold_m=MIN_DISTANCE_METERS
        )

        if simplified_df.empty:
             print("\nData became empty after simplification. Exiting.")
             sys.exit(0)

        # 3. Convert the *simplified* DataFrame to GeoJSON
        convert_df_to_geojson(simplified_df, geojson_output_path)

    except FileNotFoundError as e:
        print(f"\nFatal Error: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\nAn unexpected fatal error occurred: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

    print("\n--- Script Finished ---")
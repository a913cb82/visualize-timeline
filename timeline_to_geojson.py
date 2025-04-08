import pandas as pd
from datetime import datetime, timezone, timedelta
import sys
import os
import warnings
import json

# --- Constants ---
TIMELINE_JSON_FILENAME = "Timeline.json"
GEOJSON_OUTPUT_FILENAME = "timeline_data.geojson"

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
             if processed_count % 10000 == 0: # Progress indicator every 10000 segments
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

        original_count = len(df)
        df.drop_duplicates(subset=['timestamp', 'latitude', 'longitude'], keep='first', inplace=True)
        removed_duplicates = original_count - len(df)
        if removed_duplicates > 0:
             print(f"Removed {removed_duplicates} duplicate entries (same time and location).")

        df.sort_values(by=['timestamp', 'latitude', 'longitude'], inplace=True)
        print(f"Timeline data prepared and definitively sorted. {len(df)} valid location entries.")
        return df[['timestamp', 'latitude', 'longitude', 'timestamp_str']] # Keep timestamp_str for GeoJSON

    except json.JSONDecodeError as e:
        print(f"Error: Failed to decode JSON file '{file_path}'. It might be corrupted. Error: {e}")
        return None
    except MemoryError:
        print(f"Error: Ran out of memory trying to load '{file_path}'. The file might be too large for available RAM.")
        return None
    except Exception as e:
        print(f"An unexpected error occurred while reading or processing the Timeline JSON: {e}")
        raise


def convert_df_to_geojson(df: pd.DataFrame, output_file: str):
    """Converts processed DataFrame to GeoJSON and saves to a file."""
    if df.empty:
        print("Warning: DataFrame is empty. No GeoJSON will be created.")
        return

    print(f"Converting DataFrame to GeoJSON and saving to: {output_file}")

    geojson_feature_collection = {
        "type": "FeatureCollection",
        "features": []
    }

    for index, row in df.iterrows():
        feature = {
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [row['longitude'], row['latitude']] # GeoJSON is [lon, lat]
            },
            "properties": {
                "timestamp": row['timestamp'].isoformat(), # ISO format for JS Date parsing
                "timestamp_str": row['timestamp_str'] # Keep original string if needed for display
                # Add other properties here if needed from the DataFrame
            }
        }
        geojson_feature_collection['features'].append(feature)

    try:
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(geojson_feature_collection, f, ensure_ascii=False, indent=2) # Indent for readability
        print(f"GeoJSON file created successfully: {output_file}")
    except Exception as e:
        print(f"Error writing GeoJSON file: {e}")


# --- Main Execution Block ---
if __name__ == "__main__":
    print("--- Timeline to GeoJSON Converter ---")

    timeline_json_path = TIMELINE_JSON_FILENAME
    geojson_output_path = GEOJSON_OUTPUT_FILENAME

    # --- File Check ---
    if not os.path.exists(timeline_json_path):
        print(f"\nERROR: The required Timeline JSON file was not found at: {timeline_json_path}")
        print(f"Ensure '{TIMELINE_JSON_FILENAME}' is in the same directory.")
        sys.exit(1)
    print(f"\nFound Timeline file: {timeline_json_path}")

    # --- Load & Process Data ---
    try:
        all_locations_df = load_timeline_locations_from_json(timeline_json_path)

        if all_locations_df is None or all_locations_df.empty:
            print("\nNo valid location data loaded from Timeline JSON. Exiting.")
            sys.exit(0)

        # --- Convert to GeoJSON ---
        convert_df_to_geojson(all_locations_df, geojson_output_path)


    except FileNotFoundError as e:
        print(f"\nFatal Error: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\nAn unexpected fatal error occurred during loading or processing: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

    print("\n--- Script Finished ---")
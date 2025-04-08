import os
import csv
import datetime
import exifread # For reading EXIF data
import pandas as pd
from dateutil.parser import parse as dateutil_parse # Flexible date parsing
import sys # For better error output

# --- Configuration ---
PHOTOS_DIR = './EuropeMarch2025' # The directory containing your downloaded photos
OUTPUT_CSV_FILE = 'local_photo_locations.csv'
# Common image extensions with EXIF data
IMAGE_EXTENSIONS = ('.jpg', '.jpeg', '.tif', '.tiff')

# --- EXIF Helper Functions ---
def _convert_to_degrees(value):
    """Helper function to convert DMS EXIF tag to degrees"""
    try:
        d = float(value.values[0].num) / float(value.values[0].den)
        m = float(value.values[1].num) / float(value.values[1].den)
        s = float(value.values[2].num) / float(value.values[2].den)
        return d + (m / 60.0) + (s / 3600.0)
    except (AttributeError, ZeroDivisionError, IndexError, ValueError) as e:
        # print(f"      Error converting DMS: {e} for value {value}", file=sys.stderr)
        return None # Return None if conversion fails

def get_exif_data(filepath):
    """
    Extracts timestamp and GPS latitude/longitude from an image file's EXIF data.
    Returns a dictionary {'timestamp': datetime/None, 'latitude': float/None, 'longitude': float/None}
    """
    lat = None
    lon = None
    timestamp_dt = None

    try:
        with open(filepath, 'rb') as f:
            # Process the file, stop_tag can speed it up if you know the last tag needed
            # For flexibility, let's read all tags initially.
            tags = exifread.process_file(f, stop_tag=None) # Read all tags

        # --- Extract Timestamp ---
        # Prioritize DateTimeOriginal
        timestamp_tag = tags.get('EXIF DateTimeOriginal')
        if timestamp_tag:
            timestamp_str = str(timestamp_tag.values)
            # EXIF format is often 'YYYY:MM:DD HH:MM:SS'
            try:
                # Try standard EXIF format first
                timestamp_dt = datetime.datetime.strptime(timestamp_str, '%Y:%m:%d %H:%M:%S')
            except ValueError:
                try:
                    # Fallback to more flexible parsing if format differs
                    timestamp_dt = dateutil_parse(timestamp_str)
                except (ValueError, TypeError) as e:
                     # print(f"      Could not parse timestamp '{timestamp_str}': {e}", file=sys.stderr)
                     timestamp_dt = None # Give up if parsing fails

        # --- Extract GPS ---
        gps_latitude = tags.get('GPS GPSLatitude')
        gps_latitude_ref = tags.get('GPS GPSLatitudeRef')
        gps_longitude = tags.get('GPS GPSLongitude')
        gps_longitude_ref = tags.get('GPS GPSLongitudeRef')

        if gps_latitude and gps_latitude_ref and gps_longitude and gps_longitude_ref:
            lat_val = _convert_to_degrees(gps_latitude)
            lon_val = _convert_to_degrees(gps_longitude)

            if lat_val is not None and lon_val is not None:
                lat = lat_val
                if gps_latitude_ref.values[0] != 'N':
                    lat = -lat

                lon = lon_val
                if gps_longitude_ref.values[0] != 'E':
                    lon = -lon

        # Return None for the whole entry if no timestamp or no GPS found
        # if timestamp_dt is None or lat is None or lon is None:
        #     return None # Or decide if you want entries with partial data

        return {
            'timestamp': timestamp_dt,
            'latitude': lat,
            'longitude': lon
        }

    except FileNotFoundError:
        print(f"  Error: File not found: {filepath}", file=sys.stderr)
        return None
    except IsADirectoryError:
         # print(f"  Skipping directory: {filepath}", file=sys.stderr) # Should be handled by os.walk
         return None
    except Exception as e:
        # Catch potential errors from exifread (e.g., corrupted file)
        # print(f"  Error processing EXIF for {filepath}: {e}", file=sys.stderr)
        return None

# --- Saving Data ---
def save_to_csv(data, filename):
    """Saves the extracted location data to a CSV file."""
    if not data:
        print("No data with location/timestamp extracted to save.")
        return

    df = pd.DataFrame(data)

    # Drop rows where critical info might be missing (optional, depending on needs)
    # df.dropna(subset=['timestamp', 'latitude', 'longitude'], inplace=True)
    # For this use case, let's keep only entries with actual location
    df.dropna(subset=['latitude', 'longitude'], inplace=True)


    if df.empty:
        print("No valid location data found after filtering.")
        return

    # Sort by timestamp if possible (handle potential None timestamps)
    try:
        # Convert errors during timestamp access/comparison to NaT (Not a Time)
        df['timestamp_sort'] = pd.to_datetime(df['timestamp'], errors='coerce')
        df = df.sort_values(by='timestamp_sort').drop(columns=['timestamp_sort'])
    except Exception as sort_e:
        print(f"Warning: Could not sort by timestamp: {sort_e}", file=sys.stderr)
        # Proceed without sorting if it fails

    # Format timestamp for readability
    try:
         # Only format if timestamp column exists and contains datetime objects
         if 'timestamp' in df.columns:
            df['timestamp_str'] = df['timestamp'].dt.strftime('%Y-%m-%d %H:%M:%S')
            # Optional: handle NaT if any survived or were created
            df['timestamp_str'] = df['timestamp_str'].fillna('N/A')
         else:
             df['timestamp_str'] = 'N/A'
    except AttributeError:
         # Handle case where 'timestamp' might contain non-datetime data
         df['timestamp_str'] = df['timestamp'].astype(str).fillna('N/A')


    # Select and order columns
    output_cols = ['filepath', 'timestamp_str', 'latitude', 'longitude']
    output_df = df[output_cols]


    try:
        output_df.to_csv(filename, index=False, quoting=csv.QUOTE_MINIMAL)
        print(f"\nData successfully saved to {filename}")
        print(f"Processed {len(df)} photos with valid location data.")
    except Exception as e:
        print(f"\nError saving data to CSV: {e}", file=sys.stderr)

# --- Main Execution ---
if __name__ == '__main__':
    if not os.path.isdir(PHOTOS_DIR):
        print(f"Error: Input directory '{PHOTOS_DIR}' not found or is not a directory.", file=sys.stderr)
        sys.exit(1)

    print(f"Scanning directory: {PHOTOS_DIR}")
    all_photo_data = []
    file_count = 0
    processed_count = 0
    found_location_count = 0

    for root, dirs, files in os.walk(PHOTOS_DIR):
        for filename in files:
            file_count += 1
            # Check if the file extension is one we want to process
            if filename.lower().endswith(IMAGE_EXTENSIONS):
                processed_count += 1
                filepath = os.path.join(root, filename)
                print(f"Processing ({processed_count}): {filepath}", end='\r') # Overwrite line

                exif_info = get_exif_data(filepath)

                if exif_info and exif_info['latitude'] is not None and exif_info['longitude'] is not None:
                    # Only add if location was found
                    exif_info['filepath'] = filepath # Add filepath to the dict
                    all_photo_data.append(exif_info)
                    found_location_count += 1
            # else:
                # print(f"Skipping non-image file: {filename}")

    print(f"\nFinished scanning {file_count} files.")
    print(f"Processed {processed_count} potential image files.")
    print(f"Found location data in {found_location_count} files.")

    save_to_csv(all_photo_data, OUTPUT_CSV_FILE)
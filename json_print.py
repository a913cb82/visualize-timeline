#!/usr/bin/env python3

import json
import sys
import argparse
import io # Needed for type hinting StringIO

# --- Configuration ---
DEFAULT_MAX_ITEMS = 10
DEFAULT_MAX_STRING_LEN = 80
DEFAULT_INDENT = 2

# --- Truncation Markers ---
# Using distinct markers helps identify what was truncated
TRUNCATION_MARKER_DICT_KEY = "..."
TRUNCATION_MARKER_DICT_VALUE_TMPL = "(truncated - {} more keys)"
TRUNCATION_MARKER_LIST_ITEM_TMPL = "... (truncated - {} total items)"
TRUNCATION_MARKER_STRING = "..."

def shorten_json_recursive(data, max_items, max_str_len):
    """
    Recursively traverses a Python data structure (from JSON)
    and returns a shortened version.
    """
    if isinstance(data, dict):
        short_dict = {}
        original_len = len(data)
        count = 0
        for key, value in data.items():
            if count >= max_items:
                # Add a marker indicating truncation
                marker_value = TRUNCATION_MARKER_DICT_VALUE_TMPL.format(original_len - max_items)
                short_dict[TRUNCATION_MARKER_DICT_KEY] = marker_value
                break
            # Recursively shorten the value
            short_dict[key] = shorten_json_recursive(value, max_items, max_str_len)
            count += 1
        return short_dict

    elif isinstance(data, list):
        original_len = len(data)
        # Take the first 'max_items' and shorten each one recursively
        short_list = [shorten_json_recursive(item, max_items, max_str_len) for item in data[:max_items]]
        # Add a marker if the list was truncated
        if original_len > max_items:
             marker_item = TRUNCATION_MARKER_LIST_ITEM_TMPL.format(original_len)
             short_list.append(marker_item)
        return short_list

    elif isinstance(data, str):
        if len(data) > max_str_len:
            # Truncate the string and add a marker
            return data[:max_str_len] + TRUNCATION_MARKER_STRING
        else:
            return data # String is short enough

    else:
        # For numbers, booleans, NoneType - return as is
        return data

def main():
    parser = argparse.ArgumentParser(
        description="Print a shortened version of a JSON object (limits lists/dicts and string lengths).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter # Show defaults in help
    )
    parser.add_argument(
        'infile',
        nargs='?',
        type=argparse.FileType('r'),
        default=sys.stdin,
        help="Input JSON file (reads from stdin if omitted)."
    )
    parser.add_argument(
        '-n', '--num-items',
        type=int,
        default=DEFAULT_MAX_ITEMS,
        help="Max number of list items or dict keys to show."
    )
    parser.add_argument(
        '-l', '--str-len',
        type=int,
        default=DEFAULT_MAX_STRING_LEN,
        help="Max string length before truncating."
    )
    parser.add_argument(
        '--indent',
        type=int,
        default=DEFAULT_INDENT,
        help="Indentation level for output JSON. Use 0 for compact output."
    )
    parser.add_argument(
        '--no-unicode',
        action='store_true',
        help="Ensure output is ASCII (escapes non-ASCII characters)."
    )

    args = parser.parse_args()

    # --- Input Handling ---
    input_source: io.TextIOWrapper = args.infile
    try:
        original_data = json.load(input_source)
    except Exception as e:
        # Catch other potential read errors
        print(f"Error reading input: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        # Close the file if it wasn't stdin
        if input_source is not sys.stdin:
            input_source.close()

    # --- Shorten Data ---
    shortened_data = shorten_json_recursive(original_data, args.num_items, args.str_len)

    # --- Output ---
    try:
        # Use None for indent if 0 or less was specified, as json.dumps expects
        indent_val = args.indent if args.indent > 0 else None
        ensure_ascii_flag = args.no_unicode # True if --no-unicode is passed

        print(json.dumps(
            shortened_data,
            indent=indent_val,
            ensure_ascii=ensure_ascii_flag
            )
        )
    except TypeError as e:
        # This might occur if the shortened structure is somehow invalid for JSON,
        # though unlikely with the current logic.
        print(f"Error formatting shortened JSON for output: {e}", file=sys.stderr)
        # As a fallback, you could print the raw Python structure:
        # import pprint
        # pprint.pprint(shortened_data)
        sys.exit(1)

if __name__ == "__main__":
    main()
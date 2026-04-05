#!/usr/bin/env python3
"""
Convert raw Mac screenshot to PNG
Mac screenBits is 1-bit per pixel, MSB first
"""
from PIL import Image
import sys
import os

def convert_raw_screenshot(raw_path, output_path=None):
    """Convert raw Mac 1-bit screenshot to PNG"""

    if output_path is None:
        output_path = raw_path.replace('.raw', '.png')

    with open(raw_path, 'rb') as f:
        data = f.read()

    print(f"Raw data: {len(data)} bytes")

    # Try to guess dimensions based on file size
    # Common classic Mac resolutions:
    # 512x342 (Mac Plus/SE) = 21888 bytes at 1bpp
    # 640x480 (color Macs) = 38400 bytes at 1bpp
    # 768x576 = 55296 bytes at 1bpp
    # 832x624 = 64896 bytes at 1bpp

    # rowBytes is typically rounded up to word boundary
    possible_configs = [
        (512, 342, 64),    # Mac Plus/SE: 512 pixels = 64 bytes/row
        (640, 480, 80),    # 640 pixels = 80 bytes/row
        (768, 576, 96),    # 768 pixels = 96 bytes/row
        (800, 600, 100),   # 800 pixels = 100 bytes/row
        (832, 624, 104),   # 832 pixels = 104 bytes/row
        (1024, 768, 128),  # 1024 pixels = 128 bytes/row
    ]

    width = None
    height = None
    row_bytes = None

    for w, h, rb in possible_configs:
        expected = h * rb
        if len(data) == expected:
            width, height, row_bytes = w, h, rb
            print(f"Detected: {width}x{height}, {row_bytes} bytes/row")
            break
        # Also check if we captured partial (100 lines fallback)
        if len(data) == 100 * rb:
            width, height, row_bytes = w, 100, rb
            print(f"Detected partial: {width}x{height}, {row_bytes} bytes/row")
            break

    if width is None:
        # Try to deduce from data size
        # Assume standard row_bytes values
        for rb in [64, 80, 96, 100, 104, 128]:
            if len(data) % rb == 0:
                height = len(data) // rb
                width = rb * 8
                row_bytes = rb
                print(f"Guessed: {width}x{height}, {row_bytes} bytes/row")
                break

    if width is None:
        print("Could not determine dimensions!")
        print(f"Data size: {len(data)} bytes")
        print("Please specify width and height manually")
        return

    # Create image
    img = Image.new('1', (width, height))
    pixels = img.load()

    # Unpack 1-bit data (MSB first, black=0, white=1 on Mac)
    for y in range(height):
        row_start = y * row_bytes
        for x in range(width):
            byte_idx = row_start + (x // 8)
            bit_idx = 7 - (x % 8)  # MSB first
            if byte_idx < len(data):
                bit = (data[byte_idx] >> bit_idx) & 1
                # Mac: 0=black, 1=white. PIL 1-bit: 0=black, 1=white. Same!
                pixels[x, y] = bit

    img.save(output_path)
    print(f"Saved: {output_path}")

    # Also save inverted version (sometimes Mac screens are inverted)
    img_inv = Image.new('1', (width, height))
    pixels_inv = img_inv.load()
    for y in range(height):
        for x in range(width):
            pixels_inv[x, y] = 1 - pixels[x, y]
    inv_path = output_path.replace('.png', '_inverted.png')
    img_inv.save(inv_path)
    print(f"Saved inverted: {inv_path}")


def force_convert(raw_path, width, row_bytes=None, output_path=None):
    """Force convert with specified width"""
    if row_bytes is None:
        row_bytes = (width + 7) // 8  # Round up to byte boundary

    if output_path is None:
        output_path = raw_path.replace('.raw', '.png')

    with open(raw_path, 'rb') as f:
        data = f.read()

    height = len(data) // row_bytes
    print(f"Forcing: {width}x{height}, {row_bytes} bytes/row")

    img = Image.new('1', (width, height))
    pixels = img.load()

    for y in range(height):
        row_start = y * row_bytes
        for x in range(width):
            byte_idx = row_start + (x // 8)
            bit_idx = 7 - (x % 8)
            if byte_idx < len(data):
                bit = (data[byte_idx] >> bit_idx) & 1
                pixels[x, y] = bit

    img.save(output_path)
    print(f"Saved: {output_path}")


if __name__ == "__main__":
    raw_file = "/Users/pitforster/Desktop/Share/screenshot.raw"
    if len(sys.argv) > 1:
        raw_file = sys.argv[1]

    if os.path.exists(raw_file):
        # First try auto-detect
        convert_raw_screenshot(raw_file)

        # If that fails, try common widths
        print("\nTrying forced widths...")
        for width in [512, 640, 768, 800, 1024]:
            rb = (width + 7) // 8
            out = raw_file.replace('.raw', f'_{width}w.png')
            try:
                force_convert(raw_file, width, rb, out)
            except Exception as e:
                print(f"  {width}: {e}")
    else:
        print(f"File not found: {raw_file}")

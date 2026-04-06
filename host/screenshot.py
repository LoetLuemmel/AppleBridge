#!/usr/bin/env python3
"""
Capture screenshot of Basilisk II window from host side.
Requires macOS with Quartz framework.
"""
import subprocess
import sys
import os
from datetime import datetime

def get_basilisk_window():
    """Find Basilisk II window bounds using Quartz"""
    import Quartz
    windows = Quartz.CGWindowListCopyWindowInfo(
        Quartz.kCGWindowListOptionOnScreenOnly,
        Quartz.kCGNullWindowID
    )
    for w in windows:
        name = w.get('kCGWindowOwnerName', '')
        if 'Basilisk' in name:
            bounds = w.get('kCGWindowBounds', {})
            return {
                'id': w['kCGWindowNumber'],
                'x': int(bounds.get('X', 0)),
                'y': int(bounds.get('Y', 0)),
                'width': int(bounds.get('Width', 0)),
                'height': int(bounds.get('Height', 0))
            }
    return None

def capture_screenshot(output_path=None):
    """Capture Basilisk II window screenshot"""
    window = get_basilisk_window()
    if not window:
        print("Basilisk II window not found")
        return None

    if output_path is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = f"/tmp/basilisk_{timestamp}.png"

    # Capture using region
    region = f"{window['x']},{window['y']},{window['width']},{window['height']}"
    result = subprocess.run(
        ['screencapture', '-R', region, output_path],
        capture_output=True
    )

    if result.returncode == 0 and os.path.exists(output_path):
        print(f"Screenshot saved: {output_path}")
        print(f"Window: {window['width']}x{window['height']} at ({window['x']},{window['y']})")
        return output_path
    else:
        print(f"Screenshot failed: {result.stderr.decode()}")
        return None

if __name__ == "__main__":
    output = sys.argv[1] if len(sys.argv) > 1 else None
    capture_screenshot(output)

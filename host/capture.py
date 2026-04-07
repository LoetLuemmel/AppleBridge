#!/usr/bin/env python3
"""Capture screenshot from MacintoshBridgeHost via control port."""

import socket
import sys
from pathlib import Path
from datetime import datetime


def capture_screenshot(output_path: str | None = None, host: str = "localhost", port: int = 9001) -> str:
    """Request screenshot from MacintoshBridgeHost and save to file."""

    if output_path is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = f"/tmp/basilisk_{timestamp}.png"

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(10)
        sock.connect((host, port))
        sock.sendall(b"SCREENSHOT\n")

        # Receive response
        data = b""
        while True:
            try:
                chunk = sock.recv(65536)
                if not chunk:
                    break
                data += chunk
            except socket.timeout:
                break

    if not data:
        print("ERROR: No response received", file=sys.stderr)
        return ""

    # Parse response: SCREENSHOT:<length>\r\n<PNG data>
    if data.startswith(b"ERROR:"):
        print(data.decode("utf-8", errors="replace"), file=sys.stderr)
        return ""

    if not data.startswith(b"SCREENSHOT:"):
        print(f"ERROR: Unexpected response: {data[:50]}", file=sys.stderr)
        return ""

    # Find end of header
    header_end = data.find(b"\r\n")
    if header_end == -1:
        print("ERROR: Malformed response (no header end)", file=sys.stderr)
        return ""

    png_data = data[header_end + 2:]

    # Verify PNG magic
    if not png_data.startswith(b"\x89PNG"):
        print("ERROR: Response is not a valid PNG", file=sys.stderr)
        return ""

    # Save to file
    Path(output_path).write_bytes(png_data)
    print(f"Screenshot saved: {output_path} ({len(png_data)} bytes)")
    return output_path


if __name__ == "__main__":
    output = sys.argv[1] if len(sys.argv) > 1 else None
    capture_screenshot(output)

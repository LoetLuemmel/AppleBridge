#!/usr/bin/env python3
"""
Test script for protocol parsing
"""

import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'host'))

from protocol import CommandRequest, CommandResponse, ScreenshotRequest, ScreenshotResponse


def test_command_request():
    """Test command request encoding"""
    print("Testing CommandRequest encoding...")

    cmd = CommandRequest(command="ls -l")
    encoded = cmd.encode()

    expected = b"COMMAND:5\nls -l"
    assert encoded == expected, f"Expected {expected}, got {encoded}"

    print("✓ CommandRequest encoding passed")


def test_command_response():
    """Test command response decoding"""
    print("Testing CommandResponse decoding...")

    response_data = b"""STATUS:0
STDOUT:13
Hello, World!
STDERR:0


"""

    response = CommandResponse.decode(response_data)

    assert response.exit_code == 0
    assert "Hello, World!" in response.stdout
    assert response.stderr == ""

    print("✓ CommandResponse decoding passed")


def test_screenshot_request():
    """Test screenshot request encoding"""
    print("Testing ScreenshotRequest encoding...")

    req = ScreenshotRequest()
    encoded = req.encode()

    assert encoded == b"SCREENSHOT\n"

    print("✓ ScreenshotRequest encoding passed")


def test_screenshot_response():
    """Test screenshot response decoding"""
    print("Testing ScreenshotResponse decoding...")

    # Create fake screenshot data
    fake_data = b"\x00\x01\x02\x03\x04\x05"
    response_bytes = b"IMAGE:640:480:BMP:6\n" + fake_data

    response = ScreenshotResponse.decode(response_bytes)

    assert response.width == 640
    assert response.height == 480
    assert response.format == "BMP"
    assert response.data == fake_data

    print("✓ ScreenshotResponse decoding passed")


def main():
    print("=== Protocol Tests ===\n")

    try:
        test_command_request()
        test_command_response()
        test_screenshot_request()
        test_screenshot_response()

        print("\n✓ All protocol tests PASSED")
        return 0

    except AssertionError as e:
        print(f"\n✗ Test FAILED: {e}")
        return 1

    except Exception as e:
        print(f"\n✗ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == '__main__':
    sys.exit(main())

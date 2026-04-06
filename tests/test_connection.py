#!/usr/bin/env python3
"""
Test script to verify connection to Mac emulator
"""

import sys
import socket
import time

def test_tcp_connection(host='localhost', port=9000, timeout=5):
    """Test basic TCP connection to Mac"""
    print(f"Testing TCP connection to {host}:{port}...")

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)

        print("Connecting...")
        sock.connect((host, port))

        print("Connected! Sending test command...")

        # Send simple echo command
        test_command = "echo 'Connection test'"
        request = f"COMMAND:{len(test_command)}\n{test_command}"

        sock.sendall(request.encode('utf-8'))

        print("Waiting for response...")

        # Receive response
        response = b""
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            response += chunk
            if b'\n\n' in response:
                break

        print("\nResponse received:")
        print(response.decode('utf-8', errors='replace'))

        sock.close()

        print("\n✓ Connection test PASSED")
        return True

    except socket.timeout:
        print(f"\n✗ Connection test FAILED: Timeout after {timeout}s")
        return False

    except ConnectionRefusedError:
        print(f"\n✗ Connection test FAILED: Connection refused")
        print(f"  Make sure the Mac daemon is running on port {port}")
        return False

    except Exception as e:
        print(f"\n✗ Connection test FAILED: {e}")
        return False

def main():
    import argparse

    parser = argparse.ArgumentParser(description='Test Mac connection')
    parser.add_argument('--host', default='localhost', help='Mac host')
    parser.add_argument('--port', type=int, default=9000, help='Mac port')
    parser.add_argument('--timeout', type=int, default=5, help='Timeout in seconds')

    args = parser.parse_args()

    success = test_tcp_connection(args.host, args.port, args.timeout)
    sys.exit(0 if success else 1)

if __name__ == '__main__':
    main()

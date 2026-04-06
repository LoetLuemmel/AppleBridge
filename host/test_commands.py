#!/usr/bin/env python3
"""Test MPW commands via AppleBridge"""
import sys
from host_server import AppleBridgeServer

def main():
    server = AppleBridgeServer()

    print("Starting server, waiting for Mac connection...")
    server.start()
    print("Connected!\n")

    # Test commands
    test_commands = [
        "Directory",
        'Echo "Hello from Claude"',
        "Files",
    ]

    for cmd in test_commands:
        print(f">>> {cmd}")
        response = server.send_command(cmd)
        print(response)
        print("-" * 40)

    server.close()
    print("Done.")

if __name__ == "__main__":
    main()

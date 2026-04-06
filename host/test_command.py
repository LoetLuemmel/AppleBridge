#!/usr/bin/env python3
"""Simple test: connect and send one command"""
import socket

HOST_PORT = 9000

def get_local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
    except Exception:
        ip = '127.0.0.1'
    finally:
        s.close()
    return ip

def main():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(('0.0.0.0', HOST_PORT))
    server.listen(1)

    local_ip = get_local_ip()
    print(f"=== AppleBridge Test Server ===")
    print(f"Listening on port {HOST_PORT}")
    print(f"Configure Mac to connect to: {local_ip}:{HOST_PORT}")
    print(f"Waiting for Mac...")

    client, addr = server.accept()
    print(f"Mac connected from {addr}")

    # Verify Files still returns output
    command = "Files -l 'MeinMac:Temp:'"
    encoded = command.encode('mac_roman')
    header = f"COMMAND:{len(encoded)}\n".encode('ascii')

    print(f"\nSending: {command}")
    client.sendall(header + encoded)

    # Wait for response
    print("Waiting for response...")
    client.settimeout(30.0)
    response = b""
    try:
        while True:
            chunk = client.recv(4096)
            if not chunk:
                break
            response += chunk
            if b"\r\r" in response or b"\n\n" in response:
                break
    except socket.timeout:
        print("Timeout")

    print(f"\nResponse ({len(response)} bytes):")
    print(response.decode('mac_roman', errors='replace'))

    client.close()
    server.close()

if __name__ == "__main__":
    main()

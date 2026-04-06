#!/usr/bin/env python3
"""Send a single command to AppleBridge server via localhost"""
import socket
import sys

def send_command(command, host='127.0.0.1', port=9001):
    """Send command to the bridge's local control port"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.connect((host, port))
        sock.sendall(command.encode('utf-8'))
        sock.shutdown(socket.SHUT_WR)

        response = b""
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            response += chunk

        return response.decode('utf-8', errors='replace')
    finally:
        sock.close()

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: send_command.py <command>")
        print("Example: send_command.py 'Directory'")
        sys.exit(1)

    cmd = ' '.join(sys.argv[1:])
    print(f"Sending: {cmd}")
    response = send_command(cmd)
    print(f"Response:\n{response}")

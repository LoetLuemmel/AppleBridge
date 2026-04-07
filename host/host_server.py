#!/usr/bin/env python3
"""
AppleBridge Host Server
Mac connects TO this server on 192.168.3.154:9000
"""
import socket
import threading
import sys

HOST_INTERFACE = "192.168.3.154"  # Interface Mac connects to
HOST_PORT = 9000
CONTROL_PORT = 9001  # Local control port for sending commands

class AppleBridgeServer:
    def __init__(self, interface=HOST_INTERFACE, port=HOST_PORT):
        self.interface = interface
        self.port = port
        self.client_socket = None
        self.server_socket = None
        self.connected = False

    def start(self):
        """Listen for Mac connection"""
        print(f"=== AppleBridge Host Server ===")
        print(f"Listening on {self.interface}:{self.port}")
        print(f"Waiting for Mac to connect...")
        print()

        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server_socket.bind((self.interface, self.port))
        self.server_socket.listen(1)

        self.client_socket, addr = self.server_socket.accept()
        self.connected = True
        print(f"Mac connected from {addr}")
        print()

    def send_command(self, command):
        """Send a command to the Mac and get response"""
        if not self.connected:
            return None

        # Encode command to MacRoman first to get accurate byte length
        encoded_command = command.encode('mac_roman', errors='replace')
        # Format: COMMAND:<length>\n<command> (protocol uses \n as separator)
        header = f"COMMAND:{len(encoded_command)}\n".encode('ascii')
        self.client_socket.sendall(header + encoded_command)

        # Receive response
        response = b""
        self.client_socket.settimeout(5.0)  # 5 second timeout
        try:
            while True:
                chunk = self.client_socket.recv(4096)
                if not chunk:
                    break
                response += chunk
                # Check for end of response (double newline - Mac uses \r)
                if b"\n\n" in response or b"\r\r" in response or b"\r\n\r\n" in response:
                    break
        except socket.timeout:
            pass  # Timeout is OK, we have the data
        finally:
            self.client_socket.settimeout(None)

        return response.decode('mac_roman', errors='replace')

    def request_screenshot(self):
        """Request a screenshot from the Mac"""
        if not self.connected:
            return None

        self.client_socket.sendall("SCREENSHOT".encode('mac_roman'))

        # Receive response with timeout
        response = b""
        self.client_socket.settimeout(10.0)
        try:
            while True:
                chunk = self.client_socket.recv(65536)
                if not chunk:
                    break
                response += chunk
                # Check for error response or end marker
                if b"STATUS:" in response and (b"\r\r" in response or b"\n\n" in response):
                    break
                # For IMAGE response, parse header to get size
                if response.startswith(b"IMAGE:") and b"\r" in response:
                    # Parse: IMAGE:<width>:<height>:BMP:<size>\r<data>
                    header_end = response.find(b"\r")
                    if header_end > 0:
                        header = response[:header_end].decode('mac_roman')
                        parts = header.split(":")
                        if len(parts) >= 5:
                            expected_size = int(parts[4])
                            total_expected = header_end + 1 + expected_size
                            if len(response) >= total_expected:
                                break
        except socket.timeout:
            print("Screenshot timeout - partial data received")
        finally:
            self.client_socket.settimeout(None)

        return response

    def close(self):
        """Close connections"""
        if self.client_socket:
            self.client_socket.close()
        if self.server_socket:
            self.server_socket.close()
        self.connected = False


def interactive_mode(server):
    """Interactive command mode"""
    # Check if stdin is available (not background mode)
    import select
    import os

    if not sys.stdin.isatty():
        print("Running in non-interactive mode (no TTY).")
        print(f"Listening for commands on localhost:{CONTROL_PORT}")
        print("Use: uv run python send_command.py 'Directory'")
        print("Keeping connection alive... (Ctrl+C to exit)")

        # Start control socket server
        control_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        control_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        control_socket.bind(('127.0.0.1', CONTROL_PORT))
        control_socket.listen(5)
        control_socket.settimeout(1.0)

        try:
            while server.connected:
                try:
                    ctrl_conn, addr = control_socket.accept()
                    cmd = ctrl_conn.recv(4096).decode('utf-8').strip()
                    if cmd:
                        print(f"Control cmd: {cmd}")
                        if cmd.lower() == 'screenshot':
                            response = server.request_screenshot()
                            ctrl_conn.sendall(f"Got {len(response)} bytes".encode('utf-8'))
                        else:
                            response = server.send_command(cmd)
                            ctrl_conn.sendall((response or "No response").encode('utf-8'))
                    ctrl_conn.close()
                except socket.timeout:
                    continue
        except KeyboardInterrupt:
            pass
        finally:
            control_socket.close()
        return

    print("Interactive mode. Type commands to send to Mac.")
    print("Type 'quit' to exit, 'screenshot' for screenshot.")
    print()

    while True:
        try:
            cmd = input("Command> ").strip()
            if not cmd:
                continue
            if cmd.lower() == 'quit':
                break
            if cmd.lower() == 'screenshot':
                print("Requesting screenshot...")
                response = server.request_screenshot()
                print(f"Got {len(response)} bytes")
                if response and len(response) > 20:
                    # Parse header: IMAGE:<w>:<h>:BMP:<size>\r<data>
                    try:
                        header_end = response.find(b'\r')
                        if header_end > 0:
                            header = response[:header_end].decode('mac_roman')
                            data = response[header_end+1:]
                            parts = header.split(':')
                            if len(parts) >= 5:
                                width = int(parts[1])
                                height = int(parts[2])
                                size = int(parts[4])
                                print(f"Screen: {width}x{height}, data: {size} bytes")
                                # Save raw data
                                with open('/Users/pitforster/Desktop/Share/screenshot.raw', 'wb') as f:
                                    f.write(data[:size] if size > 0 else data)
                                print("Saved to screenshot.raw")
                    except Exception as e:
                        print(f"Parse error: {e}")
                        # Save anyway
                        with open('/Users/pitforster/Desktop/Share/screenshot.raw', 'wb') as f:
                            f.write(response)
                        print("Saved raw response to screenshot.raw")
                continue

            print(f"Sending: {cmd}")
            response = server.send_command(cmd)
            print(f"Response:\n{response}")
            print()

        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"Error: {e}")


def main():
    server = AppleBridgeServer()

    try:
        server.start()
        interactive_mode(server)
    except KeyboardInterrupt:
        print("\nShutting down...")
    finally:
        server.close()


if __name__ == "__main__":
    main()

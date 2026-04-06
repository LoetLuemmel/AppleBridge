"""
AppleBridge Mac Connection
Manages TCP connection from the Mac daemon (Mac connects OUT to us).
"""

import socket
import subprocess
import os
import sys
import threading
from typing import Optional, Tuple

# Default configuration
DEFAULT_PORT = 9000
SHARE_FOLDER = "/Users/pitforster/Desktop/Share"


class MacConnection:
    """
    Connection to classic Mac via AppleBridge.

    The Mac daemon connects OUT to us (reversed architecture due to NAT).
    We listen on a port and wait for the Mac to connect.
    """

    def __init__(self, port: int = DEFAULT_PORT):
        self.port = port
        self.server_socket: Optional[socket.socket] = None
        self.client_socket: Optional[socket.socket] = None
        self.connected = False
        self._listen_thread: Optional[threading.Thread] = None

    def start_server(self) -> bool:
        """Start listening for Mac connection."""
        try:
            self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.server_socket.bind(('0.0.0.0', self.port))
            self.server_socket.listen(1)
            print(f"AppleBridge: Listening on port {self.port}", file=sys.stderr)
            return True
        except Exception as e:
            print(f"AppleBridge: Failed to start server: {e}", file=sys.stderr)
            return False

    def wait_for_connection(self, timeout: float = None) -> bool:
        """Wait for Mac daemon to connect."""
        if not self.server_socket:
            if not self.start_server():
                return False

        try:
            if timeout:
                self.server_socket.settimeout(timeout)
            self.client_socket, addr = self.server_socket.accept()
            self.connected = True
            print(f"AppleBridge: Mac connected from {addr}", file=sys.stderr)
            return True
        except socket.timeout:
            print("AppleBridge: Timeout waiting for Mac connection", file=sys.stderr)
            return False
        except Exception as e:
            print(f"AppleBridge: Connection error: {e}", file=sys.stderr)
            return False

    def connect(self) -> bool:
        """Start server and wait for Mac connection."""
        if self.connected:
            return True
        if not self.server_socket:
            self.start_server()
        # Short timeout - Mac should already be trying to connect
        return self.wait_for_connection(timeout=10.0)

    def start_background_listen(self):
        """Start listening for Mac connection in background thread."""
        if self._listen_thread and self._listen_thread.is_alive():
            return

        if not self.server_socket:
            self.start_server()

        def listen_thread():
            self.wait_for_connection(timeout=None)  # Wait indefinitely

        self._listen_thread = threading.Thread(target=listen_thread, daemon=True)
        self._listen_thread.start()

    def is_connected(self) -> bool:
        """Check if Mac is connected."""
        return self.connected and self.client_socket is not None

    def disconnect(self):
        """Disconnect and stop server."""
        if self.client_socket:
            try:
                self.client_socket.close()
            except:
                pass
        if self.server_socket:
            try:
                self.server_socket.close()
            except:
                pass
        self.client_socket = None
        self.server_socket = None
        self.connected = False

    def send_command(self, command: str, timeout: float = 30.0) -> Tuple[int, str, str]:
        """
        Send a command to the Mac and get response.

        Returns:
            Tuple of (status_code, stdout, stderr)
        """
        if not self.connected or not self.client_socket:
            raise ConnectionError("Mac not connected")

        # Encode command to MacRoman
        encoded_command = command.encode('mac_roman', errors='replace')

        # Format: COMMAND:<length>\n<command>
        header = f"COMMAND:{len(encoded_command)}\n".encode('ascii')
        self.client_socket.sendall(header + encoded_command)

        # Receive response with timeout
        self.client_socket.settimeout(timeout)
        response = b""
        try:
            while True:
                chunk = self.client_socket.recv(4096)
                if not chunk:
                    break
                response += chunk
                # Check for end of response
                if b"\r\r" in response or b"\n\n" in response:
                    break
        except socket.timeout:
            pass
        finally:
            self.client_socket.settimeout(None)

        return self._parse_response(response)

    def _parse_response(self, response: bytes) -> Tuple[int, str, str]:
        """Parse AppleBridge response format."""
        text = response.decode('mac_roman', errors='replace')

        status = 0
        stdout = ""
        stderr = ""

        lines = text.replace('\r', '\n').split('\n')
        i = 0
        while i < len(lines):
            line = lines[i]
            if line.startswith("STATUS:"):
                try:
                    status = int(line[7:])
                except:
                    pass
            elif line.startswith("STDOUT:"):
                try:
                    length = int(line[7:])
                    # Next lines are stdout content
                    i += 1
                    stdout_lines = []
                    chars_read = 0
                    while i < len(lines) and chars_read < length:
                        stdout_lines.append(lines[i])
                        chars_read += len(lines[i]) + 1  # +1 for newline
                        i += 1
                    stdout = '\n'.join(stdout_lines)
                    continue
                except:
                    pass
            elif line.startswith("STDERR:"):
                try:
                    length = int(line[7:])
                    # Next lines are stderr content
                    i += 1
                    stderr_lines = []
                    chars_read = 0
                    while i < len(lines) and chars_read < length:
                        stderr_lines.append(lines[i])
                        chars_read += len(lines[i]) + 1
                        i += 1
                    stderr = '\n'.join(stderr_lines)
                    continue
                except:
                    pass
            i += 1

        return status, stdout.strip(), stderr.strip()

    def take_screenshot(self, output_path: Optional[str] = None) -> Optional[str]:
        """
        Capture screenshot of Basilisk II window.

        Args:
            output_path: Path to save screenshot. If None, uses temp file.

        Returns:
            Path to screenshot file, or None on failure.
        """
        if output_path is None:
            output_path = "/tmp/basilisk_screenshot.png"

        screenshot_script = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "host", "screenshot.py"
        )

        try:
            result = subprocess.run(
                ["/usr/bin/python3", screenshot_script, output_path],
                capture_output=True,
                text=True
            )
            if result.returncode == 0 and os.path.exists(output_path):
                return output_path
        except Exception:
            pass

        return None


# Singleton connection instance
_connection: Optional[MacConnection] = None


def get_connection() -> MacConnection:
    """Get or create the global Mac connection."""
    global _connection
    if _connection is None:
        _connection = MacConnection()
    return _connection



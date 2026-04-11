"""
AppleBridge Mac Connection
Connects to MacintoshBridgeHost server on localhost.
"""

import socket
import subprocess
import os
import sys
from typing import Optional, Tuple

# Default configuration
DEFAULT_HOST = "localhost"
DEFAULT_PORT = 9001
SHARE_FOLDER = "/Users/pitforster/Desktop/Share"


class MacConnection:
    """
    Connection to MacintoshBridgeHost server.

    Connects TO MacintoshBridgeHost on localhost:9001 (control port).
    MacintoshBridgeHost then forwards commands to the Mac daemon.
    """

    def __init__(self, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT):
        self.host = host
        self.port = port
        self.socket: Optional[socket.socket] = None
        self.connected = False

    def connect(self) -> bool:
        """Connect to MacintoshBridgeHost server."""
        if self.connected and self.socket:
            return True

        try:
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.socket.connect((self.host, self.port))
            self.connected = True
            print(f"AppleBridge: Connected to MacintoshBridgeHost at {self.host}:{self.port}", file=sys.stderr)
            return True
        except Exception as e:
            print(f"AppleBridge: Failed to connect to MacintoshBridgeHost: {e}", file=sys.stderr)
            self.socket = None
            self.connected = False
            return False

    def is_connected(self) -> bool:
        """Check if connected to MacintoshBridgeHost."""
        # Test actual connection since we use fresh sockets per command
        try:
            test_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            test_sock.settimeout(1.0)
            test_sock.connect((self.host, self.port))
            test_sock.close()
            return True
        except:
            return False

    def disconnect(self):
        """Disconnect from MacintoshBridgeHost."""
        if self.socket:
            try:
                self.socket.close()
            except:
                pass
        self.socket = None
        self.connected = False

    def send_command(self, command: str, timeout: float = 30.0) -> Tuple[int, str, str]:
        """
        Send a command to MacintoshBridgeHost and get response.

        Returns:
            Tuple of (status_code, stdout, stderr)
        """
        # Always create a fresh connection for each command
        # This ensures we don't have stale data and the server can close cleanly
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)

        try:
            sock.connect((self.host, self.port))

            # Send raw command - LocalControlServer will format it for Mac daemon
            # IMPORTANT: Encode as MacRoman, not UTF-8!
            message = f"{command}\n\n"
            sock.sendall(message.encode('mac_roman'))

            # Receive response until connection closes
            response = b""
            while True:
                try:
                    chunk = sock.recv(4096)
                    if not chunk:
                        # Connection closed by server - this is normal
                        break
                    response += chunk
                except socket.timeout:
                    # Timeout - use what we have
                    break

        except Exception as e:
            print(f"AppleBridge: Command error: {e}", file=sys.stderr)
            raise
        finally:
            sock.close()

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



# Singleton connection instance
_connection: Optional[MacConnection] = None


def get_connection() -> MacConnection:
    """Get or create the global Mac connection."""
    global _connection
    if _connection is None:
        _connection = MacConnection()
    return _connection



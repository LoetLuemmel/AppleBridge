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

# Opt-in control-port secret. When set (and the host server has a matching
# APPLEBRIDGE_CTRL_TOKEN), each request is prefixed with an "AUTH:<token>\n" line;
# empty (default) means the guard is off and behaviour is unchanged.
CTRL_TOKEN = os.environ.get("APPLEBRIDGE_CTRL_TOKEN", "")


class MacConnection:
    """
    Connection to the AppleBridge control port (localhost:9001).

    The control port is served by the hardened host_server.py (the proven
    path) or, in the native setup, by the Swift MacintoshBridgeHost. It
    forwards each command to the Mac daemon and returns STATUS/STDOUT/STDERR.
    A fresh socket is opened per command.
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
        """Return True if the host control server (:9001) is accepting connections.

        NOTE: this only proves host_server.py is up — it does NOT guarantee a
        Mac daemon is connected behind it. That is fine as a fast pre-check:
        send_command() now surfaces a "daemon not connected" error for any
        STATUS-less reply, so callers cannot be fooled into a false success.
        """
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

            # Send raw command terminated by \n\n. The :9001 control server
            # (hardened host_server.py, or the Swift MacintoshBridgeHost)
            # forwards it to the Mac daemon, which re-encodes to MacRoman.
            # Use UTF-8 to match the control-port decoder (utf-8). When a
            # control-port token is configured, lead with an "AUTH:<token>\n" line.
            auth = f"AUTH:{CTRL_TOKEN}\n" if CTRL_TOKEN else ""
            message = f"{auth}{command}\n\n"
            sock.sendall(message.encode('utf-8'))

            # Receive until the control server closes the socket (the normal
            # end-of-reply signal) or we hit the read timeout. A clean reply
            # always ends with a server-side close, so a timeout means the
            # reply is incomplete and must not be trusted.
            response = b""
            timed_out = False
            while True:
                try:
                    chunk = sock.recv(4096)
                    if not chunk:
                        # Connection closed by server - clean end of reply
                        break
                    response += chunk
                except socket.timeout:
                    timed_out = True
                    break

        except Exception as e:
            print(f"AppleBridge: Command error: {e}", file=sys.stderr)
            raise
        finally:
            sock.close()

        status, stdout, stderr = self._parse_response(response)

        if timed_out:
            # Incomplete reply: annotate, and downgrade a stray STATUS:0 to an
            # error so a truncated response is never seen as a clean success.
            note = f"client read timed out after {timeout:g}s (response may be truncated)"
            stderr = f"{stderr}; {note}" if stderr else note
            if status == 0:
                status = -2

        return status, stdout, stderr

    def _parse_response(self, response: bytes) -> Tuple[int, str, str]:
        """Parse the control server's framed reply into (status, stdout, stderr).

        Robustness contract: a reply with **no** ``STATUS:`` line is never a
        success. The control server emits bare, unframed ``No response`` /
        ``ERROR: ...`` sentinels when the Mac daemon is down or a command never
        round-trips, and a truncated frame can likewise lack a parseable
        STATUS. Previously the status defaulted to ``0`` (success), so all of
        these were silently reported as a clean, empty result. We now return a
        negative status for every STATUS-less reply so the caller cannot
        mistake a non-event for success.
        """
        # The :9001 control server returns the response as UTF-8.
        text = response.decode('utf-8', errors='replace')
        stripped = text.strip()

        # Bare, unframed control-server sentinels => the command did not run.
        if not stripped:
            return -1, "", "Empty reply from control server (is host_server.py running?)."
        if stripped == "No response":
            return -1, "", "Mac daemon not connected (control server returned 'No response')."
        if stripped.startswith("ERROR:"):
            return -1, "", stripped

        status: Optional[int] = None  # None => no STATUS line seen yet
        stdout = ""
        stderr = ""

        lines = text.replace('\r', '\n').split('\n')
        i = 0
        while i < len(lines):
            line = lines[i]
            if line.startswith("STATUS:"):
                try:
                    status = int(line[7:])
                except ValueError:
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
                except ValueError:
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
                except ValueError:
                    pass
            i += 1

        if status is None:
            # Bytes arrived but there was no parseable STATUS frame: treat a
            # malformed/truncated reply as an error, never as success.
            detail = stderr.strip() or f"malformed response, no STATUS line: {stripped[:200]!r}"
            return -1, stdout.strip(), detail

        return status, stdout.strip(), stderr.strip()



# Singleton connection instance
_connection: Optional[MacConnection] = None


def get_connection() -> MacConnection:
    """Get or create the global Mac connection."""
    global _connection
    if _connection is None:
        _connection = MacConnection()
    return _connection



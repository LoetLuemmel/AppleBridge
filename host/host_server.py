#!/usr/bin/env python3
"""
AppleBridge Host Server (hardened)

Mac daemon connects OUT to this server on 192.168.3.154:9000 (reversed
architecture due to NAT). Local control clients (send_command.py, build.py,
the MCP layer) connect to localhost:9001 and get one command executed per
connection.

Hardening over the original (host-only, no 68k daemon changes):
  * Accept-LOOP: bind :9000 / :9001 once, then re-accept the Mac daemon
    automatically whenever it drops. No more manual server restarts — the
    daemon's 30s auto-reconnect is picked up on its own.
  * Disconnect detection: a dead Mac socket is distinguished from a command
    timeout; on disconnect we close the socket and wait for re-accept.
  * Anti-desync drain: stale/late bytes (e.g. a Link reply that arrived after
    its timeout) are discarded before the next command, so responses can't
    shift by one frame.
  * Adaptive timeouts: long for Link/SC/Asm/Make/DumpFile, short otherwise.
  * Structured, unbuffered logging to stderr + /tmp/applebridge_server.log.

NOTE: the daemon hardcodes the host IP (192.168.3.154) and the 65536-byte
response buffer; large-output framing/overflow fixes need a 68k rebuild and
are intentionally out of scope here.
"""
import socket
import sys
import time

HOST_INTERFACE = "192.168.3.154"   # single source of truth; daemon connects here
HOST_PORT = 9000                   # Mac daemon connects to this
CONTROL_PORT = 9001                # local control clients connect to this
LOG_PATH = "/tmp/applebridge_server.log"

# Adaptive timeouts (seconds), chosen by the command's first token.
LONG_CMDS = {
    "link", "ilink", "sc", "scpp", "asm", "make",
    "dumpobj", "dumpfile", "rez", "derez", "lib", "duplicate",
}
DEFAULT_TIMEOUT = 15.0
LONG_TIMEOUT = 120.0
SCREENSHOT_TIMEOUT = 15.0

_logf = open(LOG_PATH, "a", buffering=1)  # line-buffered


def log(msg):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, file=sys.stderr, flush=True)
    try:
        _logf.write(line + "\n")
    except Exception:
        pass


def timeout_for(command):
    parts = command.strip().split(None, 1)
    tok = parts[0].lower() if parts else ""
    return LONG_TIMEOUT if tok in LONG_CMDS else DEFAULT_TIMEOUT


class AppleBridgeServer:
    def __init__(self, interface=HOST_INTERFACE, port=HOST_PORT):
        self.interface = interface
        self.port = port
        self.client_socket = None
        self.server_socket = None
        self.connected = False

    def bind_listen(self):
        """Bind and listen on :9000 once (kept open across re-accepts)."""
        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server_socket.bind((self.interface, self.port))
        self.server_socket.listen(1)
        log(f"Listening on {self.interface}:{self.port} (waiting for Mac daemon)")

    def accept_mac(self):
        """Block until a Mac daemon connects; (re)assign client_socket."""
        self.client_socket, addr = self.server_socket.accept()
        self.connected = True
        log(f"Mac connected from {addr}")
        return addr

    def _mark_disconnected(self, reason):
        log(f"Mac disconnected: {reason}")
        self.connected = False
        if self.client_socket:
            try:
                self.client_socket.close()
            except OSError:
                pass
            self.client_socket = None

    def _drain(self):
        """Discard any stale/late bytes before a new command (anti-desync).

        Returns True if the socket is still alive, False if the peer closed.
        """
        if not self.client_socket:
            return False
        alive = True
        try:
            self.client_socket.setblocking(False)
            while True:
                try:
                    chunk = self.client_socket.recv(65536)
                except BlockingIOError:
                    break          # nothing pending -> healthy idle socket
                except OSError:
                    alive = False
                    break
                if not chunk:
                    alive = False  # peer closed
                    break
                log(f"drained {len(chunk)} stale bytes (anti-desync)")
        finally:
            try:
                if self.client_socket:
                    self.client_socket.setblocking(True)
            except OSError:
                alive = False
        return alive

    def send_command(self, command):
        """Send a command to the Mac, return the raw response string (or None)."""
        if not self.connected or not self.client_socket:
            return None

        if not self._drain():
            self._mark_disconnected("drain detected closed socket")
            return None

        encoded = command.encode("mac_roman", errors="replace")
        header = f"COMMAND:{len(encoded)}\n".encode("ascii")
        try:
            self.client_socket.sendall(header + encoded)
        except OSError as e:
            self._mark_disconnected(f"send failed: {e}")
            return None

        to = timeout_for(command)
        response = b""
        try:
            self.client_socket.settimeout(to)
            while True:
                chunk = self.client_socket.recv(4096)
                if not chunk:
                    self._mark_disconnected("recv 0 (peer closed mid-response)")
                    break
                response += chunk
                if b"\n\n" in response or b"\r\r" in response or b"\r\n\r\n" in response:
                    break
        except socket.timeout:
            log(f"command timeout after {to:.0f}s: {command[:48]!r}")
        except OSError as e:
            self._mark_disconnected(f"recv error: {e}")
        finally:
            try:
                if self.client_socket:
                    self.client_socket.settimeout(None)
            except OSError:
                pass

        if not response:
            return None
        return response.decode("mac_roman", errors="replace")

    def send_raw(self, data):
        """Send a RAW verb (PING / LAUNCH:<path>) — no COMMAND: wrapper.

        The daemon's verb dispatch (ProcessRequest) matches the raw request
        bytes, exactly like SCREENSHOT. Returns the raw response string.
        """
        if not self.connected or not self.client_socket:
            return None
        if not self._drain():
            self._mark_disconnected("drain detected closed socket")
            return None
        try:
            self.client_socket.sendall(data.encode("mac_roman", errors="replace"))
        except OSError as e:
            self._mark_disconnected(f"send failed: {e}")
            return None

        response = b""
        try:
            self.client_socket.settimeout(DEFAULT_TIMEOUT)
            while True:
                chunk = self.client_socket.recv(4096)
                if not chunk:
                    self._mark_disconnected("recv 0 (peer closed mid-response)")
                    break
                response += chunk
                if b"\n\n" in response or b"\r\r" in response or b"\r\n\r\n" in response:
                    break
        except socket.timeout:
            log(f"raw verb timeout: {data[:48]!r}")
        except OSError as e:
            self._mark_disconnected(f"recv error: {e}")
        finally:
            try:
                if self.client_socket:
                    self.client_socket.settimeout(None)
            except OSError:
                pass
        return response.decode("mac_roman", errors="replace") if response else None

    def request_screenshot(self):
        """Request a screenshot; return raw bytes (or None)."""
        if not self.connected or not self.client_socket:
            return None
        if not self._drain():
            self._mark_disconnected("drain detected closed socket")
            return None
        try:
            self.client_socket.sendall("SCREENSHOT".encode("mac_roman"))
        except OSError as e:
            self._mark_disconnected(f"send failed: {e}")
            return None

        response = b""
        try:
            self.client_socket.settimeout(SCREENSHOT_TIMEOUT)
            while True:
                chunk = self.client_socket.recv(65536)
                if not chunk:
                    self._mark_disconnected("recv 0 during screenshot")
                    break
                response += chunk
                if b"STATUS:" in response and (b"\r\r" in response or b"\n\n" in response):
                    break
                if response.startswith(b"IMAGE:") and b"\r" in response:
                    header_end = response.find(b"\r")
                    if header_end > 0:
                        header = response[:header_end].decode("mac_roman")
                        parts = header.split(":")
                        if len(parts) >= 5:
                            expected = int(parts[4])
                            if len(response) >= header_end + 1 + expected:
                                break
        except socket.timeout:
            log("screenshot timeout - partial data received")
        except OSError as e:
            self._mark_disconnected(f"recv error during screenshot: {e}")
        finally:
            try:
                if self.client_socket:
                    self.client_socket.settimeout(None)
            except OSError:
                pass
        return response or None

    def close(self):
        if self.client_socket:
            try:
                self.client_socket.close()
            except OSError:
                pass
        if self.server_socket:
            try:
                self.server_socket.close()
            except OSError:
                pass
        self.connected = False


def _recv_control_command(conn):
    """Read one command from a control client.

    Handles both clients: send_command.py shuts down its write side (EOF),
    the MCP layer sends '<command>\\n\\n' and keeps the socket open. So read
    until a '\\n\\n' terminator OR EOF.
    """
    conn.settimeout(6.0)
    data = b""
    try:
        while True:
            chunk = conn.recv(4096)
            if not chunk:
                break               # EOF (send_command.py)
            data += chunk
            if b"\n\n" in data:
                break               # terminator (MCP / mac_connection)
    except socket.timeout:
        pass
    return data.decode("utf-8", errors="replace").strip()


def run_control_server(server):
    """Non-TTY production path: serve control commands, auto-re-accept the Mac."""
    control = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    control.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    control.bind(("127.0.0.1", CONTROL_PORT))
    control.listen(5)
    control.settimeout(1.0)
    log(f"Control port on localhost:{CONTROL_PORT} (send_command.py / MCP)")

    try:
        while True:
            # Make sure a Mac is connected before serving commands.
            if not server.connected:
                log("Waiting for Mac daemon to (re)connect...")
                server.accept_mac()

            try:
                ctrl_conn, _addr = control.accept()
            except socket.timeout:
                continue

            try:
                cmd = _recv_control_command(ctrl_conn)
                if cmd:
                    if cmd.lower() == "screenshot":
                        resp = server.request_screenshot()
                        out = f"Got {len(resp)} bytes" if resp else "No response"
                    elif cmd == "PING" or cmd.startswith("LAUNCH:"):
                        log(f"verb: {cmd[:60]!r}")
                        resp = server.send_raw(cmd)   # raw, not COMMAND-wrapped
                        out = resp if resp is not None else "No response"
                    else:
                        log(f"cmd: {cmd[:60]!r}")
                        resp = server.send_command(cmd)
                        out = resp if resp is not None else "No response"
                    ctrl_conn.sendall(out.encode("utf-8", errors="replace"))
            except Exception as e:  # never let one bad control conn kill the server
                log(f"control error: {e}")
                try:
                    ctrl_conn.sendall(f"ERROR: {e}".encode("utf-8"))
                except OSError:
                    pass
            finally:
                try:
                    ctrl_conn.close()
                except OSError:
                    pass
    except KeyboardInterrupt:
        pass
    finally:
        control.close()


def interactive_mode(server):
    """TTY path: type commands directly (manual debugging)."""
    print("Interactive mode. Type commands to send to Mac.")
    print("Type 'quit' to exit, 'screenshot' for screenshot.")
    print()
    while True:
        try:
            cmd = input("Command> ").strip()
            if not cmd:
                continue
            if cmd.lower() == "quit":
                break
            if cmd.lower() == "screenshot":
                resp = server.request_screenshot()
                print(f"Got {len(resp) if resp else 0} bytes")
                continue
            resp = server.send_command(cmd)
            print(f"Response:\n{resp}\n")
        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"Error: {e}")


def main():
    server = AppleBridgeServer()
    log("=== AppleBridge Host Server (hardened) ===")
    server.bind_listen()
    try:
        if sys.stdin.isatty():
            server.accept_mac()
            interactive_mode(server)
        else:
            run_control_server(server)
    except KeyboardInterrupt:
        log("Shutting down...")
    finally:
        server.close()


if __name__ == "__main__":
    main()

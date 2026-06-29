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

NOTE: the daemon hardcodes the host IP (192.168.3.154). Large responses
(>64 KB, up to a 4 MB cap) are now streamed by the daemon from a dynamically
allocated handle and read here by their declared STDOUT:<len> (length-framing,
already content-agnostic) — no per-response size limit on this side.
"""
import base64
import socket
import sys
import time

import screenshot_decode  # stdlib-only raw-pixmap -> PNG (runs under /usr/bin/python3)
import macbinary          # stdlib-only fork split/join for READFILE packaging

HOST_INTERFACE = "192.168.3.154"   # single source of truth; daemon connects here
HOST_PORT = 9000                   # Mac daemon connects to this
CONTROL_PORT = 9001                # local control clients connect to this
LOG_PATH = "/tmp/applebridge_server.log"

# Adaptive timeouts (seconds), chosen by the command's first token.
# Includes the large-output readers (catenate/files/print) so big file reads
# and listings get the long transfer budget instead of truncating at 15s —
# mac_read_file uses Catenate and mac_list_files uses Files.
LONG_CMDS = {
    "link", "ilink", "sc", "scpp", "asm", "make",
    "dumpobj", "dumpfile", "rez", "derez", "lib", "duplicate",
    "catenate", "files", "print",
}
DEFAULT_TIMEOUT = 15.0
LONG_TIMEOUT = 240.0   # multi-MB transfers (large DumpFile/Catenate) over OT
SCREENSHOT_TIMEOUT = 30.0   # full-screen pixmap transfer + decode

# Application-level heartbeat (design: /applebridge/designing-an-application-level-heartbeat/).
# The host is the ACTIVE party: during idle it PINGs the daemon every
# HEARTBEAT_INTERVAL s so the daemon's passive last-RX watchdog stays fed and so
# the host itself notices a dead/replaced daemon connection (the MACNAT path does
# not reliably deliver FIN/RST). The daemon's watchdog threshold (~30 s) must stay
# well above this interval. A missing PONG is read-bounded by HEARTBEAT_TIMEOUT;
# HEARTBEAT_MAX_MISS consecutive misses tear the connection down so the loop
# re-accepts (e.g. the daemon reconnected with a fresh socket).
HEARTBEAT_INTERVAL = 10.0   # seconds of link idle before emitting a PING
HEARTBEAT_TIMEOUT = 4.0     # bounded read for the PONG (must not hang the loop)
HEARTBEAT_MAX_MISS = 2      # consecutive unanswered PINGs -> declare disconnected

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


def screenshot_png(shot):
    """Decode a request_screenshot() dict to PNG bytes (or None)."""
    if not shot:
        return None
    return screenshot_decode.raw_to_png(
        shot["width"], shot["height"], shot["depth"],
        shot["row_bytes"], shot["clut"], shot["pixels"])


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
        buf = bytearray()

        # --- framed reader -------------------------------------------------
        # The daemon's SendCommandResult streams:
        #   STATUS:<code>\r STDOUT:<olen>\r <olen bytes>\r STDERR:<elen>\r <elen bytes>\r \r
        # The old code stopped at the first \r\r / \n\n in the stream, which
        # TRUNCATED any output that itself contained a blank line (e.g. C source
        # via Catenate). Measured root cause: daemon sent the full data, host
        # quit early. Fix: read STDOUT by its DECLARED length, then read the
        # (small) STDERR+terminator remainder the old way. Non-STATUS responses
        # (e.g. IMAGE screenshots) fall back to the legacy terminator read.
        def _fill():
            chunk = self.client_socket.recv(65536)   # large reads for MB-scale stdout
            if not chunk:
                raise ConnectionError("peer closed mid-response")
            buf.extend(chunk)

        def _read_line():
            pos = 0
            while True:
                while pos < len(buf):
                    if buf[pos] in (13, 10):        # \r or \n
                        line = bytes(buf[:pos])
                        term = buf[pos]
                        del buf[:pos + 1]
                        if buf and ((term == 13 and buf[0] == 10) or
                                    (term == 10 and buf[0] == 13)):
                            del buf[:1]             # swallow paired EOL
                        return line
                    pos += 1
                _fill()

        def _read_exact(n):
            while len(buf) < n:
                _fill()
            data = bytes(buf[:n])
            del buf[:n]
            return data

        def _read_until_terminator():
            while True:
                b = bytes(buf)
                if b"\n\n" in b or b"\r\r" in b or b"\r\n\r\n" in b:
                    data = bytes(buf)
                    buf.clear()
                    return data
                _fill()

        raw = None
        outcome = "framed"
        try:
            self.client_socket.settimeout(to)
            while len(buf) < 7:                      # enough to recognise "STATUS:"
                _fill()
            if bytes(buf[:7]) == b"STATUS:":
                status_line = _read_line()           # b"STATUS:<code>"
                out_hdr = _read_line()               # b"STDOUT:<olen>"
                try:
                    olen = int(out_hdr.split(b":", 1)[1].strip() or b"0")
                except (ValueError, IndexError):
                    olen = -1
                if olen >= 0:
                    stdout = _read_exact(olen)       # exact — no false early stop
                    rest = _read_until_terminator()  # \r + STDERR hdr + data + end (small)
                    raw = status_line + b"\r" + out_hdr + b"\r" + stdout + rest
                else:
                    rest = _read_until_terminator()
                    raw = status_line + b"\r" + out_hdr + b"\r" + rest
            else:
                outcome = "terminator"               # non-STATUS (IMAGE etc.): legacy read
                raw = _read_until_terminator()
        except socket.timeout:
            outcome = "timeout"
            log(f"command timeout after {to:.0f}s: {command[:48]!r}")
            raw = bytes(buf) if buf else None
        except (OSError, ConnectionError) as e:
            outcome = "closed"
            self._mark_disconnected(f"recv error: {e}")
            raw = bytes(buf) if buf else None
        finally:
            try:
                if self.client_socket:
                    self.client_socket.settimeout(None)
            except OSError:
                pass

        if raw is None:
            return None
        if len(raw) > 1000 or outcome != "framed":
            log(f"recv {len(raw)}B outcome={outcome} cmd={command[:32]!r}")
        return bytes(raw).decode("mac_roman", errors="replace")

    def send_raw(self, data, timeout=DEFAULT_TIMEOUT):
        """Send a RAW verb (PING / LAUNCH:<path>) — no COMMAND: wrapper.

        The daemon's verb dispatch (ProcessRequest) matches the raw request
        bytes, exactly like SCREENSHOT. Returns the raw response string.
        `timeout` bounds the response read (short for heartbeats).
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
            self.client_socket.settimeout(timeout)
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

    def heartbeat(self):
        """Send one liveness PING with a short bounded read. Returns True if the
        daemon answered with ANY bytes — content is deliberately not validated, so
        a possibly-corrupt first packet after a reconnect still counts as 'alive'
        (we only care that the peer is responsive). Returns False on no reply; the
        caller counts consecutive misses before tearing the connection down."""
        if not self.connected or not self.client_socket:
            return False
        resp = self.send_raw("PING", timeout=HEARTBEAT_TIMEOUT)
        return resp is not None

    def request_screenshot(self):
        """Request a screenshot. Returns a dict with the decoded pixmap parts:

            {width, height, depth, row_bytes, clut: bytes, pixels: bytes}

        or None on failure. The daemon streams:
            IMAGE:<w>:<h>:<depth>:<rowBytes>:<clutCount>:<dataSize>\\n
            <clutCount*3 CLUT bytes><dataSize pixel bytes>
        Read length-framed — the same content-agnostic approach as commands.
        """
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

        buf = bytearray()

        def _fill():
            chunk = self.client_socket.recv(65536)
            if not chunk:
                raise ConnectionError("peer closed during screenshot")
            buf.extend(chunk)

        def _read_line():
            # The daemon terminates the header with CR (0x0D) — classic-Mac C
            # maps '\n' to CR — so accept either CR or LF as the line end.
            while True:
                cr = buf.find(b"\r")
                lf = buf.find(b"\n")
                idx = min([x for x in (cr, lf) if x >= 0], default=-1)
                if idx >= 0:
                    line = bytes(buf[:idx])
                    del buf[:idx + 1]
                    return line
                _fill()

        def _read_exact(n):
            while len(buf) < n:
                _fill()
            data = bytes(buf[:n])
            del buf[:n]
            return data

        try:
            self.client_socket.settimeout(SCREENSHOT_TIMEOUT)
            header = _read_line()
            if not header.startswith(b"IMAGE:"):
                # Daemon reported an error (e.g. capture failed) as a STATUS frame.
                log(f"screenshot: non-IMAGE response {header[:48]!r}")
                return None
            parts = header.split(b":")
            if len(parts) < 7:
                log(f"screenshot: malformed header {header[:48]!r}")
                return None
            w, h, depth, rb, cc, ds = (int(parts[i]) for i in range(1, 7))
            clut = _read_exact(cc * 3) if cc > 0 else b""
            pixels = _read_exact(ds)
            log(f"screenshot {w}x{h} depth={depth} rowBytes={rb} clut={cc} data={ds}B")
            return {"width": w, "height": h, "depth": depth,
                    "row_bytes": rb, "clut": clut, "pixels": pixels}
        except (socket.timeout, ValueError) as e:
            log(f"screenshot read error: {e}; got {len(buf)}B")
            return None
        except (OSError, ConnectionError) as e:
            self._mark_disconnected(f"recv error during screenshot: {e}")
            return None
        finally:
            try:
                if self.client_socket:
                    self.client_socket.settimeout(None)
            except OSError:
                pass

    def put_file(self, mac_path, type_bytes, creator_bytes, data, rsrc):
        """WRITEFILE: stream a file (both forks + type/creator) to the Mac.

        Sends the binary-clean, length-framed daemon frame:
          WRITEFILE:<pathLen>:<typeHex8>:<creatorHex8>:<dataLen>:<rsrcLen>\\n
                    <pathBytes><dataBytes><rsrcBytes>
        and returns the daemon's STATUS reply string. The fork bytes go out as
        RAW bytes (NOT through the lossy mac_roman encode used for commands)."""
        if not self.connected or not self.client_socket:
            return None
        if not self._drain():
            self._mark_disconnected("drain detected closed socket")
            return None
        path_bytes = mac_path.encode("mac_roman", errors="replace")
        type_hex = bytes(type_bytes[:4].ljust(4, b" ")).hex()
        creator_hex = bytes(creator_bytes[:4].ljust(4, b" ")).hex()
        header = (f"WRITEFILE:{len(path_bytes)}:{type_hex}:{creator_hex}:"
                  f"{len(data)}:{len(rsrc)}\n").encode("ascii")
        try:
            self.client_socket.sendall(header + path_bytes + data + rsrc)
        except OSError as e:
            self._mark_disconnected(f"send failed: {e}")
            return None
        log(f"WRITEFILE {mac_path!r} data={len(data)}B rsrc={len(rsrc)}B")
        resp = b""
        try:
            self.client_socket.settimeout(LONG_TIMEOUT)
            while True:
                chunk = self.client_socket.recv(4096)
                if not chunk:
                    self._mark_disconnected("recv 0 during WRITEFILE")
                    break
                resp += chunk
                if b"\r\r" in resp or b"\n\n" in resp or b"\r\n\r\n" in resp:
                    break
        except socket.timeout:
            log("WRITEFILE reply timeout")
        except OSError as e:
            self._mark_disconnected(f"recv error: {e}")
        finally:
            try:
                if self.client_socket:
                    self.client_socket.settimeout(None)
            except OSError:
                pass
        return resp.decode("mac_roman", errors="replace") if resp else None

    def get_file(self, mac_path):
        """READFILE: pull a file's forks back. Returns (type, creator, data, rsrc)
        or None on failure / error frame."""
        if not self.connected or not self.client_socket:
            return None
        if not self._drain():
            self._mark_disconnected("drain detected closed socket")
            return None
        try:
            self.client_socket.sendall(("READFILE:" + mac_path).encode("mac_roman",
                                                                       errors="replace"))
        except OSError as e:
            self._mark_disconnected(f"send failed: {e}")
            return None

        buf = bytearray()

        def _fill():
            chunk = self.client_socket.recv(65536)
            if not chunk:
                raise ConnectionError("peer closed during READFILE")
            buf.extend(chunk)

        def _read_line():
            while True:
                cr = buf.find(b"\r")
                lf = buf.find(b"\n")
                idx = min([x for x in (cr, lf) if x >= 0], default=-1)
                if idx >= 0:
                    line = bytes(buf[:idx])
                    del buf[:idx + 1]
                    return line
                _fill()

        def _read_exact(n):
            while len(buf) < n:
                _fill()
            data = bytes(buf[:n])
            del buf[:n]
            return data

        try:
            self.client_socket.settimeout(LONG_TIMEOUT)
            header = _read_line()
            if not header.startswith(b"FILE:"):
                log(f"READFILE: non-FILE response {header[:64]!r}")
                return None
            parts = header.split(b":")
            if len(parts) < 5:
                log(f"READFILE: malformed header {header[:64]!r}")
                return None
            type_bytes = bytes.fromhex(parts[1].decode("ascii"))
            creator_bytes = bytes.fromhex(parts[2].decode("ascii"))
            data_len = int(parts[3])
            rsrc_len = int(parts[4])
            data = _read_exact(data_len)
            rsrc = _read_exact(rsrc_len)
            log(f"READFILE {mac_path!r} data={data_len}B rsrc={rsrc_len}B")
            return (type_bytes, creator_bytes, data, rsrc)
        except (socket.timeout, ValueError) as e:
            log(f"READFILE read error: {e}")
            return None
        except (OSError, ConnectionError) as e:
            self._mark_disconnected(f"recv error during READFILE: {e}")
            return None
        finally:
            try:
                if self.client_socket:
                    self.client_socket.settimeout(None)
            except OSError:
                pass

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

    last_io = time.monotonic()   # last time we exchanged anything with the daemon
    missed = 0                   # consecutive unanswered heartbeats
    try:
        while True:
            # Make sure a Mac is connected before serving commands.
            if not server.connected:
                log("Waiting for Mac daemon to (re)connect...")
                server.accept_mac()
                # The first packet over a fresh OT/MACNAT connection is often
                # corrupt; send one priming PING and discard its (possibly
                # garbled) reply so a real command isn't the one that eats it.
                server.heartbeat()
                last_io = time.monotonic()
                missed = 0

            try:
                ctrl_conn, _addr = control.accept()
            except socket.timeout:
                # Idle tick (~1 s). If the link has been quiet, emit a heartbeat
                # so the daemon's watchdog stays fed and we notice a dead/replaced
                # connection. Skip while a real command is obviously not in flight.
                if server.connected and time.monotonic() - last_io >= HEARTBEAT_INTERVAL:
                    if server.heartbeat():
                        missed = 0
                    else:
                        missed += 1
                        log(f"heartbeat missed ({missed}/{HEARTBEAT_MAX_MISS})")
                        if missed >= HEARTBEAT_MAX_MISS:
                            server._mark_disconnected("heartbeat lost")
                            missed = 0
                    last_io = time.monotonic()
                continue

            try:
                cmd = _recv_control_command(ctrl_conn)
                if cmd:
                    if cmd.lower() == "screenshot":
                        shot = server.request_screenshot()
                        try:
                            png = screenshot_png(shot)
                        except Exception as e:
                            png = None
                            log(f"screenshot decode failed: {e}")
                        if png:
                            b64 = base64.b64encode(png).decode("ascii")
                            # Framed STATUS/STDOUT so the MCP text parser extracts
                            # the base64 PNG into stdout (base64 is ASCII-safe).
                            out = f"STATUS:0\rSTDOUT:{len(b64)}\r{b64}\rSTDERR:0\r\r"
                            log(f"screenshot -> {len(png)}B PNG ({len(b64)}B base64)")
                        else:
                            out = "STATUS:-1\rSTDOUT:0\rSTDERR:17\rScreenshot failed\r\r"
                    elif cmd.startswith("WRITEFILE:"):
                        # WRITEFILE:<pathB64>:<typeHex8>:<creatorHex8>:<dataB64>:<rsrcB64>
                        # (every variable field base64/hex -> colon-free, so split is safe)
                        try:
                            f = cmd.split(":")
                            mac_path = base64.b64decode(f[1]).decode("mac_roman",
                                                                     errors="replace")
                            type_b = bytes.fromhex(f[2])
                            creator_b = bytes.fromhex(f[3])
                            data = base64.b64decode(f[4]) if f[4] else b""
                            rsrc = base64.b64decode(f[5]) if len(f) > 5 and f[5] else b""
                            resp = server.put_file(mac_path, type_b, creator_b, data, rsrc)
                            out = resp if resp is not None else "No response"
                        except Exception as e:
                            log(f"WRITEFILE parse/send error: {e}")
                            msg = str(e)
                            out = f"STATUS:-1\rSTDOUT:0\rSTDERR:{len(msg)}\r{msg}\r\r"
                    elif cmd.startswith("READFILE:"):
                        mac_path = cmd[len("READFILE:"):]
                        got = server.get_file(mac_path)
                        if got:
                            type_b, creator_b, data, rsrc = got
                            leaf = mac_path.rsplit(":", 1)[-1] or "File"
                            blob = macbinary.encode(data, rsrc, name=leaf,
                                                    type_=type_b, creator=creator_b)
                            b64 = base64.b64encode(blob).decode("ascii")
                            out = f"STATUS:0\rSTDOUT:{len(b64)}\r{b64}\rSTDERR:0\r\r"
                            log(f"READFILE -> {len(blob)}B MacBinary ({len(b64)}B base64)")
                        else:
                            out = "STATUS:-1\rSTDOUT:0\rSTDERR:14\rREADFILE failed\r\r"
                    elif (cmd == "PING" or cmd == "QUITDAEMON"
                          or cmd.startswith("LAUNCH:") or cmd.startswith("QUIT:")):
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
                # A served command is itself fresh traffic; defer the next heartbeat.
                last_io = time.monotonic()
                missed = 0
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
                shot = server.request_screenshot()
                png = screenshot_png(shot) if shot else None
                if png:
                    path = "/tmp/basilisk_shot.png"
                    with open(path, "wb") as f:
                        f.write(png)
                    print(f"Saved {len(png)} bytes -> {path} "
                          f"({shot['width']}x{shot['height']} depth {shot['depth']})")
                else:
                    print("Screenshot failed")
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

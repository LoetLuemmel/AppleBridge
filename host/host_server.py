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
import hmac
import json
import os
import select
import socket
import sys
import time

import screenshot_decode  # stdlib-only raw-pixmap -> PNG (runs under /usr/bin/python3)
import macbinary          # stdlib-only fork split/join for READFILE packaging
try:
    import bridge_doctor  # stdlib-only cross-layer stack diagnosis (DOCTOR verb)
except ImportError:       # deployed copy predating the module: degrade, don't die
    bridge_doctor = None

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
# NBP always runs its full retry window before answering (it cannot know when
# the last reply has arrived), so the daemon needs ~3 s of protocol time before
# it sends anything. Budget well above that so a busy zone can't look like a
# timeout.
NBP_TIMEOUT = 20.0
# Mounting talks to a file server over AppleTalk (name lookup, login, volume
# open), so it needs more room than a local File Manager call.
AFP_TIMEOUT = 45.0

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

# How long one pass of the control loop waits for a Mac daemon to turn up before
# going back to serving :9001. Short enough that the control port keeps its ~1 s
# responsiveness while the emulator is down, long enough not to spin the CPU.
MAC_ACCEPT_POLL = 0.5

# --- Protocol v0.2 (see docs/PROTOCOL_v0.2.md) -------------------------------
# Version negotiation + bounded reads ship HOST-side first and are fully
# backward compatible: the currently deployed v0.1 daemon answers the HELLO
# probe with an ordinary "Invalid command format" framed error (no ABVERSION),
# which we read as "legacy peer" and then behave exactly as before.
#
# Auth (the token + digest below) is DORMANT in this PR: the plumbing and its
# unit tests are here, but no daemon verifies it yet, and the host neither
# checks the daemon's PROOF nor sends AUTH2 — that is PR3. When AUTH_TOKEN is
# set we do send a real nonce in HELLO so a future v0.2 daemon can prove the
# token, but nothing gates command flow on it here.
AB_PROTOCOL_VERSION = 2
HELLO_TIMEOUT = 4.0
# Reject any peer-declared payload length above this BEFORE reading/allocating,
# so a corrupt or hostile length can neither hang a reader nor exhaust memory.
# Matches the guest's MAX_FILE_BYTES / MAX_DYNAMIC_RESPONSE ceilings.
MAX_DECLARED = 8 * 1024 * 1024
# Cap a single control-port (:9001) request; generous enough for a base64'd
# mac_put_file, bounded so a local client can't stream unbounded into memory.
MAX_CTRL_REQUEST = 12 * 1024 * 1024
# Opt-in shared secret: auth runs only when BOTH sides set one. Read from the
# environment, never a file in the TCC-protected repo. Empty -> auth skipped.
AUTH_TOKEN = os.environ.get("APPLEBRIDGE_TOKEN", "").encode("utf-8")

# Opt-in guard for the LOCAL control port (:9001) — a separate trust boundary from
# the wire auth above (which guards the daemon<->host link). The port is loopback-
# only, so this defends against *other local processes/users* on the same host. When
# APPLEBRIDGE_CTRL_TOKEN is set, a control client must prefix its request with an
# "AUTH:<token>\n" line; when unset (default) the port is open and behaviour is
# unchanged. Independent of APPLEBRIDGE_TOKEN so either boundary can be guarded alone.
CTRL_TOKEN = os.environ.get("APPLEBRIDGE_CTRL_TOKEN", "").encode("utf-8")

# --- Serial transport (Phase 5 reach; see docs/SERIAL_TRANSPORT.md) ----------
# When APPLEBRIDGE_SERIAL names a device, the host serves the daemon over a serial
# line instead of the TCP :9000 accept — for Ethernet-less machines and the pty
# test harness. The :9001 control port and all framing/dispatch are unchanged.
SERIAL_DEVICE = os.environ.get("APPLEBRIDGE_SERIAL") or None
SERIAL_BAUD = int(os.environ.get("APPLEBRIDGE_BAUD", "9600"))

_logf = open(LOG_PATH, "a", buffering=1)  # line-buffered


def redact_secrets(text):
    """Mask the password in an AFP-shaped request before it is logged.

    AFPMOUNT carries `zone:server:volume:user:password[:uam]`, so everything
    from the 4th colon on is sensitive. Matching on the "AFP" prefix rather
    than the exact verb is deliberate: the routed verbs never reach the verbatim
    logger, so what DOES arrive here is a typo — exactly the case that would
    otherwise write a real password into a long-lived log file.
    """
    if not text.startswith("AFP"):
        return text
    parts = text.split(":")
    if len(parts) <= 4:
        return text
    return ":".join(parts[:4]) + ":***"


def afp_log_label(cmd):
    """The log line for an AFP verb — never the request itself.

    AFPMOUNT's 5th field is a password, so the label names only WHERE the mount
    goes (server:volume). Keeping this a function rather than an f-string in the
    dispatcher is what makes it testable: a regression here writes credentials
    into a long-lived log file, which no live test would notice.
    """
    f = cmd.split(":")
    if cmd.startswith("AFPMOUNT:"):
        return "AFPMOUNT " + (":".join(f[2:4]) if len(f) > 3 else "?")
    if cmd.startswith("AFPUNMOUNT:"):
        return "AFPUNMOUNT " + cmd[len("AFPUNMOUNT:"):]
    return cmd.split()[0] if cmd else "?"


class SerialConn:
    """A socket-like wrapper over a raw serial fd, so AppleBridgeServer can drive a
    serial daemon link with the exact recv / sendall / settimeout / setblocking /
    close surface it already uses for TCP. Raw 8-N-1 at the chosen baud; select()
    supplies the timeout and non-blocking semantics the framing readers expect.
    stdlib-only (os + termios + select), matching the host's no-dependency rule."""

    def __init__(self, device, baud):
        self.device = device
        self._timeout = None
        self._blocking = True
        self.fd = os.open(device, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        self._configure(baud)

    def _configure(self, baud):
        import termios
        speed = getattr(termios, "B%d" % baud, None)
        if speed is None:
            raise ValueError("unsupported baud %r" % baud)
        a = termios.tcgetattr(self.fd)   # [iflag, oflag, cflag, lflag, ispeed, ospeed, cc]
        a[0] = 0                          # iflag: raw (no CR/LF xlate, no flow ctl)
        a[1] = 0                          # oflag: raw
        a[3] = 0                          # lflag: raw (no echo, no canonical mode)
        cflag = a[2]
        cflag &= ~(termios.PARENB | termios.CSTOPB | termios.CSIZE)
        cflag |= termios.CS8 | termios.CREAD | termios.CLOCAL   # 8-N-1, rx on, ignore modem lines
        a[2] = cflag
        a[4] = speed                      # ispeed
        a[5] = speed                      # ospeed
        a[6][termios.VMIN] = 0
        a[6][termios.VTIME] = 0
        termios.tcsetattr(self.fd, termios.TCSANOW, a)
        try:
            termios.tcflush(self.fd, termios.TCIOFLUSH)
        except OSError:
            pass

    def settimeout(self, t):
        self._timeout = t

    def setblocking(self, flag):
        self._blocking = bool(flag)

    def recv(self, n):
        """Match socket.recv: bytes on data, b'' if the peer/pty closed. Raises
        BlockingIOError when non-blocking and idle (so _drain sees a healthy idle
        link) and socket.timeout when a bounded blocking read expires."""
        if not self._blocking:
            r, _, _ = select.select([self.fd], [], [], 0)
            if not r:
                raise BlockingIOError()
            return os.read(self.fd, n)
        deadline = None if self._timeout is None else time.monotonic() + self._timeout
        while True:
            wait = None if deadline is None else max(0.0, deadline - time.monotonic())
            r, _, _ = select.select([self.fd], [], [], wait)
            if not r:
                raise socket.timeout()
            try:
                return os.read(self.fd, n)
            except BlockingIOError:
                continue

    def sendall(self, data):
        mv = memoryview(data)
        while mv:
            _, w, _ = select.select([], [self.fd], [], self._timeout)
            if not w:
                raise socket.timeout()
            try:
                sent = os.write(self.fd, mv)
            except BlockingIOError:
                continue
            mv = mv[sent:]

    def close(self):
        try:
            os.close(self.fd)
        except OSError:
            pass


def fnv1a64(data):
    """FNV-1a 64-bit hash -> 16 lowercase hex chars. Small and table-free, so
    the 68K daemon can compute the identical value in a few lines of C. This is
    the auth digest primitive (obfuscation-grade, docs/PROTOCOL_v0.2.md §2); the
    ab_digest() seam lets a compact SHA-1 replace it later with no wire change."""
    h = 0xcbf29ce484222325
    for b in bytes(data):
        h ^= b
        h = (h * 0x100000001b3) & 0xFFFFFFFFFFFFFFFF
    return f"{h:016x}"


def ab_digest(nonce, token):
    """Auth proof = H(nonce || token), nonce/token as bytes -> 16 hex chars."""
    return fnv1a64(bytes(nonce) + bytes(token))


def parse_hello_reply(resp):
    """Parse a daemon HELLO reply -> (version, feat_set, nonce, proof).

    A v0.2 daemon advertises 'ABVERSION:<n>;FEAT=<csv>;NONCE=<hex>;PROOF=<hex>'
    inside the STDOUT of an ordinary STATUS frame. Anything without an
    'ABVERSION:' token -> (1, set(), '', '') i.e. a legacy (v0.1) peer."""
    if not resp:
        return (1, set(), "", "")
    idx = resp.find("ABVERSION:")
    if idx < 0:
        return (1, set(), "", "")
    line = resp[idx:]
    for term in ("\r", "\n"):
        c = line.find(term)
        if c >= 0:
            line = line[:c]
    version, feat, nonce, proof = 1, set(), "", ""
    for f in line.split(";"):
        if f.startswith("ABVERSION:"):
            try:
                version = int(f[len("ABVERSION:"):])
            except ValueError:
                version = 1
        elif f.startswith("FEAT="):
            feat = set(x for x in f[len("FEAT="):].split(",") if x)
        elif f.startswith("NONCE="):
            nonce = f[len("NONCE="):]
        elif f.startswith("PROOF="):
            proof = f[len("PROOF="):]
    return (version, feat, nonce, proof)


def log(msg):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, file=sys.stderr, flush=True)
    try:
        _logf.write(line + "\n")
    except Exception:
        pass


def build_stamp():
    """Identify the running copy, so drift between the repo and the deployed
    launchd copy is visible in the log (the repo lives under ~/Documents, which
    is TCC-protected, so launchd must run a deployed copy — deploy_host.sh keeps
    it in sync and writes the .deploy_stamp read here)."""
    here = os.path.dirname(os.path.abspath(__file__))
    try:
        mtime = time.strftime("%Y-%m-%d %H:%M:%S",
                              time.localtime(os.path.getmtime(os.path.abspath(__file__))))
    except OSError:
        mtime = "?"
    stamp = ""
    try:
        with open(os.path.join(here, ".deploy_stamp")) as f:
            stamp = " deploy=" + f.read().strip()
    except OSError:
        pass
    return f"running from {here} (mtime {mtime}){stamp}"


def timeout_for(command):
    parts = command.strip().split(None, 1)
    tok = parts[0].lower() if parts else ""
    return LONG_TIMEOUT if tok in LONG_CMDS else DEFAULT_TIMEOUT


def screenshot_png(shot, region=None):
    """Decode a request_screenshot() dict to PNG bytes (or None).

    `region` optionally crops to (x, y, w, h) screen pixels."""
    if not shot:
        return None
    return screenshot_decode.raw_to_png(
        shot["width"], shot["height"], shot["depth"],
        shot["row_bytes"], shot["clut"], shot["pixels"], region=region)


class AppleBridgeServer:
    def __init__(self, interface=HOST_INTERFACE, port=HOST_PORT,
                 serial_dev=None, serial_baud=SERIAL_BAUD):
        self.interface = interface
        self.port = port
        self.serial_dev = serial_dev      # None => TCP :9000; else a serial device path
        self.serial_baud = serial_baud
        self.client_socket = None
        self.server_socket = None
        self.connected = False
        self.peer_version = 1     # negotiated protocol version (1 = legacy v0.1)
        self.peer_feat = set()    # capability tokens advertised by a v0.2 daemon
        self.authed = True        # command flow permitted (False only mid-auth-fail)
        # Crash black-box: the last verb/command handed to the daemon + when. If the
        # daemon drops right after (a guest fault killing the emulator), we log which
        # command preceded it -> the prime suspect. Cleared on a clean response.
        self.last_command = None
        self.last_command_time = 0.0

    def _note_command(self, desc):
        """Record the command about to be sent, for crash correlation. Heartbeats
        (PING/STAT) are skipped so the black-box keeps the last REAL command even
        when a ping fires between it and the drop."""
        if desc and desc[:4] in ("PING", "STAT"):
            return
        self.last_command = (desc[:120] if desc else desc)
        self.last_command_time = time.time()

    def _clear_command(self):
        """A clean response arrived -> the last command did not crash the daemon."""
        self.last_command = None

    def bind_listen(self):
        """Bind and listen on :9000 once (kept open across re-accepts). In serial
        mode there is no TCP listener — the daemon link is the serial device."""
        if self.serial_dev:
            log(f"Serial mode: {self.serial_dev} @ {self.serial_baud} baud "
                f"(no :9000 TCP listener)")
            return
        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server_socket.bind((self.interface, self.port))
        self.server_socket.listen(1)
        log(f"Listening on {self.interface}:{self.port} (waiting for Mac daemon)")

    def accept_mac(self, timeout=None):
        """Wait for a Mac daemon to connect; (re)assign client_socket.

        `timeout=None` blocks until a daemon arrives (the interactive/TTY path).
        A float POLLS for at most that many seconds and returns None if nobody
        turned up — which is what run_control_server uses, so that a missing
        daemon can never wedge the control port (:9001). Blocking here used to
        mean mac_status and every other MCP tool hung with no reply for as long
        as the emulator was down, i.e. exactly when diagnostics matter most.

        Serial has no accept — open the device and treat it as the link.
        """
        if self.serial_dev:
            while True:
                try:
                    self.client_socket = SerialConn(self.serial_dev, self.serial_baud)
                    self.connected = True
                    log(f"Serial link open on {self.serial_dev}")
                    return self.serial_dev
                except (OSError, ValueError) as e:
                    if timeout is not None:
                        # Polling caller: report "not yet" instead of sleeping on
                        # its thread, so it can go serve the control port.
                        return None
                    log(f"serial open failed ({e}); retrying in 3s")
                    time.sleep(3)
        self.server_socket.settimeout(timeout)
        try:
            self.client_socket, addr = self.server_socket.accept()
        except socket.timeout:
            return None
        finally:
            self.server_socket.settimeout(None)
        self.connected = True
        log(f"Mac connected from {addr}")
        return addr

    def _mark_disconnected(self, reason):
        log(f"Mac disconnected: {reason}")
        # Crash correlation: if a command was in flight, name it. A drop within a
        # couple of seconds of sending is the classic "this verb faulted the guest
        # and took the emulator down" signature — the single most useful clue.
        if self.last_command is not None:
            dt = time.time() - self.last_command_time
            if dt < 5.0:
                log(f"  >> last command before drop ({dt:.2f}s ago): {self.last_command!r} "
                    f"— PRIME SUSPECT for the disconnect/crash")
            else:
                log(f"  (last command was {dt:.1f}s ago: {self.last_command!r})")
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
        self._note_command(command)
        try:
            self.client_socket.sendall(header + encoded)
        except OSError as e:
            self._mark_disconnected(f"send failed: {e}")
            return None

        to = timeout_for(command)
        return self._read_framed_response(to, label=command)

    def _read_framed_response(self, to, label=""):
        """Read ONE daemon response (a request must have just been sent).

        The daemon's SendCommandResult streams:
          STATUS:<code>\\r STDOUT:<olen>\\r <olen bytes>\\r STDERR:<elen>\\r <elen bytes>\\r \\r
        Reading STDOUT by its DECLARED length avoids stopping early at a blank
        line inside the output. Non-STATUS responses (IMAGE) fall back to the
        legacy terminator read. Shared by send_command and send_apple_event."""
        buf = bytearray()

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
                if olen > MAX_DECLARED:
                    # A corrupt/hostile declared length: drop rather than read it.
                    log(f"declared STDOUT length {olen} exceeds cap {MAX_DECLARED};"
                        f" dropping link")
                    self._mark_disconnected("oversized declared STDOUT length")
                    return None
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
            log(f"command timeout after {to:.0f}s: {str(label)[:48]!r}")
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
            log(f"recv {len(raw)}B outcome={outcome} req={str(label)[:32]!r}")
        return bytes(raw).decode("mac_roman", errors="replace")

    def send_apple_event(self, target_hex, class_hex, id_hex, do_bytes):
        """AESEND: send an arbitrary Apple Event (event class/ID) to the app with
        the given creator signature, with an optional text direct object, and
        return the daemon's STATUS reply (the AE reply text rides STDOUT). The
        OSTypes go as 8-hex; the direct object is length-framed raw bytes."""
        if not self.connected or not self.client_socket:
            return None
        if not self._drain():
            self._mark_disconnected("drain detected closed socket")
            return None
        header = (f"AESEND:{target_hex}:{class_hex}:{id_hex}:"
                  f"{len(do_bytes)}\n").encode("ascii")
        try:
            self.client_socket.sendall(header + do_bytes)
        except OSError as e:
            self._mark_disconnected(f"send failed: {e}")
            return None
        log(f"AESEND target={target_hex} class={class_hex} id={id_hex} "
            f"do={len(do_bytes)}B")
        return self._read_framed_response(LONG_TIMEOUT, label="AESEND")

    def clipboard_get(self):
        """CLIPGET: return the guest's TEXT scrap (clipboard) reply frame."""
        if not self.connected or not self.client_socket:
            return None
        if not self._drain():
            self._mark_disconnected("drain detected closed socket")
            return None
        try:
            self.client_socket.sendall("CLIPGET".encode("mac_roman"))
        except OSError as e:
            self._mark_disconnected(f"send failed: {e}")
            return None
        return self._read_framed_response(DEFAULT_TIMEOUT, label="CLIPGET")

    def list_dir(self, mac_path):
        """LISTDIR:<path>: native directory listing from the daemon (PBGetCatInfo,
        no ToolServer). Returns the STATUS/STDOUT framed reply; STDOUT is one
        tab-separated line per entry (name\\ttype\\tcreator\\tsize\\tmodSecs)."""
        if not self.connected or not self.client_socket:
            return None
        if not self._drain():
            self._mark_disconnected("drain detected closed socket")
            return None
        try:
            self.client_socket.sendall(("LISTDIR:" + mac_path).encode("mac_roman",
                                                                      errors="replace"))
        except OSError as e:
            self._mark_disconnected(f"send failed: {e}")
            return None
        log(f"LISTDIR {mac_path!r}")
        return self._read_framed_response(DEFAULT_TIMEOUT, label="LISTDIR")

    def nbp_lookup(self, args):
        """NBPLOOK[:type[:zone[:object]]]: AppleTalk name lookup on the guest.

        STDOUT is one entity per line: object\\ttype\\tzone\\tnet.node.socket.
        The daemon's lookup runs its full NBP retry window (~3 s) before it can
        answer — that is protocol, not a stall — so this gets its own timeout
        above the retry budget instead of the 15 s default's implicit slack.
        """
        if not self.connected or not self.client_socket:
            return None
        if not self._drain():
            self._mark_disconnected("drain detected closed socket")
            return None
        verb = "NBPLOOK" + (":" + args if args else "")
        try:
            self.client_socket.sendall(verb.encode("mac_roman", errors="replace"))
        except OSError as e:
            self._mark_disconnected(f"send failed: {e}")
            return None
        log(f"NBPLOOK {args!r}")
        return self._read_framed_response(NBP_TIMEOUT, label="NBPLOOK")

    def afp_verb(self, verb, log_label):
        """AFPMOUNT/AFPUNMOUNT: mount or unmount an AppleShare volume.

        `log_label` is logged INSTEAD of the verb: an AFPMOUNT request carries a
        password in the clear, and this log file is long-lived. Mounting talks
        to a server over AppleTalk, so it gets the long timeout rather than the
        15 s default.
        """
        if not self.connected or not self.client_socket:
            return None
        if not self._drain():
            self._mark_disconnected("drain detected closed socket")
            return None
        try:
            self.client_socket.sendall(verb.encode("mac_roman", errors="replace"))
        except OSError as e:
            self._mark_disconnected(f"send failed: {e}")
            return None
        log(log_label)
        return self._read_framed_response(AFP_TIMEOUT, label=log_label.split()[0])

    def clipboard_set(self, data):
        """CLIPSET: replace the guest TEXT scrap. Length-framed raw bytes."""
        if not self.connected or not self.client_socket:
            return None
        if not self._drain():
            self._mark_disconnected("drain detected closed socket")
            return None
        header = f"CLIPSET:{len(data)}\n".encode("ascii")
        try:
            self.client_socket.sendall(header + data)
        except OSError as e:
            self._mark_disconnected(f"send failed: {e}")
            return None
        log(f"CLIPSET {len(data)}B")
        return self._read_framed_response(DEFAULT_TIMEOUT, label="CLIPSET")

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
        self._note_command(data)
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

    def request_log(self, max_bytes=0, timeout=DEFAULT_TIMEOUT):
        """Fetch the daemon's Verbose console ring as text via the LOG verb.

        Reads the reply BY DECLARED LENGTH rather than by terminator: the log body
        carries CR line separators (and blank lines), so send_raw's ``\\r\\r`` /
        ``\\n\\n`` terminator scan would truncate it. Parses ``STDOUT:<n><sep>``,
        then reads exactly ``n`` body bytes. Returns the log text, or None.
        ``max_bytes`` (>0) asks the daemon for only the last N bytes.
        """
        if not self.connected or not self.client_socket:
            return None
        if not self._drain():
            self._mark_disconnected("drain detected closed socket")
            return None
        verb = f"LOG:{int(max_bytes)}" if max_bytes else "LOG"
        self._note_command(verb)
        try:
            self.client_socket.sendall(verb.encode("mac_roman", errors="replace"))
        except OSError as e:
            self._mark_disconnected(f"send failed: {e}")
            return None

        buf = bytearray()
        n = None            # declared STDOUT length, once parsed
        body_start = None   # index of the first body byte
        try:
            self.client_socket.settimeout(timeout)
            while True:
                if n is None:                       # still hunting the header
                    k = buf.find(b"STDOUT:")
                    if k >= 0:
                        d = k + 7
                        while d < len(buf) and 0x30 <= buf[d] <= 0x39:
                            d += 1
                        if d < len(buf):            # the separator byte has arrived
                            try:
                                n = int(buf[k + 7:d])
                                body_start = d + 1  # skip exactly ONE separator
                            except ValueError:
                                n = None
                if (n is not None and body_start is not None
                        and len(buf) >= body_start + n):
                    break
                chunk = self.client_socket.recv(4096)
                if not chunk:
                    self._mark_disconnected("recv 0 (peer closed mid-LOG)")
                    break
                buf += chunk
        except socket.timeout:
            log(f"LOG verb timeout: got {len(buf)}B")
        except OSError as e:
            self._mark_disconnected(f"recv error during LOG: {e}")
            return None
        finally:
            try:
                if self.client_socket:
                    self.client_socket.settimeout(None)
            except OSError:
                pass

        if n is None or body_start is None:
            return None
        return bytes(buf[body_start:body_start + n]).decode("mac_roman",
                                                            errors="replace")

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

    def negotiate_version(self):
        """HELLO probe right after (re)connect: agree a protocol version and,
        when a token is configured, complete the mutual auth handshake. Sets
        self.peer_version, self.peer_feat and self.authed.

        No token (AUTH_TOKEN empty) -> auth is skipped; a v0.1 daemon ('Invalid
        command format', no ABVERSION) or a silent peer falls back to v1 with the
        link intact. This is the default zero-config behaviour, unchanged.

        Token configured (opt-in auth, docs/PROTOCOL_v0.2.md §2) -> we FAIL CLOSED:
          1. Require peer v2 with FEAT=auth, else drop (won't run unauthenticated).
          2. Verify the daemon's PROOF = H(hostNonce || token) — proves the daemon
             knows the token (stops a rogue daemon feeding us bad data).
          3. Send AUTH2 = H(daemonNonce || token) — proves WE know it (unlocks the
             daemon's command gate). Drop unless the daemon acks STATUS:0.
        Any mismatch drops the link (the daemon then reconnects and retries); a
        genuinely wrong token loops, which is the correct fail-closed posture."""
        self.peer_version = 1
        self.peer_feat = set()
        self.authed = not AUTH_TOKEN     # no token => auth not required => open
        if not self.connected or not self.client_socket:
            return
        host_nonce = os.urandom(8).hex() if AUTH_TOKEN else ""
        hello = f"HELLO:{AB_PROTOCOL_VERSION}:{host_nonce}\n"
        if not self._drain():
            self._mark_disconnected("drain detected closed socket")
            return
        try:
            self.client_socket.sendall(hello.encode("ascii"))
        except OSError as e:
            self._mark_disconnected(f"HELLO send failed: {e}")
            return
        resp = self._read_framed_response(HELLO_TIMEOUT, label="HELLO")
        version, feat, daemon_nonce, daemon_proof = parse_hello_reply(resp or "")
        self.peer_version = min(AB_PROTOCOL_VERSION, version)
        self.peer_feat = feat

        if not AUTH_TOKEN:
            if self.peer_version >= 2:
                log(f"HELLO: negotiated protocol v{self.peer_version} "
                    f"feat={sorted(self.peer_feat)}")
            else:
                log("HELLO: peer is legacy (v1); proceeding without negotiation")
            return

        # --- auth required (a token is configured): fail closed on any problem.
        if self.peer_version < 2 or "auth" not in feat:
            self._mark_disconnected("auth required but peer lacks it")
            log("auth required but peer does not offer it; link dropped")
            return
        want = ab_digest(host_nonce.encode("ascii"), AUTH_TOKEN)
        if not daemon_proof or not hmac.compare_digest(daemon_proof, want):
            self._mark_disconnected("daemon auth proof mismatch")
            log("daemon PROOF mismatch (wrong token / rogue daemon); link dropped")
            return
        our_proof = ab_digest(daemon_nonce.encode("ascii"), AUTH_TOKEN)
        try:
            self.client_socket.sendall(f"AUTH2:{our_proof}\n".encode("ascii"))
        except OSError as e:
            self._mark_disconnected(f"AUTH2 send failed: {e}")
            return
        ack = self._read_framed_response(HELLO_TIMEOUT, label="AUTH2")
        if not ack or not ack.startswith("STATUS:0"):
            self._mark_disconnected("daemon rejected AUTH2")
            log("daemon rejected AUTH2 (wrong token?); link dropped")
            return
        self.authed = True
        log(f"HELLO: negotiated protocol v{self.peer_version} + auth OK")

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
            if not (0 <= ds <= MAX_DECLARED) or not (0 <= cc <= 256):
                # Untrustworthy header: reading it would desync the wire.
                log(f"screenshot declared out of range data={ds} clut={cc}; "
                    f"dropping link")
                self._mark_disconnected("screenshot oversized declared length")
                return None
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
            if not (0 <= data_len <= MAX_DECLARED) or not (0 <= rsrc_len <= MAX_DECLARED):
                log(f"READFILE declared length out of range data={data_len} "
                    f"rsrc={rsrc_len}; dropping link")
                self._mark_disconnected("READFILE oversized declared length")
                return None
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


def split_ctrl_auth(request):
    """Strip an optional leading 'AUTH:<token>' line from a control request.

    Returns (command, token) where token is the presented secret (str) or None if
    no AUTH line was sent. The AUTH line is always stripped when present, so a
    client configured with a token works whether or not the server enforces one.
    """
    if request.startswith("AUTH:"):
        nl = request.find("\n")
        if nl < 0:
            return "", request[len("AUTH:"):]        # auth line only, no command
        return request[nl + 1:].lstrip("\n"), request[len("AUTH:"):nl]
    return request, None


def ctrl_authorized(token):
    """Whether a control request bearing `token` (str or None) may proceed.

    Open by default (no CTRL_TOKEN configured). When a token IS configured the
    check is fail-closed: a missing or mismatched token is rejected, compared in
    constant time to avoid leaking the secret by timing.
    """
    if not CTRL_TOKEN:
        return True
    if token is None:
        return False
    return hmac.compare_digest(token.encode("utf-8"), CTRL_TOKEN)


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
            if len(data) > MAX_CTRL_REQUEST:
                # Bounded read: a local client can't stream unbounded into memory.
                # Raised into run_control_server's handler, which replies ERROR.
                raise ValueError(
                    f"control request exceeds {MAX_CTRL_REQUEST} bytes")
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
    waiting_logged = False       # "waiting for daemon" logged once per outage
    try:
        while True:
            # Poll for a Mac daemon, but NEVER block on it: the control port has
            # to stay answerable while the emulator is down, or the whole stack
            # looks hung exactly when the user needs mac_status to tell them
            # which layer broke. Falls through to control.accept() either way.
            if not server.connected:
                if not waiting_logged:
                    log("Waiting for Mac daemon to (re)connect... "
                        "(control port stays live; mac_status reports daemon down)")
                    waiting_logged = True
                if server.accept_mac(timeout=MAC_ACCEPT_POLL) is not None:
                    waiting_logged = False
                    # The first packet over a fresh OT/MACNAT connection is often
                    # corrupt; send one priming PING and discard its (possibly
                    # garbled) reply so a real command isn't the one that eats it.
                    server.heartbeat()
                    # Probe the daemon's protocol version (best-effort; a v0.1
                    # daemon or a silent peer falls back to legacy, link intact).
                    server.negotiate_version()
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
                # Optional control-port auth (loopback boundary). Strip any leading
                # AUTH: line, then gate: with a token configured, an absent/wrong one
                # is rejected before the command ever reaches the daemon.
                cmd, ctrl_token = split_ctrl_auth(cmd)
                if not ctrl_authorized(ctrl_token):
                    log("control auth: rejected (missing/invalid token)")
                    msg = "control auth required"
                    ctrl_conn.sendall(
                        f"STATUS:-1\rSTDOUT:0\rSTDERR:{len(msg)}\r{msg}\r\r".encode(
                            "utf-8"))
                    continue          # finally: closes the conn
                if cmd:
                    # Fail fast and LOUD when the daemon isn't linked. Every verb
                    # below needs it, and a bare "No response" tells the user
                    # nothing about which layer broke. MACSTATUS is exempt — it is
                    # answered host-side precisely so it can report that.
                    if not server.connected and cmd not in ("MACSTATUS", "DOCTOR"):
                        # Name the layer that is ACTUALLY broken. The old fixed
                        # paragraph always blamed the Ethernet adapter, which
                        # misleads whenever the cause is a disabled launchd job,
                        # a slirp backend, a duplicated .154 alias — or simply a
                        # guest that has not finished booting.
                        reason = None
                        if bridge_doctor is not None:
                            try:
                                reason = bridge_doctor.short_reason(
                                    bridge_doctor.collect())
                            except Exception as e:
                                log(f"doctor probe failed: {e}")
                        msg = ("Mac daemon not connected - the emulator has not "
                               "dialled in. ")
                        msg += (reason if reason else
                                "Check BasiliskII is running AND that its "
                                "etherhelpertool child is alive "
                                "(pgrep -fl etherhelpertool).")
                        msg += " Run bridge_doctor for the full cross-layer report."
                        log(f"rejected {cmd[:40]!r}: no daemon connected")
                        ctrl_conn.sendall(
                            f"STATUS:-1\rSTDOUT:0\rSTDERR:{len(msg)}\r{msg}\r\r".encode(
                                "utf-8"))
                        continue          # finally: closes the conn
                    if cmd.lower() == "screenshot" or cmd.lower().startswith("screenshot:"):
                        # Optional crop: "screenshot:x:y:w:h" decodes only that region.
                        region = None
                        if ":" in cmd:
                            try:
                                rx, ry, rw, rh = (int(v) for v in cmd.split(":")[1:5])
                                region = (rx, ry, rw, rh)
                            except (ValueError, IndexError):
                                region = None
                        shot = server.request_screenshot()
                        try:
                            png = screenshot_png(shot, region=region)
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
                    elif cmd == "MACSTATUS":
                        # Liveness, answered HOST-side so it never hangs when the
                        # daemon is down. Reports the host's connection/heartbeat
                        # view, then merges the daemon's STAT counters if reachable.
                        now = time.monotonic()
                        fields = [
                            f"host_connected={1 if server.connected else 0}",
                            f"idle_seconds={now - last_io:.1f}",
                            f"missed_heartbeats={missed}",
                        ]
                        daemon_ok = 0
                        if server.connected:
                            draw = server.send_raw("STAT", timeout=4.0)
                            if draw:
                                # The daemon frames with CR (classic-Mac C maps
                                # source '\r' -> 0x0A), so read the STDOUT length
                                # digits and skip ONE separator rather than assume
                                # which newline byte it is.
                                k = draw.find("STDOUT:")
                                if k >= 0:
                                    d = k + 7
                                    while d < len(draw) and draw[d].isdigit():
                                        d += 1
                                    try:
                                        n = int(draw[k + 7:d])
                                        body = draw[d + 1:d + 1 + n]
                                        if body:
                                            fields.append(body)
                                            daemon_ok = 1
                                    except ValueError:
                                        pass
                        fields.append(f"daemon_responding={daemon_ok}")
                        payload = ";".join(fields)
                        out = f"STATUS:0\rSTDOUT:{len(payload)}\r{payload}\rSTDERR:0\r\r"
                        log(f"mac_status -> {payload}")
                    elif cmd == "DOCTOR":
                        # Cross-layer diagnosis, answered HOST-side (like
                        # MACSTATUS) so it works with the daemon down — which is
                        # exactly when it is needed. JSON payload; the MCP tool
                        # renders it. Note the MCP layer ALSO runs the same
                        # module locally, so a dead host server still gets a
                        # report; this verb serves the plain `nc` path.
                        if bridge_doctor is None:
                            m = "bridge_doctor module not deployed"
                            out = f"STATUS:-1\rSTDOUT:0\rSTDERR:{len(m)}\r{m}\r\r"
                        else:
                            try:
                                payload = json.dumps(bridge_doctor.collect())
                                out = (f"STATUS:0\rSTDOUT:{len(payload)}\r{payload}"
                                       f"\rSTDERR:0\r\r")
                                log("doctor -> "
                                    f"{json.loads(payload)['verdict']}")
                            except Exception as e:
                                m = f"doctor failed: {e}"
                                out = f"STATUS:-1\rSTDOUT:0\rSTDERR:{len(m)}\r{m}\r\r"
                    elif cmd == "CLIPGET":
                        resp = server.clipboard_get()
                        out = resp if resp is not None else "No response"
                    elif cmd.startswith("CLIPSET:"):
                        # CLIPSET:<textB64> (base64 keeps it colon/newline-safe here)
                        try:
                            b64 = cmd.split(":", 1)[1]
                            data = base64.b64decode(b64) if b64 else b""
                            resp = server.clipboard_set(data)
                            out = resp if resp is not None else "No response"
                        except Exception as e:
                            msg = str(e)
                            out = f"STATUS:-1\rSTDOUT:0\rSTDERR:{len(msg)}\r{msg}\r\r"
                    elif cmd.startswith("AESEND:"):
                        # AESEND:<targetHex8>:<classHex8>:<idHex8>:<directObjB64>
                        # (direct object base64 so it stays colon/newline-safe on
                        # the text control hop; the daemon hop length-frames it.)
                        try:
                            f = cmd.split(":")
                            target_hex, class_hex, id_hex = f[1], f[2], f[3]
                            do_bytes = (base64.b64decode(f[4])
                                        if len(f) > 4 and f[4] else b"")
                            resp = server.send_apple_event(target_hex, class_hex,
                                                           id_hex, do_bytes)
                            out = resp if resp is not None else "No response"
                        except Exception as e:
                            log(f"AESEND parse/send error: {e}")
                            msg = str(e)
                            out = f"STATUS:-1\rSTDOUT:0\rSTDERR:{len(msg)}\r{msg}\r\r"
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
                    elif cmd.startswith("LISTDIR:"):
                        # LISTDIR:<path> -> native directory listing (no ToolServer)
                        mac_path = cmd[len("LISTDIR:"):]
                        resp = server.list_dir(mac_path)
                        out = resp if resp is not None else "No response"
                    elif cmd == "NBPLOOK" or cmd.startswith("NBPLOOK:"):
                        # NBPLOOK[:type[:zone[:object]]] -> AppleTalk entities
                        # the guest can see (the Chooser's list, headless).
                        args = cmd[len("NBPLOOK:"):] if ":" in cmd else ""
                        resp = server.nbp_lookup(args)
                        out = resp if resp is not None else "No response"
                    elif cmd == "DISKINFO" or cmd.startswith("DISKINFO:"):
                        # DISKINFO[:<volume>] -> name/vRefNum/total/free per line
                        resp = server.send_raw(cmd)
                        out = resp if resp is not None else "No response"
                    elif cmd == "MONITOR" or cmd.startswith("MONITOR:"):
                        # MONITOR:0|1 -> hide/show the daemon's Verbose window
                        resp = server.send_raw(cmd)
                        out = resp if resp is not None else "No response"
                    elif cmd.startswith("AFPMOUNT:"):
                        # AFPMOUNT:<zone>:<server>:<volume>:<user>:<password>[:<uam>]
                        # The password must not reach the log: name only the
                        # server and volume (fields 2 and 3).
                        resp = server.afp_verb(cmd, afp_log_label(cmd))
                        out = resp if resp is not None else "No response"
                    elif cmd.startswith("AFPUNMOUNT:"):
                        resp = server.afp_verb(cmd, afp_log_label(cmd))
                        out = resp if resp is not None else "No response"
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
                    elif cmd == "LOG" or cmd.startswith("LOG:"):
                        # Verbose console ring as text (read by declared length —
                        # the body has CR separators). "LOG:<n>" = last n bytes.
                        mx = 0
                        if cmd.startswith("LOG:"):
                            try:
                                mx = int(cmd.split(":", 1)[1])
                            except ValueError:
                                mx = 0
                        body = server.request_log(mx)
                        if body is None:
                            msg = "LOG failed (daemon down or timed out)"
                            out = f"STATUS:-1\rSTDOUT:0\rSTDERR:{len(msg)}\r{msg}\r\r"
                        else:
                            out = f"STATUS:0\rSTDOUT:{len(body)}\r{body}\rSTDERR:0\r\r"
                            log(f"LOG -> {len(body)}B")
                    elif (cmd == "PING" or cmd == "QUITDAEMON" or cmd == "REBOOT"
                          or cmd == "SWAPSELF" or cmd == "SHUTDOWN"
                          or cmd == "JGATE" or cmd == "JMENU" or cmd == "JABOUT"
                          or cmd == "JSF" or cmd == "JSAFE" or cmd == "JPROBE"
                          or cmd.startswith("JMENU:") or cmd.startswith("JABOUT:")
                          or cmd.startswith("JSF:") or cmd.startswith("MENU:")
                          or cmd == "MSINSTALL" or cmd == "MSREAD" or cmd == "MSUNINSTALL"
                          or cmd.startswith("MSDRIVE:")
                          or cmd.startswith("LAUNCH:") or cmd.startswith("QUIT:")
                          or cmd.startswith("KEY:") or cmd.startswith("TYPE:")
                          or cmd.startswith("CLICK:")):
                        log(f"verb: {cmd[:60]!r}")
                        resp = server.send_raw(cmd)   # raw, not COMMAND-wrapped
                        out = resp if resp is not None else "No response"
                    else:
                        # Anything unrouted is logged verbatim — which is fine
                        # for MPW commands, but a MISTYPED AFP verb ("AFPMNT:")
                        # lands here too, and its 5th field is a password. Mask
                        # any AFP-shaped request before it reaches the log file.
                        log(f"cmd: {redact_secrets(cmd)[:60]!r}")
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
    server = AppleBridgeServer(serial_dev=SERIAL_DEVICE, serial_baud=SERIAL_BAUD)
    log("=== AppleBridge Host Server (hardened) ===")
    log(build_stamp())
    server.bind_listen()
    try:
        if sys.stdin.isatty():
            server.accept_mac()
            server.negotiate_version()
            interactive_mode(server)
        else:
            run_control_server(server)
    except KeyboardInterrupt:
        log("Shutting down...")
    finally:
        server.close()


if __name__ == "__main__":
    main()

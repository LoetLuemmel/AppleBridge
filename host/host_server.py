#!/usr/bin/env python3
"""
AppleBridge Host Server (hardened)

Mac daemon connects OUT to this server on <configured host IP>:9000 (reversed
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

NOTE: the daemon dials the IP= in its own prefs. Large responses
(>64 KB, up to a 4 MB cap) are now streamed by the daemon from a dynamically
allocated handle and read here by their declared STDOUT:<len> (length-framing,
already content-agnostic) — no per-response size limit on this side.
"""
import base64
import errno
import hmac
import datetime
import json
import os
import re
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
try:
    import guest_input    # drive the guest's REAL mouse (HOSTCLICK/HOSTMENU verbs)
except ImportError:       # deployed copy predating the module: degrade, don't die
    guest_input = None
try:                      # session-to-session channel, for the MACSTATUS counters
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools"))
    import notes
except Exception:         # deployed copy predating it, or an unreadable channel
    notes = None

import host_config                 # where the host's own address comes from (R1)

# Resolved, never hardcoded: APPLEBRIDGE_HOST_IP -> host/local.env -> 0.0.0.0.
# A literal here was correct on exactly one machine and produced an unreadable
# Errno 49 everywhere else. See host_config.py and docs/INSTALLER_REQUIREMENTS.md.
HOST_INTERFACE, HOST_INTERFACE_SOURCE = host_config.resolve_host_ip()
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

# AESEND addresses ANY application, not just the ToolServer we own, so the wait
# is bounded on both sides and the DAEMON's bound is the shorter one — it is the
# side that must let the guest go. These two mirror AE_SEND_DEFAULT_TIMEOUT and
# AE_SEND_MAX_TIMEOUT in mac/include/applebridge.h; tests/test_native_verbs.py
# checks they still agree.
AE_SEND_DEFAULT_TIMEOUT_TICKS = 1800    # 30 s — the daemon's default when we send no bound
AE_SEND_MAX_TIMEOUT_TICKS = 10800       # 180 s — the daemon clamps to this; asking for more is a lie
AE_SEND_READ_MARGIN = 15.0              # seconds of round-trip room on top of the daemon's bound

# Resynchronisation after an abandoned read. The daemon link is PERSISTENT, so a
# timed-out read does not merely fail its own command: the rest of that reply is
# still coming, and the next command reads it instead of its own answer. Every
# reply after that is one behind, which surfaces as nonsense — a LISTDIR that
# returns ten bytes, a screenshot answered by STATUS:-1, a WRITEFILE reporting
# "Invalid command format". Measured on a Macintosh SE/30, 2026-07-28: 117
# unparseable requests reached the daemon in one three-minute window.
#
# The old `_drain()` was non-blocking, so it removed only what had ALREADY
# arrived and missed exactly the reply still in flight. After an abandoned read
# the drain therefore WAITS for the late bytes, discards them, and says whose
# reply they were. If they keep coming past the budget the link is dropped
# instead: a stream that cannot be realigned gives every later command a wrong
# answer, and the daemon redials with a clean one within about a minute.
# Opt-in frame tracing: APPLEBRIDGE_FRAME_DEBUG=1 logs every recv boundary and
# the length-framed breakdown of each reply. Added because the SE/30's desync
# left ten bytes behind with NO timeout logged, and the ordinary log could not
# say which branch of the reader had returned early — "the log gives no hint" is
# the point at which instrumentation stops being optional.
FRAME_DEBUG = os.environ.get("APPLEBRIDGE_FRAME_DEBUG") == "1"

RESYNC_QUIET = 0.4      # seconds of silence that mean the late reply has all arrived
RESYNC_BUDGET = 3.0     # seconds to spend realigning before dropping the link

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
# A retry gets a longer bound: the first attempt may simply have been
# outrun by a 68030 over MacTCP, where a reply after 4 s is normal.
HELLO_RETRY_FACTOR = 3
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
# Optional send pacing for a guest whose serial input buffer is small (see
# SerialConn.sendall). 0 = disabled: one write, full line rate.
SERIAL_CHUNK = int(os.environ.get("APPLEBRIDGE_SERIAL_CHUNK", "0"))
SERIAL_GAP = float(os.environ.get("APPLEBRIDGE_SERIAL_GAP", "0.015"))

_logf = open(LOG_PATH, "a", buffering=1)  # line-buffered


def _framed_failure(text):
    """'<status> <stderr>' if a framed response reports failure, else None.

    The response frame carries both the code and the daemon's own explanation,
    and until 0.8d31 the host discarded both — a failed command was visible in
    the log only as a request with no matching outcome. Returns None for a
    success or for anything that is not a STATUS frame, so callers can log
    unconditionally without adding noise to the normal path.
    """
    # Line endings are NOT uniform inside one frame: classic-Mac C maps '\n' to
    # CR and '\r' to LF, so the daemon emits STATUS/STDOUT terminated by CR and
    # STDERR's length by LF. Splitting on CR alone found the code and silently
    # dropped the explanation — which is the half worth logging.
    def _split1(s):
        cr, lf = s.find("\r"), s.find("\n")
        i = min(x for x in (cr, lf) if x >= 0) if (cr >= 0 or lf >= 0) else -1
        return (s, "") if i < 0 else (s[:i], s[i + 1:])

    if not text.startswith("STATUS:"):
        return None
    head, rest = _split1(text)
    try:
        status = int(head.split(":", 1)[1].strip())
    except (ValueError, IndexError):
        return None
    if status == 0:
        return None
    detail = ""
    if "STDERR:" in rest:
        nstr, body = _split1(rest.split("STDERR:", 1)[1])
        try:
            n = int(nstr.strip())
        except ValueError:
            n = 0
        if n > 0:
            detail = body[:n]
    return f"STATUS:{status}" + (f" {detail.strip()[:160]}" if detail else "")


# The control-port verbs, derived ONCE so the hint below cannot drift from the
# dispatch above it. A hint that lists the wrong verbs is worse than none: it
# reads as authoritative and sends the caller somewhere else again.
ROUTED_VERBS = ("PING", "STATUS-via-mac_status", "DISKINFO", "LISTDIR", "MONITOR",
                "PROCLIST", "SCREENSHOT", "NBPLOOK", "AFPMOUNT", "AFPUNMOUNT",
                "CLIPGET", "CLIPSET", "LAUNCH:", "QUIT:", "KEY:", "TYPE:",
                "CLICK:", "REBOOT", "SHUTDOWN", "SWAPSELF", "QUITDAEMON")


def looks_like_verb(cmd):
    """True for a single ALL-CAPS token, i.e. something typed AS a verb.

    Deliberately narrow: an MPW command line (`Files -l :bin:`, `SC main.c`)
    has spaces and lower case, so it never trips this. `MENUTREE`, `STAT` and
    `STATUS` all do — which is the whole point.
    """
    head = (cmd or "").split(":", 1)[0].strip()
    return bool(head) and head.isupper() and " " not in head and head.isalnum()


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
        """Write, optionally PACED so the guest's input buffer cannot overrun.

        The classic Serial Manager's default input buffer is 64 bytes. A guest
        that has not installed a bigger one (every daemon before 0.8d28) loses
        bytes SILENTLY when the host streams a large payload at line rate — an
        8 KB file arrived with the right length and the wrong contents. The host
        is the only party that can throttle, so a paced write is what lets a
        FIXED daemon be deployed over a link that is still broken.

        Off by default (chunk 0 = one write, full speed). Set
        APPLEBRIDGE_SERIAL_CHUNK (bytes) and APPLEBRIDGE_SERIAL_GAP (seconds) to
        enable — e.g. 64 / 0.015 keeps a 64-byte-buffer guest fed just under
        wire rate at 57600."""
        mv = memoryview(data)
        chunk = SERIAL_CHUNK
        while mv:
            _, w, _ = select.select([], [self.fd], [], self._timeout)
            if not w:
                raise socket.timeout()
            piece = mv[:chunk] if chunk else mv
            try:
                sent = os.write(self.fd, piece)
            except BlockingIOError:
                continue
            mv = mv[sent:]
            if chunk and mv and SERIAL_GAP:
                time.sleep(SERIAL_GAP)     # let the guest drain before the next burst

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


# The three things an absent ABVERSION can mean. Collapsing them is what made a
# slow peer look like an old one: see classify_hello_reply.
HELLO_V2, HELLO_LEGACY, HELLO_SILENT, HELLO_STALE = "v2", "legacy", "silent", "stale"


def classify_hello_reply(resp):
    """Which of FOUR things happened, not which of two.

    `parse_hello_reply` answers "v1" for anything without an `ABVERSION:` token,
    which folds three different events into one verdict:

      * a genuine v0.1 daemon, which replies `Invalid command format`
      * NO reply inside the deadline
      * somebody ELSE's reply — the link is a frame behind

    On real hardware the third one happens routinely and is the reason this
    exists. The host sends a priming PING after accept and deliberately discards
    its reply, because the first packet over a fresh connection is often corrupt.
    But if that PING *times out* rather than being read, nothing is discarded:
    the reply is still in flight, `_drain()` finds nothing to remove, and the
    HELLO read then collects `STATUS:0…PONG…` — no ABVERSION, so "legacy v1".

    Observed on a Macintosh SE/30 over MacTCP, 2026-07-28: the same daemon that
    had negotiated **v2** minutes earlier was reported legacy on the next
    connect. A version verdict that depends on timing is not a verdict.

    It is not cosmetic. With a token configured the host requires v2 + FEAT=auth
    and drops the link otherwise, fail-closed — so authentication on that machine
    would have looped forever while the log blamed an old daemon.
    """
    if not resp:
        return HELLO_SILENT
    if "ABVERSION:" in resp:
        return HELLO_V2
    if "Invalid command format" in resp:
        return HELLO_LEGACY
    return HELLO_STALE


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
    """How long to wait for this command line — the longest budget any verb in
    it warrants.

    It used to read the FIRST token only, which is wrong for the compound lines
    this bridge is actually driven with. `Directory "…"; Rez … ; SetFile …` was
    classified as `Directory` — 15 s — so a `Rez` that needs the 240 s budget
    was abandoned mid-reply. Twice on 2026-07-28 that surfaced as
    `command timeout after 15s` followed by `drained N stale bytes`, and the
    caller was told "Mac daemon not connected" about a command that had in fact
    completed: the guest was fine, the host simply stopped listening. Being told
    a successful command failed is the more expensive direction of this error,
    because the obvious response is to run it again.

    Splitting on `;` alone would not do: MPW also separates with newline — CR
    over the wire — and with `&&` / `||`.

    Only each segment's FIRST token is tested, so a long-command name appearing
    as an argument (`Echo "rez"`) does not inflate the budget. Erring long is
    cheap anyway: the timeout is an upper bound, not a delay.
    """
    for seg in re.split(r"[;\r\n]|&&|\|\|", command):
        tok = seg.strip().lstrip("(").split(None, 1)
        if tok and tok[0].lower() in LONG_CMDS:
            return LONG_TIMEOUT
    return DEFAULT_TIMEOUT


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
        # A client cannot otherwise tell that a reconnect happened: uptime, rx, tx
        # and err are all cumulative for the daemon PROCESS, so after a redial
        # they simply continue (err=117 included, observed on the SE/30
        # 2026-07-28). So an identifier issued before a reconnect is
        # indistinguishable from one issued after, and "your job belonged to a
        # link that no longer exists" is a statement the bridge cannot make.
        #
        # link_generation counts accepted daemon links. It is paired with an
        # EPOCH because a bare counter restarts at 1 with the host server, which
        # would make generation 3 of today collide with generation 3 of an hour
        # ago — a value that looks continuous and is not, which is the failure
        # this project keeps finding in itself. Epoch differs => different host
        # process => any identifier from before is void, without comparing counts.
        self.link_epoch = os.urandom(4).hex()
        self.link_generation = 0
        # Label of the command whose reply we stopped waiting for, or None. This
        # is also the instrumentation that was missing: the drain used to report
        # how many bytes it discarded but never what had left them there, which
        # is why a desync read as random verb failure.
        self.desynced = None
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
        try:
            self.server_socket.bind((self.interface, self.port))
        except OSError as e:
            # EADDRNOTAVAIL is the fresh-machine failure: a configured address
            # that no interface carries. The bare errno names neither, so say
            # both before re-raising (R1).
            if e.errno == errno.EADDRNOTAVAIL:
                for line in host_config.explain_bind_failure(self.interface).splitlines():
                    log(line)
            raise
        self.server_socket.listen(1)
        log(f"Listening on {self.interface}:{self.port} (waiting for Mac daemon)")
        log(f"  host address from: {HOST_INTERFACE_SOURCE}")
        if self.interface == host_config.BIND_ALL:
            # Bound to everything, so the guest's IP= is the open question —
            # answer it here rather than leaving it to be guessed.
            hint = host_config.describe_reachability()
            if hint:
                log(f"  {hint}")

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
                    self.link_generation += 1
                    log(f"Serial link open on {self.serial_dev} "
                        f"(link {self.link_epoch}:{self.link_generation})")
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
        self.desynced = None          # a fresh stream is aligned by definition
        self.link_generation += 1
        log(f"Mac connected from {addr} (link {self.link_epoch}:{self.link_generation})")
        return addr

    def _mark_disconnected(self, reason):
        # Idempotent: callers of _drain() follow a False with their own generic
        # "drain detected closed socket", which would replace a specific reason
        # (an unrecoverable desync, say) with a vague one in the log.
        if not self.connected and self.client_socket is None:
            return
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
        # After an abandoned read, wait for the late reply rather than sweeping
        # only what has already landed — that sweep is what missed it before.
        resync = self.desynced
        deadline = time.monotonic() + RESYNC_BUDGET if resync else None
        quiet_until = time.monotonic() + RESYNC_QUIET if resync else None
        discarded = 0
        alive = True
        try:
            self.client_socket.setblocking(False)
            while True:
                try:
                    chunk = self.client_socket.recv(65536)
                except BlockingIOError:
                    if not resync:
                        break      # nothing pending -> healthy idle socket
                    now = time.monotonic()
                    if now >= quiet_until:
                        break      # silent long enough: the late reply is all in
                    if now >= deadline:
                        # Still arriving after the whole budget. This stream
                        # cannot be realigned, and continuing would give every
                        # later command somebody else's answer.
                        self._mark_disconnected(
                            f"link out of step after {resync!r} and still "
                            f"streaming after {RESYNC_BUDGET:.0f}s "
                            f"({discarded}B discarded); dropped so the daemon "
                            f"redials with a clean stream")
                        self.desynced = None
                        return False
                    time.sleep(0.02)
                    continue
                except OSError:
                    alive = False
                    break
                if not chunk:
                    alive = False  # peer closed
                    break
                discarded += len(chunk)
                if resync:
                    # The budget must be checked HERE too. Checking it only in
                    # the "nothing pending" branch means a peer that keeps
                    # sending is never tested against the deadline at all — an
                    # unbounded loop, which is how this was first written and
                    # what hung the test that now covers it.
                    if time.monotonic() >= deadline:
                        self._mark_disconnected(
                            f"link out of step after {resync!r} and still "
                            f"streaming after {RESYNC_BUDGET:.0f}s "
                            f"({discarded}B discarded); dropped so the daemon "
                            f"redials with a clean stream")
                        self.desynced = None
                        return False
                    quiet_until = time.monotonic() + RESYNC_QUIET
                    log(f"resync: discarded {len(chunk)}B left by {resync!r}")
                else:
                    log(f"drained {len(chunk)} stale bytes (anti-desync)")
        finally:
            try:
                if self.client_socket:
                    self.client_socket.setblocking(True)
            except OSError:
                alive = False
        if resync and alive:
            log(f"resync: link realigned after {resync!r} "
                f"({discarded}B discarded)" if discarded
                else f"resync: nothing arrived after {resync!r}; link looks aligned")
            self.desynced = None
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

        fills = []

        def _fill():
            chunk = self.client_socket.recv(65536)   # large reads for MB-scale stdout
            if not chunk:
                raise ConnectionError("peer closed mid-response")
            if FRAME_DEBUG:
                fills.append(len(chunk))
                log(f"  [frame] recv {len(chunk)}B {chunk[:48]!r}")
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
                    # STDERR is length-framed too, and reading it by TERMINATOR
                    # SEARCH was the defect. The daemon emits each piece as its
                    # own send:
                    #   STATUS:<c>| STDOUT:<n>| [<n bytes>|] STDERR:<m>| [<m bytes>|] |
                    # so on a fragmented link they arrive as separate recv()s.
                    # The search could then return before the STDERR section had
                    # arrived, leaving it in the socket — and the link is
                    # PERSISTENT, so the next command read those leftovers and
                    # every reply after was one behind.
                    #
                    # Measured on a Macintosh SE/30, 2026-07-28: a DISKINFO whose
                    # 142-byte payload was consumed correctly returned only ONE
                    # further byte, abandoning `STDERR:0||` — exactly the ten
                    # bytes the following LISTDIR then read as its own reply.
                    # Basilisk never showed it because the whole frame lands in
                    # one recv there. This is the project's own documented rule
                    # ("read by declared length, not by terminator") applied to
                    # the half that was still guessing.
                    rest = b""
                    if olen > 0:
                        rest += _read_line() + b"\r"      # trailer after the payload
                    err_hdr = _read_line()
                    rest += err_hdr + b"\r"
                    try:
                        elen = int(err_hdr.split(b":", 1)[1].strip() or b"0")
                    except (ValueError, IndexError):
                        elen = -1
                    if 0 <= elen <= MAX_DECLARED:
                        if elen > 0:
                            rest += _read_exact(elen) + _read_line() + b"\r"
                        rest += _read_line()              # end marker
                    else:
                        # Not a STDERR header we understand: fall back rather
                        # than read a length we did not parse.
                        rest += _read_until_terminator()
                    raw = status_line + b"\r" + out_hdr + b"\r" + stdout + rest
                    if FRAME_DEBUG:
                        log(f"  [frame] olen={olen} stdout={len(stdout)}B "
                            f"err_hdr={err_hdr!r} elen={elen} rest={len(rest)}B "
                            f"{rest[:48]!r} buf_left={len(buf)}B fills={fills}")
                else:
                    rest = _read_until_terminator()
                    raw = status_line + b"\r" + out_hdr + b"\r" + rest
            else:
                outcome = "terminator"               # non-STATUS (IMAGE etc.): legacy read
                raw = _read_until_terminator()
        except socket.timeout:
            outcome = "timeout"
            log(f"command timeout after {to:.0f}s: {str(label)[:48]!r}")
            # The remainder of this reply is still in flight on a persistent
            # link. Record it so the next drain realigns instead of handing the
            # leftovers to whatever command comes next.
            self.desynced = str(label)[:48] or "unlabelled read"
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
        if FRAME_DEBUG:
            try:
                self.client_socket.setblocking(False)
                extra = b""
                while True:
                    try:
                        c = self.client_socket.recv(65536)
                    except (BlockingIOError, InterruptedError):
                        break
                    if not c:
                        break
                    extra += c
                if extra:
                    log(f"  [frame] *** {len(extra)}B STILL IN THE SOCKET after "
                        f"{str(label)[:24]!r}: {extra[:64]!r} — the next command "
                        f"would read this as its own reply")
                    buf.extend(extra)     # put it back into the frame we return
            except OSError:
                pass
            finally:
                try:
                    self.client_socket.setblocking(True)
                except OSError:
                    pass
        text = bytes(raw).decode("mac_roman", errors="replace")
        # A FAILING command left no trace here at all: the request was logged,
        # the response was not, so the log showed a verb going out and nothing
        # coming back — indistinguishable from a verb that worked. Log the
        # status and the daemon's own error text whenever it is not 0, which is
        # rare enough to stay quiet and is the line you want when it is not.
        failure = _framed_failure(text)
        if failure:
            log(f"{label or 'command'} failed: {failure}")
        return text

    def send_apple_event(self, target_hex, class_hex, id_hex, do_bytes,
                         wait_ticks=None):
        """AESEND: send an arbitrary Apple Event (event class/ID) to the app with
        the given creator signature, with an optional text direct object, and
        return the daemon's STATUS reply (the AE reply text rides STDOUT). The
        OSTypes go as 8-hex; the direct object is length-framed raw bytes.

        `wait_ticks` bounds how long the DAEMON may block inside AESend, which is
        how long the guest is unavailable to everything else — on a cooperative
        scheduler an application that does not yield takes the machine with it.
        None leaves the daemon's own interactive default in force; 0 sends the
        event kAENoReply, which is correct whenever the target's vocabulary
        declares the reply 'null'.

        Our read timeout is derived from that bound rather than fixed at
        LONG_TIMEOUT, so the daemon is always the side that gives up first: if
        the host abandoned the read while the daemon was still waiting, we would
        report a timeout about a guest that is still starving."""
        if not self.connected or not self.client_socket:
            return None
        if not self._drain():
            self._mark_disconnected("drain detected closed socket")
            return None
        suffix = "" if wait_ticks is None else f":{int(wait_ticks)}"
        header = (f"AESEND:{target_hex}:{class_hex}:{id_hex}:"
                  f"{len(do_bytes)}{suffix}\n").encode("ascii")
        try:
            self.client_socket.sendall(header + do_bytes)
        except OSError as e:
            self._mark_disconnected(f"send failed: {e}")
            return None
        log(f"AESEND target={target_hex} class={class_hex} id={id_hex} "
            f"do={len(do_bytes)}B wait={'default' if wait_ticks is None else wait_ticks}")
        return self._read_framed_response(self._ae_read_timeout(wait_ticks),
                                          label="AESEND")

    @staticmethod
    def _ae_read_timeout(wait_ticks):
        """Seconds to wait for an AESEND reply: the daemon's own bound plus room
        for the round trip, never more than LONG_TIMEOUT."""
        if wait_ticks is None:
            ticks = AE_SEND_DEFAULT_TIMEOUT_TICKS
        else:
            ticks = max(0, int(wait_ticks))
        return min(LONG_TIMEOUT, ticks / 60.0 + AE_SEND_READ_MARGIN)

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

        # Read by DECLARED LENGTH, not by terminator scan. This used to loop on
        # recv until the buffer contained "\n\n"/"\r\r" — and that pair occurs
        # INSIDE a normal reply, because the payload's final line ends with a
        # separator and the frame adds another one straight after it. The scan
        # therefore stopped before the STDERR section, which stayed in the
        # socket; the link is persistent, so the NEXT command read those
        # leftovers as its own reply and everything after was a frame behind.
        #
        # It only bites on a fragmented link. The daemon streams each piece as
        # its own ABSend (protocol.c), so on a slow peer they arrive as separate
        # recv()s and the loop breaks with the tail still unread. On Basilisk the
        # whole frame lands in ONE recv, so the break happens after everything is
        # already buffered and nothing is left — which is why this survived so
        # long and only ever appeared on real hardware.
        #
        # Measured on a Macintosh SE/30, 2026-07-28: DISKINFO with five mounted
        # volumes left exactly `STDERR:0\n\n` — ten bytes — and the following
        # LISTDIR returned them as its answer. `request_log` immediately below
        # already reads by declared length and says why; send_raw never got the
        # lesson.
        return self._read_framed_response(timeout, label=data)

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
        # Read, classify, and RETRY when the answer is not an answer. A stale
        # frame means the link is behind, not that the peer is old; silence means
        # we did not wait long enough, not that the peer is old. Neither is
        # evidence about a version, so neither is allowed to decide one.
        resp = self._read_framed_response(HELLO_TIMEOUT, label="HELLO")
        kind = classify_hello_reply(resp)
        if kind in (HELLO_STALE, HELLO_SILENT) and self.connected:
            log(f"HELLO: {kind} reply "
                + (f"({(resp or '')[:24]!r}) " if kind == HELLO_STALE else "")
                + "— draining and retrying once before judging the version")
            if not self._drain():
                self._mark_disconnected("drain detected closed socket")
                return
            try:
                self.client_socket.sendall(hello.encode("ascii"))
            except OSError as e:
                self._mark_disconnected(f"HELLO resend failed: {e}")
                return
            resp = self._read_framed_response(HELLO_TIMEOUT * HELLO_RETRY_FACTOR,
                                              label="HELLO(retry)")
            kind = classify_hello_reply(resp)
            if kind == HELLO_STALE:
                # Two frames out of step: this link cannot be trusted for
                # anything, and every command after would read the wrong reply.
                # Dropping it is the only honest recovery — the daemon
                # reconnects and we start from a clean stream.
                self._mark_disconnected("HELLO still out of step; link desynced")
                log("HELLO: link is desynchronised (a reply behind); dropped so "
                    "the daemon reconnects with a clean stream")
                return
        version, feat, daemon_nonce, daemon_proof = parse_hello_reply(resp or "")
        self.peer_version = min(AB_PROTOCOL_VERSION, version)
        self.peer_feat = feat

        if not AUTH_TOKEN:
            if self.peer_version >= 2:
                log(f"HELLO: negotiated protocol v{self.peer_version} "
                    f"feat={sorted(self.peer_feat)}")
            elif kind == HELLO_LEGACY:
                log("HELLO: peer answered as v0.1 (Invalid command format) — "
                    "legacy, proceeding without negotiation")
            else:
                # Say what we know: nothing. The old line claimed the peer was
                # legacy, which is a statement about the daemon made from a
                # timeout on the host.
                log("HELLO: no usable reply after a retry — ASSUMING v1. This is "
                    "an assumption, not an observation; a slow peer looks the "
                    "same from here.")
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


def notes_payload(window=600):
    """One line for the NOTES field, or "" when the channel is quiet.

    Announcing, not routing. The control port carries no identity — every client
    opens a socket per command and closes it — so the server cannot know WHO is
    asking and must not pretend to. It reports THAT the channel has something;
    `notes.py list` reads the caller's own session name from its environment and
    is the only side that can say whether it is addressed to them.

    Counts and a pointer, never the note text. The field is length-delimited so
    arbitrary text would be safe to transport, but a body carrying a CR would
    still break every line-oriented reader downstream, and the text is one
    command away for anyone who wants it.

    Never raises: this runs on the path of every control response.
    """
    if notes is None:
        return ""
    try:
        lines = notes.read()
        open_count = len(notes.open_notes(lines))
        fresh = len(notes.recent(notes.all_notes(lines),
                                 datetime.datetime.now(), window))
        if not (open_count or fresh):
            return ""
        parts = []
        if open_count:
            parts.append(f"{open_count} open")
        if fresh:
            parts.append(f"{fresh} in the last {window // 60}m")
        # ASCII only. The declared length counts CHARACTERS here, as everywhere
        # on this port, so a non-ASCII body would desync any reader that counted
        # bytes instead. Not a question worth leaving open in a wire format.
        return f"session channel: {', '.join(parts)} - run host/tools/notes.py list"
    except Exception:
        return ""


def with_notes(frame):
    """Splice the optional NOTES field in before the frame's terminator.

    A named field appended at the END is the one extension this protocol takes
    without a flag day: every reader in the tree seeks its fields BY NAME —
    `mac_connection` walks lines and skips what it does not recognise,
    `smoke_e2e` and `build.py` search for their tags, `send_command` does not
    parse at all — so a field nobody looks for is a field nobody trips over.
    Verified against all four before this was written, because "it should be
    ignored" is a prediction and the parsers are checkable.

    Only the normal response path carries it. The two early rejections (control
    auth, no daemon linked) are left alone: a caller being told the bridge is
    down does not need a second subject in the same breath.
    """
    payload = notes_payload()
    if not payload or not frame.endswith("\r\r"):
        return frame
    return f"{frame[:-1]}NOTES:{len(payload)}\r{payload}\r\r"


def run_control_server(server):
    """Non-TTY production path: serve control commands, auto-re-accept the Mac."""
    bind_addr, bind_src = host_config.resolve_control_bind()
    control = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    control.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    control.bind((bind_addr, CONTROL_PORT))
    control.listen(5)
    control.settimeout(1.0)
    log(f"Control port on {bind_addr}:{CONTROL_PORT} (send_command.py / MCP)")
    log(f"  control bind from: {bind_src}")
    if not host_config.control_bind_is_local(bind_addr):
        log("  reachable from the network — token auth REQUIRED and active")

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
                            # Host-side on purpose: it identifies the LINK, so it
                            # must still be reported when the daemon is silent —
                            # which is exactly when a caller needs to know its
                            # long-running work has been orphaned.
                            f"link_epoch={server.link_epoch}",
                            f"link_generation={server.link_generation}",
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
                    elif cmd.startswith("HOSTCLICK:"):
                        # Click the guest's REAL mouse at guest coords — the only
                        # way to reach modal dialogs / menus (their tracking loops
                        # poll the hardware pointer). Handled HOST-side; needs
                        # cliclick on PATH + Accessibility permission for this
                        # process. Args: HOSTCLICK:guestX:guestY[:count].
                        if guest_input is None:
                            m = "guest_input module not deployed"
                            out = f"STATUS:-1\rSTDOUT:0\rSTDERR:{len(m)}\r{m}\r\r"
                        else:
                            try:
                                p = cmd[len("HOSTCLICK:"):].split(":")
                                gx, gy = int(p[0]), int(p[1])
                                count = int(p[2]) if len(p) > 2 and p[2] else 1
                                with guest_input.Session() as s:
                                    pt = s.point(gx, gy)
                                    s.cliclick(guest_input.build_click(pt, count, None))
                                payload = json.dumps({"ok": True, "guest": [gx, gy],
                                                      "host": list(pt), "count": count})
                                out = (f"STATUS:0\rSTDOUT:{len(payload)}\r{payload}"
                                       f"\rSTDERR:0\r\r")
                                log(f"hostclick guest=({gx},{gy}) -> host={list(pt)}")
                            except Exception as e:
                                m = f"hostclick failed: {e}"
                                out = f"STATUS:-1\rSTDOUT:0\rSTDERR:{len(m)}\r{m}\r\r"
                    elif cmd.startswith("HOSTMENU:"):
                        # Pull down a menu with the REAL mouse in ONE gesture
                        # (title press -> drag to item -> release; split gestures
                        # leave the menu open and starve the daemon). Guest coords:
                        # HOSTMENU:titleX:titleY:itemX:itemY.
                        if guest_input is None:
                            m = "guest_input module not deployed"
                            out = f"STATUS:-1\rSTDOUT:0\rSTDERR:{len(m)}\r{m}\r\r"
                        else:
                            try:
                                p = cmd[len("HOSTMENU:"):].split(":")
                                tx, ty, ix, iy = int(p[0]), int(p[1]), int(p[2]), int(p[3])
                                with guest_input.Session() as s:
                                    tp = s.point(tx, ty)
                                    ip = s.point(ix, iy)
                                    s.cliclick(guest_input.build_menu_gesture(tp, ip))
                                payload = json.dumps({"ok": True, "title": [tx, ty],
                                                      "item": [ix, iy]})
                                out = (f"STATUS:0\rSTDOUT:{len(payload)}\r{payload}"
                                       f"\rSTDERR:0\r\r")
                                log(f"hostmenu title=({tx},{ty}) item=({ix},{iy})")
                            except Exception as e:
                                m = f"hostmenu failed: {e}"
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
                        # AESEND:<targetHex8>:<classHex8>:<idHex8>:<directObjB64>[:<waitTicks>]
                        # (direct object base64 so it stays colon/newline-safe on
                        # the text control hop; the daemon hop length-frames it.)
                        # waitTicks is optional and caps how long the DAEMON may
                        # block — 0 means kAENoReply. Omitted keeps the daemon's
                        # interactive default; an out-of-range value is clamped
                        # here rather than trusted, because it decides how long
                        # the guest can be starved.
                        try:
                            f = cmd.split(":")
                            target_hex, class_hex, id_hex = f[1], f[2], f[3]
                            do_bytes = (base64.b64decode(f[4])
                                        if len(f) > 4 and f[4] else b"")
                            wait_ticks = None
                            if len(f) > 5 and f[5] != "":
                                wait_ticks = max(0, min(AE_SEND_MAX_TIMEOUT_TICKS,
                                                        int(f[5])))
                            resp = server.send_apple_event(target_hex, class_hex,
                                                           id_hex, do_bytes,
                                                           wait_ticks=wait_ticks)
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
                    elif (cmd == "MACUITREE" or cmd == "DLGTEST" or cmd == "DLGOFF"
                          or cmd == "DLGINSTALL" or cmd == "DLGTREE" or cmd == "DLGUNINSTALL"
                          or cmd == "DLGSELFMODAL" or cmd == "DLGARM" or cmd == "DLGDISARM"
                          or cmd.startswith("DLGWALK") or cmd == "DLGWDISARM"
                          or cmd == "PING" or cmd == "QUITDAEMON" or cmd == "REBOOT"
                          or cmd == "SWAPSELF" or cmd == "SHUTDOWN"
                          or cmd == "JGATE" or cmd == "JMENU" or cmd == "JABOUT"
                          or cmd == "JSF" or cmd == "JSAFE" or cmd == "JPROBE"
                          or cmd.startswith("JMENU:") or cmd.startswith("JABOUT:")
                          or cmd.startswith("JSF:") or cmd.startswith("MENU:")
                          or cmd == "MSINSTALL" or cmd == "MSREAD" or cmd == "MSUNINSTALL"
                          or cmd.startswith("MSDRIVE:")
                          or cmd == "CPINSTALL" or cmd == "CPARM" or cmd == "CPDISARM"
                          or cmd == "CPREAD" or cmd == "CPUNINSTALL"
                          or cmd == "CPJINSTALL" or cmd == "CPJUNINSTALL"
                          or cmd == "PROCLIST"
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
                        # A command that LOOKS like a control verb and is not
                        # routed gets an answer about ToolServer — which sends
                        # the caller to entirely the wrong layer. Measured
                        # 2026-08-04, three times in one day: `STATUS` and `STAT`
                        # (237 daemon errors from one polling loop) and
                        # `MENUTREE` (an hour spent believing a fresh daemon
                        # deploy had failed). In each case the reply named
                        # ToolServer while the real fault was a missing route.
                        # The verb is not silently rewritten — this only says
                        # what happened.
                        if looks_like_verb(cmd) and "no-ToolServer" in (out or ""):
                            out = out.rstrip("\r") + (
                                "\rHINT:this is not a routed control-port verb, "
                                "so it was sent to ToolServer as an MPW command. "
                                "Routed verbs: " + ", ".join(ROUTED_VERBS) + "\r\r")
                    ctrl_conn.sendall(with_notes(out).encode("utf-8", errors="replace"))
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
    log("=== AppleBridge Host Server (hardened) ===")
    log(build_stamp())
    # Before ANY socket is opened: an exposed control port with no token must
    # not reach the point of listening, however briefly.
    _ctrl_addr, _ = host_config.resolve_control_bind()
    _refusal = host_config.check_control_exposure(
        _ctrl_addr, CTRL_TOKEN.decode("utf-8", "replace") if CTRL_TOKEN else "")
    if _refusal:
        for _line in _refusal.splitlines():
            log(_line)
        return 2
    server = AppleBridgeServer(serial_dev=SERIAL_DEVICE, serial_baud=SERIAL_BAUD)
    server.bind_listen()
    try:
        # Which mode this is has always been decided by isatty() and never
        # stated, so a server started the obvious way — in a terminal, to watch
        # it come up — serves :9000 perfectly and has NO control port at all.
        # Every tool then fails against a server that looks healthy (R12).
        forced = os.environ.get("APPLEBRIDGE_FORCE_CONTROL", "").strip().lower()
        force_control = forced not in ("", "0", "false", "no")
        if sys.stdin.isatty() and not force_control:
            log("Interactive mode (stdin is a terminal): typed commands only.")
            log(f"  NOTE: there is NO control port on :{CONTROL_PORT} in this mode,")
            log("        so send_command.py, the MCP tools and host/tools/* cannot")
            log("        reach this server. For those, start it as either of:")
            log("            ./run_server.sh < /dev/null")
            log("            APPLEBRIDGE_FORCE_CONTROL=1 ./run_server.sh")
            server.accept_mac()
            server.negotiate_version()
            interactive_mode(server)
        else:
            if sys.stdin.isatty():
                log("Control mode forced by APPLEBRIDGE_FORCE_CONTROL "
                    "(no interactive prompt).")
            run_control_server(server)
    except KeyboardInterrupt:
        log("Shutting down...")
    finally:
        server.close()


if __name__ == "__main__":
    sys.exit(main() or 0)

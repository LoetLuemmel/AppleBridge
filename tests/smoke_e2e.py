#!/usr/bin/env python3
"""
smoke_e2e.py — end-to-end smoke tier for AppleBridge.

Unlike the host-edge suite (test_*.py, run by run_all.sh in CI), this driver
needs a LIVE stack: host_server.py listening on the control port, the guest
daemon connected, and — for the AE tier — ToolServer running in the emulator.
It therefore does NOT run under CI; it is the "boot emulator, prove the bridge
actually round-trips" check you run by hand before a release or after touching
the transport / control-port path.

It talks only over the loopback control port (:9001), exactly like
send_command.py, so it is pure stdlib and imports nothing from host/.

Tiers (each check is tagged):
  host       host server reachable on the control port
  bridge     daemon connected + liveness, native LISTDIR, screenshot stream
  toolserver Apple-Event round-trip through ToolServer (Echo a nonce)
  mutating   text file write -> read-back round-trip   (only with --full)

Usage:
  ./tests/smoke_e2e.py                 # read-only smoke, assumes ToolServer up
  ./tests/smoke_e2e.py --no-toolserver # skip the Echo tier (e.g. SheepShaver/OS 9)
  ./tests/smoke_e2e.py --full          # also do the file write/read round-trip
  ./tests/smoke_e2e.py --host H --port P

Exit code: 0 if no check FAILed (SKIPs are fine), 1 otherwise.
"""
import argparse
import base64
import os
import socket
import sys

# --- ANSI (only when writing to a tty) ---------------------------------------
_TTY = sys.stdout.isatty()
def _c(code, s):
    return f"\033[{code}m{s}\033[0m" if _TTY else s
GREEN, RED, YELLOW, DIM = "32", "31", "33", "2"


# --- control-port transport (mirror of send_command.py) ----------------------
def ctrl(cmd, host, port, timeout=20.0):
    """Send one control command; return the decoded reply (or raise)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect((host, port))
        sock.sendall(cmd.encode("utf-8"))
        sock.shutdown(socket.SHUT_WR)
        buf = b""
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                break
            buf += chunk
        return buf.decode("utf-8", errors="replace")
    finally:
        sock.close()


def parse_framed(resp):
    """Extract (status:int|None, stdout:str, stderr:str) from a STATUS frame.

    Mirrors host_server.py's own reader: fields are read by their declared
    length, not by a terminator, so an embedded blank line in STDOUT survives.
    Separators are CR or LF depending on the hop, so we skip exactly one.
    """
    status = None
    k = resp.find("STATUS:")
    if k >= 0:
        j = k + len("STATUS:")
        m = j
        if m < len(resp) and resp[m] == "-":
            m += 1
        while m < len(resp) and resp[m].isdigit():
            m += 1
        try:
            status = int(resp[j:m])
        except ValueError:
            status = None

    def read_len_field(tag):
        p = resp.find(tag)
        if p < 0:
            return ""
        d = p + len(tag)
        start = d
        while d < len(resp) and resp[d].isdigit():
            d += 1
        try:
            n = int(resp[start:d])
        except ValueError:
            return ""
        return resp[d + 1:d + 1 + n]  # skip one separator byte

    return status, read_len_field("STDOUT:"), read_len_field("STDERR:")


def parse_kv(payload):
    """Semicolon-joined k=v pairs (MACSTATUS/STAT body) -> dict."""
    d = {}
    for part in payload.split(";"):
        if "=" in part:
            key, val = part.split("=", 1)
            d[key.strip()] = val.strip()
    return d


# --- result plumbing ---------------------------------------------------------
class Report:
    def __init__(self):
        self.rows = []          # (state, tier, name, detail)
        self.failed = 0

    def record(self, state, tier, name, detail=""):
        self.rows.append((state, tier, name, detail))
        if state == "FAIL":
            self.failed += 1
        colour = {"PASS": GREEN, "FAIL": RED, "SKIP": YELLOW}[state]
        tag = _c(DIM, f"[{tier}]")
        line = f"  {_c(colour, state.ljust(4))} {tag:<14} {name}"
        if detail:
            line += _c(DIM, f"  — {detail}")
        print(line)

    def summary(self):
        n = len(self.rows)
        passed = sum(1 for r in self.rows if r[0] == "PASS")
        skipped = sum(1 for r in self.rows if r[0] == "SKIP")
        print()
        verdict = _c(RED, "SMOKE FAILED") if self.failed else _c(GREEN, "SMOKE PASSED")
        print(f"{verdict}  —  {passed} passed, {self.failed} failed, "
              f"{skipped} skipped  (of {n})")


def check(rep, tier, name):
    """Decorator-ish helper: run body(), turn a returned (ok, detail) into a row,
    and turn an exception into a FAIL with the message."""
    def run(body):
        try:
            ok, detail = body()
            rep.record("PASS" if ok else "FAIL", tier, name, detail)
            return ok
        except SmokeSkip as s:
            rep.record("SKIP", tier, name, str(s))
            return None
        except Exception as e:  # a raised error is a failed check, not a crash
            rep.record("FAIL", tier, name, f"{type(e).__name__}: {e}")
            return False
    return run


class SmokeSkip(Exception):
    """Raise from a check body to mark it SKIP rather than FAIL."""


# --- the checks --------------------------------------------------------------
def run_smoke(host, port, do_toolserver, do_mutating):
    rep = Report()
    print(f"AppleBridge e2e smoke — control port {host}:{port}\n")

    # 1) host server reachable ------------------------------------------------
    def _host():
        try:
            s = socket.create_connection((host, port), timeout=5)
            s.close()
            return True, f"{host}:{port} accepting connections"
        except OSError as e:
            raise Exception(f"cannot reach control port ({e}); "
                            f"start it with host/start_stack.sh")
    if not check(rep, "host", "control-port reachable")(_host):
        rep.summary()          # nothing else can work; stop here
        return rep

    # 2) daemon liveness (host-answered, so it works even if daemon is down) ---
    stat = {}

    def _live():
        _, body, _ = parse_framed(ctrl("MACSTATUS", host, port))
        kv = parse_kv(body)
        stat.update(kv)
        if kv.get("host_connected") != "1":
            raise Exception("daemon not connected (host_connected=0) — "
                            "launch :bin:AppleBridge in the emulator")
        if kv.get("daemon_responding") != "1":
            return False, f"daemon connected but STAT silent: {body[:80]}"
        return True, (f"net={kv.get('net', '?')}, "
                      f"toolserver={kv.get('toolserver', '?')}, "
                      f"home={kv.get('home', '?')}")
    check(rep, "bridge", "daemon liveness (MACSTATUS)")(_live)

    # 3) Apple-Event round-trip through ToolServer (Echo a fresh nonce) --------
    def _echo():
        if not do_toolserver:
            raise SmokeSkip("--no-toolserver")
        if stat.get("toolserver") == "0":
            raise SmokeSkip("STAT reports toolserver=0 (not running on this target)")
        nonce = "SMOKE" + base64.b16encode(os.urandom(4)).decode("ascii")
        status, out, _ = parse_framed(ctrl(f"Echo {nonce}", host, port))
        if status != 0:
            return False, f"STATUS:{status} (ToolServer up? MPW Shell gives empty replies)"
        if out.strip() != nonce:
            return False, f"echoed {out.strip()!r}, expected {nonce!r}"
        return True, f"round-tripped nonce {nonce}"
    check(rep, "toolserver", "AE echo (ToolServer)")(_echo)

    # 4) native directory listing (PBGetCatInfo path, no ToolServer) ----------
    def _listdir():
        home = stat.get("home")
        if not home or ":" not in home:
            raise SmokeSkip("no home= in STAT to derive a folder")
        folder = home.rsplit(":", 1)[0] + ":"      # containing folder of the daemon
        status, out, _ = parse_framed(ctrl(f"LISTDIR:{folder}", host, port))
        if status != 0 or not out.strip():
            return False, f"LISTDIR {folder} -> STATUS:{status}, {len(out)}B"
        return True, f"{folder} listed ({len(out.splitlines())} lines)"
    check(rep, "bridge", "native LISTDIR")(_listdir)

    # 5) screenshot streaming path (raw pixmap -> PNG, decoded host-side) ------
    def _shot():
        status, out, _ = parse_framed(ctrl("screenshot", host, port, timeout=45.0))
        if status != 0 or not out:
            return False, f"STATUS:{status}, {len(out)}B payload"
        try:
            png = base64.b64decode(out)
        except Exception as e:
            return False, f"payload not base64: {e}"
        if not png.startswith(b"\x89PNG\r\n\x1a\n"):
            return False, f"not a PNG (magic {png[:8]!r})"
        return True, f"valid PNG, {len(png)} bytes"
    check(rep, "bridge", "screenshot stream")(_shot)

    # 6) text file write -> read-back round-trip (mutating; opt-in) -----------
    def _roundtrip():
        if not do_mutating:
            raise SmokeSkip("--full not given")
        home = stat.get("home")
        if not home or ":" not in home:
            raise SmokeSkip("no home= to place a temp file")
        folder = home.rsplit(":", 1)[0] + ":"
        leaf = "ABSmoke.txt"
        mac_path = folder + leaf
        token = "smoke-" + base64.b16encode(os.urandom(6)).decode("ascii")
        payload = f"AppleBridge e2e smoke {token}\n".encode("mac_roman")
        # WRITEFILE:<pathB64>:<typeHex8>:<creatorHex8>:<dataB64>:<rsrcB64>
        write = ("WRITEFILE:"
                 + base64.b64encode(mac_path.encode("mac_roman")).decode("ascii")
                 + ":54455854:74747874:"                     # 'TEXT' / 'ttxt'
                 + base64.b64encode(payload).decode("ascii")
                 + ":")
        wstat, _, werr = parse_framed(ctrl(write, host, port))
        if wstat != 0:
            return False, f"WRITEFILE -> STATUS:{wstat} {werr[:60]}"
        # READFILE returns MacBinary base64; the data fork holds our text.
        rstat, rout, _ = parse_framed(ctrl(f"READFILE:{mac_path}", host, port))
        if rstat != 0 or not rout:
            return False, f"READFILE -> STATUS:{rstat}, {len(rout)}B"
        blob = base64.b64decode(rout)
        if token.encode("ascii") not in blob:
            return False, "read-back did not contain the written token"
        return True, f"wrote+read {leaf} ({token})"
    check(rep, "mutating", "file write/read round-trip")(_roundtrip)

    rep.summary()
    return rep


def main():
    ap = argparse.ArgumentParser(description="AppleBridge end-to-end smoke tier")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=9001)
    ap.add_argument("--no-toolserver", action="store_true",
                    help="skip the Apple-Event Echo tier (targets without ToolServer)")
    ap.add_argument("--full", action="store_true",
                    help="also run the mutating file write/read round-trip")
    args = ap.parse_args()

    rep = run_smoke(args.host, args.port,
                    do_toolserver=not args.no_toolserver,
                    do_mutating=args.full)
    sys.exit(1 if rep.failed else 0)


if __name__ == "__main__":
    main()

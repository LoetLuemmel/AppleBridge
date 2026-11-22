#!/usr/bin/env python3
"""Clipboard round trips with ANOTHER application in front -- the case the daemon got wrong until 0.8d51.

    uv run python host/tools/s7floor/cliptest.py <ctrl-port> <rounds> ["<command that puts the Finder in front>"]

Each round: note the front process, CLIPSET a text with non-ASCII characters, CLIPGET it, note the front again.
A round passes when the text comes back unchanged AND the front application is the same before and after (the
daemon may borrow the front, it must give it back). Ends with the daemon's error counter, which a successful
CLIPSET must not raise. The optional command runs before every round -- for the s7test guest:
"host/tools/s7floor/guest.sh click 560 420" (a click on the desktop).
"""
import os
import socket
import subprocess
import sys
import time

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))


def main():
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    port, rounds = int(sys.argv[1]), int(sys.argv[2])
    os.environ["APPLEBRIDGE_CTRL_PORT"] = str(port)
    sys.path.insert(0, REPO)
    from mcp import tools

    def raw(c):
        with socket.create_connection(("127.0.0.1", port), timeout=30) as s:
            s.sendall((c + "\n\n").encode())
            b = b""
            while True:
                d = s.recv(65536)
                if not d:
                    break
                b += d
        return b.decode("mac_roman")

    def front():
        return [l.split("\t")[0] for l in raw("PROCLIST").replace("\r", "\n").split("\n") if l.endswith("\t1")]

    ok = 0
    for i in range(rounds):
        if len(sys.argv) > 3:
            subprocess.run(sys.argv[3].split())
            time.sleep(1.5)
        f0 = front()
        want = "clip round %d äöü" % i
        s = tools.mac_clipboard_set(want)
        f1 = front()
        g = tools.mac_clipboard_get()
        f2 = front()
        good = g.get("text") == want and f0 == f1 == f2 and f0 != ["AppleBridge"]
        ok += good
        print(i, "front", f0, f1, f2, "|", s.get("message"), "| get", repr(g.get("text")), "OK" if good else "FAIL",
              flush=True)
    st = tools.mac_status()
    print("passed %d/%d | err %s lasterr %r" % (ok, rounds, st.get("err_count"), st.get("last_error")))
    return 0 if ok == rounds else 1


if __name__ == "__main__":
    sys.exit(main())

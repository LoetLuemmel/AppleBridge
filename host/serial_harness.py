#!/usr/bin/env python3
"""serial_harness.py — a no-hardware serial bridge for testing the AppleBridge
serial transport (docs/SERIAL_TRANSPORT.md).

Creates two linked pseudo-terminals and relays bytes between them, so:
  - Basilisk II's `seriala <PATH_A>`  (the guest's modem port), and
  - the host server's `APPLEBRIDGE_SERIAL=<PATH_B>`
talk over a lossless in-process "wire" — exercising the guest serial backend, the
host serial reader, and the length-framed protocol together, with no real serial
hardware. (Equivalent to a `socat -d -d pty,raw,echo=0 pty,raw,echo=0` bridge, but
self-contained and stdlib-only.)

Usage:
  /usr/bin/python3 host/serial_harness.py
    → prints the two device paths, then relays until Ctrl-C. Point Basilisk's
      `seriala` at PATH_A (reboot the guest with NET=Serial) and start the host
      server with APPLEBRIDGE_SERIAL=PATH_B.
"""
import os
import select
import sys


def main():
    a_master, a_slave = os.openpty()
    b_master, b_slave = os.openpty()
    for fd in (a_master, b_master):
        os.set_blocking(fd, False)

    # Keep the slave fds open (never read/write them) so each pty stays alive even
    # before/after Basilisk or the host opens the slave path. We relay only the
    # masters; bytes written to a master are delivered to whoever opened the slave.
    print(f"Basilisk seriala        : {os.ttyname(a_slave)}", flush=True)
    print(f"Host APPLEBRIDGE_SERIAL : {os.ttyname(b_slave)}", flush=True)
    print("Relaying A<->B (Ctrl-C to stop)…", file=sys.stderr, flush=True)

    peer = {a_master: b_master, b_master: a_master}
    try:
        while True:
            r, _, _ = select.select([a_master, b_master], [], [], 1.0)
            for fd in r:
                try:
                    data = os.read(fd, 4096)
                except (BlockingIOError, OSError):
                    continue
                if not data:
                    continue
                try:
                    os.write(peer[fd], data)
                except OSError:
                    pass
    except KeyboardInterrupt:
        pass
    finally:
        for fd in (a_master, a_slave, b_master, b_slave):
            try:
                os.close(fd)
            except OSError:
                pass


if __name__ == "__main__":
    main()

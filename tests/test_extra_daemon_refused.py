"""A second daemon on an occupied daemon port is refused at once, not parked in the backlog.

Measured 2026-11-21: a PowerBook Duo whose prefs lacked TCPPORT= dialled the dev guest's port 9000. Its connect
succeeded (the OS completes the handshake), no HELLO came, it timed out with "no reply from host" — and this host
logged nothing. The dead connection sat in the listen backlog until the dev guest restarted at 16:17:38 and was then
"connected" and found closed. Real sockets on 127.0.0.1, no daemon, no network.

Run: python3 tests/test_extra_daemon_refused.py   (or via pytest)
"""

import os
import socket
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "host"))
import host_server  # noqa: E402


def _server_with_a_connected_guest():
    srv = host_server.AppleBridgeServer(interface="127.0.0.1", port=0)
    lst = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    lst.bind(("127.0.0.1", 0))
    lst.listen(1)
    srv.server_socket = lst
    srv.port = lst.getsockname()[1]
    guest = socket.create_connection(("127.0.0.1", srv.port))
    srv.client_socket, _ = lst.accept()
    srv.connected = True
    return srv, guest


def _closed_by_peer(sock):
    sock.settimeout(2.0)
    try:
        return sock.recv(1) == b""
    except ConnectionResetError:
        return True


def test_a_second_daemon_is_accepted_logged_and_closed():
    srv, guest = _server_with_a_connected_guest()
    logged = []
    host_server.log, saved = logged.append, host_server.log
    try:
        intruder = socket.create_connection(("127.0.0.1", srv.port))
        assert srv.refuse_extra_daemons() == 1
        assert _closed_by_peer(intruder), "the second daemon must see its connection end, not wait"
        assert any("second daemon from 127.0.0.1" in m and "TCPPORT=" in m for m in logged), logged
        assert srv.connected and srv.client_socket is not None, "the guest already being served is untouched"
    finally:
        host_server.log = saved
        for s in (guest, srv.client_socket, srv.server_socket):
            s.close()


def test_nothing_waiting_refuses_nothing():
    srv, guest = _server_with_a_connected_guest()
    try:
        assert srv.refuse_extra_daemons() == 0
    finally:
        for s in (guest, srv.client_socket, srv.server_socket):
            s.close()


def test_with_no_guest_connected_the_port_is_left_to_accept_mac():
    srv, guest = _server_with_a_connected_guest()
    srv.connected = False
    try:
        waiting = socket.create_connection(("127.0.0.1", srv.port))
        assert srv.refuse_extra_daemons() == 0, "a free port's next connection is the next guest, not an intruder"
        waiting.close()
    finally:
        for s in (guest, srv.client_socket, srv.server_socket):
            s.close()


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"ok   {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL {t.__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)

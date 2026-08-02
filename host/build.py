#!/usr/bin/env python3
"""Control-port client helpers: talk to the bridge, read an MPW `Files` listing.

This file used to be an automated build script — compile every `.c`, link, set
the file type, optionally run the result. That half is gone, for two reasons.

**It could not work.** `file_exists()` tested the response for `Got:`, a token
this protocol does not emit anywhere: not in `host_server.py`, not in the
daemon, not in any transcript in the repository. The check therefore answered
False for every successful build, so `compile_file()` and `link_files()`
reported FAILED for compiles and links that had in fact succeeded. It is the
same defect class the rest of this codebase spent 2026-08-02 on — a
verification that cannot fire — and it sat here for months because nothing
reached it: `tests/test_decider_coverage.py` counted these functions as covered
because the string `main.c` in unrelated tests matches `\\bmain\\b`, and
`main` reaches them all.

**It was the second copy.** `mac_compile` and `mac_build` (`mcp/tools.py`) run
the same recipe over `host/mpw.py`, where the oracle lives once and is tested
against the transcripts the guest actually prints. Two implementations of one
recipe with two different existence oracles is how they drift apart, and only
one of them was ever exercised.

What remains is what other things use and what is genuinely tested: the
control-port `send_command` (this file is one of the local clients named in
`docs/PROTOCOL_v0.2.md`), and the `Files`-listing parsing that
`tests/test_build_file_list.py` pins against live Basilisk II output.
"""
import os
import socket


def send_command(command: str, host: str = '127.0.0.1', port: int = 9001) -> str:
    """Send command to AppleBridge server"""
    token = os.environ.get("APPLEBRIDGE_CTRL_TOKEN", "")
    auth = f"AUTH:{token}\n" if token else ""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.connect((host, port))
        sock.sendall((auth + command).encode('utf-8'))
        sock.shutdown(socket.SHUT_WR)

        response = b""
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            response += chunk

        return response.decode('utf-8', errors='replace')
    finally:
        sock.close()


def parse_response(response: str) -> dict:
    """Split a framed reply into its status and its stdout.

    It used to also return a `success` flag, decided by looking for `Got:` in
    the text — the phantom token described in this module's docstring. Nothing
    read that flag, and what it said was wrong, so it is gone rather than
    fixed: a caller wanting a verdict about an artefact should ask
    `host/mpw.py`, which decides it from what the guest really prints.
    """
    result = {'status': None, 'stdout': ''}

    for line in response.strip().split('\n'):
        if line.startswith('STATUS:'):
            result['status'] = int(line.split(':')[1])

    if 'STDOUT:' in response:
        parts = response.split('STDERR:')
        if len(parts) > 1:
            stdout_lines = parts[0].split('\n')
            for i, line in enumerate(stdout_lines):
                if line.startswith('STDOUT:'):
                    result['stdout'] = '\n'.join(stdout_lines[i + 1:]).strip()
                    break

    return result


def mpw_quote(path: str) -> str:
    """Quote a path for MPW if it needs it, and only then.

    `Files MeinMac:My Folder:` is two arguments to the MPW shell, so a path
    with a space has to be quoted on the way OUT as well as unquoted on the way
    back. A path the caller already quoted is left alone rather than quoted
    twice.
    """
    if not path or path[0] in "'\"":
        return path
    if any(c in path for c in " \t"):
        return "'" + path.replace("'", "∂'") + "'"
    return path


def parse_files_output(stdout: str) -> list:
    """Names out of an MPW `Files` listing — one per LINE, never per token.

    This used to `.split()` on whitespace, and classic-Mac names are full of
    spaces ('System Folder', 'AppleBridge old'). `Files` prints one name per
    line and QUOTES any name that needs it, so a real listing came back as:

        AppleBridge, 'AppleBridge, old', AppleBridgeConfig

    — the space-bearing name silently replaced by two entries that name
    nothing. Everything downstream then compiles, links or deletes against a
    file list that is wrong in a way no status code reports. `mac_list_files`
    learned this and parses by column; this function never got the lesson.

    Verified against live `Files` output from Basilisk II, 2026-07-28.
    """
    names = []
    for raw in stdout.replace('\r', '\n').split('\n'):
        name = raw.strip()
        if not name:
            continue
        if len(name) >= 2 and name[0] == "'" and name[-1] == "'":
            # MPW escapes an embedded quote with the partial-diff character.
            name = name[1:-1].replace("∂'", "'")
        if name.startswith(':'):
            # Carried over from the original. No live `Files` output has been
            # seen to start with a colon — kept because dropping a filter on
            # the strength of "I could not make it fire" is how the four
            # unfalsifiable rules in this project's first commit happened.
            continue
        names.append(name)
    return names


def get_file_list(directory: str, pattern: str = '') -> list:
    """Get list of files in directory"""
    cmd = f'Files {mpw_quote(directory + pattern)}'
    response = send_command(cmd)
    result = parse_response(response)
    return parse_files_output(result['stdout'])

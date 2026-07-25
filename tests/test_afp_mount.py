"""Tests for the host half of AFPMOUNT/AFPUNMOUNT — routing and secret hygiene.

An AFPMOUNT request carries a password in the clear (the AFP mount record has
nowhere else to put it), so the two things that must never regress are: the verb
is ROUTED (an unrouted verb falls through to the MPW path, which logs the whole
request verbatim — that is how a real password would reach a long-lived log
file), and nothing that IS logged contains the password.

Both failure modes are invisible in a live run: the mount still works, and the
leak only shows up later in a log file. Hence tests rather than trust.

Run: python3 tests/test_afp_mount.py   (or via pytest)
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "host"))
import host_server  # noqa: E402

host_server._logf = open(os.devnull, "a")

SECRET = "s3cret-pw"
MOUNT = f"AFPMOUNT:*:ApfelNetz:AppleShare:pit:{SECRET}:2"


# --- redact_secrets: the fall-through logger's guard ------------------------
def test_afp_request_is_masked_from_the_user_field_on():
    assert host_server.redact_secrets(MOUNT) == "AFPMOUNT:*:ApfelNetz:AppleShare:***"


def test_masking_keys_off_the_afp_prefix_not_the_exact_verb():
    # A MISTYPED verb is exactly what reaches the verbatim logger, since the
    # routed spellings never get there.
    out = host_server.redact_secrets(f"AFPMNT:*:Srv:Vol:user:{SECRET}")
    assert SECRET not in out
    assert out.endswith(":***")


def test_short_afp_request_without_credentials_is_left_alone():
    assert host_server.redact_secrets("AFPMOUNT:*:Srv:Vol") == "AFPMOUNT:*:Srv:Vol"


def test_non_afp_commands_are_never_touched():
    for cmd in ["Volumes -l", "Echo a:b:c:d:e", "LISTDIR:MeinMac:AppleBridge:"]:
        assert host_server.redact_secrets(cmd) == cmd


def test_unmount_has_no_secret_to_hide():
    assert host_server.redact_secrets("AFPUNMOUNT:AppleShare") == "AFPUNMOUNT:AppleShare"


# --- afp_log_label: what actually reaches the log ---------------------------
def test_mount_label_names_where_not_who():
    label = host_server.afp_log_label(MOUNT)
    assert label == "AFPMOUNT ApfelNetz:AppleShare"
    assert SECRET not in label
    assert "pit" not in label          # the user name is not log material either


def test_mount_label_survives_a_truncated_request():
    assert host_server.afp_log_label("AFPMOUNT:*") == "AFPMOUNT ?"


def test_unmount_label_names_the_volume():
    assert host_server.afp_log_label("AFPUNMOUNT:AppleShare") == "AFPUNMOUNT AppleShare"


def test_label_never_returns_the_whole_request():
    for cmd in [MOUNT, "AFPMOUNT:*:S:V:u:p", "AFPMOUNT:*:S:V:u:p:1"]:
        assert SECRET not in host_server.afp_log_label(cmd)
        assert host_server.afp_log_label(cmd) != cmd


# --- routing: the bug that cost this session --------------------------------
def test_afp_verbs_are_routed_before_the_mpw_fall_through():
    """An unrouted AFP verb is silently executed as an MPW command.

    That is not a loud failure: ToolServer swallows the unknown command and
    answers STATUS:0 with empty output, so the caller sees "success" while
    nothing was mounted — and the request (password included) lands in the log
    verbatim. Pin that both verbs have their own branch, ahead of the generic
    one.
    """
    with open(host_server.__file__.replace(".pyc", ".py")) as fh:
        src = fh.read()
    mount_at = src.index('cmd.startswith("AFPMOUNT:")')
    unmount_at = src.index('cmd.startswith("AFPUNMOUNT:")')
    fallthrough_at = src.index('log(f"cmd: {redact_secrets(cmd)')
    assert mount_at < fallthrough_at
    assert unmount_at < fallthrough_at


def test_mount_gets_a_timeout_above_a_server_login():
    # Mounting does an AppleTalk lookup + login + volume open; the 15 s default
    # would report a timeout on a healthy but slow server.
    assert host_server.AFP_TIMEOUT >= 30.0


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

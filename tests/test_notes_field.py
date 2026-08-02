"""The optional NOTES field on the control-port reply.

The session channel used to be something a session had to go and look at. This
field makes it ride on traffic that was happening anyway: the host server
appends it to a normal reply, the client parks it beside the result, and every
tool carries it without any tool knowing about it.

A protocol gets one property that makes this safe, and it was verified before
the field was written rather than assumed: every reader in this tree seeks its
fields BY NAME. `mac_connection` walks lines and skips what it does not know,
`smoke_e2e` and `build.py` search for their tags, `send_command` does not parse
at all. A field nobody looks for is a field nobody trips over — so the tests
that matter most here are the ones proving the OLD readers are undisturbed.
"""
import datetime
import os
import sys
import unittest

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(_ROOT, "host"))
sys.path.insert(0, os.path.join(_ROOT, "host", "tools"))
sys.path.insert(0, os.path.join(_ROOT, "mcp"))
import notes  # noqa: E402
import host_server  # noqa: E402
import mac_connection  # noqa: E402

PLAIN = "STATUS:0\rSTDOUT:5\rhello\rSTDERR:0\r\r"


class TheField(unittest.TestCase):

    def setUp(self):
        import tempfile
        self.path = os.path.join(tempfile.mkdtemp(prefix="ab-field-"), "notes.log")
        self._old, notes.NOTES = notes.NOTES, self.path
        self.addCleanup(lambda: setattr(notes, "NOTES", self._old))

    def _deposit(self, text="Boot-INIT built"):
        notes.append(notes.format_note(
            datetime.datetime.now().isoformat(timespec="milliseconds"),
            "apfelpilot-live", "all", None, text), self.path)

    def test_a_quiet_channel_leaves_the_frame_byte_for_byte(self):
        """No note, no field: a reply must not grow a second subject just
        because the mechanism exists."""
        self.assertEqual(host_server.with_notes(PLAIN), PLAIN)

    def test_a_waiting_note_is_announced(self):
        self._deposit()
        out = host_server.with_notes(PLAIN)
        self.assertIn("NOTES:", out)
        self.assertTrue(out.endswith("\r\r"))

    def test_the_existing_fields_are_untouched(self):
        """The property the whole extension rests on."""
        self._deposit()
        conn = mac_connection.MacConnection()
        before = conn._parse_response(PLAIN.encode())
        after = conn._parse_response(host_server.with_notes(PLAIN).encode())
        self.assertEqual(before, after)
        self.assertEqual(after, (0, "hello", ""))

    def test_the_announcement_reaches_the_client(self):
        self._deposit()
        conn = mac_connection.MacConnection()
        conn._parse_response(host_server.with_notes(PLAIN).encode())
        self.assertIn("session channel", conn.last_notes)
        self.assertIn("notes.py list", conn.last_notes)

    def test_the_announcement_never_becomes_the_output(self):
        """It is not the command's result and must not be read as one."""
        self._deposit()
        conn = mac_connection.MacConnection()
        _status, stdout, stderr = conn._parse_response(
            host_server.with_notes(PLAIN).encode())
        self.assertNotIn("session channel", stdout)
        self.assertNotIn("session channel", stderr)

    def test_the_payload_is_ascii_so_characters_and_bytes_agree(self):
        """The declared length counts characters, as everywhere on this port. A
        non-ASCII body would desync any reader that counted bytes."""
        self._deposit()
        payload = host_server.notes_payload()
        self.assertEqual(len(payload), len(payload.encode("utf-8")))

    def test_a_malformed_frame_is_left_alone(self):
        self._deposit()
        for junk in ("", "No response", "ERROR: boom", "STATUS:0\rSTDOUT:0"):
            self.assertEqual(host_server.with_notes(junk), junk)

    def test_the_channel_being_unreadable_costs_the_field_not_the_reply(self):
        notes.NOTES = "/nonexistent-dir/notes.log"
        self.assertEqual(host_server.notes_payload(), "")
        self.assertEqual(host_server.with_notes(PLAIN), PLAIN)


if __name__ == "__main__":
    unittest.main()

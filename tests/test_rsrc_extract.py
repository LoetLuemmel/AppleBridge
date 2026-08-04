"""Reading a classic-Mac resource fork.

Every code resource this project embeds — `dlginit.a`'s DPAT literals,
`main.c`'s `kJGTemplate[]` and `kCPTemplate[]` — was transcribed by hand,
because `BuildDlgINIT.emu` describes the extraction in prose and no tool did it.
On 2026-08-04 that blocked the parallel session outright: it had to rebuild the
jGNE resource and the recipe lived only in an earlier session's memory.

The properties pinned here are the ones that make a resource parser wrong in a
way that still LOOKS right — a fork read at the wrong offsets yields plausible
bytes, and plausible bytes compiled into a trap patch are a crash in somebody
else's process.

Run: python3 tests/test_rsrc_extract.py   (or via pytest)
"""
import os
import struct
import sys
import unittest

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(_ROOT, "host", "tools"))
sys.path.insert(0, os.path.join(_ROOT, "host"))
import rsrc_extract  # noqa: E402


def build_fork(entries):
    """A minimal but REAL resource fork: [(type, id, name, data), ...].

    Written out rather than captured from a file so the test states the format
    it expects instead of trusting a blob nobody can read. If this builder and
    the parser ever disagree, one of them is wrong about Inside Macintosh — and
    that is the failure worth catching.
    """
    data_blob, offsets = b"", []
    for _, _, _, payload in entries:
        offsets.append(len(data_blob))
        data_blob += struct.pack(">I", len(payload)) + payload

    by_type = {}
    for i, (rtype, rid, name, _) in enumerate(entries):
        by_type.setdefault(rtype, []).append((rid, name, offsets[i]))

    names, name_off = b"", {}
    for rtype, items in by_type.items():
        for rid, name, _ in items:
            if name:
                name_off[(rtype, rid)] = len(names)
                names += bytes([len(name)]) + name.encode("mac_roman")

    n_types = len(by_type)
    type_list = struct.pack(">H", n_types - 1)
    ref_lists, ref_at = b"", 2 + n_types * 8
    for rtype, items in by_type.items():
        type_list += rtype.encode("mac_roman") + struct.pack(">HH",
                                                             len(items) - 1, ref_at)
        for rid, name, doff in items:
            ref_lists += struct.pack(">hH", rid, name_off.get((rtype, rid), 0xFFFF))
            ref_lists += bytes([0, (doff >> 16) & 0xFF, (doff >> 8) & 0xFF,
                                doff & 0xFF]) + b"\0\0\0\0"
        ref_at += len(items) * 12

    # The type list starts at map offset 28: the map's first 24 bytes are the
    # reserved header copy/handle/refnum/attrs, then the two offset WORDS at +24
    # and +26 — so the offsets they hold point PAST themselves, to 28. Storing
    # 24 here (the first version of this builder did) aims the parser at the
    # offset words and it reads them as a type count.
    map_body = type_list + ref_lists
    map_blob = (b"\0" * 24 + struct.pack(">HH", 28, 28 + len(map_body))
                + map_body + names)
    data_off = 256
    map_off = data_off + len(data_blob)
    header = struct.pack(">IIII", data_off, map_off, len(data_blob), len(map_blob))
    return header + b"\0" * (data_off - len(header)) + data_blob + map_blob


class ReadingAFork(unittest.TestCase):

    def test_a_single_resource_fork_is_not_read_as_empty(self):
        """The classic trap: type and reference counts are stored as count-1, so
        a fork holding ONE resource stores 0. Read as a plain count that is
        "nothing here" — and a single-resource fork is exactly what a code
        patch is."""
        fork = build_fork([("DPAT", 128, "", b"\xDE\xAD\xBE\xEF")])
        got = rsrc_extract.resources(fork)
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["type"], "DPAT")
        self.assertEqual(got[0]["id"], 128)
        self.assertEqual(got[0]["data"], b"\xDE\xAD\xBE\xEF")

    def test_the_bytes_come_back_exactly(self):
        """A patch is transcribed into a build. One byte wrong is a crash in a
        foreign process, and nothing upstream would notice."""
        payload = bytes(range(256)) * 3
        fork = build_fork([("JGNE", 128, "", payload)])
        self.assertEqual(rsrc_extract.find(rsrc_extract.resources(fork),
                                           "JGNE", 128)["data"], payload)

    def test_several_types_and_ids_are_all_found(self):
        fork = build_fork([("DPAT", 128, "patch", b"\1\2"),
                           ("DPAT", 129, "", b"\3\4\5"),
                           ("JGNE", 128, "", b"\6")])
        got = rsrc_extract.resources(fork)
        self.assertEqual({(r["type"], r["id"], r["size"]) for r in got},
                         {("DPAT", 128, 2), ("DPAT", 129, 3), ("JGNE", 128, 1)})
        self.assertEqual(rsrc_extract.find(got, "DPAT", 128)["name"], "patch")

    def test_a_negative_resource_id_survives(self):
        """System icons live at negative ids (the trash is -3993/-3984). Read as
        unsigned they come back as ~61543 and no lookup ever matches."""
        fork = build_fork([("ICN#", -3993, "", b"\0\1")])
        self.assertEqual(rsrc_extract.resources(fork)[0]["id"], -3993)

    def test_an_empty_fork_reads_as_empty_and_does_not_crash(self):
        """The real-world case the synthetic builder could not produce, and it
        found a bug: an EMPTY fork stores its type count-1 as 0xFFFF. Read as
        `+1` that is 65536 types, and the walk ran off the end with a struct
        error rather than an answer. Measured on a file MPW wrote
        (Desktop/Share/Claude2Assistant.r, 2026-08-04) — header dataLen=0,
        30-byte map, type count word 0xFFFF.

        Kept as a byte pattern rather than a path: the fixture must not depend
        on a file outside the repo, but the pattern is what the file held."""
        empty = (struct.pack(">IIII", 256, 256, 0, 30) + b"\0" * (256 - 16)
                 + b"\0" * 24 + struct.pack(">HH", 28, 30) + b"\xff\xff")
        self.assertEqual(rsrc_extract.resources(empty), [])

    def test_a_truncated_map_is_an_error_not_a_traceback(self):
        """A tool that crashes instead of answering sends its caller to read a
        stack trace for what is simply the wrong input file."""
        fork = build_fork([("DPAT", 128, "", b"\1\2\3\4")])
        with self.assertRaises(ValueError):
            rsrc_extract.resources(fork[:len(fork) - 8])

    def test_a_file_that_is_not_a_fork_says_so(self):
        """"Not a resource fork" and "no resources" are different answers, and
        only one of them means the caller passed the wrong file. A parser that
        returns [] for both sends them hunting in the wrong place."""
        with self.assertRaises(ValueError):
            rsrc_extract.resources(b"this is plainly not a resource fork" * 4)
        with self.assertRaises(ValueError):
            rsrc_extract.resources(b"\0\0\0\1")

    def test_a_miss_reports_what_the_fork_actually_holds(self):
        """A miss is usually a wrong type or id, not a missing resource — so the
        error carries the inventory the caller needs to fix the call."""
        fork = build_fork([("DPAT", 128, "", b"\1")])
        with self.assertRaises(ValueError) as ctx:
            rsrc_extract.find(rsrc_extract.resources(fork), "JGNE", 128)
        self.assertIn("DPAT 128", str(ctx.exception))


class EmittingSource(unittest.TestCase):

    def test_the_c_form_round_trips_through_a_parser(self):
        """The emitted text is pasted into main.c, so it must be re-readable as
        the same bytes — the check `test_counter_probe_contract` already does
        for the block it guards."""
        import re
        payload = bytes(range(64))
        text = rsrc_extract.as_c(payload, "kTest")
        back = bytes(int(x, 16) for x in re.findall(r"0x([0-9A-F]{2}),", text))
        self.assertEqual(back, payload)
        self.assertIn("#define kTest_Size 64", text)

    def test_the_asm_form_pads_to_a_word_and_says_so(self):
        """DC.W emits words. An odd byte count silently dropping or shifting the
        last byte is the kind of off-by-one that only shows up as a crash."""
        text = rsrc_extract.as_asm(b"\x01\x02\x03", "PatchData")
        self.assertIn("kPatchLen       EQU     3", text)   # the TRUE length
        self.assertIn("padded", text)
        self.assertIn("$0102", text)
        self.assertIn("$0300", text)

    def test_an_even_payload_is_not_marked_as_padded(self):
        text = rsrc_extract.as_asm(b"\x01\x02", "PatchData")
        self.assertNotIn("padded", text)


if __name__ == "__main__":
    import sys as _s
    tests = [v for k, v in sorted(globals().items())
             if k.startswith(("Reading", "Emitting"))]
    runner = unittest.TextTestRunner(verbosity=2)
    suite = unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromTestCase(t)
                               for t in tests)
    _s.exit(0 if runner.run(suite).wasSuccessful() else 1)

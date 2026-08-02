"""The dlgpatch shared-block record contract lives in three files at once:
dlgpatch.a declares the block (the asm field layout), dlgwalk.c writes it in the
foreground app, and main.c's DLGTREE reads it back. Nothing held the three in
agreement -- a one-word drift in any offset leaves every file compiling while
DLGTREE returns garbage, the silent-drift shape test_doc_claims already guards
for prose. This test is that hold for the block: it parses the field offsets,
RECSIZE and MAXITEMS out of all three and asserts a single contract.

The record-INTERNAL layout (index/type/rect/flags/text within one 48-byte
record) is written by dlgwalk.c and read by main.c as inline literals; that is
left for prose. This test covers the block-level offsets and sizes.

Run: python3 tests/test_dlgpatch_contract.py   (or via pytest)
"""

import os
import re

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DLGPATCH_A = os.path.join(_ROOT, "mac", "journal", "dlgpatch.a")
DLGWALK_C = os.path.join(_ROOT, "mac", "journal", "dlgwalk.c")
MAIN_C = os.path.join(_ROOT, "mac", "src", "main.c")


def _asm_layout(path):
    """Byte offsets of the labeled fields in dlgpatch.a's block (Entry..Records),
    plus the Records region's declared byte size. The block runs from the `Entry`
    label to the `Go` code label; each `<label> DC.W/DC.L/DCB.B ...` advances the
    offset by its size (BRA.W is 4 bytes)."""
    layout, recbytes, off, started = {}, None, 0, False
    for raw in open(path).read().splitlines():
        s = raw.strip()
        if not started:
            if s == "Entry":
                started, off = True, 0
            continue
        if s.startswith("Go"):          # code begins -> header done
            break
        m = re.match(r"(?:(\w+)\s+)?(BRA\.W|DC\.W|DC\.L|DC\.B|DCB\.B)\b\s*(.*)", s)
        if not m:
            continue
        label, mnem, operand = m.group(1), m.group(2), m.group(3).split(";")[0]
        if label:
            layout[label] = off
        if mnem == "BRA.W":
            size = 4
        elif mnem == "DC.W":
            size = 2 * (operand.count(",") + 1)
        elif mnem == "DC.L":
            size = 4 * (operand.count(",") + 1)
        elif mnem == "DC.B":
            size = 1 * (operand.count(",") + 1)
        else:                            # DCB.B <count>,<fill>
            size = int(operand.split(",")[0].strip())
            if label == "Records":
                recbytes = size
        off += size
    return layout, recbytes


def _defines(path, names):
    src = open(path).read()
    out = {}
    for n in names:
        m = re.search(r"#define\s+" + re.escape(n) + r"\s+(\d+)", src)
        assert m, os.path.basename(path) + ": #define " + n + " not found"
        out[n] = int(m.group(1))
    return out


# dlgpatch.a field -> (#define in dlgwalk.c or None, #define in main.c or None)
_CONTRACT = [
    ("Real", None, "oDP_Real"),
    ("Armed", None, "oDP_Armed"),
    ("OneShot", None, "oDP_OneShot"),
    ("DialogUp", "oDialogUp", "oDP_Up"),
    ("Generatn", None, "oDP_Gen"),
    ("ItemCnt", "oItemCount", "oDP_Cnt"),
    ("Truncatd", "oTruncated", "oDP_Trunc"),
    ("DlgRect", "oDlgRect", "oDP_Rect"),
    ("Records", "oRecords", "oDP_Recs"),
]


def test_block_offsets_agree():
    asm, _ = _asm_layout(DLGPATCH_A)
    walk = _defines(DLGWALK_C, ["oDialogUp", "oItemCount", "oTruncated",
                                "oDlgRect", "oRecords"])
    main = _defines(MAIN_C, ["oDP_Real", "oDP_Armed", "oDP_OneShot", "oDP_Up",
                             "oDP_Gen", "oDP_Cnt", "oDP_Trunc", "oDP_Rect",
                             "oDP_Recs"])
    for field, wkey, mkey in _CONTRACT:
        assert field in asm, "dlgpatch.a: field %r not found in block" % field
        a = asm[field]
        if mkey is not None:
            assert a == main[mkey], \
                "%s: dlgpatch.a @%d != main.c %s @%d" % (field, a, mkey, main[mkey])
        if wkey is not None:
            assert a == walk[wkey], \
                "%s: dlgpatch.a @%d != dlgwalk.c %s @%d" % (field, a, wkey, walk[wkey])


def test_magic_at_offset_4():
    asm, _ = _asm_layout(DLGPATCH_A)
    assert asm.get("Magic") == 4, \
        "dlgpatch.a: Magic @%r, must be +4 (FindDlgPatch scans word@+4)" % asm.get("Magic")


def test_recsize_and_records_region():
    walk = _defines(DLGWALK_C, ["RECSIZE", "MAXITEMS"])
    main = _defines(MAIN_C, ["DP_RECSIZE"])
    _, recbytes = _asm_layout(DLGPATCH_A)
    assert walk["RECSIZE"] == main["DP_RECSIZE"], \
        "RECSIZE %d != DP_RECSIZE %d" % (walk["RECSIZE"], main["DP_RECSIZE"])
    assert recbytes is not None, "dlgpatch.a: Records region not found"
    assert recbytes == walk["MAXITEMS"] * walk["RECSIZE"], \
        "dlgpatch.a Records = %d bytes, but MAXITEMS(%d) * RECSIZE(%d) = %d" % (
            recbytes, walk["MAXITEMS"], walk["RECSIZE"],
            walk["MAXITEMS"] * walk["RECSIZE"])


def test_generation_is_asm_only():
    """Finding (1): dlgwalk.c must not bump generation (the asm head does), so it
    no longer defines that offset. Guards the double-count fix from regressing."""
    src = open(DLGWALK_C).read()
    assert "oGeneration" not in src, \
        "dlgwalk.c references oGeneration again -> generation would be double-counted"


if __name__ == "__main__":
    for _name, _fn in sorted(globals().items()):
        if _name.startswith("test_") and callable(_fn):
            _fn()
            print("ok:", _name)
    print("all dlgpatch contract checks passed")

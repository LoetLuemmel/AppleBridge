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
JGNEPATCH_A = os.path.join(_ROOT, "mac", "journal", "jgnepatch.a")


def _asm_layout(path, start="Entry", end="Go"):
    """Byte offsets of the labeled fields in dlgpatch.a's block (Entry..Records),
    plus the Records region's declared byte size. The block runs from the `Entry`
    label to the `Go` code label; each `<label> DC.W/DC.L/DCB.B ...` advances the
    offset by its size (BRA.W is 4 bytes)."""
    layout, recbytes, off, started = {}, None, 0, False
    for raw in open(path).read().splitlines():
        s = raw.strip()
        if not started:
            if s == start:
                started, off = True, 0
            continue
        if s.startswith(end):           # code begins -> header done
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


def _asm_equ(path, names):
    src = open(path).read()
    out = {}
    for n in names:
        m = re.search(r"^\s*" + re.escape(n) + r"\s+EQU\s+(\d+)", src, re.M)
        assert m, os.path.basename(path) + ": EQU " + n + " not found"
        out[n] = int(m.group(1))
    return out


# jgnepatch.a block field -> main.c #define (the SECOND DlgWalk trigger; owner review
# 2026-08-03 accepted a duplicated read-path only with this static drift guard).
_JG_CONTRACT = [
    ("jMagic", "oJG_Magic"),
    ("jReal", "oJG_jReal"),
    ("jArmed", "oJG_jArmed"),
    ("jOneShot", "oJG_jOneShot"),
    ("jBusy", "oJG_jBusy"),
    ("jTries", "oJG_jTries"),
    ("jMaxTries", "oJG_jMaxTries"),
    ("jTargetA5", "oJG_jTargetA5"),
    ("jDPBlock", "oJG_jDPBlock"),
]


def test_jgnepatch_block_offsets_agree():
    jg, _ = _asm_layout(JGNEPATCH_A, start="jEntry", end="jGo")
    main = _defines(MAIN_C, [d for _, d in _JG_CONTRACT])
    for field, mkey in _JG_CONTRACT:
        assert field in jg, "jgnepatch.a: field %r not found" % field
        assert jg[field] == main[mkey], \
            "%s: jgnepatch.a @%d != main.c %s @%d" % (field, jg[field], mkey, main[mkey])


def test_jgnepatch_magic_at_4():
    jg, _ = _asm_layout(JGNEPATCH_A, start="jEntry", end="jGo")
    assert jg.get("jMagic") == 4, "jgnepatch.a: jMagic @%r, must be +4" % jg.get("jMagic")


def test_jgnepatch_dp_offsets_agree():
    """jgnepatch's copy writes the SAME DP block; its oDP_Up/oDP_Gen EQUs must match
    dlgpatch.a's DialogUp/Generatn and main.c's oDP_Up/oDP_Gen, or the jGNE walk sets
    the wrong words in a block the entry patch also writes."""
    equ = _asm_equ(JGNEPATCH_A, ["oDP_Up", "oDP_Gen"])
    asm, _ = _asm_layout(DLGPATCH_A)
    main = _defines(MAIN_C, ["oDP_Up", "oDP_Gen"])
    assert equ["oDP_Up"] == asm["DialogUp"] == main["oDP_Up"], \
        "oDP_Up drift: jgnepatch %d, dlgpatch %d, main %d" % (equ["oDP_Up"], asm["DialogUp"], main["oDP_Up"])
    assert equ["oDP_Gen"] == asm["Generatn"] == main["oDP_Gen"], \
        "oDP_Gen drift: jgnepatch %d, dlgpatch %d, main %d" % (equ["oDP_Gen"], asm["Generatn"], main["oDP_Gen"])


# ---- MENUWALK MB block: menuwalk.c (writer) <-> main.c MENUTREE (reader) -------
# The DP block has an asm writer (dlgpatch.a); the MB block does not — menuwalk.c
# (compiled into the jGNE resource) writes it and main.c's MENUTREE reads it, both
# in C. The scalar oMB_* offsets carry the SAME names in both files; the record
# sizes/caps carry different names (menuwalk.c bare, main.c kMB_-prefixed); and the
# flag bits are a THREE-way contract with docs/MENUWALK_DESIGN.md. A one-word drift
# in any of these compiles clean and makes MENUTREE emit garbage — the same silent
# shape the DP tests above guard, now for the menu walk.

MENUWALK_C = os.path.join(_ROOT, "mac", "journal", "menuwalk.c")
MENUWALK_DOC = os.path.join(_ROOT, "docs", "MENUWALK_DESIGN.md")

# scalar offsets defined identically (same name, same value) in both C files
_MB_OFFSETS = ["oMB_Magic", "oMB_Up", "oMB_Gen", "oMB_MenuCount", "oMB_Trunc",
               "oMB_ItemCount", "oMB_MBarH", "oMB_Menus"]


def test_mb_block_offsets_agree():
    """menuwalk.c writes the MB block, main.c MENUTREE reads it; every scalar oMB_*
    offset must match byte-for-byte or MENUTREE decodes the wrong words."""
    walk = _defines(MENUWALK_C, _MB_OFFSETS)
    main = _defines(MAIN_C, _MB_OFFSETS)
    for k in _MB_OFFSETS:
        assert walk[k] == main[k], \
            "%s: menuwalk.c @%d != main.c @%d" % (k, walk[k], main[k])


def test_mb_magic_at_offset_4():
    """The Walk dispatcher (in the jGNE stub) routes on the word at +4: 'MB' $4D42
    -> MenuWalk, 'DP' $4450 -> DlgWalk. oMB_Magic must be 4 in both C files."""
    walk = _defines(MENUWALK_C, ["oMB_Magic"])
    main = _defines(MAIN_C, ["oMB_Magic"])
    assert walk["oMB_Magic"] == 4, "menuwalk.c oMB_Magic @%d, must be +4" % walk["oMB_Magic"]
    assert main["oMB_Magic"] == 4, "main.c oMB_Magic @%d, must be +4" % main["oMB_Magic"]


def test_mb_record_sizes_and_caps_agree():
    """Record sizes and caps: menuwalk.c uses bare names, main.c the kMB_ prefix.
    The derived oMB_Items base (oMB_Menus + MAX_MENUS*MENU_REC) must land at 670 so
    the item region begins where MENUTREE reads it."""
    walk = _defines(MENUWALK_C, ["MENU_REC", "MAX_MENUS", "ITEM_REC", "MAX_ITEMS", "oMB_Menus"])
    main = _defines(MAIN_C, ["kMB_MENU_REC", "kMB_MAX_MENUS", "kMB_ITEM_REC",
                             "kMB_MAX_ITEMS"])
    assert walk["MENU_REC"] == main["kMB_MENU_REC"], \
        "MENU_REC %d != kMB_MENU_REC %d" % (walk["MENU_REC"], main["kMB_MENU_REC"])
    assert walk["ITEM_REC"] == main["kMB_ITEM_REC"], \
        "ITEM_REC %d != kMB_ITEM_REC %d" % (walk["ITEM_REC"], main["kMB_ITEM_REC"])
    assert walk["MAX_MENUS"] == main["kMB_MAX_MENUS"], \
        "MAX_MENUS %d != kMB_MAX_MENUS %d" % (walk["MAX_MENUS"], main["kMB_MAX_MENUS"])
    assert walk["MAX_ITEMS"] == main["kMB_MAX_ITEMS"], \
        "MAX_ITEMS %d != kMB_MAX_ITEMS %d" % (walk["MAX_ITEMS"], main["kMB_MAX_ITEMS"])
    items_base = walk["oMB_Menus"] + walk["MAX_MENUS"] * walk["MENU_REC"]
    assert items_base == 670, "oMB_Items base drifted from 670: %d" % items_base
    # kMB_Size is a derived expression in both files; verify the derivation lands at
    # the documented 4766 from the component defines this test already pins.
    total = items_base + walk["MAX_ITEMS"] * walk["ITEM_REC"]
    assert total == 4766, "MB block size drifted from 4766: %d" % total


def test_mb_item_flag_bits():
    """ITEM_REC.flags is a three-way contract (menuwalk.c writer / main.c reader /
    doc). The bits must be distinct, bit3 (value 8) RESERVED, and bit0 (enabled) and
    bit4 (enabled-UNKNOWN) a FORBIDDEN combination: menuwalk.c sets bit0 only inside
    the idx<=31 range and bit4 only in the else, so a bit4-unaware client that reads
    bit0==0 as 'disabled' can never see it set for an unknown item."""
    main = _defines(MAIN_C, ["kMBI_Enabled", "kMBI_Separator", "kMBI_TextTrunc",
                             "kMBI_EnUnknown"])
    assert main["kMBI_Enabled"] == 1, "kMBI_Enabled must be bit0 (1)"
    assert main["kMBI_Separator"] == 2, "kMBI_Separator must be bit1 (2)"
    assert main["kMBI_TextTrunc"] == 4, "kMBI_TextTrunc must be bit2 (4)"
    assert main["kMBI_EnUnknown"] == 16, "kMBI_EnUnknown must be bit4 (16)"
    vals = list(main.values())
    assert len(set(vals)) == len(vals), "kMBI_ flag bits overlap: %s" % vals
    assert 8 not in vals, "bit3 (8) is RESERVED but a kMBI_ define claims it"
    assert main["kMBI_Enabled"] & main["kMBI_EnUnknown"] == 0, \
        "bit0(enabled) and bit4(unknown) must be distinct bits"
    # menuwalk.c writer: bit0 set ONLY under `if (idx <= 31)`, bit4 ONLY in its else
    walk_src = open(MENUWALK_C).read()
    assert re.search(
        r"if \(idx <= 31\)\s*\{[^}]*flags2 \|= 1;[^}]*\}\s*else\s*\{[^}]*flags2 \|= 16;",
        walk_src, re.S), \
        "menuwalk.c: bit0(enabled)/bit4(unknown) not split across the idx<=31 if/else " \
        "-> the forbidden bit0&bit4 combination could be written"
    # main.c reader: an EnUnknown item emits enabled:null (never true/false)
    main_src = open(MAIN_C).read()
    assert re.search(r"kMBI_EnUnknown\)[^;]*enabled[^;]*null", main_src), \
        "main.c MENUTREE: an EnUnknown item must emit enabled:null"


def test_mb_menu_flag_bits():
    """MENU_REC.flags: bit0 menu-enabled, bit1 item-points-valid. Distinct bits."""
    main = _defines(MAIN_C, ["kMBM_Enabled", "kMBM_PtsValid"])
    assert main["kMBM_Enabled"] == 1, "kMBM_Enabled must be bit0 (1)"
    assert main["kMBM_PtsValid"] == 2, "kMBM_PtsValid must be bit1 (2)"
    assert main["kMBM_Enabled"] & main["kMBM_PtsValid"] == 0, \
        "menu-enabled and points-valid must be distinct bits"


def test_mb_item_flag_doc_in_sync():
    """docs/MENUWALK_DESIGN.md is the third leg of the ITEM_REC bit contract: it must
    name bit3 reserved and the bit4 -> enabled:null rule, so the prose cannot drift
    from the code the two tests above pin."""
    doc = open(MENUWALK_DOC).read()
    assert re.search(r"bit3\s+reserved", doc, re.I), \
        "MENUWALK_DESIGN.md must state bit3 reserved"
    assert re.search(r"bit4[^\n]*(enabled-UNKNOWN|null)", doc, re.I), \
        "MENUWALK_DESIGN.md must state bit4 = enabled-UNKNOWN -> null"



if __name__ == "__main__":
    for _name, _fn in sorted(globals().items()):
        if _name.startswith("test_") and callable(_fn):
            _fn()
            print("ok:", _name)
    print("all dlgpatch contract checks passed")

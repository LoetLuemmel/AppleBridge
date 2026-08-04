"""The counter-probe block: hand-assembled 68k that must agree with the C offsets.

main.c holds the CPR2 block twice over -- once as `#define oCP_*` field offsets
that the C code writes and reads, and once as a byte table of three PIC stubs
whose `d16(A0)` displacements address those SAME fields. Nothing held the two
together. A one-field edit leaves the file compiling and the stub writing into
the wrong long -- inside `_GetNextEvent`, the hottest trap in the system, in a
foreign process. The earlier spike crashed on exactly this class: a
hand-computed PC-relative displacement that was off.

So this test does NOT compare the table against a stored copy (that only pins
whatever is there). It DECODES the stubs and re-derives every invariant from the
declared offsets:

  - each stub's `LEA d16(PC),A0` must resolve to the block base (0)
  - every `d16(A0)` displacement must be a declared field offset, and the RIGHT
    one: the GNE stub bumps CntGNE and chains via RealGNE, WNE bumps CntWNE and
    chains RealWNE, the jGNE stub bumps jCnt and chains jReal
  - every BEQ.S must land inside its own stub, on an instruction boundary
  - the two counter stubs must touch nothing but A0/D0/CCR; the jGNE stub must
    save AND restore D1-D2 on every armed path (it runs inside the Event
    Manager, where A1 and D0 belong to the caller)
  - both recorded slots must actually be used, or the two-slot fix is cosmetic

The decoder understands exactly the instruction forms the table uses. An
unknown opcode is a FAILURE, not a skip: a new form in this table is precisely
the moment a human should look.

Run: python3 tests/test_counter_probe_contract.py   (or via pytest)
"""

import os
import re

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
MAIN_C = os.path.join(_ROOT, "mac", "src", "main.c")


def _source():
    with open(MAIN_C, encoding="utf-8", errors="replace") as handle:
        return handle.read()


def _offsets(src):
    """{'Armed': 4, ...} plus 'Size' from the #define block."""
    out = {m[0]: int(m[1]) for m in re.findall(r"#define\s+oCP_(\w+)\s+(\d+)", src)}
    out["Size"] = int(re.search(r"#define\s+kCP_Size\s+(\d+)", src).group(1))
    return out


def _table(src):
    """The kCPTemplate initialiser as bytes, comments stripped."""
    body = src.split("static const unsigned char kCPTemplate[kCP_Size] = {", 1)[1]
    body = body.split("};", 1)[0]
    body = re.sub(r"/\*.*?\*/", "", body, flags=re.S)
    out = bytearray()
    for tok in re.findall(r"0x[0-9A-Fa-f]{2}|'.'", body):
        out.append(ord(tok[1]) if tok.startswith("'") else int(tok, 16))
    return bytes(out)


def _w(blob, at):
    return (blob[at] << 8) | blob[at + 1]


def _sw(blob, at):
    v = _w(blob, at)
    return v - 0x10000 if v & 0x8000 else v


# Each entry: (mask, match, length, decoder) -- only the forms this table uses.
def _decode(blob, at):
    """-> (mnemonic, length, info dict). Raises on an unrecognised opcode."""
    op = _w(blob, at)
    if op == 0x41FA:
        return "lea_pc", 4, {"target": at + 2 + _sw(blob, at + 2)}
    if op == 0x4A68:
        return "tst_w", 4, {"field": _w(blob, at + 2)}
    if (op & 0xFF00) == 0x6700:
        disp = blob[at + 1]
        assert disp != 0x00 and disp != 0xFF, f"BEQ at +{at} is not the short form"
        return "beq_s", 2, {"target": at + 2 + disp}
    if op == 0x52A8:
        return "addq_l", 4, {"field": _w(blob, at + 2)}
    if op in (0x2038, 0x2238):
        return "move_lowmem", 4, {"addr": _w(blob, at + 2),
                                  "reg": 0 if op == 0x2038 else 1}
    if op in (0xB0A8, 0xB2A8):
        return "cmp_l", 4, {"field": _w(blob, at + 2),
                            "reg": 0 if op == 0xB0A8 else 1}
    if op in (0x2140, 0x2141):
        return "move_d_to", 4, {"field": _w(blob, at + 2),
                                "reg": 0 if op == 0x2140 else 1}
    if op == 0x2168:
        return "move_f_to_f", 6, {"src": _w(blob, at + 2), "dst": _w(blob, at + 4)}
    if op == 0x2068:
        return "move_f_to_a0", 4, {"field": _w(blob, at + 2)}
    if op == 0x4ED0:
        return "jmp_a0", 2, {}
    if op == 0x48E7:
        return "movem_save", 4, {"mask": _w(blob, at + 2)}
    if op == 0x4CDF:
        return "movem_restore", 4, {"mask": _w(blob, at + 2)}
    if op == 0x4E75:
        return "rts", 2, {}
    raise AssertionError(f"unknown opcode 0x{op:04X} at +{at} — a new instruction "
                         "form in this table is a reason to look, not to skip")


def _walk(blob, start, end):
    """Decode a stub -> list of (offset, mnemonic, info)."""
    out, at = [], start
    while at < end:
        mnem, size, info = _decode(blob, at)
        out.append((at, mnem, info))
        at += size
    assert at == end, f"stub from +{start} does not end on an instruction boundary"
    return out


def _stubs(off):
    """(name, start, end, counter_field, chain_field) for the three stubs."""
    return [
        ("GNEStub", off["GNEStub"], off["WNEStub"], off["CntGNE"], off["RealGNE"]),
        ("WNEStub", off["WNEStub"], off["jReal"],   off["CntWNE"], off["RealWNE"]),
        ("jStub",   off["jStub"],   off["jRTS"],    off["jCnt"],   off["jReal"]),
    ]


def test_the_table_is_exactly_the_declared_size():
    src = _source()
    assert len(_table(src)) == _offsets(src)["Size"]


def test_every_stub_addresses_the_block_base():
    """`LEA d16(PC),A0` is how each stub finds its own data. Off by any amount and
    every field access lands somewhere else in the system heap."""
    src = _source()
    blob, off = _table(src), _offsets(src)
    for name, start, end, _, _ in _stubs(off):
        first = _walk(blob, start, end)[0]
        assert first[1] == "lea_pc", f"{name} does not start with LEA d16(PC),A0"
        assert first[2]["target"] == 0, \
            f"{name}: LEA resolves to +{first[2]['target']}, not the block base"


def test_every_field_reference_is_a_declared_field():
    src = _source()
    blob, off = _table(src), _offsets(src)
    known = {v for k, v in off.items() if k != "Size"}
    for name, start, end, _, _ in _stubs(off):
        for at, mnem, info in _walk(blob, start, end):
            for key in ("field", "src", "dst"):
                if key in info:
                    assert info[key] in known, \
                        f"{name} +{at} {mnem} touches +{info[key]}, which is not a " \
                        f"declared oCP_ field"


def test_each_stub_counts_and_chains_through_its_own_pair():
    """The copy-paste failure this guards: the WNE stub bumping CntGNE, or
    chaining through RealGNE — both leave everything compiling and the
    measurement silently wrong."""
    src = _source()
    blob, off = _table(src), _offsets(src)
    for name, start, end, counter, chain in _stubs(off):
        code = _walk(blob, start, end)
        bumped = [i["field"] for _, m, i in code if m == "addq_l"]
        assert counter in bumped, f"{name} never bumps its own counter (+{counter})"
        assert off["OtherCnt"] in bumped, f"{name} never bumps OtherCnt"
        last_load = [i["field"] for _, m, i in code if m == "move_f_to_a0"]
        assert last_load == [chain], \
            f"{name} chains through {last_load}, expected [+{chain}]"
        assert code[-1][1] == "jmp_a0", f"{name} does not end in JMP (A0)"


def test_both_recorded_slots_are_actually_used():
    """The two-slot fix, pinned. A single LastA5 was overwritten ~59x/second by a
    foreign fast poller, so the foreground's ~2/s call was never seen. Each stub
    must compare against LastA5 (the distinctness gate — this is what stops a
    flood from occupying both slots) and shift LastA5 into PrevA5."""
    src = _source()
    blob, off = _table(src), _offsets(src)
    for name, start, end, _, _ in _stubs(off):
        code = _walk(blob, start, end)
        compared = [i["field"] for _, m, i in code if m == "cmp_l"]
        assert off["SelfA5"] in compared, f"{name} lost the daemon self-filter"
        assert off["LastA5"] in compared, \
            f"{name} has no distinctness gate — a fast poller will fill both slots"
        shifts = [(i["src"], i["dst"]) for _, m, i in code if m == "move_f_to_f"]
        assert shifts == [(off["LastA5"], off["PrevA5"])], \
            f"{name} does not shift LastA5 into PrevA5 (got {shifts})"
        stored = [i["field"] for _, m, i in code if m == "move_d_to"]
        assert stored == [off["LastA5"]], f"{name} stores the A5 into {stored}"


def test_every_branch_lands_on_an_instruction_in_its_own_stub():
    """A BEQ into the middle of an instruction executes garbage. Inside
    `_GetNextEvent` that is a reboot at best."""
    src = _source()
    blob, off = _table(src), _offsets(src)
    for name, start, end, _, _ in _stubs(off):
        code = _walk(blob, start, end)
        boundaries = {at for at, _, _ in code}
        for at, mnem, info in code:
            if mnem == "beq_s":
                target = info["target"]
                assert start < target < end, \
                    f"{name} +{at}: BEQ.S leaves the stub (-> +{target})"
                assert target in boundaries, \
                    f"{name} +{at}: BEQ.S lands mid-instruction (-> +{target})"


def test_the_counter_stubs_touch_only_scratch_registers():
    """A0 and D0 are scratch across a trap; anything else is the caller's."""
    src = _source()
    blob, off = _table(src), _offsets(src)
    for name, start, end, _, _ in _stubs(off)[:2]:      # GNE + WNE only
        for at, mnem, info in _walk(blob, start, end):
            assert mnem not in ("movem_save", "movem_restore"), \
                f"{name} saves registers — it should not need to"
            assert info.get("reg", 0) == 0, \
                f"{name} +{at} uses D1; the counter stubs are D0-only"


def test_the_jgne_stub_restores_what_it_saved_on_every_armed_path():
    """It runs inside the Event Manager, where A1 is the EventRecord* and D0 the
    result. It works in D1 and must give D1-D2 back — the earlier spike crashed
    because it treated the jGNE caller's scratch as a guarantee."""
    src = _source()
    blob, off = _table(src), _offsets(src)
    name, start, end, _, _ = _stubs(off)[2]
    code = _walk(blob, start, end)
    saves = [(at, i["mask"]) for at, m, i in code if m == "movem_save"]
    restores = [(at, i["mask"]) for at, m, i in code if m == "movem_restore"]
    assert len(saves) == 1 and len(restores) == 1, \
        f"{name}: expected exactly one save/restore pair, got {saves} / {restores}"
    assert saves[0][1] == 0x6000, f"{name}: predecrement save mask is not D1-D2"
    assert restores[0][1] == 0x0006, f"{name}: postincrement mask is not D1-D2"
    # Every BEQ taken after the save must still reach the restore, never the chain.
    restore_at = restores[0][0]
    for at, mnem, info in code:
        if mnem == "beq_s" and at > saves[0][0]:
            assert info["target"] == restore_at, \
                f"{name} +{at}: an armed-path branch skips the MOVEM restore"


def test_the_cpread_buffer_holds_the_worst_case_reply():
    """Counted, not assumed. Adding "prev" pushed the worst case to 164 chars
    against a 160-byte stack buffer — the field was added and the buffer was
    not. Every long is printed signed, so 11 characters each."""
    src = _source()
    block = src.split('if (strncmp(request, "CPREAD"', 1)[1].split("return true;", 1)[0]
    size = int(re.search(r"char body\[(\d+)\]", block).group(1))

    # The two branches (no block installed / installed) are MUTUALLY EXCLUSIVE, so
    # the worst case is the larger of them, not their sum. Counting both was this
    # test's own first version: it reported the right number for the wrong reason,
    # and would have raised a false alarm on any code where the short branch grew.
    def literals(text):
        return sum(len(s.encode().decode("unicode_escape"))
                   for s in re.findall(r'StatStr\(b,\s*"((?:[^"\\]|\\.)*)"\)', text))

    head, _, tail = block.partition("} else {")
    numbers = len(re.findall(r"StatDec\(b,", tail))
    worst = max(literals(head),
                literals(tail) + 11 * numbers + len("false"))   # widest bool word
    assert worst < size, (f"CPREAD can emit {worst} chars into char body[{size}] — "
                          "grow the buffer or drop a field")


if __name__ == "__main__":
    import sys
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    bad = 0
    for fn in tests:
        try:
            fn()
            print(f"ok: {fn.__name__}")
        except AssertionError as exc:
            bad += 1
            print(f"FAIL: {fn.__name__}\n  {exc}")
    print("all counter-probe checks passed" if not bad else f"{bad} failed")
    sys.exit(1 if bad else 0)

#!/usr/bin/env python3
"""
Generate the AppleBridge Installer's Finder icon as Rez `data` statements.

Same approach as mac/controlpanel/gen_icon.py, and for the same reason: a code
generator so the icon is reproducible and reviewable rather than hand-packed
hex, and so a change is a diff instead of an opaque blob.

WHY the installer needs one at all: the kit volume shows four applications with
identical generic icons, and a newcomer has no way to tell which one to run.
That is the same discoverability problem as the silent helper picker — the
program is correct and the person in front of it is stuck. A distinct icon is
the cheapest possible fix.

Motif: a package with an arrow coming down into it — the one drawing everybody
reads as "install" — over the blue AppleBridge arch so it still belongs to the
suite.

Emits ICN# (32x32 1-bit + mask), icl4 (32x32 4-bit), ics#/ics4 (16x16), plus
FREF/BNDL and the 'ABis' signature resource, all at id 128 (the application
convention, where the control panel uses its cdev range).

Run on the host:  python3 gen_installer_icon.py  ->  installer_icon.r
"""

PAL = {'.': 0, 'W': 0, 'B': 6, 'R': 3, 'K': 15, 'L': 12, 'M': 13, 'D': 14, 'T': 0}
INK = set('KBMDR')       # black/blue/red/greys read as "ink" in the 1-bit icon
OPAQUE = set('WBKLMDR')  # everything but 'T' is part of the shape

RES_ID = 128             # applications conventionally use 128


def blank(n):
    return [['T'] * n for _ in range(n)]


def draw16():
    """The small icon, drawn at 16x16 rather than scaled down from 32.

    The Human Interface Guidelines say to redraw at each size, and the reason
    is visible the moment you don't: halving the 32x32 closes the wrench's
    fork, fills the screen bezel and turns the arch into a blob. Everything
    here is thinned to the smallest stroke that still reads -- one-pixel
    prongs, a four-pixel arch, a two-pixel arrow shaft.
    """
    g = blank(16)

    def px(x, y, c):
        if 0 <= x < 16 and 0 <= y < 16:
            g[y][x] = c

    def box(x0, y0, x1, y1, edge, fill=None):
        for y in range(y0, y1 + 1):
            for x in range(x0, x1 + 1):
                if x in (x0, x1) or y in (y0, y1):
                    px(x, y, edge)
                elif fill:
                    px(x, y, fill)

    box(3, 8, 14, 15, 'K', 'L')           # the Mac
    box(5, 9, 12, 12, 'D', 'K')           # screen, with case visible either side
    for x in range(7, 11):                # arch, four pixels wide
        px(x, 10, 'B')
    px(6, 11, 'B'); px(11, 11, 'B')       # footings
    for x in range(6, 12):                # disk slot
        px(x, 14, 'D')

    for y in range(1, 5):                 # arrow shaft
        px(7, y, 'R'); px(8, y, 'R')
    for i, (a, b) in enumerate([(5, 10), (6, 9), (7, 8)]):
        for x in range(a, b + 1):         # arrow head
            px(x, 5 + i, 'R')

    px(1, 3, 'D'); px(3, 3, 'D')          # wrench: one-pixel prongs
    px(1, 4, 'D'); px(3, 4, 'D')
    for x in range(1, 4):
        px(x, 5, 'D')                     # back of the jaw
    for y in range(6, 13):
        px(2, y, 'D')                     # handle

    return g


def draw(n):
    """n x n: a wrench and a red arrow descending into a compact Macintosh.

    The grammar is the era's, not an invention — see the note at the top of
    this file: source at the top, arrow down, DESTINATION at the bottom. The
    destination is a compact Mac wearing the AppleBridge arch on its screen,
    which carries the brand without needing a fourth object.
    """
    g = blank(n)
    s = n / 32.0

    def px(x, y, c):
        xi, yi = int(round(x * s)), int(round(y * s))
        if 0 <= xi < n and 0 <= yi < n:
            g[yi][xi] = c

    def box(x0, y0, x1, y1, edge, fill=None):
        for y in range(y0, y1 + 1):
            for x in range(x0, x1 + 1):
                on_edge = x in (x0, x1) or y in (y0, y1)
                if on_edge:
                    px(x, y, edge)
                elif fill:
                    px(x, y, fill)

    # --- the destination: a compact Macintosh ----------------------------
    box(9, 15, 23, 29, 'K', 'L')          # case
    box(11, 17, 21, 24, 'D', 'K')         # screen bezel, dark screen
    for x in range(12, 21):               # the AppleBridge arch, lit
        dy = ((x - 16.0) / 4.5) ** 2
        px(x, int(23 - 3.5 * (1 - dy)), 'B')
    px(12, 23, 'B'); px(20, 23, 'B')      # arch footings
    for x in range(13, 20):               # disk slot
        px(x, 26, 'D')
    for x in range(10, 23):               # base shadow
        px(x, 28, 'M')

    # --- the red arrow, descending into it -------------------------------
    for y in range(2, 9):                 # shaft
        px(15, y, 'R'); px(16, y, 'R')
    for i, (a, b) in enumerate([(11, 20), (12, 19), (13, 18), (14, 17), (15, 16)]):
        for x in range(a, b + 1):         # head: wide at the top, apex below
            px(x, 9 + i, 'R')

    # --- the wrench, standing to the left --------------------------------
    # Upright rather than the diagonal a wrench usually gets: at 32x32 a
    # rotated fork loses its opening to the grid and reads as a stick.
    for y in range(4, 8):                 # the two jaw prongs, gap between
        px(3, y, 'D'); px(4, y, 'M')
        px(6, y, 'M'); px(7, y, 'D')
    for x in range(3, 8):                 # the back of the jaw
        px(x, 8, 'D')
    for y in range(9, 23):                # handle, run down beside the Mac
        px(4, y, 'D'); px(5, y, 'M'); px(6, y, 'D')
    px(4, 23, 'D'); px(5, 23, 'D'); px(6, 23, 'D')   # rounded end

    return g


def bits1(g, pred):
    out = bytearray()
    n = len(g)
    for row in g:
        acc, bit = 0, 0
        for x in range(n):
            acc = (acc << 1) | (1 if pred(row[x]) else 0)
            bit += 1
            if bit == 8:
                out.append(acc); acc, bit = 0, 0
        if bit:
            out.append(acc << (8 - bit))
    return bytes(out)


def nibbles4(g):
    out = bytearray()
    for row in g:
        for i in range(0, len(row), 2):
            out.append((PAL[row[i]] << 4) | PAL[row[i + 1]])
    return bytes(out)


def hexblock(b, per=16):
    lines = []
    for i in range(0, len(b), per):
        chunk = b[i:i + per]
        pairs = ' '.join(chunk.hex()[j:j + 4] for j in range(0, len(chunk.hex()), 4))
        lines.append('\t$"%s"' % pairs)
    return '\n'.join(lines)


def data_res(typ, rid, b):
    return "data '%s' (%d, purgeable) {\n%s\n};\n" % (typ, rid, hexblock(b))


if __name__ == "__main__":
    g32, g16 = draw(32), draw16()
    icn = bits1(g32, lambda c: c in INK) + bits1(g32, lambda c: c in OPAQUE)
    ics = bits1(g16, lambda c: c in INK) + bits1(g16, lambda c: c in OPAQUE)

    # FREF: this signature's documents/app -> 'APPL', local id 0
    fref = bytes.fromhex('4150504c' + '0000' + '00')
    # BNDL: creator 'ABis', 2 arrays (FREF, ICN#), each mapping local 0 -> 128
    bndl = bytes.fromhex(
        '41426973' + '0000' + '0001' +
        '46524546' + '0000' + '0000' + '%04x' % RES_ID +
        '49434e23' + '0000' + '0000' + '%04x' % RES_ID)
    sig = bytes([len('AppleBridge Installer')]) + b'AppleBridge Installer'

    parts = [
        "/* AppleBridge Installer icon - GENERATED by gen_installer_icon.py;",
        "   do not hand-edit. Rez this ONTO the built app, and give the file the",
        "   bundle bit (SetFile -a B) or the Finder will not look for it. */", "",
        data_res('ICN#', RES_ID, icn), data_res('icl4', RES_ID, nibbles4(g32)),
        data_res('ics#', RES_ID, ics), data_res('ics4', RES_ID, nibbles4(g16)),
        data_res('FREF', RES_ID, fref), data_res('BNDL', RES_ID, bndl),
        data_res('ABis', 0, sig),
    ]
    open('installer_icon.r', 'w').write('\n'.join(parts))
    print("wrote installer_icon.r")

    legend = {'T': ' ', 'W': '.', 'L': ':', 'B': '#', 'K': '@', 'M': 'o',
              'D': '-', 'R': '*'}
    print('\n'.join(''.join(legend[c] for c in row) for row in g32))

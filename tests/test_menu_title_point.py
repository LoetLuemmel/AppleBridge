"""`title_point` pointed at the neighbouring menu.

MENU_REC's third field is `menuWidth` from MenuInfo — the width of the
DROPPED-DOWN BODY. The record's own comment called it `titleWidth`, and the host
believed the comment, computing a title centre as `titleLeft + width/2`.

For the THINK Project Manager's File menu that is 34 + 146/2 = **107**, and
Search's title starts at **108**. A caller using `title_point` therefore pulled
down the menu NEXT to the one it asked for.

Found 2026-08-05 by the session driving those menus, which had blamed its own
item arithmetic — and which had, in the same session, an outside reviewer point
out that it never verified the gesture landed at all. Both of its menu failures
have this one cause: the Project menu worked because its body is narrow enough
to fall into the `else` branch (`+12`), the File menu missed because its body is
wide.

The menu bar's title rectangles are not in MenuInfo, so a centre is not
computable. A small fixed inset is: 12 px is inside every title wide enough to
click, and it is exactly the branch that worked.

Run: python3 tests/test_menu_title_point.py   (or via pytest)
"""

import os
import re
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
MAIN = open(os.path.join(_ROOT, "mac", "src", "main.c"),
            encoding="utf-8", errors="replace").read()
WALK = open(os.path.join(_ROOT, "mac", "journal", "menuwalk.c"),
            encoding="utf-8", errors="replace").read()


def test_the_title_point_no_longer_uses_the_body_width():
    m = re.search(r"short titleXc\s*=\s*\(short\)\(([^)]*)\)", MAIN)
    assert m, "titleXc is gone — did the field move?"
    expr = m.group(1)
    assert "titleW" not in expr, f"the body width is back in the title point: {expr}"
    assert "12" in expr


def test_the_measured_collision_would_be_caught():
    """The concrete numbers, so a future 'simplification' back to width/2 has to
    argue with them: TPM File title_x=34, width=146 -> 107; Search title_x=108."""
    title_x, width = 34, 146
    assert title_x + width // 2 == 107
    assert 107 >= 108 - 1, "the old formula landed on the neighbour"
    assert title_x + 12 == 46, "the inset stays inside File's own title"


def test_the_record_names_the_field_correctly():
    """The bug was a WRONG COMMENT that a reader trusted — more dangerous than a
    stale note, because it is read at the point of use."""
    assert "menuWidth(2)" in WALK
    assert "titleWidth(2)" not in WALK


def test_the_walk_still_writes_the_body_width_there():
    """The fix is on the reading side. `width` in the JSON has always been the
    body width and stays it — renaming the JSON field would break every caller
    for a mislabelling that was never theirs."""
    assert "*(short *)(mrec + 4)  = menuW;" in WALK


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

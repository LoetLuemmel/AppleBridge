"""Tests for key-modifier parsing and the mac_key / mac_menu wire verbs.

Covers the host half of the input-injection completeness work: turning modifier
names into the Event Manager mask, building the extended `KEY:<cc>:<kc>:<mods>`
verb, and the mac_menu Command-key convention. The daemon half (evtQModifiers +
KeyMap poke) is verified on-device; this pins the verb the daemon receives.

Run: python3 tests/test_input_modifiers.py   (or via pytest)
"""

import os
import sys

# Import tools.py directly, stubbing its relative import of mac_connection, so we
# avoid the name clash between this repo's ./mcp package and the installed `mcp`
# SDK (same approach as test_parse_response.py).
_MCP = os.path.join(os.path.dirname(__file__), "..", "mcp")
sys.path.insert(0, _MCP)
import importlib.util  # noqa: E402


def _load_tools():
    import types
    # Provide a stand-in `mac_connection` so `from .mac_connection import ...`
    # and the top-level import both resolve without a live socket layer.
    stub = types.ModuleType("mac_connection")
    stub.get_connection = lambda: None  # never called in these tests
    stub.MacConnection = object
    sys.modules.setdefault("mac_connection", stub)

    # Load tools.py as a top-level module named `abtools`, rewriting its single
    # relative import to the stub above.
    path = os.path.join(_MCP, "tools.py")
    src = open(path).read().replace("from .mac_connection import get_connection",
                                    "from mac_connection import get_connection")
    mod = types.ModuleType("abtools")
    mod.__file__ = os.path.abspath(path)  # tools.py reads __file__ at import time
    exec(compile(src, path, "exec"), mod.__dict__)
    return mod


tools = _load_tools()


# --- _modifiers_mask ------------------------------------------------------

def test_mask_none_is_zero():
    assert tools._modifiers_mask(None) == 0


def test_mask_empty_list_is_zero():
    assert tools._modifiers_mask([]) == 0


def test_mask_command_aliases():
    for name in ("cmd", "command", "Command", "APPLE", "meta"):
        assert tools._modifiers_mask([name]) == 256, name


def test_mask_each_bit():
    assert tools._modifiers_mask(["shift"]) == 512
    assert tools._modifiers_mask(["option"]) == 2048
    assert tools._modifiers_mask(["alt"]) == 2048
    assert tools._modifiers_mask(["control"]) == 4096
    assert tools._modifiers_mask(["ctrl"]) == 4096
    assert tools._modifiers_mask(["caps"]) == 1024


def test_mask_combined_is_ored():
    assert tools._modifiers_mask(["command", "shift"]) == 256 + 512
    assert tools._modifiers_mask(["cmd", "option", "shift"]) == 256 + 2048 + 512


def test_mask_accepts_raw_int_passthrough():
    assert tools._modifiers_mask(768) == 768


def test_mask_normalises_whitespace_and_case():
    assert tools._modifiers_mask([" Shift "]) == 512
    assert tools._modifiers_mask(["OPTION"]) == 2048
    assert tools._modifiers_mask(["Caps Lock"]) == 1024  # spaces stripped


def test_mask_unknown_modifier_raises():
    raised = False
    try:
        tools._modifiers_mask(["hyper"])
    except ValueError:
        raised = True
    assert raised, "unknown modifier must raise ValueError"


# --- mac_key / mac_menu verb construction ---------------------------------
# Capture the verb string without a live daemon by stubbing _inject.

def _capture(fn, *a, **kw):
    seen = {}
    orig = tools._inject
    tools._inject = lambda verb, label: seen.setdefault("verb", verb) or {"success": True}
    try:
        fn(*a, **kw)
    finally:
        tools._inject = orig
    return seen["verb"]


def test_mac_key_plain_is_backward_compatible_shape():
    # No modifiers -> mask 0, still the 3-field form the new daemon parses.
    # 'a' happens to BE key code 0, so this line is unchanged by the keycode fix.
    assert _capture(tools.mac_key, 97) == "KEY:97:0:0"


def test_mac_key_with_command():
    # 'n' sits at key code 45; sending 0 here made the app see the A key.
    assert _capture(tools.mac_key, 110, modifiers=["command"]) == "KEY:110:45:256"


def test_mac_key_with_keycode_and_mods():
    # An explicitly passed key_code still wins over the derived one.
    assert _capture(tools.mac_key, 97, 0, ["command", "shift"]) == "KEY:97:0:768"
    assert _capture(tools.mac_key, 111, 3, ["command"]) == "KEY:111:3:256"


def test_mac_key_derives_keycode_from_named_char():
    # key="o" -> char 111 at physical key 31 (the O key).
    assert _capture(tools.mac_key, key="o", modifiers=["command"]) == "KEY:111:31:256"


def test_mac_key_named_special_keys_keep_their_codes():
    assert _capture(tools.mac_key, key="return") == "KEY:13:36:0"
    assert _capture(tools.mac_key, key="escape") == "KEY:27:53:0"


def test_mac_key_unmapped_char_falls_back_to_zero():
    # A MacRoman char with no US key position: charCode still carries it.
    assert _capture(tools.mac_key, 246, modifiers=["command"]) == "KEY:246:0:256"


def test_keycode_table_matches_inside_macintosh():
    for ch, code in (("a", 0), ("q", 12), ("o", 31), ("s", 1), ("n", 45),
                     ("w", 13), ("z", 6), ("y", 16), (".", 47), ("1", 18)):
        assert tools._keycode_for_char(ord(ch)) == code, ch


def test_mac_menu_defaults_to_command():
    # Cmd-Q: 'q' is char 113 at key code 12, cmdKey 256.
    assert _capture(tools.mac_menu, "Q") == "KEY:113:12:256"


def test_mac_menu_lowercases_the_key():
    assert _capture(tools.mac_menu, "N") == "KEY:110:45:256"


def test_mac_menu_adds_extra_modifiers_but_always_command():
    # Cmd-Shift-S: 's' 115 at key code 1, cmd 256 + shift 512 = 768.
    assert _capture(tools.mac_menu, "S", modifiers=["shift"]) == "KEY:115:1:768"


def test_mac_menu_rejects_multichar_key():
    r = tools.mac_menu("Quit")
    assert r["success"] is False


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
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"ERR  {t.__name__}: {e!r}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)

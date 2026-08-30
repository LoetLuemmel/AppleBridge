"""Tests for host/tool_schema.py — one source of tool definitions, two wrappers.

The MCP surface and a local model's `tools` block describe the SAME 31 tools.
Writing them twice is the obvious shortcut: the second copy drifts the moment
either side changes, and the drift surfaces as a model calling a parameter that
no longer exists — an error the bridge reports plainly and that reads like the
model's fault.

The load-bearing test here is `test_every_exported_name_can_be_dispatched`, and
it runs against the REAL tables rather than a fixture. A fixture would only ever
prove that the converter copies; the question worth asking is whether `TOOLS`
and `TOOL_HANDLERS` still agree — nothing else in the repo enforces that, and a
check written the obvious way (`getattr(tools, name)`) would have been wrong:
`bridge_doctor` resolves to the imported MODULE, not the handler.

Run: python3 tests/test_tool_schema.py   (or via pytest)
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "host"))
import tool_schema as ts  # noqa: E402

FAKE = [
    {"name": "a_tool",
     "description": "Does the thing.\n\nAnd here is a page of hard-won lore\n"
                    "about when it lies to you.",
     "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}},
                     "required": ["path"]}},
    {"name": "no_args", "description": "Takes nothing."},
]


# --- the shape a local model needs ------------------------------------------
def test_the_entry_is_ollamas_function_shape():
    e = ts.to_ollama(FAKE)[0]
    assert e["type"] == "function"
    assert set(e["function"]) == {"name", "description", "parameters"}


def test_the_schema_passes_through_untouched():
    """Converting must not INVENT. A parameter this module made up would be one
    the bridge rejects, and the model would have no way to know why."""
    assert ts.to_ollama(FAKE)[0]["function"]["parameters"] == FAKE[0]["inputSchema"]


def test_a_tool_without_a_schema_still_gets_an_object():
    """Never `null`: Ollama wants the key, and a model handed null parameters
    has been observed to invent them."""
    p = ts.to_ollama(FAKE)[1]["function"]["parameters"]
    assert p == {"type": "object", "properties": {}}


# --- the drift guard, against the real tables -------------------------------
def test_every_exported_name_can_be_dispatched():
    """A name in TOOLS but not in TOOL_HANDLERS is a tool the model can call and
    the bridge cannot run."""
    tools, handlers = ts.load_tools()
    assert ts.undispatchable(tools, handlers) == []


def test_the_guard_would_catch_a_name_with_no_handler():
    """The test above passes today; this one proves it CAN fail. A guard only
    ever checked in its passing state is a guard nobody has tested."""
    assert ts.undispatchable(FAKE, {"a_tool"}) == ["no_args"]


def test_every_profile_names_only_tools_that_exist():
    """A profile rots silently: someone renames a tool, the profile keeps the old
    name, and the model is handed a smaller toolbox with no complaint."""
    tools, _ = ts.load_tools()
    known = {d["name"] for d in tools}
    for name, wanted in ts.PROFILES.items():
        assert not [w for w in wanted if w not in known], name


# --- selection --------------------------------------------------------------
def test_an_unknown_name_is_reported_not_dropped():
    chosen, unknown = ts.select(FAKE, only=["a_tool", "ghost"])
    assert [d["name"] for d in chosen] == ["a_tool"]
    assert unknown == ["ghost"]


def test_no_selection_means_everything():
    chosen, unknown = ts.select(FAKE)
    assert len(chosen) == len(FAKE) and unknown == []


def test_the_cli_refuses_an_unknown_name():
    """Refusing beats exporting a short list: the caller asked for something and
    must not get silence plus fewer tools."""
    assert ts.main(["--only", "mac_compile,ghost", "--quiet"]) == 2


# --- --brief, which exists because context is the binding constraint --------
def test_brief_keeps_the_first_paragraph_and_drops_the_lore():
    d = ts.to_ollama(FAKE, brief=True)[0]["function"]["description"]
    assert d == "Does the thing."


def test_brief_splits_at_the_authors_blank_line_not_a_character_count():
    """A length cap would cut mid-sentence and hand the model half a warning,
    which is worse than none."""
    long_first = {"name": "x", "description": "A. B. C. D. E. F.\n\nlore"}
    assert ts.to_ollama([long_first], brief=True)[0]["function"]["description"] \
        == "A. B. C. D. E. F."


def test_brief_actually_shrinks_the_real_descriptions():
    """The claim in the module docstring, measured rather than asserted: the full
    text is ~19.6 kB, and the node that would hold the model has ~2 GB free."""
    tools, _ = ts.load_tools()
    full = ts.size_report(ts.to_ollama(tools))["description_chars"]
    brief = ts.size_report(ts.to_ollama(tools, brief=True))["description_chars"]
    assert brief * 4 < full, (brief, full)


def test_the_build_profile_stays_small_enough_to_be_worth_having():
    """A profile that ends up the same size as everything is not a profile."""
    tools, _ = ts.load_tools()
    chosen, _ = ts.select(tools, profile="build")
    r = ts.size_report(ts.to_ollama(chosen, brief=True))
    assert r["tools"] <= 8 and r["description_chars"] < 1500, r


def test_the_control_arm_is_not_advertised_to_the_measured_model():
    """`mac_compile`'s `lint` selects the control arm of a with/without
    measurement. Exported, it would tell the model in the WITHOUT arm that a C89
    lint exists — and a control arm that knows what it controls for is not one.
    Both export modes, because --brief keeps a different slice of the text."""
    tools, _ = ts.load_tools()
    chosen, _ = ts.select(tools, only=["mac_compile"])
    for brief in (True, False):
        fn = ts.to_ollama(chosen, brief=brief)[0]["function"]
        assert "lint" not in fn["parameters"]["properties"], (brief, fn["parameters"])
        assert "lint" not in fn["description"].lower(), brief


def test_the_parameter_is_hidden_from_the_model_and_not_from_us():
    """The wall is at the export. Removing it from the tool itself would cost
    honesty on the MCP surface — which is not the measured party — to buy
    nothing."""
    tools, _ = ts.load_tools()
    entry = next(t for t in tools if t["name"] == "mac_compile")
    assert "lint" in entry["inputSchema"]["properties"]


def test_stripping_touches_only_the_named_tool():
    """A blanket filter would quietly eat a parameter somewhere else."""
    kept = ts.strip_harness_params("mac_status", {"type": "object",
                                                  "properties": {"lint": {}}})
    assert "lint" in kept["properties"]


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

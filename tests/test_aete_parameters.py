"""The `aete` fields that say whether an event can be BUILT, not just understood.

`guest_explore.py aete` read the reply type, the direct parameter and the named
parameters — and threw all three away. Everything it kept says what an
application *understands*; only those three say what a caller must be able to
*construct*.

Requested by the parallel session on 2026-08-05 as the one part of its
throwaway THINK C tooling that "produces knowledge rather than uses it", after
it had written the same 60 lines by hand to answer one question: can `AESEND`,
which sends a `typeChar` direct object and nothing else, reach `KAHL/MAKE`?

Tested against the REAL terminology — 4266 bytes of `aete` pulled off this
project's own THINK Project Manager and kept as a fixture. A parser tested only
against its author's synthetic bytes tests the author's understanding; this one
is checked against a resource somebody else wrote, which is where the
count-minus-one and the empty-fork traps in `rsrc_extract` came from.

Run: python3 tests/test_aete_parameters.py   (or via pytest)
"""

import os
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(_ROOT, "host", "tools"))
import guest_explore as ge  # noqa: E402

FIXTURE = os.path.join(_ROOT, "tests", "fixtures", "think_project_manager.aete")


def events():
    with open(FIXTURE, "rb") as fh:
        suites = ge.parse_aete(fh.read(), align=False)
    assert suites, "the real terminology stopped parsing"
    found = {}
    for _name, _desc, evs, _declared in suites:
        for ev, cls, eid, _ed, sig in evs:
            found[f"{cls}/{eid}"] = (ev, sig)
    return found


def test_the_terminology_still_parses():
    assert len(events()) > 20


def test_make_declares_a_required_object_direct_parameter():
    """The finding this was built for. `KAHL/MAKE` wants an object specifier,
    and AESEND cannot construct one — a fact worth having BEFORE spending an
    afternoon on it."""
    _, sig = events()["KAHL/MAKE"]
    assert sig["direct"]["type"].strip() == "obj"
    assert not sig["direct"]["optional"], "MAKE's direct parameter is mandatory"
    named = {p["name"] for p in sig["named"]}
    assert {"confirm", "makeflags"} <= named, named
    assert all(p["optional"] for p in sig["named"]), "both named ones are optional"


def test_run_needs_nothing_and_is_therefore_reachable():
    _, sig = events()["KAHL/RUN "]
    assert sig["direct"]["type"].strip() == "null"
    assert all(p["optional"] for p in sig["named"])
    assert "reachable" in ge.verdict(sig)


def test_the_optional_bit_is_read_the_same_way_elsewhere():
    """0x8000 = optional, cross-checked against a DIFFERENT suite in the same
    resource: `create` has `file` mandatory where the rest is optional. Four
    independent places agreeing is what turned this from a guess into a reading.
    """
    _, sig = events()["core/crel"]
    by_name = {p["name"]: p for p in sig["named"]}
    assert by_name, "create declares no named parameters?"
    mandatory = [n for n, p in by_name.items() if not p["optional"]]
    optional = [n for n, p in by_name.items() if p["optional"]]
    assert mandatory and optional, (mandatory, optional)


def test_the_verdict_names_the_reason_not_just_the_answer():
    """A bare no sends the reader to guess. The sentence has to say WHICH
    parameter and WHY the tool cannot supply it."""
    _, make = events()["KAHL/MAKE"]
    text = ge.verdict(make)
    assert "NOT constructible" in text
    assert "typeChar" in text and "obj" in text


def test_the_verdict_admits_it_reads_a_declaration():
    """Measured the same day: `KAHL/MAKE`, sent with NO direct parameter against
    a never-compiled project, built it — 7374 -> 53224 bytes. So a "no" here
    means "not according to the terminology", and the docstring must say so or
    the tool overstates what it knows."""
    # Whitespace-normalised: the first version matched an exact phrase that a
    # line wrap had split, which asserts the FORMATTING rather than the claim.
    # The house rule is to pin the property, not the sentence.
    doc = " ".join(ge.verdict.__doc__.split())
    assert "declaration" in doc.lower()
    assert "built a project anyway" in doc, doc


def test_a_four_tuple_reader_would_now_fail_loudly():
    """The events grew a fifth field. A caller unpacking four would raise rather
    than silently read the signature as a description — which is why the change
    is a tuple and not a dict bolted onto the side."""
    _, sig = events()["KAHL/RUN "]
    assert set(sig) == {"reply", "direct", "named"}


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

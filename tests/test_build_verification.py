"""A build step may only claim what it verified.

Every test here is one of the wrong conclusions a session reached on 2026-08-02,
turned into something that executes. That day cost most of a working day and
ended with a plan to rewrite a working design in hand-written assembly; the real
causes were a source file with no HFS type and an ordinary compile error, both
stated by the tools on the first attempt into a stderr nobody read.

The tools could not have said otherwise: `SC` is silent on success and on
failure, and `mac/src/command.c` never propagates its exit status, so `STATUS:0`
means "the Apple Event was delivered". `mac_compile` turned that into `success`.
"""
import os
import sys
import types
import unittest

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_MCP = os.path.join(_ROOT, "mcp")
_HOST = os.path.join(_ROOT, "host")

sys.path.insert(0, _HOST)
import mpw  # noqa: E402


def _load_tools():
    """Import mcp/tools.py flat, stubbing its relative import (the repo's ./mcp
    package would otherwise clash with the installed `mcp` SDK). Same approach
    as test_doc_claims.py."""
    sys.path.insert(0, _MCP)
    stub = types.ModuleType("mac_connection")
    stub.get_connection = lambda: None
    stub.MacConnection = object
    sys.modules.setdefault("mac_connection", stub)
    path = os.path.join(_MCP, "tools.py")
    src = open(path).read().replace("from .mac_connection import get_connection",
                                    "from mac_connection import get_connection")
    mod = types.ModuleType("abtools_bv")
    mod.__file__ = os.path.abspath(path)
    exec(compile(src, path, "exec"), mod.__dict__)
    return mod


tools = _load_tools()

# Both captured live on 2026-08-02, from the SAME defect — a source file with no
# TEXT type. The two tools word it differently and, crucially, case it
# differently, which is why the classifier may not be case-sensitive.
NO_TYPE_ASM = ('### Cannot open ":src:dlgpatch.a"\n'
               '# Not a text file (OS Error -31001)\n\n'
               'Asm - Execution terminated!')
NO_TYPE_SC = ("Fatal error: unable to open file "
              "'MeinMac:MPW:FortApoc:probe:notype.c' (not a TEXT file)")


class FakeConn:
    """Answers by matching a fragment of the command; records what was sent."""

    def __init__(self, script):
        self.script = script
        self.sent = []

    def is_connected(self):
        return True

    def send_command(self, command, timeout=30.0):
        self.sent.append(command)
        for fragment, reply in self.script:
            if fragment in command:
                return (0, reply, "")
        return (0, "", "")


def compile_with(script, **kwargs):
    conn = FakeConn(script)
    tools.get_connection = lambda: conn
    kwargs.setdefault("source_path", "MeinMac:MPW:P:src:x.c")
    return tools.mac_compile(**kwargs), conn


class CompileVerification(unittest.TestCase):

    def test_a_compile_whose_object_never_appeared_is_not_a_success(self):
        """The day, in one assertion.

        SC answers with the silence it always answers with, and the object file
        is not there afterwards. The old implementation returned success=True
        because the Apple Event came back with STATUS:0.
        """
        result, _ = compile_with([("Exists", "NoDir:-1701")])
        self.assertFalse(result["success"])
        self.assertTrue(result["verified"])

    def test_a_compile_whose_object_appeared_is_a_success(self):
        result, _ = compile_with([("Exists", "MeinMac:MPW:P:src:x.o")])
        self.assertTrue(result["success"])

    def test_the_compiler_stderr_reaches_the_caller_with_its_remedy(self):
        """-31001 was documented verbatim in TROUBLESHOOTING.md and still cost a
        day. The remedy now travels with the diagnostic."""
        result, _ = compile_with([("Catenate", NO_TYPE_ASM), ("Exists", "NoDir:-1701")])
        self.assertFalse(result["success"])
        self.assertTrue(any("-31001" in e for e in result["errors"]), result["errors"])
        self.assertTrue(any("SetFile -t TEXT" in r for r in result["remedies"]),
                        result["remedies"])

    def test_the_two_wordings_of_one_defect_are_both_errors(self):
        """SC lowercases "error" where Asm capitalises it. A case-sensitive
        classifier reported the SC form as having no errors while still
        attaching the remedy — the two halves of one result disagreeing.
        Caught by the live acceptance, not by the unit tests."""
        for captured in (NO_TYPE_ASM, NO_TYPE_SC):
            got = mpw.classify_diagnostics(captured)
            self.assertTrue(got["errors"], f"no error line for: {captured!r}")
            self.assertTrue(any("SetFile -t TEXT" in r for r in got["remedies"]))

    def test_the_redirect_and_the_read_are_never_the_same_command(self):
        """Sent as one line the capture comes back empty — which is what made a
        session conclude the transport had destroyed its redirect."""
        _, conn = compile_with([("Exists", "x.o")])
        for command in conn.sent:
            self.assertFalse(mpw.STDERR_REDIRECT in command and "Catenate" in command,
                             f"redirect and read share a command line: {command!r}")
        self.assertTrue(any(mpw.STDERR_REDIRECT in c for c in conn.sent),
                        "no stderr capture was attempted at all")

    def test_a_missing_artifact_with_no_diagnostics_probes_toolserver(self):
        """"Nothing was produced" is two different situations. The probe names
        which one, and only runs when something already failed."""
        result, conn = compile_with([("Exists", "NoDir:-1701")])
        self.assertTrue(any(c.startswith("Echo ") for c in conn.sent), conn.sent)
        self.assertFalse(result["toolserver_alive"])

    def test_a_successful_compile_does_not_pay_for_the_probe(self):
        _, conn = compile_with([("Exists", "x.o")])
        self.assertFalse(any(c.startswith("Echo ") for c in conn.sent), conn.sent)

    def test_a_stale_error_file_cannot_be_attributed_to_this_step(self):
        """`≥` truncates only when the tool runs. If it never runs, Catenate
        returns the PREVIOUS step's diagnostics."""
        _, conn = compile_with([("Exists", "x.o")])
        deletes = [c for c in conn.sent if c.startswith("Delete ")]
        self.assertTrue(deletes, conn.sent)
        self.assertIn(".err", deletes[0])

    def test_the_object_we_verify_is_the_object_we_told_sc_to_write(self):
        """The invariant, not a literal name.

        Without `output_path` the object path was DERIVED (`x.c` -> `x.o`) and
        `-o` was never passed. `SC x.c` writes `x.c.o`, so `Exists` looked for a
        file SC does not create and a successful compile answered
        `success: false`. A false negative — and the kind that sends a caller
        off to repair a source that is not broken.

        Asserting the two paths AGREE catches it however the naming changes;
        asserting a literal would only pin today's spelling.
        """
        result, conn = compile_with([("Exists", "MeinMac:MPW:P:src:x.o")])
        sc = [c for c in conn.sent if c.startswith("SC ")]
        self.assertEqual(len(sc), 1, conn.sent)
        self.assertIn(f"-o '{result['object']}'", sc[0])

    def test_a_derived_object_path_is_still_verified(self):
        """The caller passing nothing must not silently lose the check."""
        result, _ = compile_with([("Exists", "MeinMac:MPW:P:src:x.o")])
        self.assertTrue(result["verified"])
        self.assertTrue(result["success"])

    def test_an_output_path_hidden_in_options_is_reported_unverified(self):
        """Guessing the artefact path and then checking the guess would be a new
        lie, not a check."""
        result, _ = compile_with([], options="-o :obj:elsewhere.o")
        self.assertFalse(result["verified"])
        self.assertIsNone(result["success"])


class Oracles(unittest.TestCase):

    def test_the_exists_oracle_reads_the_transcripts_the_guest_prints(self):
        self.assertTrue(mpw.artifact_exists("MeinMac:MPW:P:obj:x.o"))
        self.assertFalse(mpw.artifact_exists("NoDir:-1701"))
        self.assertFalse(mpw.artifact_exists(""))
        self.assertFalse(mpw.artifact_exists("__SENDERR__:boom"))

    def test_the_dead_got_oracle_is_gone(self):
        """host/build.py tested for 'Got:', a token this protocol does not emit,
        so its existence check answered False for every successful build. The
        functions that used it are gone; the token may not come back."""
        offenders = []
        for folder in (_HOST, _MCP):
            for name in sorted(os.listdir(folder)):
                if not name.endswith(".py"):
                    continue
                with open(os.path.join(folder, name), encoding="utf-8") as handle:
                    text = handle.read()
                if "'Got:'" in text or '"Got:"' in text:
                    offenders.append(name)
        self.assertEqual(offenders, [],
                         "the dead 'Got:' oracle came back")

    def test_error_52_is_a_warning_and_a_real_error_is_not(self):
        got = mpw.classify_diagnostics(
            "### Link: Warning: File was not needed for link: (Error 52) StdCLib.o")
        self.assertEqual(got["errors"], [])
        self.assertTrue(got["warnings"])

    def test_the_remedy_table_names_the_rule_not_just_the_symptom(self):
        self.assertTrue(any("ILink -model far" in r
                            for r in mpw.classify_diagnostics("Error 48")["remedies"]))
        self.assertTrue(any("Rez" in r
                            for r in mpw.classify_diagnostics("err -903")["remedies"]))


class RunStep(unittest.TestCase):
    """The driver itself: the order of the four commands is the whole design."""

    def _run(self, script):
        conn = FakeConn(script)
        return mpw.run_step(lambda c, t=30.0: conn.send_command(c, t)[1],
                            "SC :src:x.c -o :obj:x.o", ":obj:x.o", ":obj:x.err"), conn

    def test_the_target_and_the_error_file_are_cleared_before_the_tool_runs(self):
        _, conn = self._run([("Exists", ":obj:x.o")])
        self.assertTrue(conn.sent[0].startswith("Delete -i "), conn.sent)
        self.assertIn(":obj:x.o", conn.sent[0])
        self.assertIn(":obj:x.err", conn.sent[0])

    def test_the_artifact_decides_even_when_the_status_was_fine(self):
        ok, _ = self._run([("Exists", ":obj:x.o")])
        self.assertTrue(ok["success"])
        bad, _ = self._run([("Exists", "NoDir:-1701")])
        self.assertFalse(bad["success"])

    def test_the_capture_and_the_read_back_are_two_commands(self):
        _, conn = self._run([("Exists", ":obj:x.o")])
        redirects = [c for c in conn.sent if mpw.STDERR_REDIRECT in c]
        reads = [c for c in conn.sent if c.startswith("Catenate ")]
        self.assertEqual(len(redirects), 1)
        self.assertEqual(len(reads), 1)
        self.assertNotEqual(redirects[0], reads[0])


class Descriptions(unittest.TestCase):
    """The schema text is the only channel that reaches an agent at the moment
    of USE, without any decision to go and read something. It drifted once
    already: after the tools began verifying artefacts, `mac_compile` still
    advertised "Output is source.c.o by default. Returns success status" —
    both halves false, and silent about what success now means."""

    def _description(self, name):
        for tool in tools.TOOLS:
            if tool["name"] == name:
                return tool["description"]
        self.fail(f"{name} is not in TOOLS")

    def test_the_compile_description_names_what_it_now_returns(self):
        text = self._description("mac_compile")
        for key in ("verified", "remedies", "toolserver_alive"):
            self.assertIn(key, text, f"the description never mentions `{key}`")
        self.assertNotIn("source.c.o", text, "the stale default object name is back")

    def test_the_compile_description_says_what_success_means(self):
        text = self._description("mac_compile").lower()
        self.assertIn("object file is on disk", text)

    def test_the_execute_description_warns_about_the_silence(self):
        text = self._description("mpw_execute")
        self.assertIn("hint", text)
        self.assertIn("≥", text)
        self.assertIn("2>&1", text, "the rule that crashes the shell is unstated")


class Hints(unittest.TestCase):

    def test_a_one_line_redirect_and_read_is_named(self):
        self.assertTrue(mpw.redirect_and_read_on_one_line(
            "SC x.c -o x.o ≥ e.err ; Catenate e.err"))
        self.assertFalse(mpw.redirect_and_read_on_one_line("SC x.c -o x.o ≥ e.err"))
        self.assertFalse(mpw.redirect_and_read_on_one_line("Catenate e.err"))

    def test_the_silent_tools_are_recognised_anywhere_in_the_line(self):
        self.assertEqual(mpw.silent_tool("Directory X: ; SC a.c").lower(), "sc")
        self.assertIsNone(mpw.silent_tool("Echo hello"))

    def test_mpw_execute_hints_but_never_rewrites_the_command(self):
        conn = FakeConn([])
        tools.get_connection = lambda: conn
        result = tools.mpw_execute("SC x.c -o x.o")
        self.assertEqual(conn.sent, ["SC x.c -o x.o"])
        self.assertIn("hint", result)
        self.assertIn("STATUS:0", result["hint"])

    def test_mpw_execute_keeps_its_existing_keys(self):
        conn = FakeConn([("Echo", "hi")])
        tools.get_connection = lambda: conn
        result = tools.mpw_execute("Echo hi")
        for key in ("success", "status", "output", "error"):
            self.assertIn(key, result)
        self.assertNotIn("hint", result, "a tool that printed needs no hint")


if __name__ == "__main__":
    unittest.main()

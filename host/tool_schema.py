#!/usr/bin/env python3
"""Export the bridge's tool definitions in the shape a LOCAL model can use.

The 30 tools in `mcp/tools.py` carry name, description and JSON Schema. That is
already the whole contract — what a caller may pass and what it means. It is
only wrapped for MCP, which is Claude-side; a local model on the Jetson speaks
Ollama's `/api/chat` with a `tools` block instead.

**So this converts, it does not define.** Writing the schemas a second time by
hand is the obvious shortcut and the wrong one: two definitions of one tool drift
the moment either side changes, and the drift shows up as a model calling a
parameter that no longer exists — which the bridge answers with a plain error
that looks like the model's fault. One source, two wrappers.

Three things this does beyond copying, each for a measured reason:

**It checks that every exported name can actually be dispatched.** Names come
from `TOOLS`, handlers from `TOOL_HANDLERS`, and nothing enforced that the two
agree. A name in one and not the other is a tool the model can call and the
bridge cannot run. (Not hypothetical: `getattr(tools, "bridge_doctor")` returns
the imported *module*, not the handler — the dispatch table is the only truth
here, and a check written against the obvious thing would have been wrong.)

**It can shorten the descriptions.** They are essays, 19.6 kB in total, written
for a model with a large context that benefits from the whole gotcha. Measured
2026-08-05: the model that would drive this (`qwen2.5-coder:7b`, 4.7 GB of
weights) is on a node with ~2 GB free. `--brief` keeps the first paragraph —
what the tool DOES — and drops the operating lore, which belongs in the harness
anyway (a rule a small model reads in the system prompt is forgotten by the third
step; the tools have to enforce it, see docs/OPERATING_NOTES.md).

**It offers profiles.** The smallest end-to-end proof needs five tools, not
thirty. Handing a small model everything is the same mistake as handing it the
whole screen: more to be wrong about, no more that it can do.

Usage
    tool_schema.py                       # all 30, full descriptions, to stdout
    tool_schema.py --profile build --brief
    tool_schema.py --only mac_compile,mac_list_files --out tools.json

stdlib only. Importing this imports `mcp.tools`, which has no side effects at
import time (the connection is opened lazily, on the first call).
"""

import argparse
import json
import os
import sys

# Profiles are deliberately small. `build` is the set the first closed loop
# needs: compile something, look at the result, ask the guest what it thinks.
# It has no input injection and no screenshot on purpose — a loop that can only
# build and read cannot leave the guest in a state somebody has to repair.
PROFILES = {
    "build": ["mac_compile", "mac_build", "mac_list_files", "mac_read_file",
              "mac_write_file", "mpw_execute", "mac_status"],
    "drive": ["mac_screenshot", "mac_type", "mac_key", "mac_click", "mac_menu",
              "mac_menu_front", "launch_app", "mac_status"],
}


def load_tools():
    """-> (TOOLS, set of dispatchable names). Repo layout, not installed."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if root not in sys.path:
        sys.path.insert(0, root)
    from mcp import tools as t                                  # noqa: PLC0415
    return t.TOOLS, set(t.TOOL_HANDLERS)


def first_paragraph(text):
    """The description down to its first blank line, whitespace tidied.

    The first paragraph of every tool here answers "what does this do"; what
    follows is the operating lore. Keeping the split at the blank line means the
    boundary is the author's, not a character count that would cut mid-sentence.
    """
    para = (text or "").strip().split("\n\n", 1)[0]
    return " ".join(para.split())


def to_ollama(tools, brief=False):
    """MCP tool defs -> Ollama /api/chat `tools` entries."""
    out = []
    for d in tools:
        desc = d.get("description", "")
        # An empty schema still has to be an object: Ollama requires the key,
        # and a model handed `null` parameters has been observed to invent them.
        params = d.get("inputSchema") or {"type": "object", "properties": {}}
        out.append({
            "type": "function",
            "function": {
                "name": d["name"],
                "description": first_paragraph(desc) if brief else desc.strip(),
                "parameters": params,
            },
        })
    return out


def select(tools, only=None, profile=None):
    """-> (chosen tools, names asked for that do not exist).

    An unknown name is REPORTED, never silently dropped. A profile that quietly
    loses a tool produces a model that cannot do the job and cannot say why.
    """
    if only:
        wanted = [n.strip() for n in only if n.strip()]
    elif profile:
        wanted = list(PROFILES[profile])
    else:
        return list(tools), []
    by_name = {d["name"]: d for d in tools}
    chosen = [by_name[n] for n in wanted if n in by_name]
    return chosen, [n for n in wanted if n not in by_name]


def undispatchable(tools, handlers):
    """Exported names with no handler — the drift this module exists to catch."""
    return sorted(d["name"] for d in tools if d["name"] not in handlers)


def size_report(entries):
    """Bytes of description text, because context is the binding constraint."""
    chars = sum(len(e["function"]["description"]) for e in entries)
    return {"tools": len(entries), "description_chars": chars}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    g = p.add_mutually_exclusive_group()
    g.add_argument("--profile", choices=sorted(PROFILES),
                   help="a named subset (see PROFILES)")
    g.add_argument("--only", help="comma-separated tool names")
    p.add_argument("--brief", action="store_true",
                   help="first paragraph of each description only")
    p.add_argument("--out", help="write here instead of stdout")
    p.add_argument("--quiet", action="store_true",
                   help="no size report on stderr")
    args = p.parse_args(argv)

    tools, handlers = load_tools()
    chosen, unknown = select(tools, args.only.split(",") if args.only else None,
                             args.profile)
    if unknown:
        print(f"unknown tool name(s): {', '.join(unknown)}", file=sys.stderr)
        return 2
    missing = undispatchable(chosen, handlers)
    if missing:
        # Refuse rather than export: a model that calls this would get a plain
        # "Unknown tool" back and no way to tell that the fault is ours.
        print(f"exported but not dispatchable: {', '.join(missing)}",
              file=sys.stderr)
        return 3

    entries = to_ollama(chosen, args.brief)
    text = json.dumps(entries, indent=2, ensure_ascii=False)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
    else:
        print(text)
    if not args.quiet:
        r = size_report(entries)
        print(f"{r['tools']} tool(s), {r['description_chars']} chars of "
              f"description", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())

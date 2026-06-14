# Architecture Layers: OpenTransport + MCP

> **The question:** "Now that we have OpenTransport *and* MCP in our bridge, does
> this give us extra features — or is it simply doubled?"
>
> **The answer:** Not doubled. They are **complementary layers** sitting at opposite
> ends of the stack. OpenTransport gets the 68k Mac onto the network; MCP gets Claude
> connected to the bridge. Remove either and the closed-loop, AI-driven retro
> development workflow stops working.

## The full stack

```
Claude Code (AI / LLM)
    │  MCP protocol (stdio)            ← AI tool interface
    ▼
MCP server  ──:9001──▶  host_server.py  ──:9000──▶  Mac daemon (AppleBridge, C, 68k)
                                          ▲                    │
                                   TCP/IP over OpenTransport   │  Apple Events
                                   (Mac dials OUT, NAT-safe)   ▼
                                                      ToolServer / MPW Shell (System 7.6.1)
```

Each arrow is a different protocol solving a different problem.

## What each layer provides

### OpenTransport — the Mac side
- 🌐 Puts the emulated 68k Mac (System 7.6.1) on a TCP/IP network.
- 📡 Enables **outbound** TCP connections *from* the classic Mac.
- 🔄 Makes the **NAT-reversed architecture** possible: the Mac dials OUT to the host
  on `:9000`, because Basilisk II sits behind NAT and cannot accept inbound connections.
- 💡 The breakthrough: a 30-year-old Mac initiating a modern network connection.

### MCP — the host / AI side
- 🤖 Lets Claude Code drive the Mac as a set of tools.
- 🛠️ Standardized, declarative tools with schemas — `mpw_execute`, `mac_compile`,
  `mac_read_file`, `mac_write_file`, `mac_list_files`, `mac_screenshot`, `launch_app`.
- 🔌 Plugin architecture: any MCP client can use the bridge, not just one script.
- 📝 Self-documenting interface with validation and error handling instead of raw,
  hand-typed commands.

## Why they are not redundant

| Layer | Solves | Without it |
|-------|--------|-----------|
| **OpenTransport** | 68k Mac networking + NAT traversal | Mac is offline; host can never reach it |
| **MCP** | AI ↔ bridge tool interface | Claude has no standard way to command the Mac |

- ❌ **OpenTransport only:** the Mac can network, but there is no AI integration.
- ❌ **MCP only:** Claude has tools, but nothing on the other end to reach.
- ✅ **Both together:** Claude can develop *for* the classic Mac, *on* the classic
  Mac, with a full feedback loop.

## What the combination unlocks

1. **AI-powered retro development** — Claude writes C / 68k assembly on the host,
   compiles on the authentic SC/Asm toolchain, links, runs, and reads real output
   back for closed-loop debugging.
2. **Breaking the emulator NAT barrier** — OpenTransport lets the Mac connect OUT,
   MCP lets the AI command IN; the reversed-TCP design resolves the NAT problem.
3. **Closed-loop feedback** — errors, file listings, dumps, and screenshots flow
   back to the AI, so iteration is automatic instead of copy/paste.

### Before vs. after

**Before:** boot Basilisk II, type commands by hand in MPW, copy/paste errors, search
1990s docs, repeat the whole loop manually.

**After:** describe the goal to Claude; it writes, compiles, tests, and debugs on an
authentic System 7.6.1 environment, with modern AI assistance.

## TL;DR

Not doubled — **complementary layers** that together enable AI-driven retro-computing
development. OpenTransport gets the Mac online; MCP gets Claude connected. Together they
make it possible for an AI to build, run, and debug authentic 68k Macintosh software
through natural language.

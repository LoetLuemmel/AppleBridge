# START HERE - AppleBridge for Basilisk II

**Your Setup**: Basilisk II + System 7.6.1 + TCP/IP ✓

You have the complete starter code for connecting Claude AI to your classic Mac!

## What You Have Now

```
✓ Python host communicator (connects to Claude API)
✓ Mac C daemon code (runs in Basilisk II)
✓ Protocol specification
✓ Complete documentation
✓ Test scripts
```

## Quick Navigation

**New to the project? Read this:**
1. **[QUICKSTART.md](QUICKSTART.md)** ← Start here! Fastest path to running system
2. **[mac/BUILD_NOTES.md](mac/BUILD_NOTES.md)** ← Important 68k build information

**Reference documentation:**
- **[README.md](README.md)** - Project overview and architecture
- **[docs/SETUP.md](docs/SETUP.md)** - Comprehensive setup guide
- **[docs/USAGE.md](docs/USAGE.md)** - How to use once running
- **[protocol/PROTOCOL.md](protocol/PROTOCOL.md)** - Technical protocol details
- **[NEXT_STEPS.md](NEXT_STEPS.md)** - Implementation details and improvements

## Critical: Basilisk II Uses 68k!

Since you're using **Basilisk II** (not SheepShaver), you need the **68k** build:

```
# In Basilisk II MPW Shell:
make -f Makefile.68k
```

NOT the default PowerPC Makefile!

See [mac/BUILD_NOTES.md](mac/BUILD_NOTES.md) for details.

## Your Next 3 Steps

### 1. Get MPW Running in Basilisk II
- Download MPW if you don't have it
- Install to `HD:MPW:`
- Test it works

### 2. Build Mac Daemon
- Transfer `mac/` files to Basilisk II (use `./copy_to_basilisk.sh`)
- Build with `Makefile.68k`
- Run `:bin:AppleBridge`

### 3. Test from Host
```bash
cd host/
uv pip install anthropic pillow
uv run tests/test_connection.py
```

## Expected Timeline

- **15 min**: Transfer files and attempt first build
- **30 min**: Debug build issues (normal for vintage code!)
- **15 min**: Get daemon running and test connection
- **15 min**: Set up Claude API and test integration

**Total: ~1-2 hours** for a working system (if everything goes smoothly)

## Most Likely Issues

1. **Build fails**: See [mac/BUILD_NOTES.md](mac/BUILD_NOTES.md) - probably need to use 68k tools
2. **Connection refused**: Check Basilisk II networking and port forwarding
3. **Command execution fails**: Use simple echo version first (in QUICKSTART.md)

## What Works vs What Needs Work

**Should work out of the box:**
- ✓ TCP networking layer
- ✓ Protocol parsing
- ✓ Basic communication
- ✓ Host-side Python code

**Needs testing/refinement:**
- ⚠️ MPW command execution (placeholder in template)
- ⚠️ Screenshot capture (may need BMP encoding)
- ⚠️ Error handling edge cases
- ⚠️ 68k-specific compatibility

## Getting Help

**Build issues?** → [mac/BUILD_NOTES.md](mac/BUILD_NOTES.md)
**Connection issues?** → [QUICKSTART.md](QUICKSTART.md) troubleshooting section
**Want to understand it better?** → [README.md](README.md) and [docs/SETUP.md](docs/SETUP.md)

## The Goal

When working, you'll be able to do this:

```bash
$ uv run main.py

You: Can you list the files in the current MPW directory?

Claude: I'll execute the 'Files' command to list the directory contents.
[Executes: Files]

The current directory contains:
  AppleBridge
  bin
  obj
  src
  include
  Makefile

You: Can you help me compile a simple C program?

Claude: I can help with that! Let me create and compile a simple program...
[Executes various MPW commands]
```

## Ready?

Open **[QUICKSTART.md](QUICKSTART.md)** and let's get started! 🚀

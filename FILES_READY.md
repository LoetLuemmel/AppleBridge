# ✓ Files Ready for Basilisk II

The AppleBridge Mac daemon files have been copied to your Basilisk II shared folder!

## What's Been Done

1. ✓ All Mac C source files copied
2. ✓ 68k Makefile included
3. ✓ Build instructions included
4. ✓ Global CLAUDE.md updated with shared folder path

## Location

**Shared Folder**: `/Users/pitforster/Desktop/Share/AppleBridge/`

This folder is accessible from within Basilisk II.

## Files Copied

```
AppleBridge/
├── README.txt          ← Start here in Basilisk II!
├── BUILD_NOTES.md      ← Build troubleshooting
├── Makefile.68k        ← Use this one for 68k!
├── Makefile            ← Don't use (PowerPC)
├── include/
│   └── applebridge.h
└── src/
    ├── main.c
    ├── network.c
    ├── protocol.c
    ├── command.c
    ├── screenshot.c
    └── utils.c
```

## Next Steps in Basilisk II

1. **Open the shared folder** (should appear on desktop)
2. **Copy AppleBridge folder to**: `HD:MPW:AppleBridge:`
3. **Open MPW Shell**
4. **Run these commands**:
   ```
   Directory HD:MPW:AppleBridge:
   make -f Makefile.68k dirs
   make -f Makefile.68k
   bin:AppleBridge
   ```

## Next Steps on Host

Once the Mac daemon is running:

```bash
cd host/
uv pip install anthropic pillow
cp .env.example .env
# Edit .env with your ANTHROPIC_API_KEY

# Test connection
uv run tests/test_connection.py

# Run interactive mode
uv run main.py
```

## Need to Update Files?

Just run the copy script again:

```bash
./copy_to_basilisk.sh
```

This will refresh all files in the shared folder.

## Documentation

- **Quick start**: [QUICKSTART.md](QUICKSTART.md)
- **Entry point**: [START_HERE.md](START_HERE.md)
- **Full guide**: [docs/SETUP.md](docs/SETUP.md)
- **68k build help**: [mac/BUILD_NOTES.md](mac/BUILD_NOTES.md)

Good luck! 🚀

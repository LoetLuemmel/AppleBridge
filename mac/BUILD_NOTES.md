# Build Notes for Mac Daemon

## Important: 68k vs PowerPC

**Basilisk II** emulates **68k** Macintosh computers.
**SheepShaver** emulates **PowerPC** Macintosh computers.

The default `Makefile` is for PowerPC (SheepShaver).

Since you're using **Basilisk II**, use **Makefile.68k** instead!

## Building on Basilisk II (68k)

1. **Use the 68k Makefile**:
   ```
   # Rename or copy
   Duplicate Makefile.68k Makefile
   # Or build directly
   make -f Makefile.68k
   ```

2. **Compiler differences**:
   - **PowerPC**: MrC (C compiler), PPCLink (linker)
   - **68k**: SC or SCpp (C compiler), Link (linker)

3. **Check your MPW version**:
   - Type: `SC -version` or `MrC -version`
   - If only SC works, you have 68k tools (correct for Basilisk II)
   - If only MrC works, you have PowerPC tools (wrong for Basilisk II)

## Library Compatibility

### Open Transport Availability

**System 7.6.1** should include Open Transport, but check:

1. **Verify Open Transport**:
   - Apple Menu → About This Macintosh
   - Look for "Open Transport" in the info
   - Or check Extensions folder for "Open Transport" files

2. **Version requirements**:
   - Need Open Transport 1.1.2 or later
   - Included with System 7.5.3+ usually
   - May need separate install on 7.6.1

3. **If Open Transport not available**:
   - You can install it separately for System 7.6.1
   - Or modify code to use MacTCP (older API, more work)

## Common Build Issues

### Issue: "SC not found" or "MrC not found"

**Solution**: Check which compiler you have:
```
which SC
which MrC
```

Use the appropriate Makefile for your tools.

### Issue: "Can't find OpenTransportLib"

**Solution**: Open Transport may not be installed. Options:
1. Install Open Transport for System 7.6.1
2. Use MacTCP instead (requires code changes)
3. Check library paths in Makefile

### Issue: "time.h not found" or time functions fail

System 7 has limited time functions. Fix in `utils.c`:

Replace:
```c
#include <time.h>
time_t now;
time(&now);
strftime(timeStr, sizeof(timeStr), "%Y-%m-%d %H:%M:%S", localtime(&now));
```

With:
```c
// Use TickCount() instead
unsigned long ticks = TickCount();
sprintf(timeStr, "%lu", ticks);
```

### Issue: "sprintf not found"

Use older function names:
- Change `sprintf` to `sprintf` (should work)
- Or use `NumToString` for Mac-specific string formatting

### Issue: "atol not found"

System 7 stdlib may be limited. Implement simple version:
```c
long simple_atol(const char *str) {
    long result = 0;
    int sign = 1;

    if (*str == '-') {
        sign = -1;
        str++;
    }

    while (*str >= '0' && *str <= '9') {
        result = result * 10 + (*str - '0');
        str++;
    }

    return sign * result;
}
```

## Testing the Build

### Step 1: Test compilation of one file

```
SC -i :include: :src:utils.c -o :obj:utils.c.o
```

If this works, the compiler and paths are correct.

### Step 2: Build all objects

```
make -f Makefile.68k
```

Watch for errors and fix one at a time.

### Step 3: Test the binary

```
:bin:AppleBridge
```

Should show startup message and listen on port 9000.

## Minimal Working Version

If full build fails, try this minimal version first:

1. **Simplify command.c** - just echo back commands (see QUICKSTART.md)
2. **Disable screenshots** - comment out screenshot code in main.c
3. **Simplify logging** - just use printf, skip time formatting

Get basic TCP communication working first, then add features!

## Alternative: Build as MPW Tool

Instead of a standalone application, you could build this as an MPW tool that runs within the MPW Shell:

**Advantages**:
- Direct access to shell
- Easier command execution
- Simpler integration

**Disadvantages**:
- More complex startup
- Requires MPW Shell to be running

If interested, see MPW documentation on building tools.

## Need Help?

Common MPW/System 7 resources:
- Inside Macintosh books (online at archive.org)
- MPW Reference Manual
- Vintage Mac programming forums
- Basilisk II documentation for file transfer methods

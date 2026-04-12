# 68k Assembly Template for Mac Apps

**Date:** 2026-04-11
**Source:** `/Users/pitforster/Desktop/Share/32BitAExamples copy/Count.a`

## Working Example Structure

From Apple's Count.a example (MPW Tool that successfully assembles and links).

### Key Differences from Failed Approach

| Our Counter.a | Working Count.a | Notes |
|---------------|-----------------|-------|
| `main PROC EXPORT` | `Count MAIN` | Use `MAIN` directive! |
| No `-model far` | `Asm -model far` | Required for modern linking |
| Missing runtime libs | `MacRuntime.o Interface.o` | Needed for Mac apps |
| No segment org | `SEG 'MAIN'`, `SEG 'INIT'` | Organize code into segments |

### Build Commands (from MakeFile)

**Assembly:**
```
Asm -model far Count.a
```

**Linking (MPW Tool):**
```
Link -w -model far -c 'MPS ' -t MPST Count.a.o FStubs.a.o \
    -sn INTENV=Main \
    -sn %A5Init=Main \
    "{Libraries}"Stubs.o \
    "{Libraries}"MacRuntime.o \
    "{Libraries}"IntEnv.o \
    "{Libraries}"ToolLibs.o \
    "{Libraries}"Interface.o \
    -o Count
```

### For Standalone APPL (Not MPW Tool)

Remove MPW-specific libraries (IntEnv.o, ToolLibs.o):

```
Asm -model far counter.a
Link -model far -m MAIN counter.o \
    "{Libraries}"Interface.o \
    "{Libraries}"MacRuntime.o \
    -o CounterAsm -t APPL -c '????'
Rez CounterAsm.r -a -o CounterAsm
```

## Code Structure Template

```asm
        TITLE       'MyApp'
        CASE    OBJ

; Include system headers
        INCLUDE 'intenv.a'
        INCLUDE 'TextUtils.a'

; Import external symbols
        IMPORT  _InitCursorCtl

; Constants
kBufSize    EQU     1024

; Global data structure
Globals     RECORD
myVar       DS.L    1
myBuffer    DS.B    kBufSize
            ENDR

; ============================================
; Main Entry Point
; ============================================
        SEG 'MAIN'
MyApp   MAIN
        WITH    Globals

        ; Your initialization code here
        JSR     (Init).L

        ; Main logic here

        ; Cleanup and exit
        JMP     (Stop).L

        ENDWITH
        ENDPROC

; ============================================
; Init - Initialization
; ============================================
        SEG 'INIT'
Init    PROC
        ; Toolbox initialization
        _InitGraf
        _InitFonts
        _InitWindows
        ; etc.

        RTS
        ENDPROC

; ============================================
; Other procedures
; ============================================
        SEG 'MySegment'
MyProc  PROC
        Link    A6,#0
        ; ...
        UNLK    A6
        RTS
        ENDPROC

        END
```

## Key Concepts

### 1. MAIN Directive
- Declares the main entry point for the application
- Not the same as `PROC EXPORT`
- Use: `MyApp MAIN`

### 2. SEG Directive
- Organizes code into segments (for code resource management)
- Example: `SEG 'MAIN'`, `SEG 'INIT'`

### 3. RECORD/ENDR
- Structured global data relative to A5
- Easier than individual DS.L declarations

### 4. -model far
- Enables 32-bit addressing
- Required for modern MPW 3.2+ assembler
- Must use on both Asm and Link commands

### 5. Required Libraries
For standalone apps:
- **Interface.o** - Toolbox trap definitions
- **MacRuntime.o** - C runtime initialization (even for asm!)

For MPW tools (add these):
- **IntEnv.o** - MPW environment
- **ToolLibs.o** - MPW tool libraries
- **Stubs.o** - Library stubs

## Next Steps for OurTest2

To fix the Counter assembly app:

1. **Rewrite using MAIN directive** instead of PROC EXPORT
2. **Add SEG organization** for code modules
3. **Build with `-model far`** on both Asm and Link
4. **Link with MacRuntime.o and Interface.o**
5. **Use proper segment names** with `-sn` if needed

## References

- Working example: `/Users/pitforster/Desktop/Share/32BitAExamples copy/`
- Count.a - Full MPW tool source
- MakeFile - Build instructions
- Instructions - Apple documentation

---

**Note:** Saved for next session to properly implement OurTest2 assembly Counter app.

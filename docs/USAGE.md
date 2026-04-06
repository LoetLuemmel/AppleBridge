# AppleBridge Usage Guide

How to use AppleBridge to connect Claude to your classic Mac.

## Basic Usage

### Starting the System

1. **Start Mac Daemon** (in MPW Shell):
   ```
   bin:AppleBridge
   ```

2. **Start Host Communicator**:
   ```bash
   uv run main.py
   ```

### Interactive Mode

In interactive mode, you chat with Claude and it can execute MPW commands:

```
You: What directory am I in?
Claude: Let me check that for you.
[Claude executes: pwd]
You are in HD:MPW:

You: Can you compile a simple C program for me?
Claude: I'll create and compile a hello world program.
[Claude executes: echo 'void main() { printf("Hello!"); }' > hello.c]
[Claude executes: MrC hello.c -o hello]
Done! The program has been compiled to 'hello'.
```

### Single Command Mode

Execute one command and exit:

```bash
uv run main.py --command "Directory"
```

Output:
```
Exit code: 0
Stdout:
HD:MPW:AppleBridge:
```

### Test Mode

Verify connection:

```bash
uv run main.py --test
```

## Working with Screenshots

### Request a Screenshot

In interactive mode:
```
You: /screenshot
Next message will include a screenshot

You: What do you see on the screen?
Claude: [analyzes screenshot] I can see the MPW Shell window with...
```

### Programmatic Screenshot

```python
from config import AppleBridgeConfig
from claude_bridge import ClaudeBridge

config = AppleBridgeConfig.from_env()
bridge = ClaudeBridge(config)

bridge.start()
screenshot = bridge.capture_screenshot()
screenshot.save("mac_screen.png")
bridge.stop()
```

## MPW Command Examples

### File Operations

```
You: List all .c files
[Claude executes: Files -r *.c]

You: Show me the contents of hello.c
[Claude executes: Catenate hello.c]

You: Create a new file called test.txt with "Hello World"
[Claude executes: Echo "Hello World" > test.txt]
```

### Compilation (SC Compiler)

```
You: Compile hello.c
[Claude executes: SC hello.c -o hello.o]

You: Link to executable
[Claude executes: Link -model far hello.o "{Libraries}Interface.o" "{Libraries}MacRuntime.o" -o hello]
[Claude executes: SetFile -t APPL -c '????' hello]
```

### Navigation

```
You: Change to the parent directory
[Claude executes: Directory ::]

You: What's in the System folder?
[Claude executes: Files "HD:System Folder:"]
```

## Advanced Usage

### Custom Configuration

Create a Python script:

```python
from config import AppleBridgeConfig
from claude_bridge import ClaudeBridge

# Custom config
config = AppleBridgeConfig(
    mac_host="192.168.1.100",
    mac_port=9000,
    claude_api_key="your-key",
    command_timeout=60,
    debug=True
)

bridge = ClaudeBridge(config)
bridge.start()

# Execute command
result = bridge.execute_mpw_command("Directory")
print(result.stdout)

bridge.stop()
```

### Batch Commands

```python
commands = [
    "Directory",
    "Files",
    "echo 'Building...'",
    "make all"
]

bridge.start()
for cmd in commands:
    result = bridge.execute_mpw_command(cmd)
    print(f"{cmd}: {result.stdout}")
bridge.stop()
```

### Error Handling

```python
try:
    bridge.start()
    result = bridge.execute_mpw_command("invalid_command")

    if result.exit_code != 0:
        print(f"Command failed: {result.stderr}")

except ConnectionError:
    print("Could not connect to Mac")
except TimeoutError:
    print("Command timed out")
finally:
    bridge.stop()
```

### Compile/Link Error Capture

**CRITICAL**: Do NOT use `2>&1` - it crashes MPW Shell!

Use MPW's `≥` operator to redirect stderr to a file:

```bash
# Compile with stderr capture
uv run python send_command.py 'SC file.c -o file.o ≥ compile.err'

# Check if compile succeeded (file exists = success)
uv run python send_command.py 'Exists file.o'
# Success: Returns "path:to:file.o" with "Got:XX"
# Failure: Returns "STDOUT:0" with "NoDir:-1701;Empty"

# Read error/warning messages
uv run python send_command.py 'Catenate compile.err'
```

**Response pattern reference:**

| Scenario | STDOUT | Response Pattern |
|----------|--------|-----------------|
| File exists | Filename | `Got:XX` |
| File missing | 0 | `NoDir:-1701;Empty` |

**Example error output:**
```
SC C Compiler 8.9.0d3e1
File "bad_code.c"; line 4 #Error: expression expected#
File "bad_code.c"; line 5 #Error: missing ',' between declaration...#
```

**Note:** Successful compiles may still have warnings in compile.err - always check file existence to determine pass/fail.

### Makefile Execution

MPW Make only PRINTS commands, it doesn't execute them. Use this pattern:

```bash
# Generate build script, then execute it
uv run python send_command.py 'Make > BuildIt; BuildIt'
```

**Makefile syntax notes:**
- Use `ƒ` (option-f) for dependencies, NOT `::`
- Use `{Libraries}` NOT `{LIBS}` for library paths
- Example: `main.o ƒ main.c helpers.h`

## Claude Integration Patterns

### Code Analysis

```
You: Can you analyze the code in myprogram.c and suggest improvements?
[Claude reads file, analyzes, provides suggestions]

You: Can you refactor the sorting function to be more efficient?
[Claude reads current implementation, writes improved version]
```

### Build Automation

```
You: Set up a build process for this project
[Claude examines files, creates Makefile, tests build]

You: The build is failing, can you debug it?
[Claude runs build, reads errors, fixes issues]
```

### Documentation

```
You: Can you document all the functions in utils.c?
[Claude reads code, adds comments and documentation]

You: Create a README for this project
[Claude analyzes project structure, creates documentation]
```

## Tips and Best Practices

### MPW vs Unix Commands

MPW uses different commands than Unix:

| Task | MPW Command | Unix Equivalent |
|------|-------------|-----------------|
| List files | `Files` | `ls` |
| Show content | `Catenate` | `cat` |
| Current dir | `Directory` | `pwd` |
| Create dir | `NewFolder` | `mkdir` |
| Delete | `Delete` | `rm` |
| Find text | `Search` | `grep` |

### Path Syntax

MPW uses `:` as path separator:
- Absolute: `HD:Folder:File`
- Relative: `:Subfolder:File`
- Parent: `::`

### Working with Claude

**Be specific**:
```
Good: "Compile hello.c with optimization enabled"
Better: "Use MrC to compile hello.c with -opt speed flag"
```

**Provide context**:
```
"I'm working on a QuickDraw program. Can you help me debug why
the window isn't appearing? Here's the code in window.c"
```

**Use screenshots**:
```
/screenshot
The compiler is showing an error but I can't understand it.
What does this error mean?
```

## Limitations

- Commands must complete within timeout (default 30s)
- Maximum command length: 8KB
- Maximum response size: 64KB
- One command at a time (no background processes)
- No interactive programs (that require user input)

## Examples Repository

See the `examples/` directory for:
- Build automation scripts
- Code analysis workflows
- Screenshot-based debugging
- Multi-step compilation processes

## Getting Help

- Check `docs/TROUBLESHOOTING.md`
- Review MPW documentation
- Ask Claude: "How do I do X in MPW?"

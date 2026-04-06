# AppleBridge Protocol Specification

Version 0.1.0

## Overview

The AppleBridge protocol defines the communication between the host system and the classic Macintosh emulator. It uses a simple text-based protocol over TCP/IP for command execution and a binary format for screenshot transmission.

## Connection

- **Protocol**: TCP/IP
- **Default Port**: 9000
- **Connection Model**: Request-Response (synchronous)
- **Character Encoding**: UTF-8 for text, binary for images

## Message Types

### 1. Command Execution

#### Request Format
```
COMMAND:<length>\n<command>
```

**Fields**:
- `COMMAND:` - Protocol identifier (ASCII)
- `<length>` - Length of command in bytes (ASCII decimal number)
- `\n` - Newline separator
- `<command>` - The actual MPW shell command (UTF-8)

**Example**:
```
COMMAND:11
echo "Hello"
```

#### Response Format
```
STATUS:<exit_code>\n
STDOUT:<length>\n<output>\n
STDERR:<length>\n<errors>\n
\n
```

**Fields**:
- `STATUS:` - Exit code line
- `<exit_code>` - Command exit code (0 = success)
- `STDOUT:` - Standard output section
- `<length>` - Length of stdout in bytes
- `<output>` - Command output
- `STDERR:` - Standard error section
- `<length>` - Length of stderr in bytes
- `<errors>` - Error output
- `\n\n` - Double newline marks end of response

**Example**:
```
STATUS:0
STDOUT:6
Hello

STDERR:0


```

### 2. Screenshot Capture

#### Request Format
```
SCREENSHOT\n
```

**Fields**:
- `SCREENSHOT` - Protocol identifier
- `\n` - Newline terminator

#### Response Format
```
IMAGE:<width>:<height>:<format>:<length>\n<binary_data>
```

**Fields**:
- `IMAGE:` - Protocol identifier
- `<width>` - Image width in pixels
- `<height>` - Image height in pixels
- `<format>` - Image format (BMP, RGB, etc.)
- `<length>` - Length of binary data in bytes
- `\n` - Separator
- `<binary_data>` - Raw image data

**Example Header**:
```
IMAGE:640:480:BMP:307200\n
<binary data follows>
```

## Error Handling

### Protocol Errors

If the Mac daemon cannot parse a request, it responds with:
```
STATUS:-1
STDOUT:0

STDERR:<length>
<error_message>

```

### Common Error Codes

- `0` - Success
- `-1` - General error
- `-2` - Command execution failed
- `-3` - Screenshot capture failed
- `-4` - Protocol parsing error

## Limitations

- **Maximum Command Length**: 8,192 bytes
- **Maximum Response Length**: 65,536 bytes
- **Command Timeout**: 30 seconds (configurable)
- **Connection**: One request per connection (connection closed after response)

## Future Extensions

Potential protocol extensions for future versions:

1. **Persistent Connections**: Keep connection open for multiple commands
2. **Async Responses**: Support for long-running commands
3. **File Transfer**: Upload/download files to/from Mac
4. **Mouse/Keyboard Events**: Simulate user interaction
5. **Compression**: Compress large responses
6. **Authentication**: Add security layer

## Implementation Notes

### For Host Developers

- Always validate response format before parsing
- Implement timeouts for all operations
- Handle partial reads for large responses
- Close connection after each request in current version

### For Mac Developers

- Use Open Transport APIs for network I/O
- Buffer large responses appropriately
- Handle client disconnections gracefully
- Log errors for debugging

## Protocol Version Negotiation

Currently not implemented. Future versions may add:
```
VERSION:0.1.0\n
```

As first line of connection handshake.

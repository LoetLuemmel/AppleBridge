# Next Steps for AppleBridge

You now have complete starter code templates for the AppleBridge project. Here's what to do next.

**Your Setup**: Basilisk II with System 7.6.1 and TCP/IP ✓

System 7.6.1 includes Open Transport, so the code templates should work perfectly!

## Immediate Next Steps

### 1. Verify Network Connectivity

First, make sure you can connect from host to Basilisk II:

1. **Check Mac's IP address**:
   - In Basilisk II: Apple Menu → Control Panels → TCP/IP
   - Note the IP address (usually 10.0.2.15 with default networking)

2. **Test from host**:
   ```bash
   ping <mac_ip_address>
   ```
   If ping doesn't work, that's okay - Basilisk networking can be tricky with ICMP

3. **Verify port forwarding** (if needed):
   - Basilisk II usually uses "slirp" networking
   - You may need to configure port forwarding from localhost:9000 → Mac:9000
   - Check your Basilisk II preferences/documentation

### 2. Install MPW in Basilisk II

1. **Download MPW**:
   - MPW Golden Master Release
   - Available from Apple or Macintosh Garden

2. **Install on emulated Mac**:
   - Copy to hard drive image
   - Recommended location: `HD:MPW:`

3. **Test MPW**:
   - Launch MPW Shell
   - Try: `echo "Hello"`
   - Verify compiler: `MrC -version`

### 3. Build the Mac Daemon

**Critical**: The template provided uses some placeholder code. You'll need to:

1. **Transfer files to Mac**:
   ```
   # On host, the files are in: mac/
   # Copy to Mac at: HD:MPW:AppleBridge:
   ```

2. **Review command.c**:
   - The `system()` call is a placeholder
   - Replace with one of these approaches:
     - MPW `Execute()` function
     - Apple Events to MPW Shell
     - Implement as MPW tool with direct shell access
   - See comments in command.c for guidance

3. **Compile**:
   ```
   # In MPW Shell:
   Directory HD:MPW:AppleBridge:
   make dirs
   make
   ```

4. **Fix compilation errors**:
   - Check header paths
   - Verify library links
   - Ensure Open Transport is available

### 4. Test the Connection

1. **Start Mac daemon**:
   ```
   # In MPW Shell:
   bin:AppleBridge
   ```

2. **On host, test**:
   ```bash
   cd host/
   uv pip install -e .
   uv run tests/test_connection.py
   ```

3. **Debug connection issues**:
   - Check port 9000 is accessible
   - Verify emulator networking
   - Check firewall settings

### 5. Integrate with Claude

1. **Get Claude API key**:
   - Sign up at https://console.anthropic.com
   - Create API key
   - Add credits

2. **Configure**:
   ```bash
   cp .env.example .env
   # Edit .env with your API key
   ```

3. **Test**:
   ```bash
   uv run main.py --test
   uv run main.py
   ```

## Code That Needs Implementation

### Priority 1: Command Execution (mac/src/command.c)

The current implementation uses a placeholder `system()` call. Replace with:

```c
// Option A: MPW Execute() - Recommended
#include <EPPC.h>

OSErr ExecuteMPWCommand(const char *command, char *output) {
    // Use MPW Execute() API
    // See Inside Macintosh: Interapplication Communication
}

// Option B: Apple Events to MPW Shell
OSErr SendToMPWShell(const char *command, char *output) {
    // Build Apple Event
    // Send 'misc'/'dosc' to MPW Shell process
    // Wait for reply
}
```

### Priority 2: Screenshot Optimization (mac/src/screenshot.c)

Current implementation captures raw pixels. Improve:

1. **Add BMP encoding**:
   - Convert RGB to BMP format
   - Add proper headers
   - Handle different bit depths

2. **Compression**:
   - Consider RLE compression
   - Or send to host for conversion

3. **Error handling**:
   - Handle different screen modes
   - Support multiple monitors

### Priority 3: Protocol Robustness (both sides)

1. **Add message framing**:
   - Current implementation is basic
   - Add proper delimiters
   - Handle partial reads/writes

2. **Timeout handling**:
   - Implement proper timeouts
   - Clean up on disconnect

3. **Error recovery**:
   - Reconnection logic
   - Graceful degradation

## Testing Strategy

### Phase 1: Basic TCP
- [ ] Can connect from host to Mac
- [ ] Can send/receive simple strings
- [ ] Connection cleanup works

### Phase 2: Command Execution
- [ ] Can execute simple MPW command (echo)
- [ ] Can retrieve stdout
- [ ] Can handle errors
- [ ] Can execute longer commands (compile)

### Phase 3: Claude Integration
- [ ] Claude can execute commands
- [ ] Multi-turn conversation works
- [ ] Tool results are formatted correctly

### Phase 4: Screenshots
- [ ] Can capture screenshot
- [ ] Can transfer to host
- [ ] Claude can analyze images
- [ ] Performance is acceptable

## Known Challenges and Solutions

### Challenge: MPW Command Execution

**Problem**: No direct `system()` equivalent in MPW
**Solution**: Use Execute() from EPPC.h or Apple Events

**Resources**:
- Inside Macintosh: Interapplication Communication
- MPW documentation on Execute()
- Example code in MPW SDK

### Challenge: Network Latency

**Problem**: Emulator adds latency
**Solution**:
- Use async operations where possible
- Implement command queuing
- Cache results

### Challenge: Memory Constraints

**Problem**: Classic Mac has limited memory
**Solution**:
- Stream large responses
- Limit screenshot size
- Implement memory monitoring

### Challenge: Character Encoding

**Problem**: Mac Roman vs UTF-8
**Solution**:
- Implement proper encoding conversion
- Handle special characters
- Test with various inputs

## Resources

### MPW Development
- Inside Macintosh (entire series)
- MPW C Reference
- Open Transport Programmer's Guide
- Mac OS 8/9 SDK documentation

### Modern Development
- Anthropic API docs: https://docs.anthropic.com
- Claude Tool Use guide
- Python socket programming

### Community
- Emaculation forums (emulator help)
- Vintage Mac communities
- Classic Mac developer forums

## Alternative Approaches

If the full implementation proves challenging:

### Minimal Viable Product
1. Skip screenshots initially
2. Implement read-only commands first
3. Use simple text protocol only
4. Add features incrementally

### Different Architecture
1. Use shared folder instead of TCP
2. Batch mode (write commands to file, read results)
3. HTTP instead of raw TCP
4. Use existing Mac telnet server

## Success Metrics

You'll know you're successful when:

1. **Basic**: Can execute `echo "hello"` and get result
2. **Functional**: Can compile a C file via Claude
3. **Complete**: Can debug code using screenshots
4. **Advanced**: Can have multi-turn conversation about Mac development

## Getting Help

If you get stuck:

1. **Check logs**: Enable debug mode on both sides
2. **Simplify**: Test each component individually
3. **Document**: Keep notes on what works/doesn't
4. **Ask**: Classic Mac communities are helpful

## Have Fun!

This is a unique project bridging 30 years of computing. The templates give you a solid foundation. Now bring it to life!

Good luck!

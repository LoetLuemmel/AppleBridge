# MacintoshBridgeHost

Native macOS Swift app to host the MacintoshBridge connection from Basilisk II.

## Features

- **TCP Server** on port 9000 for Mac client connections
- **Screen Capture** with proper macOS permissions
- **ToolServer Integration** via Apple Events
- **Status Window** showing connection state

## Setup in Xcode

1. Open Xcode and create a new project:
   - macOS → App
   - Product Name: `MacintoshBridgeHost`
   - Team: Your team or Personal Team
   - Organization Identifier: `com.yourname`
   - Interface: AppKit (Storyboard not needed)
   - Language: Swift
   - Uncheck "Use Core Data" and "Include Tests"

2. Delete the auto-generated files:
   - Delete `ViewController.swift`
   - Delete `Main.storyboard`

3. Copy the Swift files from this directory:
   - `AppDelegate.swift`
   - `TCPServer.swift`
   - `ScreenCapture.swift`
   - `CommandHandler.swift`

4. Configure the project:
   - Select project in navigator → Target → General
   - Set Deployment Target to macOS 12.0 or higher
   - Under "App Icons and Launch Images", clear "Main Interface"

5. Add entitlements:
   - Select project → Target → Signing & Capabilities
   - Click "+ Capability"
   - Add "Incoming Connections (Server)"
   - Add "Outgoing Connections (Client)"
   - Or manually copy `MacintoshBridgeHost.entitlements` and reference it

6. Update Info.plist:
   - Copy content from `MacintoshBridgeHost/Info.plist`
   - Or manually add the privacy description keys

7. Build and run!

## Permissions

On first run, the app will request:

1. **Screen Recording** - Required to capture Basilisk II window
   - System Preferences → Privacy & Security → Screen Recording
   - Enable MacintoshBridgeHost

2. **Network** - macOS may prompt to allow incoming connections
   - Click "Allow" when prompted

3. **Automation** - For sending commands to ToolServer
   - System Preferences → Privacy & Security → Automation
   - Enable MacintoshBridgeHost → ToolServer

## Usage

1. Start MacintoshBridgeHost
2. Start Basilisk II with ToolServer running
3. Launch MacintoshBridge client in the emulator
4. The client will connect to port 9000

## Commands

The server accepts:
- **SCREENSHOT** - Captures Basilisk II window and returns PNG data
- **PING** - Returns PONG (connection test)
- Any other command is forwarded to ToolServer via Apple Events

## Protocol

### Request Format
Commands are sent as text, terminated by `\r\n\r\n` or `\n\n`

### Response Format
```
STATUS:<exit_code>
STDOUT:<length>
<stdout_data>
STDERR:<length>
<stderr_data>

```

### Screenshot Response
```
SCREENSHOT:<length>
<png_data>
```

## Building from Command Line

```bash
xcodebuild -project MacintoshBridgeHost.xcodeproj -scheme MacintoshBridgeHost -configuration Release
```

## Troubleshooting

### "Screen Recording permission not granted"
Go to System Preferences → Privacy & Security → Screen Recording and enable MacintoshBridgeHost.

### "Connection refused"
Make sure no other app is using port 9000. Check with:
```bash
lsof -i :9000
```

### ToolServer not responding
Ensure ToolServer is running in Basilisk II before sending commands.

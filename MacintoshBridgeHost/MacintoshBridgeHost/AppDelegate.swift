import Cocoa
import Network

@main
class AppDelegate: NSObject, NSApplicationDelegate {

    var window: NSWindow!
    var statusLabel: NSTextField!
    var connectionLabel: NSTextField!
    var lastCommandLabel: NSTextField!
    var screenshotButton: NSButton!

    var tcpServer: TCPServer?
    var controlServer: LocalControlServer?
    var screenCapture: ScreenCapture?

    func applicationDidFinishLaunching(_ notification: Notification) {
        // Request screen capture permission on launch
        screenCapture = ScreenCapture()
        screenCapture?.requestPermission()

        // Purge screenshots older than 24 hours
        screenCapture?.purgeOldScreenshots()

        // Create main window
        setupWindow()

        // Start Mac daemon server (port 9000)
        startMacDaemonServer()

        // Start MCP control server (port 9001)
        startControlServer()
    }

    func applicationWillTerminate(_ notification: Notification) {
        tcpServer?.stop()
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        return true
    }

    // MARK: - Window Setup

    private func setupWindow() {
        let windowRect = NSRect(x: 0, y: 0, width: 400, height: 300)
        window = NSWindow(
            contentRect: windowRect,
            styleMask: [.titled, .closable, .miniaturizable],
            backing: .buffered,
            defer: false
        )
        window.title = "MacintoshBridge Host"
        window.center()

        let contentView = NSView(frame: windowRect)
        window.contentView = contentView

        // Title
        let titleLabel = createLabel(text: "MacintoshBridge Host Server", bold: true, size: 16)
        titleLabel.frame = NSRect(x: 20, y: 250, width: 360, height: 30)
        contentView.addSubview(titleLabel)

        // Status
        statusLabel = createLabel(text: "Status: Starting...", bold: false, size: 13)
        statusLabel.frame = NSRect(x: 20, y: 210, width: 360, height: 25)
        contentView.addSubview(statusLabel)

        // Connection
        connectionLabel = createLabel(text: "Connection: None", bold: false, size: 13)
        connectionLabel.frame = NSRect(x: 20, y: 180, width: 360, height: 25)
        contentView.addSubview(connectionLabel)

        // Last command
        lastCommandLabel = createLabel(text: "Last Command: -", bold: false, size: 13)
        lastCommandLabel.frame = NSRect(x: 20, y: 150, width: 360, height: 25)
        contentView.addSubview(lastCommandLabel)

        // Screenshot button
        screenshotButton = NSButton(frame: NSRect(x: 20, y: 100, width: 150, height: 32))
        screenshotButton.title = "Capture Screenshot"
        screenshotButton.bezelStyle = .rounded
        screenshotButton.target = self
        screenshotButton.action = #selector(captureScreenshot)
        contentView.addSubview(screenshotButton)

        // Version label
        let versionLabel = createLabel(text: "v1.0 - Built with Claude", bold: false, size: 11)
        versionLabel.frame = NSRect(x: 20, y: 20, width: 360, height: 20)
        versionLabel.textColor = .secondaryLabelColor
        contentView.addSubview(versionLabel)

        window.makeKeyAndOrderFront(nil)
    }

    private func createLabel(text: String, bold: Bool, size: CGFloat) -> NSTextField {
        let label = NSTextField(labelWithString: text)
        label.font = bold ? NSFont.boldSystemFont(ofSize: size) : NSFont.systemFont(ofSize: size)
        label.isEditable = false
        label.isSelectable = false
        label.isBordered = false
        label.backgroundColor = .clear
        return label
    }

    // MARK: - Server

    private func startMacDaemonServer() {
        tcpServer = TCPServer(port: 9000)
        tcpServer?.delegate = self
        tcpServer?.start()

        // Link CommandHandler with Mac daemon server
        CommandHandler.shared.macDaemonServer = tcpServer

        updateStatus("Mac daemon: Listening on port 9000")
    }

    private func startControlServer() {
        controlServer = LocalControlServer(port: 9001)
        controlServer?.delegate = self
        controlServer?.macDaemonServer = tcpServer
        controlServer?.start()

        updateStatus("MCP control: Listening on port 9001")
    }

    // MARK: - Actions

    @objc func captureScreenshot() {
        guard let image = screenCapture?.captureBasiliskWindow() else {
            showAlert(message: "Could not capture Basilisk II window. Make sure it's running and screen recording permission is granted.")
            return
        }

        // Save to dedicated screenshot directory
        let screenshotDir: URL
        do {
            screenshotDir = try screenCapture!.getScreenshotDirectory()
        } catch ScreenCapture.ScreenshotDirectoryError.noSharedFolder {
            showAlert(message: "No shared folder configured.\n\nPlease configure a shared folder in Basilisk II:\n1. Open Basilisk II preferences\n2. Go to Volumes tab\n3. Set a \"Unix Root\" folder for file sharing")
            return
        } catch ScreenCapture.ScreenshotDirectoryError.sharedFolderNotFound(let path) {
            showAlert(message: "Shared folder not found:\n\(path)\n\nPlease create this folder or update Basilisk II preferences.")
            return
        } catch {
            showAlert(message: "Could not access screenshot directory: \(error.localizedDescription)")
            return
        }

        let timestamp = ISO8601DateFormatter().string(from: Date())
            .replacingOccurrences(of: ":", with: "-")
        let fileURL = screenshotDir.appendingPathComponent("basilisk_\(timestamp).png")

        if screenCapture?.saveImage(image, to: fileURL) == true {
            showAlert(message: "Screenshot saved to:\n\(fileURL.path)")
        } else {
            showAlert(message: "Failed to save screenshot")
        }
    }

    // MARK: - UI Updates

    func updateStatus(_ status: String) {
        DispatchQueue.main.async {
            self.statusLabel.stringValue = "Status: \(status)"
        }
    }

    func updateConnection(_ connection: String) {
        DispatchQueue.main.async {
            self.connectionLabel.stringValue = "Connection: \(connection)"
        }
    }

    func updateLastCommand(_ command: String) {
        DispatchQueue.main.async {
            let truncated = command.count > 40 ? String(command.prefix(40)) + "..." : command
            self.lastCommandLabel.stringValue = "Last Command: \(truncated)"
        }
    }

    private func showAlert(message: String) {
        let alert = NSAlert()
        alert.messageText = "MacintoshBridge"
        alert.informativeText = message
        alert.alertStyle = .informational
        alert.addButton(withTitle: "OK")
        alert.runModal()
    }
}

// MARK: - TCPServerDelegate

extension AppDelegate: TCPServerDelegate {
    func serverDidStart() {
        updateStatus("Mac daemon: Listening on port 9000")
    }

    func serverDidAcceptConnection(from address: String) {
        updateConnection("Mac: \(address)")
        updateStatus("Mac daemon: Connected")
    }

    func serverDidDisconnect() {
        updateConnection("Mac: None")
        updateStatus("Mac daemon: Listening on port 9000")
    }

    func serverDidReceiveCommand(_ command: String) {
        updateLastCommand("Mac: \(command)")
    }

    func serverDidEncounterError(_ error: Error) {
        updateStatus("Mac daemon error: \(error.localizedDescription)")
    }
}

// MARK: - LocalControlServerDelegate

extension AppDelegate: LocalControlServerDelegate {
    func controlServerDidStart() {
        updateStatus("MCP control: Listening on port 9001")
    }

    func controlServerDidAcceptConnection(from address: String) {
        updateConnection("MCP: \(address)")
        updateStatus("MCP control: Connected")
    }

    func controlServerDidDisconnect() {
        updateConnection("MCP: None")
        updateStatus("MCP control: Listening on port 9001")
    }

    func controlServerDidReceiveCommand(_ command: String) {
        updateLastCommand("MCP: \(command)")
    }

    func controlServerDidEncounterError(_ error: Error) {
        updateStatus("MCP control error: \(error.localizedDescription)")
    }
}

import Foundation

class CommandHandler {

    static let shared = CommandHandler()

    private let screenCapture = ScreenCapture()

    private init() {}

    // MARK: - Command Processing

    func handle(command: String, server: TCPServer) {
        let trimmed = command.trimmingCharacters(in: .whitespacesAndNewlines)

        // Check for special commands
        if trimmed.uppercased() == "SCREENSHOT" {
            handleScreenshot(server: server)
            return
        }

        if trimmed.uppercased() == "PING" {
            sendResponse("PONG", to: server)
            return
        }

        // Forward command to ToolServer via AppleScript
        forwardToToolServer(command: trimmed, server: server)
    }

    // MARK: - Screenshot Handling

    private func handleScreenshot(server: TCPServer) {
        guard let image = screenCapture.captureBasiliskWindow() else {
            sendResponse("ERROR:Could not capture Basilisk II window", to: server)
            return
        }

        guard let imageData = screenCapture.getImageData(image, format: .png) else {
            sendResponse("ERROR:Could not encode image", to: server)
            return
        }

        // Send image with header: SCREENSHOT:<length>\r\n<data>
        let header = "SCREENSHOT:\(imageData.count)\r\n"
        guard let headerData = header.data(using: .utf8) else {
            sendResponse("ERROR:Internal error", to: server)
            return
        }

        var responseData = Data()
        responseData.append(headerData)
        responseData.append(imageData)

        server.send(data: responseData) { error in
            if let error = error {
                print("Failed to send screenshot: \(error)")
            } else {
                print("Screenshot sent (\(imageData.count) bytes)")
            }
        }
    }

    // MARK: - ToolServer Forwarding

    private func forwardToToolServer(command: String, server: TCPServer) {
        // Use osascript to send Apple Event to ToolServer
        let script = """
        tell application "ToolServer"
            do script "\(escapeForAppleScript(command))"
        end tell
        """

        let task = Process()
        task.executableURL = URL(fileURLWithPath: "/usr/bin/osascript")
        task.arguments = ["-e", script]

        let stdoutPipe = Pipe()
        let stderrPipe = Pipe()
        task.standardOutput = stdoutPipe
        task.standardError = stderrPipe

        do {
            try task.run()
            task.waitUntilExit()

            let stdoutData = stdoutPipe.fileHandleForReading.readDataToEndOfFile()
            let stderrData = stderrPipe.fileHandleForReading.readDataToEndOfFile()

            let stdout = String(data: stdoutData, encoding: .utf8) ?? ""
            let stderr = String(data: stderrData, encoding: .utf8) ?? ""

            let status = task.terminationStatus

            // Format response similar to Python version
            let response = formatResponse(status: Int(status), stdout: stdout, stderr: stderr)
            sendResponse(response, to: server)

        } catch {
            sendResponse("ERROR:Failed to execute command - \(error.localizedDescription)", to: server)
        }
    }

    // MARK: - Response Formatting

    private func formatResponse(status: Int, stdout: String, stderr: String) -> String {
        let stdoutTrimmed = stdout.trimmingCharacters(in: .whitespacesAndNewlines)
        let stderrTrimmed = stderr.trimmingCharacters(in: .whitespacesAndNewlines)

        var response = "STATUS:\(status)\r\n"
        response += "STDOUT:\(stdoutTrimmed.count)\r\n"
        response += stdoutTrimmed
        response += "\r\n"
        response += "STDERR:\(stderrTrimmed.count)\r\n"
        response += stderrTrimmed
        response += "\r\n\r\n"

        return response
    }

    private func sendResponse(_ response: String, to server: TCPServer) {
        server.sendString(response) { error in
            if let error = error {
                print("Failed to send response: \(error)")
            }
        }
    }

    private func escapeForAppleScript(_ string: String) -> String {
        // Escape backslashes and quotes for AppleScript
        return string
            .replacingOccurrences(of: "\\", with: "\\\\")
            .replacingOccurrences(of: "\"", with: "\\\"")
    }
}

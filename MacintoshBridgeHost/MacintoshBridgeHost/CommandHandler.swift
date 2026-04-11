import Foundation

class CommandHandler {

    static let shared = CommandHandler()

    private let screenCapture = ScreenCapture()
    weak var macDaemonServer: TCPServer?  // Reference to Mac daemon connection

    // Pending response handler
    private var pendingResponseHandler: ((String) -> Void)?
    private let responseQueue = DispatchQueue(label: "com.macintoshbridge.response")

    private init() {}

    // MARK: - Command Processing

    // New completion-handler based API for both servers
    func handle(command: String, completion: @escaping (String) -> Void) {
        let trimmed = command.trimmingCharacters(in: .whitespacesAndNewlines)

        // Check for special commands
        if trimmed.uppercased() == "SCREENSHOT" {
            handleScreenshot(completion: completion)
            return
        }

        if trimmed.uppercased() == "PING" {
            completion(formatResponse(status: 0, stdout: "PONG", stderr: ""))
            return
        }

        // Forward command to Mac daemon
        forwardToMacDaemon(command: trimmed, completion: completion)
    }

    // Legacy method for backward compatibility with TCPServer
    func handle(command: String, server: TCPServer) {
        handle(command: command) { response in
            server.sendString(response) { error in
                if let error = error {
                    print("Failed to send response: \(error)")
                }
            }
        }
    }

    // MARK: - Screenshot Handling

    private func handleScreenshot(completion: @escaping (String) -> Void) {
        guard let image = screenCapture.captureBasiliskWindow() else {
            completion("ERROR:Could not capture Basilisk II window\n\n")
            return
        }

        guard let imageData = screenCapture.getImageData(image, format: .png) else {
            completion("ERROR:Could not encode image\n\n")
            return
        }

        // Encode as base64 for text transport
        let base64 = imageData.base64EncodedString()

        // Return in MCP format (STATUS/STDOUT/STDERR)
        let response = formatResponse(status: 0, stdout: base64, stderr: "")
        completion(response)
    }

    // MARK: - Mac Daemon Forwarding

    private func forwardToMacDaemon(command: String, completion: @escaping (String) -> Void) {
        guard let macServer = macDaemonServer, macServer.connection != nil else {
            completion("ERROR:Mac daemon not connected\n\n")
            return
        }

        // Format command for Mac daemon protocol
        guard let commandData = command.data(using: .macOSRoman) else {
            completion("ERROR:Invalid command encoding\n\n")
            return
        }

        let header = "COMMAND:\(commandData.count)\n".data(using: .ascii)!
        var packet = Data()
        packet.append(header)
        packet.append(commandData)

        // Store the completion handler for when response arrives
        responseQueue.sync {
            pendingResponseHandler = completion
        }

        // Send to Mac daemon
        macServer.send(data: packet) { error in
            if let error = error {
                self.responseQueue.sync {
                    self.pendingResponseHandler = nil
                }
                completion("ERROR:Failed to send to Mac: \(error.localizedDescription)\n\n")
            } else {
                print("Command forwarded to Mac daemon, waiting for response...")

                // Set timeout for response (30 seconds)
                DispatchQueue.global().asyncAfter(deadline: .now() + 30.0) {
                    self.responseQueue.sync {
                        if self.pendingResponseHandler != nil {
                            print("Response timeout - no response from Mac daemon")
                            self.pendingResponseHandler = nil
                            completion("ERROR:Timeout waiting for Mac daemon response\n\n")
                        }
                    }
                }
            }
        }
    }

    // MARK: - Response Handling

    // Called by TCPServer when Mac daemon sends a response
    func handleMacDaemonResponse(_ response: String) {
        print("Received response from Mac daemon: \(response.prefix(100))...")

        responseQueue.sync {
            if let handler = pendingResponseHandler {
                handler(response)
                pendingResponseHandler = nil
            } else {
                print("Warning: Received response but no pending handler")
            }
        }
    }

    // MARK: - Response Formatting

    private func formatResponse(status: Int, stdout: String, stderr: String) -> String {
        let stdoutTrimmed = stdout.trimmingCharacters(in: .whitespacesAndNewlines)
        let stderrTrimmed = stderr.trimmingCharacters(in: .whitespacesAndNewlines)

        var response = "STATUS:\(status)\n"
        response += "STDOUT:\(stdoutTrimmed.count)\n"
        response += stdoutTrimmed
        response += "\n"
        response += "STDERR:\(stderrTrimmed.count)\n"
        response += stderrTrimmed
        response += "\n\n"

        return response
    }
}

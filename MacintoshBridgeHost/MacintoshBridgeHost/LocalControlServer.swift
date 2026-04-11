import Foundation
import Network

protocol LocalControlServerDelegate: AnyObject {
    func controlServerDidStart()
    func controlServerDidAcceptConnection(from address: String)
    func controlServerDidDisconnect()
    func controlServerDidReceiveCommand(_ command: String)
    func controlServerDidEncounterError(_ error: Error)
}

class LocalControlServer {

    private let port: UInt16
    private var listener: NWListener?
    private var connections: [NWConnection] = []
    private let queue = DispatchQueue(label: "com.macintoshbridge.controlserver")

    weak var delegate: LocalControlServerDelegate?
    weak var macDaemonServer: TCPServer?  // Reference to Mac connection for forwarding

    // Current command buffer per connection
    private var receiveBuffers: [ObjectIdentifier: Data] = [:]

    init(port: UInt16 = 9001) {
        self.port = port
    }

    // MARK: - Public Methods

    func start() {
        do {
            // Create TCP listener for localhost only
            let parameters = NWParameters.tcp
            parameters.allowLocalEndpointReuse = true
            parameters.prohibitedInterfaceTypes = [.wifi, .cellular, .wiredEthernet]  // Localhost only

            listener = try NWListener(using: parameters, on: NWEndpoint.Port(rawValue: port)!)

            listener?.stateUpdateHandler = { [weak self] state in
                self?.handleListenerState(state)
            }

            listener?.newConnectionHandler = { [weak self] connection in
                self?.handleNewConnection(connection)
            }

            listener?.start(queue: queue)

        } catch {
            delegate?.controlServerDidEncounterError(error)
        }
    }

    func stop() {
        connections.forEach { $0.cancel() }
        connections.removeAll()
        listener?.cancel()
        listener = nil
        receiveBuffers.removeAll()
    }

    func send(data: Data, to connection: NWConnection, completion: @escaping (Error?) -> Void) {
        connection.send(content: data, completion: .contentProcessed { error in
            completion(error)
        })
    }

    func sendString(_ string: String, to connection: NWConnection, completion: @escaping (Error?) -> Void) {
        guard let data = string.data(using: .utf8) else {
            completion(NSError(domain: "LocalControlServer", code: -2, userInfo: [NSLocalizedDescriptionKey: "Invalid string encoding"]))
            return
        }
        send(data: data, to: connection, completion: completion)
    }

    // MARK: - Private Methods

    private func handleListenerState(_ state: NWListener.State) {
        switch state {
        case .ready:
            print("Control server listening on port \(port)")
            DispatchQueue.main.async {
                self.delegate?.controlServerDidStart()
            }

        case .failed(let error):
            print("Control server failed: \(error)")
            DispatchQueue.main.async {
                self.delegate?.controlServerDidEncounterError(error)
            }

        case .cancelled:
            print("Control server cancelled")

        default:
            break
        }
    }

    private func handleNewConnection(_ newConnection: NWConnection) {
        // Allow multiple connections from localhost
        print("Control server: New connection")

        connections.append(newConnection)
        let connectionID = ObjectIdentifier(newConnection)
        receiveBuffers[connectionID] = Data()

        newConnection.stateUpdateHandler = { [weak self] state in
            self?.handleConnectionState(state, for: newConnection)
        }

        newConnection.start(queue: queue)
        startReceiving(from: newConnection)

        // Get remote address
        if let endpoint = newConnection.currentPath?.remoteEndpoint,
           case let NWEndpoint.hostPort(host, port) = endpoint {
            let address = "\(host):\(port)"
            DispatchQueue.main.async {
                self.delegate?.controlServerDidAcceptConnection(from: address)
            }
        }
    }

    private func handleConnectionState(_ state: NWConnection.State, for connection: NWConnection) {
        switch state {
        case .ready:
            print("Control connection ready")

        case .failed(let error):
            print("Control connection failed: \(error)")
            removeConnection(connection)

        case .cancelled:
            print("Control connection cancelled")
            removeConnection(connection)

        default:
            break
        }
    }

    private func removeConnection(_ connection: NWConnection) {
        let connectionID = ObjectIdentifier(connection)
        receiveBuffers.removeValue(forKey: connectionID)
        connections.removeAll { $0 === connection }

        DispatchQueue.main.async {
            self.delegate?.controlServerDidDisconnect()
        }
    }

    private func startReceiving(from connection: NWConnection) {
        connection.receive(minimumIncompleteLength: 1, maximumLength: 65536) { [weak self] data, _, isComplete, error in
            guard let self = self else { return }

            let connectionID = ObjectIdentifier(connection)

            if let data = data, !data.isEmpty {
                if var buffer = self.receiveBuffers[connectionID] {
                    buffer.append(data)
                    self.receiveBuffers[connectionID] = buffer
                    self.processReceivedData(from: connection)
                }
            }

            if isComplete {
                print("Control connection closed by remote")
                connection.cancel()
                self.removeConnection(connection)
            } else if let error = error {
                print("Control receive error: \(error)")
                connection.cancel()
                self.removeConnection(connection)
            } else {
                // Continue receiving
                self.startReceiving(from: connection)
            }
        }
    }

    private func processReceivedData(from connection: NWConnection) {
        let connectionID = ObjectIdentifier(connection)
        guard var buffer = receiveBuffers[connectionID] else { return }

        // Look for command terminator (\n\n or \r\n\r\n)
        if let string = String(data: buffer, encoding: .utf8) {
            // Check for double newline
            if string.contains("\n\n") {
                if let range = string.range(of: "\n\n") {
                    let command = String(string[..<range.lowerBound])
                    // Remove processed command from buffer
                    let remainingString = String(string[range.upperBound...])
                    receiveBuffers[connectionID] = remainingString.data(using: .utf8) ?? Data()
                    handleCommand(command, from: connection)
                    return
                }
            } else if string.contains("\r\n\r\n") {
                if let range = string.range(of: "\r\n\r\n") {
                    let command = String(string[..<range.lowerBound])
                    let remainingString = String(string[range.upperBound...])
                    receiveBuffers[connectionID] = remainingString.data(using: .utf8) ?? Data()
                    handleCommand(command, from: connection)
                    return
                }
            }
        }
    }

    private func handleCommand(_ command: String, from connection: NWConnection) {
        let trimmed = command.trimmingCharacters(in: .whitespacesAndNewlines)
        print("Control server received command: \(trimmed)")

        DispatchQueue.main.async {
            self.delegate?.controlServerDidReceiveCommand(trimmed)
        }

        // Process command through CommandHandler
        processCommand(trimmed, from: connection)
    }

    private func processCommand(_ command: String, from connection: NWConnection) {
        // Check for special commands
        if command.uppercased() == "PING" {
            sendResponse("PONG\n\n", to: connection)
            return
        }

        if command.uppercased() == "SCREENSHOT" {
            handleScreenshot(from: connection)
            return
        }

        // Forward command to Mac daemon via TCPServer
        forwardToMacDaemon(command, from: connection)
    }

    private func handleScreenshot(from connection: NWConnection) {
        let screenCapture = ScreenCapture()

        guard let image = screenCapture.captureBasiliskWindow() else {
            sendResponse("ERROR:Could not capture Basilisk II window\n\n", to: connection)
            return
        }

        guard let imageData = screenCapture.getImageData(image, format: .png) else {
            sendResponse("ERROR:Could not encode image\n\n", to: connection)
            return
        }

        // Encode as base64 for text transport
        let base64 = imageData.base64EncodedString()

        // Send response in MCP format
        let response = """
        STATUS:0
        STDOUT:\(base64.count)
        \(base64)
        STDERR:0


        """

        sendResponse(response, to: connection)
    }

    private func forwardToMacDaemon(_ command: String, from connection: NWConnection) {
        guard let macServer = macDaemonServer else {
            sendResponse("ERROR:Mac daemon not connected\n\n", to: connection)
            return
        }

        // Check if Mac is connected
        guard macServer.connection != nil else {
            sendResponse("ERROR:Mac daemon not connected\n\n", to: connection)
            return
        }

        // Use CommandHandler to send command and handle response
        CommandHandler.shared.sendCommand(command) { [weak self] response in
            guard let self = self else { return }

            // Forward Mac daemon's response back to MCP client
            self.sendResponse(response, to: connection)
        }
    }

    private func sendResponse(_ response: String, to connection: NWConnection) {
        guard let data = response.data(using: .utf8) else { return }

        connection.send(content: data, completion: .contentProcessed { error in
            if let error = error {
                print("Failed to send response: \(error)")
            }
        })
    }
}

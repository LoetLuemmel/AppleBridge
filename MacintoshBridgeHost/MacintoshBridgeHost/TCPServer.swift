import Foundation
import Network

protocol TCPServerDelegate: AnyObject {
    func serverDidStart()
    func serverDidAcceptConnection(from address: String)
    func serverDidDisconnect()
    func serverDidReceiveCommand(_ command: String)
    func serverDidEncounterError(_ error: Error)
}

class TCPServer {

    private let port: UInt16
    private var listener: NWListener?
    var connection: NWConnection?  // Made internal for CommandHandler access
    private let queue = DispatchQueue(label: "com.macintoshbridge.tcpserver")

    weak var delegate: TCPServerDelegate?

    // Current command buffer
    private var receiveBuffer = Data()

    init(port: UInt16 = 9000) {
        self.port = port
    }

    // MARK: - Public Methods

    func start() {
        do {
            // Create TCP listener
            let parameters = NWParameters.tcp
            parameters.allowLocalEndpointReuse = true

            listener = try NWListener(using: parameters, on: NWEndpoint.Port(rawValue: port)!)

            listener?.stateUpdateHandler = { [weak self] state in
                self?.handleListenerState(state)
            }

            listener?.newConnectionHandler = { [weak self] connection in
                self?.handleNewConnection(connection)
            }

            listener?.start(queue: queue)

        } catch {
            delegate?.serverDidEncounterError(error)
        }
    }

    func stop() {
        connection?.cancel()
        listener?.cancel()
        connection = nil
        listener = nil
    }

    func send(data: Data, completion: @escaping (Error?) -> Void) {
        guard let connection = connection else {
            completion(NSError(domain: "TCPServer", code: -1, userInfo: [NSLocalizedDescriptionKey: "No connection"]))
            return
        }

        connection.send(content: data, completion: .contentProcessed { error in
            completion(error)
        })
    }

    func sendString(_ string: String, completion: @escaping (Error?) -> Void) {
        guard let data = string.data(using: .utf8) else {
            completion(NSError(domain: "TCPServer", code: -2, userInfo: [NSLocalizedDescriptionKey: "Invalid string encoding"]))
            return
        }
        send(data: data, completion: completion)
    }

    // MARK: - Private Methods

    private func handleListenerState(_ state: NWListener.State) {
        switch state {
        case .ready:
            print("Server listening on port \(port)")
            DispatchQueue.main.async {
                self.delegate?.serverDidStart()
            }

        case .failed(let error):
            print("Server failed: \(error)")
            DispatchQueue.main.async {
                self.delegate?.serverDidEncounterError(error)
            }

        case .cancelled:
            print("Server cancelled")

        default:
            break
        }
    }

    private func handleNewConnection(_ newConnection: NWConnection) {
        // Only accept one connection at a time
        if connection != nil {
            print("Rejecting new connection - already connected")
            newConnection.cancel()
            return
        }

        connection = newConnection

        newConnection.stateUpdateHandler = { [weak self] state in
            self?.handleConnectionState(state)
        }

        newConnection.start(queue: queue)
        startReceiving()

        // Get remote address
        if let endpoint = newConnection.currentPath?.remoteEndpoint,
           case let NWEndpoint.hostPort(host, port) = endpoint {
            let address = "\(host):\(port)"
            DispatchQueue.main.async {
                self.delegate?.serverDidAcceptConnection(from: address)
            }
        }
    }

    private func handleConnectionState(_ state: NWConnection.State) {
        switch state {
        case .ready:
            print("Connection ready")

        case .failed(let error):
            print("Connection failed: \(error)")
            connection = nil
            DispatchQueue.main.async {
                self.delegate?.serverDidDisconnect()
            }

        case .cancelled:
            print("Connection cancelled")
            connection = nil
            DispatchQueue.main.async {
                self.delegate?.serverDidDisconnect()
            }

        default:
            break
        }
    }

    private func startReceiving() {
        connection?.receive(minimumIncompleteLength: 1, maximumLength: 65536) { [weak self] data, _, isComplete, error in
            guard let self = self else { return }

            if let data = data, !data.isEmpty {
                self.receiveBuffer.append(data)
                self.processReceivedData()
            }

            if isComplete {
                print("Connection closed by remote")
                self.connection?.cancel()
                self.connection = nil
                DispatchQueue.main.async {
                    self.delegate?.serverDidDisconnect()
                }
            } else if let error = error {
                print("Receive error: \(error)")
                self.connection?.cancel()
                self.connection = nil
                DispatchQueue.main.async {
                    self.delegate?.serverDidEncounterError(error)
                }
            } else {
                // Continue receiving
                self.startReceiving()
            }
        }
    }

    private func processReceivedData() {
        // Protocol: 4-byte length prefix (big endian) followed by command data
        // Or for compatibility: Look for \r\n\r\n or \n\n terminator

        // Try length-prefixed first
        if receiveBuffer.count >= 4 {
            let length = receiveBuffer.prefix(4).withUnsafeBytes { $0.load(as: UInt32.self).bigEndian }

            if receiveBuffer.count >= 4 + Int(length) {
                let commandData = receiveBuffer.subdata(in: 4..<(4 + Int(length)))
                receiveBuffer.removeFirst(4 + Int(length))

                if let command = String(data: commandData, encoding: .utf8) {
                    handleCommand(command)
                }
                return
            }
        }

        // Fallback: Look for line terminators (compatibility with Python client)
        if let string = String(data: receiveBuffer, encoding: .utf8) {
            if string.contains("\r\n\r\n") || string.contains("\n\n") {
                let separator = string.contains("\r\n\r\n") ? "\r\n\r\n" : "\n\n"
                if let range = string.range(of: separator) {
                    let command = String(string[..<range.lowerBound])
                    receiveBuffer.removeAll()
                    handleCommand(command)
                }
            }
        }
    }

    private func handleCommand(_ command: String) {
        // Check if this is a response from Mac daemon (starts with STATUS:)
        if command.hasPrefix("STATUS:") {
            print("Received response from Mac daemon")
            CommandHandler.shared.handleMacDaemonResponse(command)
            return
        }

        // Otherwise, it's a command
        print("Received command: \(command)")
        DispatchQueue.main.async {
            self.delegate?.serverDidReceiveCommand(command)
        }

        // Process command through CommandHandler
        CommandHandler.shared.handle(command: command, server: self)
    }
}

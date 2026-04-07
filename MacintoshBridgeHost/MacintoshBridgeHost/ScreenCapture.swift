import Foundation
import Cocoa
import ScreenCaptureKit
import UniformTypeIdentifiers

class ScreenCapture {

    // MARK: - Permission

    /// Request screen capture permission
    @discardableResult
    func requestPermission() -> Bool {
        return CGPreflightScreenCaptureAccess()
    }

    /// Check if screen capture permission is granted
    func hasPermission() -> Bool {
        return CGPreflightScreenCaptureAccess()
    }

    // MARK: - Capture using ScreenCaptureKit

    /// Capture the Basilisk II window (synchronous wrapper)
    func captureBasiliskWindow() -> CGImage? {
        var capturedImage: CGImage?
        let semaphore = DispatchSemaphore(value: 0)

        Task {
            capturedImage = await captureBasiliskWindowAsync()
            semaphore.signal()
        }

        _ = semaphore.wait(timeout: .now() + 5.0)
        return capturedImage
    }

    /// Capture the Basilisk II window (async)
    func captureBasiliskWindowAsync() async -> CGImage? {
        do {
            // Get all shareable content
            let content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: true)

            // Find Basilisk II window
            guard let window = content.windows.first(where: { window in
                window.owningApplication?.applicationName.lowercased().contains("basilisk") == true
            }) else {
                print("Basilisk II window not found")
                return nil
            }

            // Create filter for just this window
            let filter = SCContentFilter(desktopIndependentWindow: window)

            // Configure capture
            let config = SCStreamConfiguration()
            config.width = Int(window.frame.width * 2)  // Retina
            config.height = Int(window.frame.height * 2)
            config.scalesToFit = false
            config.showsCursor = false

            // Capture
            let image = try await SCScreenshotManager.captureImage(
                contentFilter: filter,
                configuration: config
            )

            return image

        } catch {
            print("Screen capture error: \(error)")
            return nil
        }
    }

    /// Capture with completion handler
    func captureBasiliskWindow(completion: @escaping (CGImage?) -> Void) {
        Task {
            let image = await captureBasiliskWindowAsync()
            await MainActor.run { completion(image) }
        }
    }

    // MARK: - Save

    /// Save CGImage to file
    func saveImage(_ image: CGImage, to url: URL) -> Bool {
        guard let destination = CGImageDestinationCreateWithURL(
            url as CFURL,
            UTType.png.identifier as CFString,
            1,
            nil
        ) else {
            print("Failed to create image destination")
            return false
        }

        CGImageDestinationAddImage(destination, image, nil)

        if CGImageDestinationFinalize(destination) {
            print("Screenshot saved to: \(url.path)")
            return true
        } else {
            print("Failed to save screenshot")
            return false
        }
    }

    /// Capture and save Basilisk II window to path
    func captureAndSave(to path: String) -> Bool {
        guard let image = captureBasiliskWindow() else {
            return false
        }

        let url = URL(fileURLWithPath: path)
        return saveImage(image, to: url)
    }

    /// Get raw image data (for sending over network)
    func getImageData(_ image: CGImage, format: ImageFormat = .png) -> Data? {
        let data = NSMutableData()

        guard let destination = CGImageDestinationCreateWithData(
            data as CFMutableData,
            format.utType as CFString,
            1,
            nil
        ) else {
            return nil
        }

        CGImageDestinationAddImage(destination, image, nil)

        if CGImageDestinationFinalize(destination) {
            return data as Data
        }

        return nil
    }

    enum ImageFormat {
        case png
        case jpeg

        var utType: String {
            switch self {
            case .png: return UTType.png.identifier
            case .jpeg: return UTType.jpeg.identifier
            }
        }
    }

    // MARK: - Screenshot Directory Management

    enum ScreenshotDirectoryError: Error {
        case noSharedFolder
        case sharedFolderNotFound(String)
    }

    /// Get the screenshot storage directory, creating if needed
    /// Reads Basilisk II prefs to find the shared folder, then uses Screenshots subfolder
    func getScreenshotDirectory() throws -> URL {
        // Try to read shared folder from Basilisk II prefs
        let prefsPath = NSHomeDirectory() + "/.basilisk_ii_prefs"

        guard let prefsContent = try? String(contentsOfFile: prefsPath, encoding: .utf8) else {
            throw ScreenshotDirectoryError.noSharedFolder
        }

        for line in prefsContent.components(separatedBy: .newlines) {
            if line.hasPrefix("extfs ") {
                let sharedPath = String(line.dropFirst(6)) // Remove "extfs "

                // Check if shared folder exists
                var isDir: ObjCBool = false
                guard FileManager.default.fileExists(atPath: sharedPath, isDirectory: &isDir), isDir.boolValue else {
                    throw ScreenshotDirectoryError.sharedFolderNotFound(sharedPath)
                }

                let screenshotDir = URL(fileURLWithPath: sharedPath).appendingPathComponent("Screenshots")

                // Create Screenshots subdirectory if it doesn't exist
                try? FileManager.default.createDirectory(at: screenshotDir, withIntermediateDirectories: true)

                return screenshotDir
            }
        }

        throw ScreenshotDirectoryError.noSharedFolder
    }

    /// Delete screenshots older than specified hours (default 24)
    func purgeOldScreenshots(olderThan hours: Int = 24) {
        guard let screenshotDir = try? getScreenshotDirectory() else {
            // No shared folder configured - nothing to purge
            return
        }

        let cutoff = Date().addingTimeInterval(-Double(hours) * 3600)

        guard let files = try? FileManager.default.contentsOfDirectory(
            at: screenshotDir,
            includingPropertiesForKeys: [.creationDateKey]
        ) else { return }

        var purgedCount = 0
        for file in files {
            guard file.pathExtension.lowercased() == "png" else { continue }

            if let attrs = try? file.resourceValues(forKeys: [.creationDateKey]),
               let created = attrs.creationDate,
               created < cutoff {
                try? FileManager.default.removeItem(at: file)
                purgedCount += 1
            }
        }

        if purgedCount > 0 {
            print("Purged \(purgedCount) old screenshot(s)")
        }
    }
}

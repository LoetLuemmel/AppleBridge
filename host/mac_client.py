"""
Mac Client - TCP connection to the Mac emulator
"""

import socket
import logging
from typing import Optional
from contextlib import contextmanager

from .protocol import (
    CommandRequest, CommandResponse,
    ScreenshotRequest, ScreenshotResponse,
    ProtocolError
)
from .config import AppleBridgeConfig


logger = logging.getLogger(__name__)


class MacClient:
    """Client for communicating with Mac TCP daemon"""

    def __init__(self, config: AppleBridgeConfig):
        self.config = config
        self.socket: Optional[socket.socket] = None
        self._connected = False

    def connect(self) -> None:
        """Establish connection to Mac"""
        logger.info(f"Connecting to Mac at {self.config.mac_host}:{self.config.mac_port}")

        try:
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.socket.settimeout(self.config.connection_timeout)
            self.socket.connect((self.config.mac_host, self.config.mac_port))
            self._connected = True
            logger.info("Connected to Mac successfully")
        except socket.error as e:
            logger.error(f"Failed to connect to Mac: {e}")
            raise ConnectionError(f"Could not connect to Mac: {e}")

    def disconnect(self) -> None:
        """Close connection to Mac"""
        if self.socket:
            try:
                self.socket.close()
            except:
                pass
            self.socket = None
            self._connected = False
            logger.info("Disconnected from Mac")

    def is_connected(self) -> bool:
        """Check if connected to Mac"""
        return self._connected and self.socket is not None

    def execute_command(self, command: str, timeout: Optional[int] = None) -> CommandResponse:
        """Execute a command on the Mac and return the response"""
        if not self.is_connected():
            raise ConnectionError("Not connected to Mac")

        timeout = timeout or self.config.command_timeout

        logger.debug(f"Executing command: {command}")

        # Send command request
        request = CommandRequest(command=command, timeout=timeout)
        try:
            self.socket.sendall(request.encode())
        except socket.error as e:
            logger.error(f"Failed to send command: {e}")
            raise ProtocolError(f"Send failed: {e}")

        # Receive response
        try:
            self.socket.settimeout(timeout)
            response_data = self._receive_all()
            response = CommandResponse.decode(response_data)

            logger.debug(f"Command completed with exit code {response.exit_code}")
            return response

        except socket.timeout:
            logger.error(f"Command timed out after {timeout}s")
            raise TimeoutError(f"Command timed out after {timeout}s")
        except Exception as e:
            logger.error(f"Failed to receive response: {e}")
            raise ProtocolError(f"Receive failed: {e}")

    def get_screenshot(self) -> ScreenshotResponse:
        """Request a screenshot from the Mac"""
        if not self.is_connected():
            raise ConnectionError("Not connected to Mac")

        logger.debug("Requesting screenshot")

        # Send screenshot request
        request = ScreenshotRequest()
        try:
            self.socket.sendall(request.encode())
        except socket.error as e:
            logger.error(f"Failed to send screenshot request: {e}")
            raise ProtocolError(f"Send failed: {e}")

        # Receive screenshot response
        try:
            self.socket.settimeout(30)  # Screenshots may take longer
            response_data = self._receive_all()
            response = ScreenshotResponse.decode(response_data)

            logger.debug(f"Screenshot received: {response.width}x{response.height} {response.format}")
            return response

        except Exception as e:
            logger.error(f"Failed to receive screenshot: {e}")
            raise ProtocolError(f"Screenshot failed: {e}")

    def _receive_all(self, buffer_size: int = 4096) -> bytes:
        """Receive all data until connection closes or delimiter found"""
        data = bytearray()

        while True:
            try:
                chunk = self.socket.recv(buffer_size)
                if not chunk:
                    break
                data.extend(chunk)

                # Check if we've received a complete message
                # For now, we'll use a simple heuristic: look for double newline
                if b'\n\n' in data:
                    break

            except socket.timeout:
                break

        return bytes(data)

    @contextmanager
    def session(self):
        """Context manager for Mac session"""
        try:
            self.connect()
            yield self
        finally:
            self.disconnect()


def test_connection(config: AppleBridgeConfig) -> bool:
    """Test connection to Mac"""
    client = MacClient(config)
    try:
        with client.session():
            response = client.execute_command("echo 'Connection test'")
            return response.exit_code == 0
    except Exception as e:
        logger.error(f"Connection test failed: {e}")
        return False

"""
AppleBridge Protocol Handler
Implements the communication protocol between host and Mac
"""

from dataclasses import dataclass
from typing import Optional
import struct


@dataclass
class CommandRequest:
    """Request to execute a command on Mac"""
    command: str
    timeout: int = 30

    def encode(self) -> bytes:
        """Encode command request for transmission"""
        cmd_bytes = self.command.encode('utf-8')
        length = len(cmd_bytes)

        # Protocol: COMMAND:<length>\n<command>
        header = f"COMMAND:{length}\n".encode('ascii')
        return header + cmd_bytes


@dataclass
class CommandResponse:
    """Response from Mac after executing a command"""
    exit_code: int
    stdout: str
    stderr: str

    @classmethod
    def decode(cls, data: bytes) -> 'CommandResponse':
        """Decode response from Mac.

        Wire format:
            STATUS:<code>\\n STDOUT:<len>\\n <data>\\n STDERR:<len>\\n <data>\\n\\n

        STDOUT/STDERR are read by their declared byte length so MULTI-LINE
        output is captured. (The original implementation kept only the first
        line, truncating any multi-line command output.)
        """
        text = data.decode('utf-8', errors='replace').replace('\r', '\n')
        lines = text.split('\n')

        exit_code = 0
        stdout = ""
        stderr = ""

        i = 0
        while i < len(lines):
            line = lines[i]

            if line.startswith("STATUS:"):
                try:
                    exit_code = int(line[7:])
                except ValueError:
                    pass
                i += 1

            elif line.startswith("STDOUT:") or line.startswith("STDERR:"):
                is_out = line.startswith("STDOUT:")
                try:
                    length = int(line[7:])
                except ValueError:
                    length = 0
                i += 1
                collected = []
                chars = 0
                while i < len(lines) and chars < length:
                    collected.append(lines[i])
                    chars += len(lines[i]) + 1  # +1 for the stripped newline
                    i += 1
                value = '\n'.join(collected)
                if is_out:
                    stdout = value
                else:
                    stderr = value

            else:
                i += 1

        return cls(exit_code=exit_code, stdout=stdout.strip(), stderr=stderr.strip())


@dataclass
class ScreenshotRequest:
    """Request a screenshot from Mac"""

    def encode(self) -> bytes:
        """Encode screenshot request"""
        return b"SCREENSHOT\n"


@dataclass
class ScreenshotResponse:
    """Screenshot response from Mac"""
    width: int
    height: int
    format: str
    data: bytes

    @classmethod
    def decode(cls, data: bytes) -> 'ScreenshotResponse':
        """Decode screenshot response"""
        # Protocol: IMAGE:<width>:<height>:<format>:<length>\n<binary_data>
        header_end = data.find(b'\n')
        if header_end == -1:
            raise ValueError("Invalid screenshot response: no header")

        header = data[:header_end].decode('ascii')
        image_data = data[header_end+1:]

        parts = header.split(':')
        if len(parts) != 5 or parts[0] != "IMAGE":
            raise ValueError(f"Invalid screenshot header: {header}")

        _, width, height, fmt, length = parts

        return cls(
            width=int(width),
            height=int(height),
            format=fmt,
            data=image_data[:int(length)]
        )


class ProtocolError(Exception):
    """Protocol-level error"""
    pass

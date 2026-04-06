"""
AppleBridge Configuration
Manages settings for the host communicator
"""

import os
from dataclasses import dataclass
from typing import Optional


@dataclass
class AppleBridgeConfig:
    """Configuration for AppleBridge host communicator"""

    # Mac emulator connection
    mac_host: str = "localhost"
    mac_port: int = 9000
    connection_timeout: int = 10

    # Claude API
    claude_api_key: Optional[str] = None
    claude_model: str = "claude-sonnet-4-5-20250929"

    # Protocol settings
    max_command_length: int = 8192
    max_response_length: int = 65536
    command_timeout: int = 30

    # Screenshot settings
    screenshot_format: str = "PNG"
    screenshot_max_width: int = 1024
    screenshot_max_height: int = 768

    # Debug settings
    debug: bool = False
    log_level: str = "INFO"

    @classmethod
    def from_env(cls) -> 'AppleBridgeConfig':
        """Load configuration from environment variables"""
        return cls(
            mac_host=os.getenv("APPLEBRIDGE_MAC_HOST", "localhost"),
            mac_port=int(os.getenv("APPLEBRIDGE_MAC_PORT", "9000")),
            connection_timeout=int(os.getenv("APPLEBRIDGE_TIMEOUT", "10")),
            claude_api_key=os.getenv("ANTHROPIC_API_KEY"),
            claude_model=os.getenv("CLAUDE_MODEL", "claude-sonnet-4-5-20250929"),
            debug=os.getenv("APPLEBRIDGE_DEBUG", "").lower() in ("1", "true", "yes"),
            log_level=os.getenv("APPLEBRIDGE_LOG_LEVEL", "INFO"),
        )

    def validate(self) -> None:
        """Validate configuration"""
        if not self.claude_api_key:
            raise ValueError("ANTHROPIC_API_KEY environment variable must be set")

        if self.mac_port < 1 or self.mac_port > 65535:
            raise ValueError(f"Invalid port: {self.mac_port}")

        if self.command_timeout < 1:
            raise ValueError("command_timeout must be positive")

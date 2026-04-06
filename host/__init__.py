"""
AppleBridge Host Communicator
"""

__version__ = "0.1.0"

from .config import AppleBridgeConfig
from .mac_client import MacClient
from .claude_bridge import ClaudeBridge

__all__ = ["AppleBridgeConfig", "MacClient", "ClaudeBridge"]

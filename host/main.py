#!/usr/bin/env python3
"""
AppleBridge Main Entry Point
"""

import sys
import logging
import argparse

from config import AppleBridgeConfig
from claude_bridge import ClaudeBridge
from mac_client import test_connection


def setup_logging(level: str = "INFO") -> None:
    """Configure logging"""
    logging.basicConfig(
        level=getattr(logging, level.upper()),
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler(sys.stdout)
        ]
    )


def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(
        description="AppleBridge - Connect Claude to classic Mac MPW shell"
    )

    parser.add_argument(
        "--test",
        action="store_true",
        help="Test connection to Mac and exit"
    )

    parser.add_argument(
        "--command",
        type=str,
        help="Execute a single MPW command and exit"
    )

    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging"
    )

    args = parser.parse_args()

    # Load configuration
    config = AppleBridgeConfig.from_env()

    if args.debug:
        config.debug = True
        config.log_level = "DEBUG"

    setup_logging(config.log_level)
    logger = logging.getLogger(__name__)

    try:
        config.validate()
    except ValueError as e:
        logger.error(f"Configuration error: {e}")
        return 1

    # Test mode
    if args.test:
        logger.info("Testing connection to Mac...")
        if test_connection(config):
            print("✓ Connection test successful")
            return 0
        else:
            print("✗ Connection test failed")
            return 1

    # Single command mode
    if args.command:
        from mac_client import MacClient

        client = MacClient(config)
        try:
            with client.session():
                response = client.execute_command(args.command)
                print(f"Exit code: {response.exit_code}")
                if response.stdout:
                    print(f"Stdout:\n{response.stdout}")
                if response.stderr:
                    print(f"Stderr:\n{response.stderr}", file=sys.stderr)
                return response.exit_code
        except Exception as e:
            logger.error(f"Command failed: {e}")
            return 1

    # Interactive mode (default)
    bridge = ClaudeBridge(config)
    try:
        bridge.interactive_session()
        return 0
    except Exception as e:
        logger.error(f"Bridge error: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())

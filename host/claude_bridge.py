"""
Claude Bridge - Integration between Claude API and Mac
"""

import logging
from typing import List, Dict, Any, Optional
from io import BytesIO
from PIL import Image
import base64

from anthropic import Anthropic

from .config import AppleBridgeConfig
from .mac_client import MacClient
from .protocol import CommandResponse, ScreenshotResponse


logger = logging.getLogger(__name__)


class ClaudeBridge:
    """Bridge between Claude API and Mac emulator"""

    def __init__(self, config: AppleBridgeConfig):
        self.config = config
        self.mac_client = MacClient(config)
        self.anthropic = Anthropic(api_key=config.claude_api_key)
        self.conversation_history: List[Dict[str, Any]] = []

    def start(self) -> None:
        """Start the bridge session"""
        logger.info("Starting Claude Bridge")
        self.mac_client.connect()

    def stop(self) -> None:
        """Stop the bridge session"""
        logger.info("Stopping Claude Bridge")
        self.mac_client.disconnect()

    def execute_mpw_command(self, command: str) -> CommandResponse:
        """Execute an MPW command and return response"""
        logger.info(f"Executing MPW command: {command}")
        return self.mac_client.execute_command(command)

    def capture_screenshot(self) -> Optional[Image.Image]:
        """Capture screenshot from Mac and convert to PIL Image"""
        try:
            screenshot = self.mac_client.get_screenshot()

            # Convert to PIL Image
            image = Image.frombytes(
                'RGB',
                (screenshot.width, screenshot.height),
                screenshot.data
            )

            # Resize if too large
            if (image.width > self.config.screenshot_max_width or
                image.height > self.config.screenshot_max_height):
                image.thumbnail(
                    (self.config.screenshot_max_width, self.config.screenshot_max_height),
                    Image.Resampling.LANCZOS
                )

            return image

        except Exception as e:
            logger.error(f"Screenshot failed: {e}")
            return None

    def image_to_base64(self, image: Image.Image) -> str:
        """Convert PIL Image to base64 string"""
        buffer = BytesIO()
        image.save(buffer, format=self.config.screenshot_format)
        return base64.b64encode(buffer.getvalue()).decode('utf-8')

    def send_message_to_claude(
        self,
        message: str,
        include_screenshot: bool = False
    ) -> str:
        """Send a message to Claude with optional screenshot"""

        # Build message content
        content = []

        # Add screenshot if requested
        if include_screenshot:
            screenshot = self.capture_screenshot()
            if screenshot:
                content.append({
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": f"image/{self.config.screenshot_format.lower()}",
                        "data": self.image_to_base64(screenshot),
                    }
                })

        # Add text message
        content.append({
            "type": "text",
            "text": message
        })

        # Add to conversation history
        self.conversation_history.append({
            "role": "user",
            "content": content
        })

        # Call Claude API
        logger.info(f"Sending message to Claude: {message[:100]}...")

        response = self.anthropic.messages.create(
            model=self.config.claude_model,
            max_tokens=4096,
            tools=[
                {
                    "name": "execute_mpw_command",
                    "description": "Execute a command in the MPW (Macintosh Programmer's Workshop) shell. Returns stdout, stderr, and exit code.",
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "command": {
                                "type": "string",
                                "description": "The MPW shell command to execute"
                            }
                        },
                        "required": ["command"]
                    }
                },
                {
                    "name": "capture_screenshot",
                    "description": "Capture a screenshot of the current Mac screen state",
                    "input_schema": {
                        "type": "object",
                        "properties": {}
                    }
                }
            ],
            messages=self.conversation_history
        )

        # Process response and handle tool calls
        return self._process_claude_response(response)

    def _process_claude_response(self, response) -> str:
        """Process Claude's response and handle tool calls"""
        assistant_message = []
        tool_results = []

        for block in response.content:
            if block.type == "text":
                assistant_message.append(block.text)

            elif block.type == "tool_use":
                logger.info(f"Claude requested tool: {block.name}")

                # Execute the tool
                if block.name == "execute_mpw_command":
                    command = block.input.get("command")
                    result = self.execute_mpw_command(command)

                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": f"Exit code: {result.exit_code}\n\nStdout:\n{result.stdout}\n\nStderr:\n{result.stderr}"
                    })

                elif block.name == "capture_screenshot":
                    screenshot = self.capture_screenshot()
                    if screenshot:
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": [{
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": f"image/{self.config.screenshot_format.lower()}",
                                    "data": self.image_to_base64(screenshot)
                                }
                            }]
                        })
                    else:
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": "Screenshot capture failed"
                        })

        # Add assistant message to history
        self.conversation_history.append({
            "role": "assistant",
            "content": response.content
        })

        # If there are tool results, continue the conversation
        if tool_results:
            self.conversation_history.append({
                "role": "user",
                "content": tool_results
            })

            # Get next response from Claude
            next_response = self.anthropic.messages.create(
                model=self.config.claude_model,
                max_tokens=4096,
                messages=self.conversation_history
            )

            return self._process_claude_response(next_response)

        # Return the text response
        return "\n".join(assistant_message)

    def interactive_session(self) -> None:
        """Run an interactive session with Claude"""
        print("=== Claude + MPW Bridge ===")
        print("Type your messages to Claude. Commands:")
        print("  /screenshot - Include screenshot with next message")
        print("  /quit - Exit session")
        print()

        try:
            self.start()

            include_screenshot = False

            while True:
                try:
                    user_input = input("You: ").strip()

                    if not user_input:
                        continue

                    if user_input == "/quit":
                        break

                    if user_input == "/screenshot":
                        include_screenshot = True
                        print("Next message will include a screenshot")
                        continue

                    # Send to Claude
                    response = self.send_message_to_claude(
                        user_input,
                        include_screenshot=include_screenshot
                    )

                    print(f"\nClaude: {response}\n")

                    # Reset screenshot flag
                    include_screenshot = False

                except KeyboardInterrupt:
                    print("\n\nInterrupted")
                    break
                except Exception as e:
                    logger.error(f"Error in interactive session: {e}")
                    print(f"Error: {e}")

        finally:
            self.stop()

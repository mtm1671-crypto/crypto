"""Shell command execution plugin for Neuromancy."""

import asyncio

from .base import BasePlugin, PluginManifest, ToolSchema


class ShellPlugin(BasePlugin):
    """Plugin that executes shell commands."""

    DEFAULT_TIMEOUT = 30

    @property
    def manifest(self) -> PluginManifest:
        return PluginManifest(
            name="shell",
            version="0.1.0",
            description="Execute shell commands and capture output.",
            tools=[
                ToolSchema(
                    name="run_command",
                    description="Run a shell command and return its combined stdout and stderr output.",
                    parameters={
                        "type": "object",
                        "properties": {
                            "command": {
                                "type": "string",
                                "description": "The shell command to execute.",
                            },
                            "timeout": {
                                "type": "integer",
                                "description": "Timeout in seconds (default 30).",
                            },
                        },
                        "required": ["command"],
                    },
                    requires_approval=True,
                ),
            ],
        )

    async def execute(self, tool_name: str, arguments: dict) -> str:
        if tool_name != "run_command":
            raise ValueError(f"Unknown tool: {tool_name}")

        command = arguments["command"]
        timeout = arguments.get("timeout", self.DEFAULT_TIMEOUT)

        try:
            process = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            process.kill()
            await process.communicate()
            return f"Command timed out after {timeout} seconds."

        output_parts: list[str] = []
        if stdout:
            output_parts.append(stdout.decode(errors="replace"))
        if stderr:
            output_parts.append(stderr.decode(errors="replace"))

        combined = "".join(output_parts)
        if not combined.strip():
            return f"Command completed with exit code {process.returncode} (no output)."
        return combined

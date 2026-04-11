"""File operations plugin for Neuromancy."""

from pathlib import Path

from .base import BasePlugin, PluginManifest, ToolSchema


class FileOpsPlugin(BasePlugin):
    """Plugin that provides file read, write, and directory listing operations."""

    @property
    def manifest(self) -> PluginManifest:
        return PluginManifest(
            name="file_ops",
            version="0.1.0",
            description="Read, write, and list files and directories.",
            tools=[
                ToolSchema(
                    name="read_file",
                    description="Read the contents of a file and return it as a string.",
                    parameters={
                        "type": "object",
                        "properties": {
                            "path": {
                                "type": "string",
                                "description": "Absolute or relative path to the file to read.",
                            },
                        },
                        "required": ["path"],
                    },
                    requires_approval=False,
                ),
                ToolSchema(
                    name="write_file",
                    description="Write content to a file, creating it if necessary.",
                    parameters={
                        "type": "object",
                        "properties": {
                            "path": {
                                "type": "string",
                                "description": "Absolute or relative path to the file to write.",
                            },
                            "content": {
                                "type": "string",
                                "description": "The content to write to the file.",
                            },
                        },
                        "required": ["path", "content"],
                    },
                    requires_approval=True,
                ),
                ToolSchema(
                    name="list_directory",
                    description="List all entries in a directory.",
                    parameters={
                        "type": "object",
                        "properties": {
                            "path": {
                                "type": "string",
                                "description": "Absolute or relative path to the directory to list.",
                            },
                        },
                        "required": ["path"],
                    },
                    requires_approval=False,
                ),
            ],
        )

    async def execute(self, tool_name: str, arguments: dict) -> str:
        if tool_name == "read_file":
            return await self._read_file(arguments)
        elif tool_name == "write_file":
            return await self._write_file(arguments)
        elif tool_name == "list_directory":
            return await self._list_directory(arguments)
        else:
            raise ValueError(f"Unknown tool: {tool_name}")

    async def _read_file(self, arguments: dict) -> str:
        path = Path(arguments["path"])
        try:
            return path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return f"Error: File not found: {path}"
        except PermissionError:
            return f"Error: Permission denied: {path}"
        except Exception as e:
            return f"Error reading file: {e}"

    async def _write_file(self, arguments: dict) -> str:
        path = Path(arguments["path"])
        content = arguments["content"]
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            return f"Successfully wrote {len(content)} characters to {path}"
        except PermissionError:
            return f"Error: Permission denied: {path}"
        except Exception as e:
            return f"Error writing file: {e}"

    async def _list_directory(self, arguments: dict) -> str:
        path = Path(arguments["path"])
        try:
            entries = sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
            if not entries:
                return f"Directory is empty: {path}"
            lines: list[str] = []
            for entry in entries:
                suffix = "/" if entry.is_dir() else ""
                lines.append(f"{entry.name}{suffix}")
            return "\n".join(lines)
        except FileNotFoundError:
            return f"Error: Directory not found: {path}"
        except NotADirectoryError:
            return f"Error: Not a directory: {path}"
        except PermissionError:
            return f"Error: Permission denied: {path}"
        except Exception as e:
            return f"Error listing directory: {e}"

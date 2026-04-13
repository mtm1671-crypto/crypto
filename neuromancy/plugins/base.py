"""Plugin framework for Neuromancy tool system."""

from abc import ABC, abstractmethod

from pydantic import BaseModel, Field


class ToolSchema(BaseModel):
    """Schema describing a single tool exposed by a plugin."""

    name: str
    description: str
    parameters: dict = Field(default_factory=dict, description="JSON Schema for tool parameters")
    requires_approval: bool = False


class PluginManifest(BaseModel):
    """Manifest describing a plugin and its available tools."""

    name: str
    version: str
    description: str
    tools: list[ToolSchema]
    credentials_required: list[str] = Field(default_factory=list)


class BasePlugin(ABC):
    """Abstract base class for all Neuromancy plugins."""

    @property
    @abstractmethod
    def manifest(self) -> PluginManifest:
        """Return the plugin manifest describing this plugin and its tools."""
        ...

    @abstractmethod
    async def execute(self, tool_name: str, arguments: dict) -> str:
        """Execute a tool by name with the given arguments.

        Args:
            tool_name: The name of the tool to execute.
            arguments: A dictionary of arguments for the tool.

        Returns:
            The tool's output as a string.

        Raises:
            ValueError: If the tool_name is not recognized by this plugin.
        """
        ...


class _PluginToolView(BasePlugin):
    """Wraps an existing plugin while exposing only a subset of its tools.

    Used by ``PluginRegistry.filtered()`` to produce a scoped view of a plugin
    without mutating the original. The wrapped plugin's execution is delegated
    transparently for allowed tools; invoking a filtered-out tool raises
    KeyError to prevent caller-side caching from bypassing the restriction.
    """

    def __init__(self, wrapped: BasePlugin, tools: list[ToolSchema]):
        self._wrapped = wrapped
        self._tools = tools
        self._allowed_tool_names = {t.name for t in tools}
        self._manifest = PluginManifest(
            name=wrapped.manifest.name,
            version=wrapped.manifest.version,
            description=wrapped.manifest.description,
            tools=tools,
            credentials_required=wrapped.manifest.credentials_required,
        )

    @property
    def manifest(self) -> PluginManifest:
        return self._manifest

    async def execute(self, tool_name: str, arguments: dict) -> str:
        if tool_name not in self._allowed_tool_names:
            raise KeyError(
                f"Tool '{tool_name}' is not permitted in the restricted view "
                f"of plugin '{self._wrapped.manifest.name}'"
            )
        return await self._wrapped.execute(tool_name, arguments)


class PluginRegistry:
    """Registry that manages plugins and provides tool lookup for the LLM."""

    def __init__(self) -> None:
        self._plugins: dict[str, BasePlugin] = {}
        self._tool_to_plugin: dict[str, str] = {}

    def register(self, plugin: BasePlugin) -> None:
        """Register a plugin by its manifest name.

        Args:
            plugin: The plugin instance to register.
        """
        manifest = plugin.manifest
        self._plugins[manifest.name] = plugin
        for tool in manifest.tools:
            self._tool_to_plugin[tool.name] = manifest.name

    def get_all_tool_schemas(self) -> list[dict]:
        """Return OpenAI-format tool definitions for all registered tools.

        Returns:
            A list of dicts, each with type="function" and a function spec
            containing name, description, and parameters.
        """
        schemas: list[dict] = []
        for plugin in self._plugins.values():
            for tool in plugin.manifest.tools:
                schemas.append({
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.parameters,
                    },
                })
        return schemas

    def find_plugin_for_tool(self, tool_name: str) -> tuple[BasePlugin, ToolSchema]:
        """Find the plugin and tool schema for a given tool name.

        Args:
            tool_name: The name of the tool to look up.

        Returns:
            A tuple of (plugin_instance, tool_schema).

        Raises:
            KeyError: If no plugin provides a tool with that name.
        """
        plugin_name = self._tool_to_plugin[tool_name]
        plugin = self._plugins[plugin_name]
        for tool in plugin.manifest.tools:
            if tool.name == tool_name:
                return plugin, tool
        raise KeyError(f"Tool '{tool_name}' not found in plugin '{plugin_name}'")

    def requires_approval(self, tool_name: str) -> bool:
        """Check whether a tool requires user approval before execution.

        Args:
            tool_name: The name of the tool to check.

        Returns:
            True if the tool requires approval, False otherwise.

        Raises:
            KeyError: If no plugin provides a tool with that name.
        """
        _, tool = self.find_plugin_for_tool(tool_name)
        return tool.requires_approval

    def filtered(self, allowlist: set[str] | None) -> "PluginRegistry":
        """Return a new registry exposing only tools in the allowlist.

        This is the scoping primitive used by skills and (future) cron jobs to
        restrict what an LLM or procedure can invoke. Plugins whose every tool
        is excluded are not registered in the resulting view; plugins with a
        partial overlap are wrapped in ``_PluginToolView``.

        Args:
            allowlist: The set of tool names permitted in the resulting
                registry. ``None`` means no restriction — the new registry
                contains every plugin from the source. An empty set means
                nothing is permitted; the new registry has no tools.

        Returns:
            A fresh ``PluginRegistry`` populated with the scoped plugins. The
            original registry is never mutated.
        """
        new = PluginRegistry()
        for plugin in self._plugins.values():
            if allowlist is None:
                new.register(plugin)
                continue
            allowed_tools = [t for t in plugin.manifest.tools if t.name in allowlist]
            if not allowed_tools:
                continue
            new.register(_PluginToolView(plugin, allowed_tools))
        return new

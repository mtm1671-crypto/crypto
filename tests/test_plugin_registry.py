"""Tests for PluginRegistry — registration, lookup, and filtered() scoping."""

import pytest

from neuromancy.plugins.base import (
    BasePlugin,
    PluginManifest,
    PluginRegistry,
    ToolSchema,
)


class _FakePlugin(BasePlugin):
    """Minimal plugin for registry tests."""

    def __init__(
        self,
        name: str,
        tool_specs: list[tuple[str, bool]],
        credentials: list[str] | None = None,
    ):
        self._name = name
        self._tool_specs = tool_specs  # list of (tool_name, requires_approval)
        self._credentials = credentials or []
        self.calls: list[tuple[str, dict]] = []

    @property
    def manifest(self) -> PluginManifest:
        return PluginManifest(
            name=self._name,
            version="1.0",
            description=f"fake plugin {self._name}",
            tools=[
                ToolSchema(
                    name=tname,
                    description=f"tool {tname}",
                    parameters={"type": "object", "properties": {}},
                    requires_approval=req,
                )
                for tname, req in self._tool_specs
            ],
            credentials_required=self._credentials,
        )

    async def execute(self, tool_name: str, arguments: dict) -> str:
        self.calls.append((tool_name, arguments))
        return f"{self._name}:{tool_name}:ok"


# ---------------------------------------------------------------------------
# Existing registry behavior — regression guard
# ---------------------------------------------------------------------------


def test_register_and_lookup_unfiltered():
    r = PluginRegistry()
    r.register(_FakePlugin("A", [("tool_a1", False)]))
    plugin, tool = r.find_plugin_for_tool("tool_a1")
    assert plugin.manifest.name == "A"
    assert tool.name == "tool_a1"


def test_unfiltered_get_all_tool_schemas_returns_every_tool():
    r = PluginRegistry()
    r.register(_FakePlugin("A", [("tool_a1", False), ("tool_a2", True)]))
    r.register(_FakePlugin("B", [("tool_b1", False)]))
    schemas = r.get_all_tool_schemas()
    names = {s["function"]["name"] for s in schemas}
    assert names == {"tool_a1", "tool_a2", "tool_b1"}


def test_requires_approval_flows_through_unfiltered_lookup():
    r = PluginRegistry()
    r.register(_FakePlugin("A", [("risky", True), ("safe", False)]))
    assert r.requires_approval("risky") is True
    assert r.requires_approval("safe") is False


# ---------------------------------------------------------------------------
# filtered() — the new method
# ---------------------------------------------------------------------------


def test_filtered_allows_listed_tools_only():
    r = PluginRegistry()
    r.register(_FakePlugin("A", [("tool_a1", False), ("tool_a2", False)]))
    r.register(_FakePlugin("B", [("tool_b1", False)]))

    restricted = r.filtered({"tool_a1", "tool_b1"})
    schemas = restricted.get_all_tool_schemas()
    names = {s["function"]["name"] for s in schemas}
    assert names == {"tool_a1", "tool_b1"}


def test_filtered_none_allowlist_exposes_all_tools():
    """Passing None means 'no restriction' — same tools as the unfiltered registry."""
    r = PluginRegistry()
    r.register(_FakePlugin("A", [("tool_a1", False)]))
    r.register(_FakePlugin("B", [("tool_b1", False)]))

    restricted = r.filtered(None)
    schemas = restricted.get_all_tool_schemas()
    names = {s["function"]["name"] for s in schemas}
    assert names == {"tool_a1", "tool_b1"}


def test_filtered_empty_allowlist_exposes_no_tools():
    """Empty set is explicit 'nothing permitted' — stricter than None."""
    r = PluginRegistry()
    r.register(_FakePlugin("A", [("tool_a1", False)]))

    restricted = r.filtered(set())
    assert restricted.get_all_tool_schemas() == []


def test_filtered_lookup_raises_for_excluded_tool():
    r = PluginRegistry()
    r.register(_FakePlugin("A", [("tool_a1", False), ("tool_a2", False)]))
    restricted = r.filtered({"tool_a1"})
    with pytest.raises(KeyError):
        restricted.find_plugin_for_tool("tool_a2")


def test_filtered_drops_plugins_with_no_surviving_tools():
    """A plugin whose every tool is excluded shouldn't be registered at all."""
    r = PluginRegistry()
    r.register(_FakePlugin("Keep", [("keep_me", False)]))
    r.register(_FakePlugin("Drop", [("drop_me", False)]))

    restricted = r.filtered({"keep_me"})
    schemas = restricted.get_all_tool_schemas()
    plugin_names = {s["function"]["name"] for s in schemas}
    assert plugin_names == {"keep_me"}
    # Looking up a tool from the dropped plugin should raise
    with pytest.raises(KeyError):
        restricted.find_plugin_for_tool("drop_me")


def test_filtered_preserves_requires_approval_flag():
    """Approval semantics must survive filtering — that's the whole point of the flag."""
    r = PluginRegistry()
    r.register(_FakePlugin("A", [("risky", True), ("safe", False)]))
    restricted = r.filtered({"risky", "safe"})
    assert restricted.requires_approval("risky") is True
    assert restricted.requires_approval("safe") is False


@pytest.mark.asyncio
async def test_filtered_execute_works_for_allowed_tool():
    """A tool that's in the allowlist should execute normally via the filtered registry."""
    src = _FakePlugin("A", [("tool_a1", False)])
    r = PluginRegistry()
    r.register(src)

    restricted = r.filtered({"tool_a1"})
    plugin, _ = restricted.find_plugin_for_tool("tool_a1")
    result = await plugin.execute("tool_a1", {"x": 1})
    assert result == "A:tool_a1:ok"
    # Execution should have reached the underlying plugin
    assert src.calls == [("tool_a1", {"x": 1})]


@pytest.mark.asyncio
async def test_filtered_execute_raises_for_tool_removed_from_manifest():
    """If someone finds a way to call a filtered-out tool via the wrapper, it must fail.

    This guards against a future caller caching a plugin reference across
    filtering and accidentally invoking a disallowed tool.
    """
    src = _FakePlugin("A", [("allowed", False), ("forbidden", False)])
    r = PluginRegistry()
    r.register(src)

    restricted = r.filtered({"allowed"})
    # Get the wrapped plugin via the allowed tool
    plugin, _ = restricted.find_plugin_for_tool("allowed")
    # Now try to execute a tool that isn't in the filtered view
    with pytest.raises(KeyError):
        await plugin.execute("forbidden", {})


def test_filtered_preserves_plugin_metadata():
    """Version, description, and credentials_required should flow through to the filtered view."""
    r = PluginRegistry()
    r.register(
        _FakePlugin(
            "A",
            [("tool_a1", False)],
            credentials=["API_KEY_A"],
        )
    )
    restricted = r.filtered({"tool_a1"})
    plugin, _ = restricted.find_plugin_for_tool("tool_a1")
    assert plugin.manifest.version == "1.0"
    assert plugin.manifest.description == "fake plugin A"
    assert plugin.manifest.credentials_required == ["API_KEY_A"]


def test_filtered_with_nonexistent_tool_in_allowlist_is_harmless():
    """Listing a tool that no plugin exposes shouldn't error — it just does nothing."""
    r = PluginRegistry()
    r.register(_FakePlugin("A", [("real_tool", False)]))
    restricted = r.filtered({"real_tool", "ghost_tool"})
    names = {s["function"]["name"] for s in restricted.get_all_tool_schemas()}
    assert names == {"real_tool"}

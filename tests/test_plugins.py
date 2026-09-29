from plugins.base import IntentPattern, CLAUDIOCore, PluginBase, PluginManifest, ToolParameter, ToolSchema


class DummyPlugin(PluginBase):
    manifest = PluginManifest(
        name="dummy",
        version="1.0.0",
        description="Dummy plugin for testing",
        category="test",
        dependencies=[],
        permissions=[],
    )

    async def initialize(self, core):
        pass

    async def shutdown(self):
        pass

    def get_tools(self):
        return [
            ToolSchema(
                name="dummy_tool",
                description="A dummy tool",
                parameters={"arg1": ToolParameter(type="string", description="Test arg")},
            )
        ]

    def get_intents(self):
        return [
            IntentPattern(
                name="dummy_intent",
                patterns=["dummy", "test dummy"],
                entities=[],
            )
        ]


def test_plugin_base():
    core = CLAUDIOCore()
    plugin = DummyPlugin()
    core.register_plugin(plugin)

    assert "dummy" in core.plugins
    assert "dummy_tool" in core.tools
    assert "dummy_intent" in core.intents

    tool = core.get_tool("dummy_tool")
    assert tool is not None
    assert tool.name == "dummy_tool"

    intent = core.get_intent("dummy_intent")
    assert intent is not None
    assert intent.name == "dummy_intent"
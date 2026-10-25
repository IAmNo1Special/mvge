from __future__ import annotations

import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mvge.core.channel import (
    ChannelConfig,
    Model,
)
from mvge.provider.base import Realm

# Ensure runes path is in sys.path when installed
RUNES_DIR = Path("~/.agents/extensions").expanduser()
if str(RUNES_DIR) not in sys.path and RUNES_DIR.is_dir():
    sys.path.insert(0, str(RUNES_DIR))

pytestmark = pytest.mark.skipif(
    not (RUNES_DIR / "ollama_realm").is_dir(),
    reason="External runes in ~/.agents/extensions not installed on host",
)


def test_ollama_realm_unit() -> None:
    from ollama_realm.ollama import OllamaRealm, OllamaRealmFactory

    realm = OllamaRealm(api_key="test-key", base_url="http://localhost:11434")
    assert realm.realm_name == "ollama"
    assert realm._strip_model_id("ollama/llama3.2") == "llama3.2"
    assert realm._strip_model_id("llama3.2") == "llama3.2"

    model = Model(
        id="ollama/qwen2.5-coder",
        name="Qwen",
        realm="ollama",
        base_url="",
        api_key="",
    )
    config = ChannelConfig(model=model, system_prompt="You are a helper.")
    url, headers, payload = realm._prepare_request(model, [], config)

    assert url == "http://localhost:11434/v1/chat/completions"
    assert headers["Authorization"] == "Bearer test-key"
    assert payload["model"] == "qwen2.5-coder"
    assert payload["stream_options"] == {"include_usage": True}
    assert payload["messages"][0] == {"role": "system", "content": "You are a helper."}

    # Test chunk parsing
    chunk = {
        "choices": [
            {
                "delta": {
                    "content": "Hello world",
                    "reasoning": "Thinking deeply",
                },
                "finish_reason": None,
            }
        ]
    }
    parsed = realm._parse_sse_chunk(chunk)
    assert parsed is not None
    assert parsed.content == "Hello world"
    assert parsed.contemplation == "Thinking deeply"

    # Test factory
    factory = OllamaRealmFactory()
    inst = factory(api_key="k", base_url="http://localhost:11434")
    assert isinstance(inst, Realm)


def test_google_realm_unit() -> None:
    from google_realm.google import (
        GoogleRealm,
        GoogleRealmFactory,
        _clean_schema,
        _convert_invocations,
        _convert_tools,
    )

    # 1. Schema cleaning
    schema = {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "title": "SearchArgs",
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "query": {
                "type": "string",
                "title": "Search Query",
            }
        },
        "$defs": {"Extra": {"type": "string"}},
    }
    cleaned = _clean_schema(schema)
    assert "$schema" not in cleaned
    assert "title" not in cleaned
    assert "additionalProperties" not in cleaned
    assert "$defs" not in cleaned
    assert "query" in cleaned["properties"]
    assert "title" not in cleaned["properties"]["query"]

    # 2. Tool conversion
    tools = [
        {
            "name": "search",
            "description": "web search",
            "parameters": schema,
        }
    ]
    converted = _convert_tools(tools)
    assert len(converted) == 1
    assert "functionDeclarations" in converted[0]
    decl = converted[0]["functionDeclarations"][0]
    assert decl["name"] == "search"
    assert "$schema" not in decl["parameters"]

    # 3. Invocation conversion with consecutive turn merging
    class MockInv:
        def __init__(
            self, role: str, content: Any, name: str = "", spell_cast_id: str = ""
        ) -> None:
            self.role = role
            self.content = content
            self.name = name
            self.spell_cast_id = spell_cast_id

    invs = [
        MockInv("user", "Hello 1"),
        MockInv("user", "Hello 2"),  # consecutive user
        MockInv(
            "assistant",
            [
                {"type": "text", "text": "Thought..."},
                {
                    "type": "spell_cast",
                    "spell_cast": {
                        "id": "c1",
                        "name": "search",
                        "arguments": {"q": "test"},
                    },
                },
            ],
        ),
        MockInv(
            "spellResult",
            [{"type": "text", "text": "Search results"}],
            name="search",
            spell_cast_id="c1",
        ),
    ]
    contents = _convert_invocations(invs)
    # Consecutive user messages should be merged into single user role
    assert contents[0]["role"] == "user"
    assert len(contents[0]["parts"]) == 2
    assert contents[0]["parts"][0]["text"] == "Hello 1"
    assert contents[0]["parts"][1]["text"] == "Hello 2"

    # Assistant turn has model role
    assert contents[1]["role"] == "model"
    assert contents[1]["parts"][0]["text"] == "Thought..."
    assert "functionCall" in contents[1]["parts"][1]
    assert contents[1]["parts"][1]["functionCall"]["name"] == "search"

    # Tool result is represented as user functionResponse
    assert contents[2]["role"] == "user"
    assert "functionResponse" in contents[2]["parts"][0]
    assert contents[2]["parts"][0]["functionResponse"]["name"] == "search"

    # 4. GoogleRealm methods
    realm = GoogleRealm(api_key="AIzaSyTestKey")
    assert realm.realm_name == "google"
    assert realm._strip_model_id("google/gemini-2.5-pro") == "gemini-2.5-pro"

    factory = GoogleRealmFactory()
    inst = factory(api_key="AIzaSyTestKey")
    assert isinstance(inst, Realm)


def test_subagents_agent_store(tmp_path: Path) -> None:
    from subagents import agent_store

    with patch.object(
        agent_store, "get_agent_dir", return_value=tmp_path / "test_agent"
    ):
        res = agent_store.save_agent_profile(
            name="test_agent",
            system_prompt="You are a test specialist.",
            guidelines="Be thorough.\nWrite clean code.",
            model_id="google/gemini-2.5-pro",
            tools=["read", "grep"],
            description="Testing agent",
        )
        assert res["name"] == "test_agent"
        assert (tmp_path / "test_agent" / "SYSTEM.md").is_file()
        assert (tmp_path / "test_agent" / "GUIDELINES.md").is_file()
        assert (tmp_path / "test_agent" / "config.json").is_file()

        loaded = agent_store.load_agent_profile("test_agent")
        assert loaded is not None
        assert "You are a test specialist." in loaded["system_prompt"]
        assert "Be thorough." in loaded["guidelines"]
        assert loaded["config"]["model"] == "google/gemini-2.5-pro"
        assert loaded["config"]["tools"] == ["read", "grep"]


@pytest.mark.asyncio
async def test_subagents_manager_recursion_and_management() -> None:
    from subagents.manager import SubagentManager, _depth_var

    mgr = SubagentManager()

    # Test recursion limit
    token = _depth_var.set(1)
    try:
        res = await mgr.invoke("Do something", role="edit-mode")
        assert "Maximum subagent recursion depth" in res
    finally:
        _depth_var.reset(token)

    # Test management commands
    assert "No subagent background tasks found." in mgr.manage(action="list")
    assert "Error: 'task_id' is required" in mgr.manage(action="status", task_id="")


@pytest.mark.asyncio
async def test_mcp_client_unit() -> None:
    from mcp.client import AsyncMCPStdioClient

    client = AsyncMCPStdioClient("mock-cmd")

    # Mock process and streams
    mock_stdin = MagicMock()
    mock_stdin.write = MagicMock()
    mock_stdin.drain = AsyncMock()

    mock_process = MagicMock()
    mock_process.stdin = mock_stdin
    client.process = mock_process

    # Test call_tool with simulated response
    async def fake_send_request(
        method: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        if method == "tools/list":
            return {
                "tools": [
                    {
                        "name": "echo",
                        "description": "Echo input",
                        "inputSchema": {
                            "type": "object",
                            "properties": {"msg": {"type": "string"}},
                        },
                    }
                ]
            }
        if method == "tools/call":
            return {
                "content": [
                    {
                        "type": "text",
                        "text": f"Echo: {params.get('arguments', {}).get('msg')}",
                    }
                ],
                "isError": False,
            }
        return {}

    client._send_request = fake_send_request  # type: ignore[method-assign]

    tools = await client.list_tools()
    assert len(tools) == 1
    assert tools[0]["name"] == "echo"

    res = await client.call_tool("echo", {"msg": "hello"})
    assert res == "Echo: hello"

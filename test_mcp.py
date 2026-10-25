"""Tests for MCP tool discovery, schema conversion and agent routing.

No real MCP server or npm package is started: ``mcp_client._load_sdk`` is
replaced with a tiny fake that speaks just enough of the stdio/session API.
"""

import types
from contextlib import asynccontextmanager

import pytest

import agent
import logger
import mcp_client

# Populated per-test: server name -> list of fake tool descriptions.
_SERVER_TOOLS = {}
_CALLS = []


class _FakeParams:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class _FakeSession:
    def __init__(self, read, write):
        self.server = write

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def initialize(self):
        if self.server == "broken":
            raise RuntimeError("server exploded during initialize")

    async def list_tools(self):
        return types.SimpleNamespace(tools=_SERVER_TOOLS.get(self.server, []))

    async def call_tool(self, name, arguments):
        _CALLS.append((self.server, name, arguments))
        if self.server == "broken":
            raise RuntimeError("call exploded")
        if name == "fail":
            return types.SimpleNamespace(
                isError=True,
                content=[types.SimpleNamespace(type="text", text="permission denied")],
            )
        return types.SimpleNamespace(
            content=[types.SimpleNamespace(type="text", text=f"{self.server}:{name}")]
        )


class _FakeStdio:
    def __init__(self):
        self.params = []

    @asynccontextmanager
    async def __call__(self, params):
        self.params.append(params)
        # ``write`` doubles as the fake server identity used by _FakeSession.
        name = params.args[0] if params.args else ""
        yield f"read-{name}", name


@pytest.fixture
def fake_sdk(monkeypatch):
    """Install the fake SDK and reset its registries for each test."""
    _SERVER_TOOLS.clear()
    _CALLS.clear()
    stdio = _FakeStdio()
    monkeypatch.setattr(
        mcp_client,
        "_load_sdk",
        lambda: (_FakeSession, _FakeParams, stdio),
    )
    return stdio


def _tool(name, description="", schema=None):
    return types.SimpleNamespace(
        name=name,
        description=description,
        inputSchema=schema or {"type": "object", "properties": {}},
    )


@pytest.fixture
def log_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_LOG_DIR", str(tmp_path))
    return tmp_path


def _read(path):
    return path.read_text(encoding="utf-8") if path.exists() else ""


# --------------------------------------------------------------------------- #
# configuration parsing
# --------------------------------------------------------------------------- #

def test_parse_servers_compact_and_object_forms():
    servers = mcp_client.parse_servers(
        '{"filesystem": "npx -y server-filesystem /tmp",'
        ' "brave": {"command": "npx", "args": ["-y", "brave"],'
        ' "env": {"BRAVE_API_KEY": "key"}, "cwd": "/tmp"}}'
    )

    assert servers["filesystem"]["command"] == "npx"
    assert servers["filesystem"]["args"] == ["-y", "server-filesystem", "/tmp"]
    assert servers["brave"]["env"] == {"BRAVE_API_KEY": "key"}
    assert servers["brave"]["cwd"] == "/tmp"


def test_parse_servers_accepts_claude_desktop_wrapper():
    servers = mcp_client.parse_servers(
        '{"mcpServers": {"fs": "npx -y server-filesystem /tmp"}}'
    )

    assert list(servers) == ["fs"]
    assert servers["fs"]["command"] == "npx"


def test_parse_servers_empty_and_invalid():
    assert mcp_client.parse_servers("") == {}
    assert mcp_client.parse_servers("   ") == {}
    with pytest.raises(ValueError):
        mcp_client.parse_servers('["not", "an", "object"]')
    with pytest.raises(ValueError):
        mcp_client.parse_servers('{"fs": {"args": ["missing-command"]}}')
    with pytest.raises(Exception):
        mcp_client.parse_servers("{not json")


# --------------------------------------------------------------------------- #
# discovery and schema conversion
# --------------------------------------------------------------------------- #

def test_get_mcp_tools_returns_openai_schemas(fake_sdk):
    _SERVER_TOOLS["demo"] = [
        _tool(
            "read_file",
            "Read a file",
            {"type": "object", "properties": {"path": {"type": "string"}}},
        ),
        _tool("list_files", "List files"),
    ]
    manager = mcp_client.MCPManager(servers={"demo": "fake demo"})

    try:
        tools = manager.get_mcp_tools()
    finally:
        manager.close()

    assert [tool["function"]["name"] for tool in tools] == [
        "mcp__demo__read_file",
        "mcp__demo__list_files",
    ]
    assert tools[0] == {
        "type": "function",
        "function": {
            "name": "mcp__demo__read_file",
            "description": "Read a file",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
            },
        },
    }
    # The pre-normalised parameters are passed through unchanged.
    assert tools[1]["function"]["parameters"] == {"type": "object", "properties": {}}
    assert fake_sdk.params[0].command == "fake"


def test_discovery_caches_tools_between_calls(fake_sdk):
    _SERVER_TOOLS["demo"] = [_tool("ping")]
    manager = mcp_client.MCPManager(servers={"demo": "fake demo"})

    try:
        first = manager.get_mcp_tools()
        second = manager.get_mcp_tools()
    finally:
        manager.close()

    assert first == second
    assert len(fake_sdk.params) == 1  # the server was started once, not twice


def test_unconfigured_manager_returns_no_tools(fake_sdk):
    manager = mcp_client.MCPManager(servers={})

    assert manager.get_mcp_tools() == []
    assert fake_sdk.params == []
    manager.close()


def test_failed_server_is_logged_and_does_not_raise(fake_sdk, log_dir):
    _SERVER_TOOLS["broken"] = [_tool("ping")]
    manager = mcp_client.MCPManager(servers={"broken": "fake broken"})

    try:
        assert manager.get_mcp_tools() == []
    finally:
        manager.close()

    log = _read(log_dir / "command_errors.log")
    assert "MCP server 'broken' failed to start or list tools" in log
    assert "RuntimeError: server exploded during initialize" in log
    assert "Traceback (most recent call last)" in log


def test_invalid_env_configuration_is_logged(monkeypatch, log_dir):
    monkeypatch.setenv("MCP_SERVERS", "{not json")
    manager = mcp_client.MCPManager()

    assert manager.get_mcp_tools() == []
    assert "Invalid MCP_SERVERS configuration" in _read(
        log_dir / "command_errors.log"
    )


def test_invalid_tuning_env_never_raises(monkeypatch):
    monkeypatch.setenv("MCP_TIMEOUT", "not-a-number")
    monkeypatch.setenv("MCP_RETRY_SECONDS", "also-bad")

    manager = mcp_client.MCPManager()

    assert manager._timeout == mcp_client.DEFAULT_TIMEOUT
    assert manager._retry_seconds == mcp_client.RETRY_SECONDS
    manager.close()


def test_manager_reads_servers_from_environment(fake_sdk, monkeypatch):
    monkeypatch.setenv("MCP_SERVERS", '{"envfs": "fake envfs"}')
    _SERVER_TOOLS["envfs"] = [_tool("ping")]
    manager = mcp_client.MCPManager()

    try:
        tools = manager.get_mcp_tools()
    finally:
        manager.close()

    assert [tool["function"]["name"] for tool in tools] == ["mcp__envfs__ping"]


# --------------------------------------------------------------------------- #
# execution
# --------------------------------------------------------------------------- #

def test_execute_mcp_tool_routes_to_the_right_server(fake_sdk):
    _SERVER_TOOLS["first"] = [_tool("echo")]
    _SERVER_TOOLS["second"] = [_tool("echo")]
    manager = mcp_client.MCPManager(
        servers={"first": "fake first", "second": "fake second"}
    )

    try:
        manager.get_mcp_tools()
        result = manager.execute_mcp_tool("second", "echo", {"text": "hello"})
    finally:
        manager.close()

    assert result == "second:echo"
    assert _CALLS == [("second", "echo", {"text": "hello"})]


def test_execute_mcp_tool_reports_error_results(fake_sdk, log_dir):
    _SERVER_TOOLS["demo"] = [_tool("fail")]
    manager = mcp_client.MCPManager(servers={"demo": "fake demo"})

    try:
        manager.get_mcp_tools()
        result = manager.execute_mcp_tool("demo", "fail", {})
    finally:
        manager.close()

    assert result == "permission denied"
    assert "returned an error: permission denied" in _read(
        log_dir / "command_errors.log"
    )


def test_execute_mcp_tool_failure_returns_friendly_string(fake_sdk, log_dir):
    _SERVER_TOOLS["broken"] = [_tool("ping")]
    manager = mcp_client.MCPManager(servers={"broken": "fake broken"})

    try:
        result = manager.execute_mcp_tool("broken", "ping", {})
    finally:
        manager.close()

    assert result == mcp_client.MCP_ERROR
    assert "MCP tool 'broken.ping' failed" in _read(
        log_dir / "command_errors.log"
    )


def test_execute_mcp_tool_rejects_unknown_server(fake_sdk, log_dir):
    manager = mcp_client.MCPManager(servers={"demo": "fake demo"})

    try:
        result = manager.execute_mcp_tool("missing", "ping", {})
    finally:
        manager.close()

    assert result == mcp_client.MCP_ERROR
    assert "is not configured" in _read(log_dir / "command_errors.log")


# --------------------------------------------------------------------------- #
# agent integration
# --------------------------------------------------------------------------- #

class _RecordingManager:
    def __init__(self, target=("demo", "read_file"), result="file contents"):
        self.target = target
        self.result = result
        self.calls = []

    def resolve_tool_name(self, name):
        return self.target

    def execute_mcp_tool(self, server, tool, arguments):
        self.calls.append((server, tool, arguments))
        return self.result


def _call(name, arguments="{}"):
    return types.SimpleNamespace(
        id="call_1",
        function=types.SimpleNamespace(name=name, arguments=arguments),
    )


def test_run_tool_routes_mcp_calls_outside_dispatch(monkeypatch):
    manager = _RecordingManager(result="the file body")
    monkeypatch.setattr(agent, "mcp_manager", manager)

    message = agent._run_tool(
        _call("mcp__demo__read_file", '{"path": "/tmp/a.txt"}')
    )

    assert message == {
        "role": "tool",
        "tool_call_id": "call_1",
        "content": "the file body",
    }
    assert manager.calls == [("demo", "read_file", {"path": "/tmp/a.txt"})]


def test_run_tool_can_route_by_parsing_the_prefixed_name(monkeypatch):
    manager = _RecordingManager(target=None, result="ok")
    monkeypatch.setattr(agent, "mcp_manager", manager)

    message = agent._run_tool(_call("mcp__demo__echo", '{"text": "hi"}'))

    assert message["content"] == "ok"
    assert manager.calls == [("demo", "echo", {"text": "hi"})]


def test_run_tool_keeps_native_dispatch_working(monkeypatch):
    called = {}
    monkeypatch.setitem(
        agent._DISPATCH, "fn_test", lambda value: called.setdefault("value", value)
    )

    message = agent._run_tool(_call("fn_test", '{"value": 7}'))

    assert message["content"] == "7"
    assert called == {"value": 7}


def test_run_tool_handles_bad_mcp_arguments(monkeypatch):
    manager = _RecordingManager()
    monkeypatch.setattr(agent, "mcp_manager", manager)

    agent._run_tool(_call("mcp__demo__read_file", "not json"))

    assert manager.calls == [("demo", "read_file", {})]


def test_tools_for_appends_mcp_tools(monkeypatch):
    schema = {
        "type": "function",
        "function": {
            "name": "mcp__demo__echo",
            "description": "",
            "parameters": {"type": "object", "properties": {}},
        },
    }
    monkeypatch.setattr(agent.mcp_manager, "get_mcp_tools", lambda: [schema])

    tools = agent.tools_for("what is the weather?")

    assert tools[-1] is schema
    assert "mcp__demo__echo" in [tool["function"]["name"] for tool in tools]


def test_tools_for_is_unchanged_without_mcp(monkeypatch):
    monkeypatch.setenv("MCP_SERVERS", "")
    expected = list(agent._SCHEMAS_BY_INTENT["GENERAL"])

    assert agent.tools_for("hello there") == expected

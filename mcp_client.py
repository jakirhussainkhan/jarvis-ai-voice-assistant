"""Model Context Protocol (MCP) client support for Jarvis.

Jarvis can use tools exposed by external MCP servers as well as its native
Python tools. Servers are declared in ``MCP_SERVERS`` (a JSON object, usually in
``~/.jarvis/.env``):

    MCP_SERVERS='{"filesystem": "npx -y @modelcontextprotocol/server-filesystem /home/me"}'

Tools are exposed to the LLM with a collision-proof prefix:

    mcp__<server>__<tool>        e.g. mcp__filesystem__read_file

The official ``mcp`` SDK is imported lazily. If it is missing, if a server fails
to start, or if a tool call fails, the problem is written to
``~/.jarvis/logs/command_errors.log`` and Jarvis keeps running with a short
friendly error string. Nothing here is allowed to crash the voice loop.
"""

import asyncio
import concurrent.futures
import copy
import hashlib
import json
import os
import re
import shlex
import threading
import time
from contextlib import AsyncExitStack

import logger

MCP_PREFIX = "mcp__"
MCP_ERROR = "I couldn't execute that command, sir."
DEFAULT_TIMEOUT = 15.0
RETRY_SECONDS = 30.0
RESULT_LIMIT = 12_000

_INVALID_NAME = re.compile(r"[^A-Za-z0-9_-]+")
_NAME_LIMIT = 64


def _env_float(name, default):
    """Read a numeric env var without ever raising on a bad value."""
    try:
        value = float(os.getenv(name, default))
    except (TypeError, ValueError):
        return float(default)
    return value if value > 0 else float(default)


def _load_sdk():
    """Return the three SDK objects MCPManager needs.

    Kept in one small function so the dependency stays optional and tests can
    replace it with a fake without spawning a real server.
    """
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    return ClientSession, StdioServerParameters, stdio_client


def _safe_component(value):
    """Make one ``mcp__`` name component safe for OpenAI function names."""
    text = _INVALID_NAME.sub("_", str(value or "")).strip("_")
    return text or "tool"


def _address(server, tool):
    """Return the prefixed, length-safe tool name for ``server``/``tool``."""
    base = f"{MCP_PREFIX}{_safe_component(server)}__{_safe_component(tool)}"
    if len(base) <= _NAME_LIMIT:
        return base
    digest = hashlib.sha1(base.encode("utf-8")).hexdigest()[:8]
    return f"{base[:_NAME_LIMIT - 9]}_{digest}"


def _server_spec(value):
    """Normalise one ``MCP_SERVERS`` entry into command/args/env/cwd."""
    if isinstance(value, str):
        parts = shlex.split(value)
        if not parts:
            raise ValueError("empty server command")
        return {"command": parts[0], "args": parts[1:], "env": {}, "cwd": None}

    if isinstance(value, (list, tuple)):
        if not value:
            raise ValueError("empty server command")
        return {"command": str(value[0]), "args": [str(v) for v in value[1:]],
                "env": {}, "cwd": None}

    if isinstance(value, dict):
        command = value.get("command")
        args = value.get("args", [])
        if isinstance(command, str) and not args:
            parts = shlex.split(command)
            command, args = (parts[0], parts[1:]) if parts else ("", [])
        if not command:
            raise ValueError("missing server command")
        env = value.get("env") or {}
        if not isinstance(env, dict):
            raise ValueError("server env must be an object")
        return {
            "command": str(command),
            "args": [str(v) for v in (args or [])],
            "env": {str(k): str(v) for k, v in env.items()},
            "cwd": value.get("cwd"),
        }

    raise ValueError("server must be a command string, list or object")


def parse_servers(raw):
    """Parse ``MCP_SERVERS`` JSON into ``{name: spec}``.

    Both the compact form and a full object form are accepted:

        {"filesystem": "npx -y ..."}
        {"filesystem": {"command": "npx", "args": ["-y", "..."], "env": {...}}}
        {"mcpServers": {...}}       # Claude Desktop style wrapper
    """
    if not raw or (isinstance(raw, str) and not raw.strip()):
        return {}
    data = raw if isinstance(raw, (dict, list)) else json.loads(raw)
    if isinstance(data, dict) and "mcpServers" in data:
        data = data["mcpServers"]
    if not isinstance(data, dict):
        raise ValueError("MCP_SERVERS must be a JSON object")

    servers = {}
    for name, value in data.items():
        name = str(name).strip()
        if not name:
            continue
        servers[name] = _server_spec(value)
    return servers


def _result_text(result):
    """Render an MCP ``CallToolResult`` (or a test double) as plain text."""
    if result is None:
        return "MCP tool completed with no output."
    if isinstance(result, str):
        return result.strip() or "MCP tool completed with no output."
    if isinstance(result, dict):
        if "content" not in result:
            return json.dumps(result, ensure_ascii=False, default=str)
        result = result["content"]

    parts = []
    items = result if isinstance(result, (list, tuple)) else getattr(
        result, "content", None
    )
    for item in items or []:
        text = getattr(item, "text", None)
        if text:
            parts.append(str(text))
            continue
        kind = getattr(item, "type", None)
        if kind == "image":
            parts.append(f"[image: {getattr(item, 'mimeType', 'unknown')}]")
            continue
        if isinstance(item, dict):
            parts.append(json.dumps(item, ensure_ascii=False, default=str))
            continue
        dumped = None
        for method in ("model_dump", "dict"):
            fn = getattr(item, method, None)
            if callable(fn):
                try:
                    dumped = fn()
                except Exception:
                    dumped = None
                break
        parts.append(
            json.dumps(dumped, ensure_ascii=False, default=str)
            if dumped is not None else str(item)
        )

    structured = (getattr(result, "structuredContent", None)
                  or getattr(result, "structured_content", None))
    if structured and not parts:
        parts.append(json.dumps(structured, ensure_ascii=False, default=str))

    text = "\n".join(part for part in parts if part).strip()
    if not text:
        text = str(result).strip()
    text = text or "MCP tool completed with no output."
    if len(text) > RESULT_LIMIT:
        text = text[:RESULT_LIMIT].rstrip() + "\n...[truncated]"
    return text


class MCPManager:
    """Own a background asyncio loop and persistent MCP server sessions.

    The official SDK is async, while Jarvis (and the OpenAI tool loop) is sync.
    A dedicated daemon thread runs one event loop; every public method is a
    blocking, exception-safe wrapper around a coroutine on that loop.
    """

    def __init__(self, servers=None, timeout=None):
        self._servers_override = servers
        try:
            self._timeout = float(timeout) if timeout else _env_float(
                "MCP_TIMEOUT", DEFAULT_TIMEOUT
            )
        except (TypeError, ValueError):
            self._timeout = DEFAULT_TIMEOUT
        if self._timeout <= 0:
            self._timeout = DEFAULT_TIMEOUT
        self._retry_seconds = _env_float("MCP_RETRY_SECONDS", RETRY_SECONDS)
        self._lock = threading.RLock()
        self._loop = None
        self._thread = None
        self._ready = threading.Event()
        self._connections = {}
        self._tools = []
        self._tool_index = {}
        self._signature = None
        self._last_attempt = 0.0
        self._config_error_at = 0.0

    # -- configuration ------------------------------------------------------ #
    def _configured_servers(self):
        raw = (self._servers_override if self._servers_override is not None
               else os.getenv("MCP_SERVERS"))
        return parse_servers(raw or "")

    def _signature_for(self, servers):
        return json.dumps(servers, sort_keys=True, default=str)

    # -- event loop lifecycle ---------------------------------------------- #
    def _ensure_started(self):
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._ready.clear()
            self._loop = asyncio.new_event_loop()
            self._thread = threading.Thread(
                target=self._run_loop,
                args=(self._loop,),
                name="jarvis-mcp",
                daemon=True,
            )
            self._thread.start()
        # The loop is created in another thread; wait until it is ready before
        # scheduling work on it.
        if not self._ready.wait(timeout=5):
            raise RuntimeError("the MCP event loop did not start")

    def _run_loop(self, loop):
        asyncio.set_event_loop(loop)
        self._ready.set()
        try:
            loop.run_forever()
        finally:
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(
                    asyncio.gather(*pending, return_exceptions=True)
                )
            loop.close()

    def _submit(self, coro, timeout=None):
        """Run ``coro`` on the MCP loop and wait for its result."""
        self._ensure_started()
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        try:
            return future.result(timeout=timeout or self._timeout)
        except concurrent.futures.TimeoutError:
            future.cancel()
            raise TimeoutError("MCP operation timed out")

    async def _connect(self, name, spec):
        """Open one stdio session and keep its exit stack alive."""
        ClientSession, StdioServerParameters, stdio_client = _load_sdk()
        env = dict(os.environ)
        env.update(spec.get("env") or {})
        params_args = {
            "command": spec["command"],
            "args": list(spec["args"]),
            "env": env,
        }
        if spec.get("cwd"):
            params_args["cwd"] = spec["cwd"]
        params = StdioServerParameters(**params_args)
        stack = AsyncExitStack()
        try:
            read, write = await asyncio.wait_for(
                stack.enter_async_context(stdio_client(params)),
                timeout=self._timeout,
            )
            session = await asyncio.wait_for(
                stack.enter_async_context(ClientSession(read, write)),
                timeout=self._timeout,
            )
            await asyncio.wait_for(session.initialize(), timeout=self._timeout)
        except BaseException:
            try:
                await stack.aclose()
            except Exception:
                pass
            raise
        return {"session": session, "stack": stack}

    async def _list_tools(self, connection):
        result = await asyncio.wait_for(
            connection["session"].list_tools(), timeout=self._timeout
        )
        return list(getattr(result, "tools", None) or [])

    async def _call_tool(self, connection, tool_name, arguments):
        return await asyncio.wait_for(
            connection["session"].call_tool(tool_name, arguments or {}),
            timeout=self._timeout,
        )

    async def _close_connection(self, connection):
        await connection["stack"].aclose()

    def close(self):
        """Close every session and stop the background loop (best effort)."""
        with self._lock:
            connections = list(self._connections.values())
            self._connections.clear()
            self._tools = []
            self._tool_index = {}
            self._signature = None
            loop, thread = self._loop, self._thread
            self._loop = None
            self._thread = None

        for connection in connections:
            try:
                if loop is not None and thread is not None and thread.is_alive():
                    future = asyncio.run_coroutine_threadsafe(
                        self._close_connection(connection), loop
                    )
                    future.result(timeout=2)
            except Exception:
                pass

        if loop is not None and thread is not None and thread.is_alive():
            loop.call_soon_threadsafe(loop.stop)
            thread.join(timeout=2)

    # -- public API --------------------------------------------------------- #
    def get_mcp_tools(self):
        """Return every configured MCP tool as an OpenAI function schema.

        Never raises: a bad ``MCP_SERVERS`` value, a missing SDK and a server
        that refuses to start are all logged and simply produce no tools.
        """
        with self._lock:
            try:
                servers = self._configured_servers()
            except Exception as exc:
                # A typo in .env should not write a traceback on every turn.
                now = time.monotonic()
                if now - self._config_error_at >= self._retry_seconds:
                    logger.log_command_error("Invalid MCP_SERVERS configuration", exc)
                    self._config_error_at = now
                return []

            if not servers:
                if self._connections:
                    self.close()
                return []

            signature = self._signature_for(servers)
            if (signature == self._signature
                    and time.monotonic() - self._last_attempt < self._retry_seconds):
                # Cache discovery briefly: a successful server is not re-spawned
                # every turn, and a broken one is not retried in a tight loop.
                return copy.deepcopy(self._tools)

            if self._signature is not None and signature != self._signature:
                self.close()

            self._signature = signature
            self._last_attempt = time.monotonic()
            tools = []
            index = {}

            for name, spec in servers.items():
                try:
                    connection = self._connections.get(name)
                    if connection is None:
                        connection = self._submit(self._connect(name, spec))
                        self._connections[name] = connection
                    for tool in self._submit(self._list_tools(connection)):
                        schema = self._schema(name, tool, index)
                        if schema is not None:
                            tools.append(schema)
                            index[schema["function"]["name"]] = (
                                name, getattr(tool, "name", "")
                            )
                except Exception as exc:
                    logger.log_command_error(
                        f"MCP server '{name}' failed to start or list tools", exc
                    )

            self._tools = tools
            self._tool_index = index
            return copy.deepcopy(tools)

    def _schema(self, server, tool, index):
        """Convert one MCP tool description into an OpenAI tool schema."""
        tool_name = str(getattr(tool, "name", "") or "").strip()
        if not tool_name:
            return None
        prefixed = _address(server, tool_name)
        if prefixed in index:
            # Extremely rare sanitisation collision; keep names deterministic
            # but make the second one unique.
            digest = hashlib.sha1(
                f"{server}/{tool_name}".encode("utf-8")
            ).hexdigest()[:8]
            prefixed = f"{prefixed[: _NAME_LIMIT - 9]}_{digest}"
        parameters = (getattr(tool, "inputSchema", None)
                      or getattr(tool, "input_schema", None)
                      or {"type": "object", "properties": {}})
        if not isinstance(parameters, dict):
            parameters = {"type": "object", "properties": {}}
        if "type" not in parameters:
            parameters = {"type": "object", **parameters}
        return {
            "type": "function",
            "function": {
                "name": prefixed,
                "description": str(getattr(tool, "description", "") or ""),
                "parameters": parameters,
            },
        }

    def resolve_tool_name(self, name):
        """Map ``mcp__server__tool`` back to ``(server, tool)``, or ``None``."""
        return self._tool_index.get(str(name or ""))

    def execute_mcp_tool(self, server_name, tool_name, arguments):
        """Call ``tool_name`` on ``server_name`` and return plain text.

        Always returns a string. Exceptions, timeouts and MCP-level error
        results are logged to ``command_errors.log`` and converted into a clean
        message for the LLM.
        """
        server_name = str(server_name or "").strip()
        tool_name = str(tool_name or "").strip()
        if not server_name or not tool_name:
            logger.log_command_error("MCP tool call had no server or tool name")
            return MCP_ERROR

        with self._lock:
            try:
                servers = self._configured_servers()
            except Exception as exc:
                logger.log_command_error("Invalid MCP_SERVERS configuration", exc)
                return MCP_ERROR

            if server_name not in servers:
                logger.log_command_error(
                    f"MCP server '{server_name}' is not configured"
                )
                return MCP_ERROR

            try:
                connection = self._connections.get(server_name)
                if connection is None:
                    connection = self._submit(
                        self._connect(server_name, servers[server_name])
                    )
                    self._connections[server_name] = connection
                result = self._submit(
                    self._call_tool(connection, tool_name, arguments or {})
                )
            except Exception as exc:
                logger.log_command_error(
                    f"MCP tool '{server_name}.{tool_name}' failed", exc
                )
                return MCP_ERROR

            if getattr(result, "isError", False) or getattr(result, "is_error", False):
                text = _result_text(result)
                logger.log_command_error(
                    f"MCP tool '{server_name}.{tool_name}' returned an error: {text}"
                )
                return text or MCP_ERROR
            return _result_text(result)

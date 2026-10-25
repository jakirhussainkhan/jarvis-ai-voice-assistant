# MCP tool servers

Jarvis can use tools from any [Model Context Protocol](https://modelcontextprotocol.io)
(MCP) server alongside its built-in desktop/web/memory tools. MCP servers run as
local child processes; Jarvis discovers their tools at runtime and exposes them
to the LLM.

MCP support is optional. With no `MCP_SERVERS` setting Jarvis starts exactly as
before and no subprocess is launched.

## 1. Install the SDK

MCP support requires the official Python SDK (already listed in
`requirements.txt`):

```bash
pip install mcp
```

Jarvis imports it lazily. If it is missing, the rest of Jarvis keeps working and
the failure is noted in `command_errors.log`.

The example servers below are Node.js packages, so they also need Node.js 18+
(`node --version`) and `npx`.

## 2. Configure `MCP_SERVERS`

Add one JSON object to your `.env` file. It maps a **server name** to the
command that starts that server:

```dotenv
MCP_SERVERS='{"filesystem": "npx -y @modelcontextprotocol/server-filesystem /home/me/Documents"}'
```

The key (`filesystem`) is used in tool names and error messages. Keep it short,
lowercase and unique.

### Compact form

The value is split like a shell command line. This is the easiest form when the
command needs no extra environment variables:

```dotenv
MCP_SERVERS='{"filesystem": "npx -y @modelcontextprotocol/server-filesystem /home/me/Documents"}'
```

### Object form

Use the object form when you need environment variables, an explicit working
directory, or a command line that is easier to read as a list:

```dotenv
MCP_SERVERS='{"brave": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-brave-search"], "env": {"BRAVE_API_KEY": "your-key"}}}'
```

Supported fields:

| Field     | Required | Meaning                                            |
|-----------|----------|----------------------------------------------------|
| `command` | yes      | Executable to run (for example `npx` or `python`)  |
| `args`    | no       | List of arguments                                  |
| `env`     | no       | Extra environment variables for that server only   |
| `cwd`     | no       | Working directory for the server process           |

The server inherits Jarvis's environment, with `env` merged on top.

### Multiple servers

```dotenv
MCP_SERVERS='{"filesystem": "npx -y @modelcontextprotocol/server-filesystem /home/me/Documents", "brave": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-brave-search"], "env": {"BRAVE_API_KEY": "your-key"}}}'
```

A Claude Desktop style wrapper is also accepted, so an existing config can be
pasted in directly:

```dotenv
MCP_SERVERS='{"mcpServers": {"filesystem": "npx -y @modelcontextprotocol/server-filesystem /home/me/Documents"}}'
```

## 3. Popular servers

### Filesystem

Read, write, list and search files under directories you explicitly allow.
Pass one or more directory paths; the server cannot reach anything outside them.

```dotenv
MCP_SERVERS='{"filesystem": "npx -y @modelcontextprotocol/server-filesystem /home/me/Documents"}'
```

Windows example (use forward slashes or escaped backslashes inside JSON):

```dotenv
MCP_SERVERS='{"filesystem": "npx -y @modelcontextprotocol/server-filesystem C:/Users/me/Documents"}'
```

### Brave Search

Web search through the Brave Search API. Create a key at
<https://brave.com/search/api/> and pass it through `env`:

```dotenv
MCP_SERVERS='{"brave": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-brave-search"], "env": {"BRAVE_API_KEY": "your-key"}}}'
```

### Any other server

Any command that speaks MCP over stdio works. For a Python server:

```dotenv
MCP_SERVERS='{"my_tools": {"command": "python", "args": ["/home/me/my_mcp_server.py"], "cwd": "/home/me"}}'
```

## 4. Tool naming

Every MCP tool is exposed to the LLM with a collision-proof prefix:

```
mcp__<server>__<tool>
```

For example, the filesystem server's `read_file` tool from a server named
`filesystem` becomes `mcp__filesystem__read_file`. Characters that OpenAI does
not allow in function names are replaced with `_`, and names longer than 64
characters are shortened with a stable hash suffix.

When the LLM calls one of these names, Jarvis routes it to the matching server
instead of its native `_DISPATCH` tools.

## 5. Timeouts, retries and logging

Two optional settings tune behaviour:

```dotenv
MCP_TIMEOUT=15          # seconds for startup and for each tool call
MCP_RETRY_SECONDS=30    # retry a failed server after this many seconds
```

Discovered tools are cached briefly so servers are not restarted on every
conversation turn. If a server fails to start or a tool call fails:

- Jarvis does **not** crash and the current reply still completes.
- The full traceback is written to `~/.jarvis/logs/command_errors.log`
  (or `$JARVIS_LOG_DIR/command_errors.log`).
- The LLM receives a short error string such as
  `I couldn't execute that command, sir.` and can tell you what happened.

Inspect the log with:

```bash
python main.py --logs
```

## 6. Security notes

- MCP servers are separate processes with the same permissions as your user.
  Only configure servers you trust.
- Filesystem access is limited to the directories you pass on the command line.
  Do not point it at `/`, your home directory root, or credential folders.
- Prefer per-server `env` entries for API keys instead of putting secrets in the
  server command line, which may appear in process listings.
- Review a server's tool list before enabling it. Every discovered tool is
  offered to the LLM.
- `.env` is git-ignored; never commit real API keys.

## 7. Troubleshooting

**No MCP tools appear**

- Check that `MCP_SERVERS` is valid JSON in your `.env` (single quotes around the
  whole value are fine; double quotes inside).
- Confirm the command works on its own, for example:
  `npx -y @modelcontextprotocol/server-filesystem /home/me/Documents`.
- Make sure `mcp` is installed in the same Python environment as Jarvis.
- Look for `MCP server '...' failed to start` in `command_errors.log`.

**`npx` cannot be found**

Jarvis inherits the environment of the process that launched it. Start Jarvis
from a shell where `node`/`npx` are on `PATH`, or use the absolute path to the
executable in `command`.

**Server starts but a tool call times out**

Increase `MCP_TIMEOUT`. The first `npx` run may need extra time to download the
package.

**Configuration changes are not picked up immediately**

Discovery is cached for `MCP_RETRY_SECONDS` (default 30 seconds). Restart Jarvis
or wait for the cache to expire.

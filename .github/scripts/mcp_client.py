# SPDX-License-Identifier: Apache-2.0
"""Hold a session with the installed `ocura-oss mcp`, using the official MCP client library.

The test suite checks the server against this project's reading of the protocol; this
checks it against a client other people use.
"""

import asyncio
import json
import sys
import tempfile
from importlib.metadata import version

from mcp import ClientSession, MCPError, StdioServerParameters
from mcp.client.stdio import stdio_client

from ocura_oss import initialize, run


async def session(root: str, run_id: str) -> None:
    server = StdioServerParameters(
        command=sys.executable, args=["-m", "ocura_oss", "mcp", "--root", root]
    )
    async with stdio_client(server) as (read, write), ClientSession(read, write) as client:
        opened = await client.initialize()
        print("mcp", version("mcp"), "| protocol", opened.protocol_version)
        await client.send_ping()
        listed = await client.list_tools()
        names = [tool.name for tool in listed.tools]
        assert names == ["runs", "show", "logs", "compare", "verify", "attempts"], names

        async def call(name: str, **arguments: object) -> tuple[str, bool]:
            result = await client.call_tool(name, arguments)
            return result.content[0].text, bool(result.is_error)

        text, failed = await call("runs", limit=5)
        assert not failed and [item["id"] for item in json.loads(text)["runs"]] == [run_id], text
        text, failed = await call("show", run=run_id, command=True)
        assert not failed and json.loads(text)["run"]["id"] == run_id, text
        text, failed = await call("logs", run=run_id, tail=2)
        assert not failed and text.split() == ["beta", "gamma"], text
        text, failed = await call("logs", run=run_id, search="amm")
        assert not failed and text.split() == ["3:gamma"], text
        text, failed = await call("verify")
        assert not failed and json.loads(text)["status"] == "ok", text
        for name in ("compare", "attempts"):
            text, failed = await call(name)
            assert not failed and isinstance(json.loads(text), dict), (name, text)

        text, failed = await call("show", run="ffff")
        assert failed and json.loads(text)["error"]["message"], text
        try:
            await client.call_tool("run", {"command": ["echo"]})
        except MCPError as refused:
            print("a tool the server does not offer:", refused)
        else:
            raise AssertionError("the server accepted a call to `run`")


def main() -> None:
    with tempfile.TemporaryDirectory() as root:
        initialize(root)
        script = "print('alpha'); print('beta'); print('gamma')"
        record = run([sys.executable, "-c", script], root=root).record
        asyncio.run(session(root, record.id))
    print("MCP client session passed")


if __name__ == "__main__":
    main()

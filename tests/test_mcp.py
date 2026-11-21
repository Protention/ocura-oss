# SPDX-License-Identifier: Apache-2.0

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from ocura_oss import Store, cli, initialize, mcp, run

MODERN = {
    "io.modelcontextprotocol/protocolVersion": "2026-07-28",
    "io.modelcontextprotocol/clientCapabilities": {},
}


class McpServerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        initialize(self.root)
        script = "print('alpha'); print('beta'); print('gamma')"
        self.record = run([sys.executable, "-c", script], root=self.root).record
        self.server = mcp.Server(cli.main, self.root)

    def ask(self, method, params=None):
        request = {"jsonrpc": "2.0", "id": 7, "method": method, "params": params or {}}
        return self.server.answer(json.dumps(request).encode())

    def call(self, name, **arguments):
        result = self.ask("tools/call", {"name": name, "arguments": arguments})["result"]
        return result["content"][0]["text"], result["isError"]

    def test_a_client_that_opens_with_initialize_lists_and_calls_tools(self):
        requests = [
            {"id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
            {"method": "notifications/initialized"},
            {"id": 2, "method": "ping"},
            {"id": 3, "method": "tools/list"},
            {"id": 4, "method": "tools/call", "params": {"name": "runs"}},
        ]
        sent = "".join(json.dumps({"jsonrpc": "2.0", **item}) + "\n" for item in requests)
        served = subprocess.run(
            [sys.executable, "-m", "ocura_oss", "mcp", "--root", str(self.root)],
            input=sent.encode(),
            capture_output=True,
            check=False,
            timeout=30,
        )
        self.assertEqual(served.returncode, 0, served.stderr)
        # Stdout holds one reply per request and nothing else; the notification has none.
        opened, pinged, listed, called = (
            json.loads(line)["result"] for line in served.stdout.splitlines()
        )
        self.assertEqual(opened["protocolVersion"], "2025-06-18")
        self.assertEqual(pinged, {})
        self.assertEqual(
            [tool["name"] for tool in listed["tools"]],
            ["runs", "show", "logs", "compare", "verify", "attempts", "note", "tag"],
        )
        page = json.loads(called["content"][0]["text"])
        self.assertEqual([item["id"] for item in page["runs"]], [self.record.id])

    def test_a_request_that_names_its_version_needs_no_handshake(self):
        found = self.ask("server/discover", {"_meta": MODERN})["result"]
        self.assertEqual(found["resultType"], "complete")
        self.assertIn("2026-07-28", found["supportedVersions"])
        shown = self.ask(
            "tools/call",
            {"name": "show", "arguments": {"run": self.record.id[:8]}, "_meta": MODERN},
        )["result"]
        self.assertEqual(shown["resultType"], "complete")
        self.assertEqual(json.loads(shown["content"][0]["text"])["run"]["id"], self.record.id)

        other = {**MODERN, "io.modelcontextprotocol/protocolVersion": "1900-01-01"}
        refused = self.ask("tools/list", {"_meta": other})["error"]
        self.assertEqual((refused["code"], refused["data"]["requested"]), (-32022, "1900-01-01"))
        # Without a version, this method does not exist: the client falls back to initialize.
        self.assertEqual(self.ask("server/discover")["error"]["code"], -32601)

    def test_logs_returns_the_end_of_the_output_or_the_matching_lines(self):
        text, failed = self.call("logs", run=self.record.id, tail=2)
        self.assertEqual((text.split(), failed), (["beta", "gamma"], False))
        text, _failed = self.call("logs", run=self.record.id, search="amm")
        self.assertEqual(text.split(), ["3:gamma"])
        text, failed = self.call("logs", run=self.record.id, tail=0)
        self.assertEqual((json.loads(text)["error"]["code"], failed), ("invalid_input", True))

    def test_a_refused_call_is_an_error_and_a_check_that_finds_problems_is_not(self):
        for arguments in ({"run": "ffff"}, {"run": self.record.id, "root": "elsewhere"}, {}):
            text, failed = self.call("show", **arguments)
            self.assertTrue(failed and json.loads(text)["error"]["message"], arguments)
        unknown = self.ask("tools/call", {"name": "run", "arguments": {}})
        self.assertEqual(unknown["error"]["code"], -32602)

        store = Store(self.root)
        store.resolve_log_path(self.record, "stdout").unlink()
        text, failed = self.call("verify")
        self.assertEqual((json.loads(text)["status"], failed), ("failed", False))

    def test_note_and_tag_edit_a_runs_note_unless_the_server_reads_an_archive(self):
        text, _failed = self.call("tag", tags=["diverged", "baseline"], run=self.record.id[:8])
        self.assertEqual(json.loads(text)["note"]["tags"], ["baseline", "diverged"])
        self.call("tag", run=self.record.id, tags=["baseline"], remove=True)
        self.call("note", text="--lr was too high", run=self.record.id)  # Not read as an option.
        note = json.loads(self.call("show", run=self.record.id)[0])["note"]
        self.assertEqual((note["text"], note["tags"]), ("--lr was too high", ["diverged"]))
        page = json.loads(self.call("runs", tag="diverged")[0])
        self.assertEqual([item["id"] for item in page["runs"]], [self.record.id])
        for tags in ([3], [], ["two words"]):  # Refused by the server, the parser, the store.
            text, failed = self.call("tag", run=self.record.id, tags=tags)
            self.assertEqual((json.loads(text)["error"]["code"], failed), ("invalid_input", True))

        self.server = mcp.Server(cli.main, self.root, archive=True)
        text, failed = self.call("note", run=self.record.id, text="")
        self.assertEqual((json.loads(text)["error"]["code"], failed), ("invalid_state", True))
        self.assertEqual(json.loads(self.call("show", run=self.record.id)[0])["note"], note)


if __name__ == "__main__":
    unittest.main()

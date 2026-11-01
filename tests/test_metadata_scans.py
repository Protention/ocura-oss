# SPDX-License-Identifier: Apache-2.0

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ocura_oss import Store, branch, branching, cli, initialize, run


class MetadataScanTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        initialize(self.root)
        self.store = Store(self.root)

    def record(self, data=b"output\x00\xff\r\n", *, pathway=None, code=0):
        return run(
            [
                sys.executable,
                "-c",
                f"import sys; sys.stdout.buffer.write({data!r}); sys.exit({code})",
            ],
            root=self.root,
            pathway_id=pathway,
        )

    def test_pathway_and_comparison_discovery_scan_atoms_once(self):
        baseline = self.record()
        for reason in ("first", "second", "third"):
            child = branch(baseline.chokepoint.id, root=self.root, reason=reason)
            self.record(pathway=child.id)
        with patch.object(self.store, "list_atoms", wraps=self.store.list_atoms) as scans:
            self.assertEqual(len(cli._pathway_summaries(self.store)), 4)
            self.assertEqual(scans.call_count, 1)
        with patch.object(self.store, "list_atoms", wraps=self.store.list_atoms) as scans:
            self.assertEqual(len(branching.compare(self.store, baseline.chokepoint.id).children), 3)
            self.assertEqual(scans.call_count, 1)


if __name__ == "__main__":
    unittest.main()

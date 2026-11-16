# SPDX-License-Identifier: Apache-2.0
# Copyright Tony Burns

"""Check extraction of the awrel MCP client version from JSON arguments."""

from pathlib import Path
import re
import unittest

from renovate_allowlist_test import parse_preset


class AwrelPinTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        config = parse_preset((Path(__file__).parent / "default.json5").read_text())
        cls.manager = next(manager for manager in config["customManagers"]
                           if manager.get("depNameTemplate") == "tbhb-dev/awrel")
        # Python spells the named capture differently from Renovate's RE2.
        cls.pattern = re.compile(cls.manager["matchStrings"][0].replace("(?<", "(?P<"))

    def test_manager(self):
        self.assertEqual(self.manager["customType"], "regex")
        self.assertEqual(self.manager["datasourceTemplate"], "github-tags")
        files = self.manager["managerFilePatterns"][0][1:-1]
        for name in (".mcp.json", "tools/.mcp.json"):
            self.assertIsNotNone(re.search(files, name))
        for name in ("mcp.json", ".mcp.json5", "other.json"):
            self.assertIsNone(re.search(files, name))

    def test_json_arguments(self):
        for source in (
            '"args": ["run", "go.tbhb.dev/awrel/cmd/awrel@v0.5.0", "mcp"],',
            '"args": [\n  "run",\n  "go.tbhb.dev/awrel/cmd/awrel@v0.5.0",\n  "mcp"\n],',
        ):
            with self.subTest(source=source):
                matches = list(self.pattern.finditer(source))
                self.assertEqual([match["currentValue"] for match in matches], ["v0.5.0"])

    def test_unrelated_and_comment_lines(self):
        for source in (
            '"args": ["run", "example.com/other/cmd/other@v0.5.0", "mcp"],',
            '# go run go.tbhb.dev/awrel/cmd/awrel@v0.5.0 mcp',
        ):
            with self.subTest(source=source):
                self.assertIsNone(self.pattern.search(source))


if __name__ == "__main__":
    unittest.main()

# SPDX-License-Identifier: Apache-2.0
# Copyright Tony Burns

"""Run the inline consistency check in caller checkout fixtures."""

import os
from pathlib import Path
import re
import subprocess
import tempfile
import textwrap
import unittest


WORKFLOW = Path(__file__).parent / ".github/workflows/renovate-config-validator.yaml"
SHA = "a" * 40
OTHER = "b" * 40


def workflow_script():
    step = re.search(
        r"^      - name: Check renovate-config pin consistency\n(?P<step>.*?)(?=^      - name:|\Z)",
        WORKFLOW.read_text(), re.MULTILINE | re.DOTALL,
    )
    if step is None:
        raise AssertionError("Missing pin consistency step")
    script = re.search(r"^        run: \|\n(?P<script>(?:^          .*\n|^\n)+)", step["step"], re.MULTILINE)
    if script is None or "${{" in script["script"]:
        raise AssertionError("Expected a literal run block using environment variables")
    return textwrap.dedent(script["script"])


class PinConsistencyTest(unittest.TestCase):
    def check_pins(self, preset_ref, workflow_ref, expected):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".github/workflows").mkdir(parents=True)
            config = '{extends: []}' if preset_ref is None else (
                '{extends: ["github>tbhb-dev/renovate-config//default.json5#' + preset_ref + '",],}'
            )
            (root / "renovate.json5").write_text(config)
            for suffix in ("yaml", "yml"):
                content = "name: no references\n" if workflow_ref is None else (
                    "jobs:\n  validate:\n    uses: tbhb-dev/renovate-config/.github/workflows/"
                    + "renovate-config-validator.yaml@" + workflow_ref + " # main\n"
                )
                (root / f".github/workflows/ci.{suffix}").write_text(content)
            result = subprocess.run(
                ["bash", "--noprofile", "--norc", "-e", "-o", "pipefail", "-c", workflow_script()],
                cwd=root, env={**os.environ, "CONFIGS": "renovate.json5\n\n"},
                capture_output=True, text=True, check=False,
            )
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        self.assertEqual(result.stderr, "")
        return result.stdout

    def test_all_equal(self):
        self.assertIn("3 references, all pins consistent", self.check_pins(SHA, SHA, 0))

    def test_different_workflow_pin(self):
        output = self.check_pins(SHA, OTHER, 1)
        self.assertIn(f"renovate.json5: {SHA}", output)
        self.assertIn(f".github/workflows/ci.yaml: {OTHER}", output)
        self.assertIn(f".github/workflows/ci.yml: {OTHER}", output)

    def test_short_sha_and_untrusted_ref(self):
        for ref in ("abc123", "main", "::warning::injected"):
            with self.subTest(ref=ref):
                output = self.check_pins(SHA, ref, 1)
                self.assertIn("(non-sha ref)", output)
                self.assertNotIn(ref, output)

    def test_no_references(self):
        self.assertIn("0 references", self.check_pins(None, None, 0))


if __name__ == "__main__":
    unittest.main()

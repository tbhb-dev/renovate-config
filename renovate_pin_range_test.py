# SPDX-License-Identifier: Apache-2.0
# Copyright Tony Burns

"""Exercise the inline pin-range script with Git trees and a fake gh API."""

import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import textwrap
import unittest


WORKFLOW = Path(__file__).parent / ".github/workflows/renovate-config-validator.yaml"
OLD = "a" * 40
NEW = "b" * 40


def workflow_script():
    step = re.search(
        r"^      - name: Summarize pin-bump ranges\n(?P<step>.*?)(?=^      - name:|\Z)",
        WORKFLOW.read_text(), re.MULTILINE | re.DOTALL,
    )
    if step is None or "if: github.event_name == 'pull_request'" not in step["step"]:
        raise AssertionError("Missing pull request pin-range step")
    script = re.search(r"^        run: \|\n(?P<script>(?:^          .*\n|^\n)+)", step["step"], re.MULTILINE)
    if script is None or "${{" in script["script"]:
        raise AssertionError("Expected a literal run block using environment variables")
    for name, value in (("BASE_SHA", "base"), ("HEAD_SHA", "head")):
        if f"{name}: ${{{{ github.event.pull_request.{value}.sha }}}}" not in step["step"]:
            raise AssertionError("Missing event sha environment variable")
    return textwrap.dedent(script["script"])


def pin_files(ref):
    return {
        "renovate.json5": '{extends: ["github>tbhb-dev/renovate-config//default.json5#' + ref + '",],}',
        ".github/workflows/ci.yaml": "jobs:\n  check:\n    uses: tbhb-dev/renovate-config/"
        + ".github/workflows/renovate-config-validator.yaml@" + ref + " # main\n",
        ".github/workflows/rules.yml": "jobs:\n  check:\n    uses: tbhb-dev/rules/"
        + ".github/workflows/ci.yml@" + ref + " # main\n",
        "MODULE.bazel": 'git_override(\n    commit = "' + ref
        + '",\n    module_name = "tbhb_rules",\n)\n',
    }


class PinRangeTest(unittest.TestCase):
    def run_fixture(self, new=NEW, status="ahead", unchanged=False, api_error=False, added=False, only=None):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(["git", "init", "-q", str(root)], check=True, capture_output=True)
            # Import fixture objects without invoking the host's identity hooks or signer.
            stream = bytearray()
            for branch, files in (("base", {} if added else pin_files(OLD)),
                                  ("head", pin_files(OLD if unchanged else new))):
                stream.extend(f"commit refs/heads/{branch}\ncommitter Fixture <fixture@example.test> 1 +0000\ndata 7\nfixture\n".encode())
                if branch == "head":
                    stream.extend(b"from refs/heads/base\n")
                for name, content in files.items():
                    if only is not None and name != only:
                        continue
                    data = content.encode()
                    stream.extend(f"M 100644 inline {name}\ndata {len(data)}\n".encode() + data + b"\n")
                stream.extend(b"\n")
            subprocess.run(["git", "fast-import", "--quiet"], input=stream, cwd=root, check=True, capture_output=True)
            base, head = [subprocess.check_output(["git", "rev-parse", f"refs/heads/{branch}"], cwd=root).decode().strip()
                          for branch in ("base", "head")]
            binary = root / "bin"
            binary.mkdir()
            fake = binary / "gh"
            fake.write_text('''#!/usr/bin/env python3
import json
import os
from pathlib import Path
import sys
endpoint = sys.argv[2]
with open(os.environ["CALLS"], "a") as output:
    output.write(endpoint + "\\n")
if os.environ["API_ERROR"] == "yes":
    print("::warning::private API error", file=sys.stderr)
    sys.exit(1)
if endpoint.endswith("...main"):
    print(json.dumps({"status": os.environ["STATUS"]}))
else:
    print(Path(os.environ["RESPONSE"]).read_text())
''')
            fake.chmod(0o755)
            response = root / "response.json"
            response.write_text(json.dumps({
                "commits": [{"sha": f"{index:040x}", "commit": {"message": "::warning::subject " + str(index) + "\nbody"}}
                            for index in range(55)],
                "files": [{"filename": "MODULE.bazel"}, {"filename": "::error::file\nnext"}],
            }))
            summary = root / "summary.md"
            calls = root / "calls"
            result = subprocess.run(
                ["bash", "--noprofile", "--norc", "-e", "-o", "pipefail", "-c", workflow_script()], cwd=root,
                env={**os.environ, "PATH": str(binary) + os.pathsep + os.environ["PATH"],
                     "BASE_SHA": base, "HEAD_SHA": head, "CONFIGS": "renovate.json5\n",
                     "GH_TOKEN": "fixture-token", "GITHUB_STEP_SUMMARY": str(summary),
                     "CALLS": str(calls), "RESPONSE": str(response), "STATUS": status,
                     "API_ERROR": "yes" if api_error else "no"},
                text=True, capture_output=True, check=False,
            )
            return result, summary.read_text() if summary.exists() else "", calls.read_text() if calls.exists() else ""

    def test_changed_pins_on_main(self):
        for status in ("ahead", "identical"):
            with self.subTest(status=status):
                result, summary, calls = self.run_fixture(status=status)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(result.stderr, "")
                self.assertIn("tbhb-dev/renovate-config", summary)
                self.assertIn("tbhb-dev/rules", summary)
                self.assertIn(f"{OLD} -> {NEW}", summary)
                self.assertIn("MODULE.bazel", summary)
                self.assertIn("subject 49", summary)
                self.assertNotIn("subject 50", summary)
                self.assertNotIn("subject", result.stdout)
                self.assertFalse(any(line.startswith("::") for line in summary.splitlines()))
                self.assertEqual(calls.count("...main"), 2)
                self.assertIn(f"{OLD}...{NEW}?per_page=50&page=1", calls)

    def test_each_pin_format(self):
        for name in pin_files(NEW):
            with self.subTest(file=name):
                result, summary, calls = self.run_fixture(only=name)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn(name, summary)
                self.assertEqual(calls.count("...main"), 1)
                result, _, calls = self.run_fixture(only=name, new="abcdef0")
                self.assertEqual(result.returncode, 1)
                self.assertEqual(calls, "")

    def test_short_sha(self):
        result, _, calls = self.run_fixture(new="abcdef0")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "::error::Changed pin must use a full 40-character sha\n")
        self.assertEqual(calls, "")

    def test_not_on_main(self):
        for status in ("diverged", "behind"):
            with self.subTest(status=status):
                result, _, _ = self.run_fixture(status=status)
                self.assertEqual(result.returncode, 1)
                self.assertEqual(result.stdout, f"::error::Changed pin is not on main: {NEW}\n")

    def test_no_changed_pins(self):
        result, summary, calls = self.run_fixture(unchanged=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("No pin changes.", summary)
        self.assertEqual(calls, "")

    def test_api_failure_is_sanitized(self):
        result, summary, _ = self.run_fixture(api_error=True)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stderr, "")
        self.assertEqual(result.stdout, "::error::Pin range check could not read history or compare pins\n")
        self.assertEqual(summary, "")

    def test_new_pins(self):
        result, summary, calls = self.run_fixture(added=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("New pin has no previous range", summary)
        self.assertEqual(calls.count("...main"), 2)
        self.assertNotIn("per_page", calls)


if __name__ == "__main__":
    unittest.main()

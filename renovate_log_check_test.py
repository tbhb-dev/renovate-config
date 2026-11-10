# SPDX-License-Identifier: Apache-2.0
# Copyright Tony Burns

"""Run the reusable workflow's actual log check against local NDJSON fixtures."""

import os
from pathlib import Path
import re
import subprocess
import tempfile
import textwrap
import unittest


WORKFLOW = Path(__file__).parent / ".github/workflows/renovate.yaml"
FINISHED = '{"level":30,"msg":"Repository finished"}\n'
STARTED = '{"level":30,"msg":"Repository started"}\n'


def workflow_script():
    """Extract one literal run block; fail if the workflow changes its shape."""
    step = re.search(
        r"^      - name: Check Renovate log\n(?P<step>.*?)(?=^      - name:|\Z)",
        WORKFLOW.read_text(),
        re.MULTILINE | re.DOTALL,
    )
    if step is None:
        raise AssertionError("Missing log check step")
    script = re.search(
        r"^        run: \|\n(?P<script>(?:^          .*\n|^\n)+)",
        step["step"],
        re.MULTILINE,
    )
    if script is None:
        raise AssertionError("Expected a literal run block")
    if "${{" in script["script"]:
        raise AssertionError("Run block must use environment variables")
    return textwrap.dedent(script["script"])


class RenovateLogCheckTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.script = workflow_script()

    def check_log(self, fixture, expected, annotations=(), summary=None):
        # Handwritten fixture records use the pinned logger's level/msg/branch fields.
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "fixture.ndjson"
            if fixture is not None:
                log.write_text(fixture)
            result = subprocess.run(
                ["bash", "--noprofile", "--norc", "-e", "-o", "pipefail", "-c", self.script],
                env={**os.environ, "LOG_FILE": str(log)},
                text=True,
                capture_output=True,
                check=False,
            )
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        self.assertEqual(result.stderr, "")
        expected_lines = [f"::error::{message}" for message in annotations]
        if summary is not None:
            expected_lines.append(summary)
        # Exact output comparison catches extra fields, parser leaks and commands.
        self.assertEqual(result.stdout.splitlines(), expected_lines)

    def test_clean_and_noise(self):
        self.check_log(STARTED + FINISHED, 0, summary="Renovate log check: 2 entries, 0 failures")
        self.check_log(
            STARTED
            + '{"level":40,"msg":"Hidden Unicode characters have been discovered…"}\n'
            + '{"level":40,"msg":"`mise lock` requires…"}\n'
            + FINISHED,
            0,
            summary="Renovate log check: 4 entries, 0 failures",
        )

    def test_known_failures(self):
        # Expected labels are independent of the workflow allowlist.
        fixtures = [
            ('{"level":40,"msg":"Error updating branch","branch":"renovate/bazel-9.x"}\n',
             "Error updating branch"),
            ('{"level":40,"msg":"artifactErrors","branch":"renovate/bazel-9.x"}\n',
             "artifactErrors"),
            ('{"level":40,"msg":"Failed to update MODULE.bazel.lock","branch":"renovate/bazel-9.x"}\n',
             "Failed to update MODULE.bazel.lock"),
        ]
        for fixture, label in fixtures:
            with self.subTest(label=label):
                self.check_log(
                    fixture + FINISHED, 1, [f"{label} (branch: renovate/bazel-9.x)"]
                )

    def test_error_and_fatal(self):
        for level in (50, 60, 70):
            with self.subTest(level=level):
                self.check_log(
                    '{"level":' + str(level) + ',"msg":"private stderr","branch":"renovate/pkg"}\n'
                    + FINISHED,
                    1,
                    ["error-level entry (branch: renovate/pkg)"],
                )

    def test_mechanism_failures(self):
        for fixture, annotation in (
            (None, "Renovate log file is missing"),
            ("", "Renovate log file is empty"),
            ("   \n", "Renovate log file is unparseable"),
            (FINISHED + '{"level":40,"msg":"::warning::private stderr"',
             "Renovate log file is unparseable"),
            (STARTED, "Renovate log is missing the end-of-run marker"),
            ('{"level":"50","msg":"bad level"}\n' + FINISHED,
             "Renovate log file is unparseable"),
            ('{"level":30}\n' + FINISHED, "Renovate log file is unparseable"),
            ('null\n' + FINISHED, "Renovate log file is unparseable"),
            ('[]\n' + FINISHED, "Renovate log file is unparseable"),
        ):
            with self.subTest(fixture=fixture):
                self.check_log(fixture, 1, [annotation])

    def test_annotations_cannot_inject_commands(self):
        # Includes a trailing newline: jq's regex end anchor alone accepts it.
        for branch in ('"::warning::injected"', '"safe\\n::warning::injected"',
                       '"safe\\n"', '""', '"' + "a" * 201 + '"', 'null', '42'):
            with self.subTest(branch=branch):
                self.check_log(
                    '{"level":40,"msg":"artifactErrors","branch":' + branch
                    + ',"stderr":"\\n::warning::private stderr"}\n' + FINISHED,
                    1,
                    ["artifactErrors (branch: (no branch))"],
                )
        self.check_log(
            '{"level":50,"msg":"\\n::warning::injected","branch":"::warning::injected"}\n'
            + FINISHED,
            1,
            ["error-level entry (branch: (no branch))"],
        )
        self.check_log(
            '{"level":40,"msg":"Error updating branch\\n::warning::injected"}\n'
            + '{"level":40,"msg":"::warning::injected"}\n'
            + FINISHED,
            0,
            summary="Renovate log check: 3 entries, 0 failures",
        )

    def test_missing_branch_and_all_failures_reported(self):
        self.check_log(
            '{"level":40,"msg":"artifactErrors"}\n'
            + '{"level":50,"msg":"Error updating branch","branch":"renovate/a_B-1.2"}\n'
            + FINISHED,
            1,
            ["artifactErrors (branch: (no branch))",
             "error-level entry (branch: renovate/a_B-1.2)"],
        )

    def test_branch_length_boundary(self):
        branch = "a" * 200
        self.check_log(
            '{"level":40,"msg":"artifactErrors","branch":"' + branch + '"}\n' + FINISHED,
            1,
            [f"artifactErrors (branch: {branch})"],
        )


if __name__ == "__main__":
    unittest.main()

# SPDX-License-Identifier: Apache-2.0
# Copyright Tony Burns

"""Run the automerge audit's inline script with fake GitHub API responses."""

import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import textwrap
import unittest


WORKFLOW = Path(__file__).parent / ".github/workflows/renovate.yaml"
APPROVAL = {"type": "pull_request", "ruleset_id": 42,
            "parameters": {"required_approving_review_count": 1}}
STATUS = {"type": "required_status_checks", "ruleset_id": 73,
          "parameters": {"required_status_checks": [{"context": "conclusion"}]}}


def workflow_script():
    source = WORKFLOW.read_text()
    step = re.search(
        r"^      - name: Audit automerge rulesets\n(?P<step>.*?)(?=^      - name:|\Z)",
        source, re.MULTILINE | re.DOTALL,
    )
    if step is None:
        raise AssertionError("Missing automerge audit step")
    if not source.index("- name: Get token") < step.start() < source.index("- name: Self-hosted Renovate"):
        raise AssertionError("Audit must run after token creation and before Renovate")
    if "GH_TOKEN: ${{ steps.token.outputs.token }}" not in step["step"]:
        raise AssertionError("Audit must use the Renovate App token")
    script = re.search(r"^        run: \|\n(?P<script>(?:^          .*\n|^\n)+)", step["step"], re.MULTILINE)
    if script is None or "${{" in script["script"]:
        raise AssertionError("Expected a literal run block using environment variables")
    return textwrap.dedent(script["script"])


class RulesetAuditTest(unittest.TestCase):
    def run_fixture(self, rules=None, bypass="never", api_error=False, extra_rules=(), missing_field=False):
        rules = [APPROVAL, STATUS] if rules is None else rules
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = root / "fixture.json"
            fixture.write_text(json.dumps({"pages": [rules, list(extra_rules)], "bypass": bypass}))
            calls = root / "calls"
            fake = root / "gh"
            fake.write_text('''#!/usr/bin/env python3
import json
import os
from pathlib import Path
import sys
endpoint = sys.argv[-1]
with open(os.environ["CALLS"], "a") as output:
    output.write(endpoint + "\\n")
if os.environ["API_ERROR"] == "yes":
    print("::warning::private API failure", file=sys.stderr)
    sys.exit(1)
fixture = json.loads(Path(os.environ["FIXTURE"]).read_text())
if "/rules/branches/" in endpoint:
    assert "--paginate" in sys.argv and "--slurp" in sys.argv
    print(json.dumps(fixture["pages"]))
else:
    assert "/rulesets/" in endpoint
    print(json.dumps({} if os.environ["MISSING_FIELD"] == "yes" else
                     {"current_user_can_bypass": fixture["bypass"]}))
''')
            fake.chmod(0o755)
            result = subprocess.run(
                ["bash", "--noprofile", "--norc", "-e", "-o", "pipefail", "-c", workflow_script()],
                cwd=root, env={**os.environ, "PATH": str(root) + os.pathsep + os.environ["PATH"],
                               "GH_TOKEN": "fixture-app-token", "REPO": "fixture/repo", "FIXTURE": str(fixture),
                               "CALLS": str(calls), "API_ERROR": "yes" if api_error else "no",
                               "MISSING_FIELD": "yes" if missing_field else "no"},
                text=True, capture_output=True, check=False,
            )
            self.assertEqual(result.stderr, "")
            return result, calls.read_text().splitlines()

    def assert_failure(self, message, **fixture):
        result, calls = self.run_fixture(**fixture)
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertEqual(result.stdout, f"::error::{message}\n")
        return calls

    def test_protected_repo_and_all_rulesets(self):
        extra = {"type": "deletion", "ruleset_id": 99}
        result, calls = self.run_fixture(extra_rules=[extra, APPROVAL])
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(result.stdout, "Automerge ruleset audit: approval and status checks required, 3 rulesets cannot be bypassed\n")
        self.assertEqual(calls, ["repos/fixture/repo/rules/branches/main?per_page=100",
                                 "repos/fixture/repo/rulesets/42", "repos/fixture/repo/rulesets/73",
                                 "repos/fixture/repo/rulesets/99"])

    def test_no_rules(self):
        self.assert_failure("Default branch has no approval rule requiring at least one review", rules=[])

    def test_no_approvals(self):
        zero = {**APPROVAL, "parameters": {"required_approving_review_count": 0}}
        self.assert_failure("Default branch has no approval rule requiring at least one review", rules=[zero, STATUS])

    def test_no_status_checks(self):
        self.assert_failure("Default branch has no status-check rule", rules=[APPROVAL])

    def test_bypass_is_refused(self):
        for bypass in ("always", "pull_requests_only"):
            with self.subTest(bypass=bypass):
                self.assert_failure("the Renovate App can bypass ruleset 42", bypass=bypass)

    def test_missing_bypass_field_fails_closed(self):
        self.assert_failure("the Renovate App can bypass ruleset 42", missing_field=True)

    def test_api_failure_is_sanitized(self):
        self.assert_failure("Could not verify automerge ruleset protection", api_error=True)

    def test_invalid_ruleset_id_fails_closed(self):
        self.assert_failure("Could not verify ruleset bypass protection", extra_rules=[
            {"type": "deletion", "ruleset_id": "::warning::injected"},
        ])


if __name__ == "__main__":
    unittest.main()

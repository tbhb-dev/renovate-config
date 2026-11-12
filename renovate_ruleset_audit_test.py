# SPDX-License-Identifier: Apache-2.0
# Copyright Tony Burns

"""Run the automerge audit's inline script against a fake GitHub API over TLS."""

# cspell:ignore newkey pkeyopt paramgen keyout addext
import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
import re
import shutil
import ssl
import subprocess
import sys
import tempfile
import textwrap
import threading
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
    def run_fixture(self, pages=None, bypass="never", status=200, extra_rules=(), missing_field=False,
                    require_approval=None, mode="", api_url=None, token="fixture-app-token", path=None):
        pages = [[APPROVAL, STATUS, *extra_rules]] if pages is None else pages
        requests = []

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                requests.append((self.path, self.headers.get("Authorization")))
                if status != 200:
                    self.send_response(status)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                if mode == "redirect":
                    self.send_response(302)
                    self.send_header("Location", "https://localhost:1/elsewhere")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                if mode == "garbage":
                    body = b"not json"
                elif mode == "deep":
                    body = b"[" * 200000 + b"]" * 200000
                elif "/rules/branches/" in self.path:
                    number = int(re.search(r"[&?]page=([0-9]+)", self.path)[1])
                    body = json.dumps(pages[number - 1] if number <= len(pages) else []).encode()
                else:
                    body = json.dumps({} if missing_field else {"current_user_can_bypass": bypass}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            # The step accepts only https, so serve the fake API over TLS with a throwaway certificate.
            certificate, key = root / "cert.pem", root / "key.pem"
            subprocess.run(
                ["openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1", "-nodes",
                 "-keyout", str(key), "-out", str(certificate), "-days", "1", "-subj", "/CN=localhost",
                 "-addext", "subjectAltName=DNS:localhost"],
                check=True, capture_output=True,
            )
            server = HTTPServer(("127.0.0.1", 0), Handler)
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(certificate, key)
            server.socket = context.wrap_socket(server.socket, server_side=True)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            search_path = os.environ["PATH"]
            if mode == "no-curl":
                # A directory with only the tools the script needs, so curl cannot be found.
                tools = root / "tools"
                tools.mkdir()
                for name in ("bash", "python3"):
                    (tools / name).symlink_to((shutil.which(name) if name == "bash" else os.path.realpath(sys.executable)))
                search_path = str(tools)
            try:
                result = subprocess.run(
                    ["bash", "--noprofile", "--norc", "-e", "-o", "pipefail", "-c", workflow_script()], cwd=root,
                    env={**os.environ, "PATH": search_path,
                         "GH_TOKEN": token, "REPO": "fixture/repo",
                         "GITHUB_API_URL": (api_url or "https://localhost:{port}").format(port=server.server_port),
                         "SSL_CERT_FILE": str(certificate), "CURL_CA_BUNDLE": str(certificate),
                         **({} if require_approval is None else {"REQUIRE_APPROVAL": require_approval})},
                    text=True, capture_output=True, check=False, timeout=120,
                )
            finally:
                server.shutdown()
                server.server_close()
            self.assertEqual(result.stderr, "")
            self.assertTrue(all(auth == f"Bearer {token}" for _, auth in requests))
            return result, [path for path, _ in requests]

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
        self.assertEqual(calls, ["/repos/fixture/repo/rules/branches/main?per_page=100&page=1",
                                 "/repos/fixture/repo/rulesets/42", "/repos/fixture/repo/rulesets/73",
                                 "/repos/fixture/repo/rulesets/99"])

    def test_rules_on_later_pages_count(self):
        filler = [{"type": "deletion", "ruleset_id": 99}] * 100
        result, calls = self.run_fixture(pages=[filler, [APPROVAL, STATUS]])
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(calls[:2], ["/repos/fixture/repo/rules/branches/main?per_page=100&page=1",
                                     "/repos/fixture/repo/rules/branches/main?per_page=100&page=2"])

    def test_endless_pages_fail_closed(self):
        filler = [{"type": "deletion", "ruleset_id": 99}] * 100
        calls = self.assert_failure("Could not verify default branch rules", pages=[filler] * 10)
        self.assertEqual(len(calls), 10)

    def test_no_rules(self):
        self.assert_failure("Default branch has no approval rule requiring at least one review", pages=[[]])

    def test_no_approvals(self):
        zero = {**APPROVAL, "parameters": {"required_approving_review_count": 0}}
        self.assert_failure("Default branch has no approval rule requiring at least one review", pages=[[zero, STATUS]])

    def test_approval_kept_unless_input_is_false(self):
        zero = {**APPROVAL, "parameters": {"required_approving_review_count": 0}}
        for value in ("true", "", "False"):
            with self.subTest(value=value):
                self.assert_failure("Default branch has no approval rule requiring at least one review",
                                    pages=[[zero, STATUS]], require_approval=value)

    def test_approval_not_required(self):
        zero = {**APPROVAL, "parameters": {"required_approving_review_count": 0}}
        result, _ = self.run_fixture(pages=[[zero, STATUS]], require_approval="false")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(result.stdout, "Automerge ruleset audit: status checks required, approval check off, 2 rulesets cannot be bypassed\n")

    def test_status_checks_still_required_without_approval(self):
        self.assert_failure("Default branch has no status-check rule", pages=[[]], require_approval="false")

    def test_bypass_still_refused_without_approval(self):
        self.assert_failure("the Renovate App can bypass ruleset 42", bypass="always", require_approval="false")

    def test_no_status_checks(self):
        self.assert_failure("Default branch has no status-check rule", pages=[[APPROVAL]])

    def test_bypass_is_refused(self):
        for bypass in ("always", "pull_requests_only"):
            with self.subTest(bypass=bypass):
                self.assert_failure("the Renovate App can bypass ruleset 42", bypass=bypass)

    def test_missing_bypass_field_fails_closed(self):
        self.assert_failure("the Renovate App can bypass ruleset 42", missing_field=True)

    def test_http_status_is_reported(self):
        for status in (403, 404, 500):
            with self.subTest(status=status):
                calls = self.assert_failure(f"Could not verify automerge ruleset protection (HTTP {status})", status=status)
                self.assertEqual(len(calls), 1)

    def test_missing_curl_has_its_own_message(self):
        result, calls = self.run_fixture(mode="no-curl")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "::error::Automerge audit could not run curl\n")
        self.assertEqual(calls, [])

    def test_redirects_are_refused(self):
        self.assert_failure("Could not verify automerge ruleset protection (HTTP 302)", mode="redirect")

    def test_unreadable_data(self):
        for mode in ("garbage", "deep"):
            with self.subTest(mode=mode):
                self.assert_failure("Could not verify automerge ruleset protection (unreadable data)", mode=mode)

    def test_api_url_must_be_plain_https(self):
        for url in ("http://localhost:{port}", "https://user:secret@localhost:{port}",
                    "https://localhost:{port}\nheader = \"X-Injected: yes\"", "https://localhost:{port}/\"",
                    "https://localhost:{port} "):
            with self.subTest(url=url):
                calls = self.assert_failure("Automerge audit refuses a GitHub API URL that is not plain https", api_url=url)
                self.assertEqual(calls, [])

    def test_odd_token_is_refused(self):
        calls = self.assert_failure("Automerge audit: the job token has unexpected characters", token="a\"b")
        self.assertEqual(calls, [])

    def test_invalid_ruleset_id_fails_closed(self):
        self.assert_failure("Could not verify ruleset bypass protection", extra_rules=[
            {"type": "deletion", "ruleset_id": "::warning::injected"},
        ])


if __name__ == "__main__":
    unittest.main()

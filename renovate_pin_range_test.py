# SPDX-License-Identifier: Apache-2.0
# Copyright Tony Burns

"""Exercise the inline pin-range script with Git trees and a fake GitHub API server."""

from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import textwrap
import threading
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
    def run_fixture(self, new=NEW, status="ahead", unchanged=False, api_error=False, added=False, only=None, mode="", api_url=None):
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
            # The step must not need gh: a gh on PATH that fails would break it if used.
            binary = root / "bin"
            binary.mkdir()
            fake = binary / "gh"
            fake.write_text("#!/bin/sh\necho gh must not be called >&2\nexit 99\n")
            fake.chmod(0o755)
            response = {
                "commits": [{"sha": f"{index:040x}", "commit": {"message": "::warning::subject " + str(index) + "\nbody"}}
                            for index in range(55)],
                "files": [{"filename": "MODULE.bazel"}, {"filename": "::error::file\nnext"}],
            }
            requests = []

            class Handler(BaseHTTPRequestHandler):
                def do_GET(self):
                    requests.append((self.path, self.headers.get("Authorization")))
                    if self.path == "/elsewhere":
                        code, body = 200, {"status": "ahead"}
                    elif api_error:
                        code, body = 500, {"message": "::warning::private API error"}
                    elif mode == "bad-status":
                        self.wfile.write(b"HTTP/1.1 ::warning::fixture-token\r\n\r\n")
                        return
                    elif mode == "truncated":
                        self.send_response(200)
                        self.send_header("Content-Length", "100")
                        self.end_headers()
                        self.wfile.write(b"{}")
                        return
                    elif mode == "big":
                        data = b" " * (8 * 1024 * 1024 + 1)
                        self.send_response(200)
                        self.send_header("Content-Length", str(len(data)))
                        self.end_headers()
                        self.wfile.write(data)
                        return
                    elif mode.startswith("redirect"):
                        data = b"{}"
                        self.send_response(int(mode.removeprefix("redirect:")))
                        self.send_header("Location", "/elsewhere")
                        self.send_header("Content-Length", "2")
                        self.end_headers()
                        self.wfile.write(data)
                        return
                    elif self.path.endswith("...main"):
                        code, body = 200, {"status": status}
                    else:
                        code, body = 200, response
                    data = b"not json" if mode == "garbage" else json.dumps(body).encode()
                    self.send_response(code)
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)

                def log_message(self, *args):
                    pass

            server = HTTPServer(("127.0.0.1", 0), Handler)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            summary = root / "summary.md"
            try:
                result = subprocess.run(
                    ["bash", "--noprofile", "--norc", "-e", "-o", "pipefail", "-c", workflow_script()], cwd=root,
                    env={**os.environ, "PATH": str(binary) + os.pathsep + os.environ["PATH"],
                         "BASE_SHA": base, "HEAD_SHA": head, "CONFIGS": "renovate.json5\n",
                         "GH_TOKEN": "fixture-token", "GITHUB_STEP_SUMMARY": str(summary),
                         "GITHUB_API_URL": api_url or f"http://127.0.0.1:{server.server_port}"},
                    text=True, capture_output=True, check=False,
                )
            finally:
                server.shutdown()
                server.server_close()
            self.assertTrue(all(auth == "Bearer fixture-token" for _, auth in requests))
            calls = "".join(path + "\n" for path, _ in requests)
            return result, summary.read_text() if summary.exists() else "", calls

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
        self.assertEqual(result.stdout, "::error::Pin range check could not reach the GitHub API (HTTP 500)\n")
        self.assertEqual(summary, "")

    def test_redirects_are_refused(self):
        for code in (301, 302, 303, 307, 308):
            with self.subTest(code=code):
                result, summary, calls = self.run_fixture(mode=f"redirect:{code}")
                self.assertEqual(result.returncode, 1)
                self.assertEqual(result.stdout, f"::error::Pin range check could not reach the GitHub API (HTTP {code})\n")
                self.assertEqual(result.stderr, "")
                self.assertEqual(len(calls.splitlines()), 1)
                self.assertNotIn("/elsewhere", calls)
                self.assertEqual(summary, "")

    def test_oversized_reply_is_refused(self):
        result, summary, calls = self.run_fixture(mode="big")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "::error::Pin range check: the GitHub API returned too much data\n")
        self.assertEqual(result.stderr, "")
        self.assertEqual(len(calls.splitlines()), 1)
        self.assertEqual(summary, "")

    def test_non_https_api_url_is_refused(self):
        result, summary, calls = self.run_fixture(api_url="http://api.example.test")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "::error::Pin range check refuses a GitHub API URL that is not https\n")
        self.assertEqual(result.stderr, "")
        self.assertEqual(calls, "")
        self.assertEqual(summary, "")

    def test_protocol_errors_report_only_the_class(self):
        for mode, name in (("bad-status", "BadStatusLine"), ("truncated", "IncompleteRead")):
            with self.subTest(mode=mode):
                result, summary, calls = self.run_fixture(mode=mode)
                self.assertEqual(result.returncode, 1)
                self.assertEqual(result.stdout, f"::error::Pin range check could not reach the GitHub API ({name})\n")
                self.assertEqual(result.stderr, "")
                self.assertEqual(summary, "")
                self.assertEqual(len(calls.splitlines()), 1)

    def test_unreadable_data(self):
        result, summary, _ = self.run_fixture(mode="garbage")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "::error::Pin range check: the GitHub API returned unreadable data\n")
        self.assertEqual(result.stderr, "")
        self.assertEqual(summary, "")

    def test_new_pins(self):
        result, summary, calls = self.run_fixture(added=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("New pin has no previous range", summary)
        self.assertEqual(calls.count("...main"), 2)
        self.assertNotIn("per_page", calls)


if __name__ == "__main__":
    unittest.main()

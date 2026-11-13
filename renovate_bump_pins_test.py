# SPDX-License-Identifier: Apache-2.0
# Copyright Tony Burns

"""Check that a pin bump moves every reference or fails."""

from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).parent / "renovate_bump_pins.py"
NEW = "c" * 40
OLD = "a" * 40
OLDER = "b" * 40
CALLER = "tbhb-dev/renovate-config/.github/workflows/renovate.yaml@"
VALIDATOR = "tbhb-dev/renovate-config/.github/workflows/renovate-config-validator.yaml@"
PRESET = "github>tbhb-dev/renovate-config//default.json5#"


def write(root, name, text):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def run(root, sha=NEW):
    return subprocess.run(
        [sys.executable, "-I", str(SCRIPT), sha, str(root)],
        capture_output=True, text=True, check=False,
    )


class BumpPinsTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def test_left_behind_pin_fails(self):
        # A pin the script cannot move must fail the run, not pass silently.
        write(self.root, ".github/workflows/renovate.yaml", f"    uses: {CALLER}{OLD}\n")
        write(self.root, ".github/workflows/ci.yaml", f"    uses: {VALIDATOR}main # main\n")
        result = run(self.root)
        self.assertEqual(result.returncode, 1)
        self.assertIn("left behind: .github/workflows/ci.yaml: main", result.stderr)

    def test_short_hash_is_left_behind(self):
        write(self.root, ".github/renovate.json5", f'{{extends: ["{PRESET}abc123"]}}\n')
        result = run(self.root)
        self.assertEqual(result.returncode, 1)
        self.assertIn("abc123", result.stderr)

    def test_moves_every_pin_in_mixed_checkout(self):
        write(self.root, ".github/workflows/renovate.yaml", f"    uses: {CALLER}{OLD} # main\n")
        write(self.root, ".github/workflows/renovate-config-validator.yml", f"    uses: {VALIDATOR}{OLDER}\n")
        write(self.root, ".github/renovate.json5", f'{{extends: ["{PRESET}{OLDER}", "{PRESET}{OLD}"]}}\n')
        result = run(self.root)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("moved 4 pins to ccccccc", result.stdout)
        for path in self.root.rglob("*"):
            if path.is_file():
                text = path.read_text()
                self.assertNotIn(OLD, text)
                self.assertNotIn(OLDER, text)
                self.assertIn(NEW, text)

    def test_no_references_and_rerun(self):
        write(self.root, ".github/renovate.json5", "{extends: []}\n")
        self.assertEqual(run(self.root).returncode, 0)
        write(self.root, ".github/workflows/renovate.yaml", f"    uses: {CALLER}{NEW}\n")
        result = run(self.root)
        self.assertEqual(result.returncode, 0)
        self.assertIn("moved 0 pins", result.stdout)

    def test_skips_worktrees_and_other_file_types(self):
        write(self.root, ".worktrees/x/.github/renovate.json5", f'"{PRESET}{OLD}"\n')
        write(self.root, "README.md", f"{CALLER}main\n")
        self.assertEqual(run(self.root).returncode, 0)
        self.assertIn(OLD, (self.root / ".worktrees/x/.github/renovate.json5").read_text())

    def test_rejects_bad_hash_argument(self):
        self.assertEqual(run(self.root, "abc").returncode, 2)


if __name__ == "__main__":
    unittest.main()

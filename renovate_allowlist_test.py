# SPDX-License-Identifier: Apache-2.0
# Copyright Tony Burns

"""Check every preset command against the reusable workflow's allowlist."""

import json
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).parent
# Match strings first, preserving comment markers and commas inside strings.
TOKENS = re.compile(r'"(?:\\.|[^"\\])*"|//[^\n]*|/\*.*?\*/', re.DOTALL)


def parse_preset(source):
    """Read the JSON5 subset used by presets without changing string values."""
    source = TOKENS.sub(lambda m: m[0] if m[0].startswith('"') else "", source)
    # Transform only the gaps between strings, where keys and commas occur.
    parts = re.split(r'("(?:\\.|[^"\\])*")', source)
    for index in range(0, len(parts), 2):
        parts[index] = re.sub(r'([A-Za-z_$][\w$]*)\s*:', r'"\1":', parts[index])
        parts[index] = re.sub(r',\s*([}\]])', r'\1', parts[index])
    return json.loads("".join(parts))


def allowed_patterns(source):
    """Extract the single-quoted folded YAML scalar and decode its JSON list."""
    matches = re.findall(
        r"^          RENOVATE_ALLOWED_COMMANDS: '((?:[^']|'')*)'\s*$",
        source,
        re.MULTILINE,
    )
    if len(matches) != 1:
        raise ValueError("Expected one single-quoted RENOVATE_ALLOWED_COMMANDS value")
    value = re.sub(r"\n[ \t]*", " ", matches[0]).replace("''", "'")
    patterns = json.loads(value)
    if not isinstance(patterns, list) or not all(isinstance(p, str) for p in patterns):
        raise ValueError("RENOVATE_ALLOWED_COMMANDS must be a list of strings")
    return patterns


def check_allowlist(commands, patterns):
    """Return every command coverage and unused-pattern failure."""
    errors = []
    for command in commands:
        count = sum(re.fullmatch(pattern, command) is not None for pattern in patterns)
        if count != 1:
            errors.append(f"Command {command!r} matches {count} regular expressions")
    for pattern in patterns:
        if not any(re.fullmatch(pattern, command) is not None for command in commands):
            errors.append(f"Regular expression {pattern!r} matches no command")
    return errors


class RenovateAllowlistTest(unittest.TestCase):
    def test_repository(self):
        commands = []
        for preset in sorted(ROOT.glob("*.json5")):
            config = parse_preset(preset.read_text())
            for rule in config.get("packageRules", []):
                commands.extend(rule.get("postUpgradeTasks", {}).get("commands", []))
        patterns = allowed_patterns((ROOT / ".github/workflows/renovate.yaml").read_text())
        self.assertTrue(commands, "No post-upgrade commands found")
        self.assertEqual(check_allowlist(commands, patterns), [])

    def test_negative_fixtures(self):
        for commands, patterns, detail in (
            (["unsafe"], ["^allowed$"], "Command 'unsafe' matches 0"),
            (["allowed"], ["^allowed$", "^unused$"], "'matches no command'"),
            (["allowed"], ["^allowed$", "^allow.*$"], "Command 'allowed' matches 2"),
        ):
            with self.subTest(commands=commands, patterns=patterns):
                errors = check_allowlist(commands, patterns)
                self.assertTrue(errors)
                if "matches no command" in detail:
                    self.assertIn("Regular expression '^unused$' matches no command", errors)
                else:
                    self.assertTrue(any(detail in error for error in errors), errors)

    def test_json5_strings_survive(self):
        config = parse_preset('''// comment
        {url: "https://example.test/a,]", commands: ["run //:target",],}
        ''')
        self.assertEqual(config, {"url": "https://example.test/a,]", "commands": ["run //:target"]})


if __name__ == "__main__":
    unittest.main()

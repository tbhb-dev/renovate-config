# SPDX-License-Identifier: Apache-2.0
# Copyright Tony Burns

"""Resolve representative updates through the presets' packageRules."""

# cspell:ignore bazelbuild

from fnmatch import fnmatchcase
from pathlib import Path
import unittest

from renovate_allowlist_test import parse_preset

ROOT = Path(__file__).parent
DAILY = ["* 5-7 * * *"]
RULES = "https://github.com/tbhb-dev/rules"
CONFIG = "https://github.com/tbhb-dev/renovate-config"


def rules(*presets):
    found = []
    for preset in presets:
        found += parse_preset((ROOT / preset).read_text()).get("packageRules", [])
    return found


def matches(rule, update):
    """Apply the match keys these presets use; any other key fails the match."""
    for key, wanted in rule.items():
        if not key.startswith("match"):
            continue
        field = {
            "matchManagers": "manager",
            "matchUpdateTypes": "updateType",
            "matchPackageNames": "packageName",
            "matchDepNames": "depName",
            "matchDatasources": "datasource",
        }.get(key)
        if field is None or field not in update:
            return False
        if not any(fnmatchcase(update[field], pattern) for pattern in wanted):
            return False
    return True


def resolve(presets, update):
    """Merge every matching rule in order, so the last one wins."""
    resolved = {}
    for rule in rules(*presets):
        if matches(rule, update):
            resolved.update(rule)
    return resolved


DEFAULT = ["default.json5"]
BAZEL = DEFAULT + ["bazel.json5"]
WORKER = BAZEL + ["cloudflare-worker.json5"]
RULES_CLOUDFLARE = BAZEL + ["rules-cloudflare.json5"]


class GroupingTest(unittest.TestCase):
    def check(self, presets, update, group, schedule=DAILY):
        resolved = resolve(presets, update)
        self.assertEqual(resolved.get("groupName"), group, update)
        if schedule is not None:
            self.assertEqual(resolved.get("schedule"), schedule, update)
        return resolved

    def test_pins_group(self):
        for update_type in ["minor", "patch", "digest", "pinDigest"]:
            action = {"manager": "github-actions", "packageName": "actions/checkout",
                      "depName": "actions/checkout", "datasource": "github-tags",
                      "updateType": update_type}
            self.assertEqual(self.check(DEFAULT, action, "pins")["rebaseWhen"], "behind-base-branch")
        tool = {"manager": "mise", "packageName": "vale", "depName": "vale",
                "datasource": "github-releases", "updateType": "minor"}
        self.check(BAZEL, tool, "pins")
        config = {"manager": "regex", "packageName": CONFIG,
                  "depName": "tbhb-dev/renovate-config", "datasource": "git-refs",
                  "updateType": "digest"}
        self.assertIsNone(self.check(BAZEL, config, "pins").get("minimumReleaseAge"))
        for name in ["tbhb_rules", "tbhb_rules_ansible", RULES]:
            rules_pin = {"manager": "bazel-module", "packageName": name,
                         "depName": name, "datasource": "git-refs",
                         "updateType": "digest"}
            self.check(BAZEL, rules_pin, "pins")

    def test_separate_groups(self):
        major = {"manager": "github-actions", "packageName": "actions/checkout",
                 "depName": "actions/checkout", "datasource": "github-tags",
                 "updateType": "major"}
        self.check(DEFAULT, major, "github-actions", schedule=None)
        runner = {"manager": "github-actions", "packageName": "ubuntu",
                  "depName": "ubuntu", "datasource": "github-runners",
                  "updateType": "minor"}
        self.assertEqual(self.check(DEFAULT, runner, "{{{depName}}} runner")["rebaseWhen"], "conflicted")
        engine = {"manager": "github-actions", "packageName": "ghcr.io/renovatebot/renovate",
                  "depName": "ghcr.io/renovatebot/renovate", "datasource": "docker",
                  "updateType": "minor"}
        self.assertEqual(self.check(DEFAULT, engine, "renovate")["rebaseWhen"], "conflicted")
        bazel = {"manager": "bazelisk", "packageName": "bazelbuild/bazel",
                 "depName": "bazel", "datasource": "github-releases",
                 "updateType": "patch"}
        self.check(BAZEL, bazel, "bazel version")
        worker_rules = {"manager": "bazel-module", "packageName": "tbhb_rules",
                        "depName": "tbhb_rules", "datasource": "git-refs",
                        "updateType": "digest"}
        resolved = self.check(WORKER, worker_rules, "tbhb_rules")
        self.assertIn("postUpgradeTasks", resolved)
        self.assertEqual(resolved["rebaseWhen"], "conflicted")

    def test_rules_cloudflare(self):
        commands = [
            "bazelisk run //:worker_types_test.update",
            "bazelisk run //nested:worker_types_test.update",
        ]
        for name in [
            "vitest",
            "@vitest/some-subpackage",
            "@cloudflare/vitest-plugin",
            "wrangler",
            "playwright",
            "playwright-core",
        ]:
            update = {"manager": "npm", "packageName": name,
                      "depName": name, "datasource": "npm",
                      "updateType": "minor"}
            resolved = self.check(RULES_CLOUDFLARE, update, None, schedule=None)
            self.assertIs(resolved.get("automerge"), False, update)
            self.assertIn("postUpgradeTasks", resolved, update)
            self.assertEqual(resolved["postUpgradeTasks"].get("commands"), commands, update)
        other = {"manager": "npm", "packageName": "typescript",
                 "depName": "typescript", "datasource": "npm",
                 "updateType": "minor"}
        self.assertNotIn("postUpgradeTasks", self.check(RULES_CLOUDFLARE, other, None, schedule=None))

    def test_security_updates_skip_cooldown(self):
        default = parse_preset((ROOT / "default.json5").read_text())
        self.assertIs(default.get("osvVulnerabilityAlerts"), True)
        alerts = default["vulnerabilityAlerts"]
        self.assertIn("minimumReleaseAge", alerts)
        self.assertIsNone(alerts["minimumReleaseAge"])
        self.assertEqual(alerts["rebaseWhen"], "conflicted")

    def test_later_groups_reset_rebase(self):
        """Rules merge field by field, so a group rule after the pins rules must reset rebaseWhen."""
        for preset in sorted(ROOT.glob("*.json5")):
            for rule in rules(preset.name):
                group = rule.get("groupName")
                if group in (None, "pins") or (preset.name == "default.json5" and group == "github-actions"):
                    continue
                self.assertEqual(rule.get("rebaseWhen"), "conflicted", (preset.name, group))
        for presets, name in [(BAZEL, "bazel"), (BAZEL + ["python.json5"], "uv")]:
            update = {"manager": "mise", "packageName": name, "depName": name,
                      "datasource": "github-releases", "updateType": "patch"}
            if name == "bazel":
                update["packageName"] = "bazelbuild/bazel"
            self.assertEqual(resolve(presets, update).get("rebaseWhen"), "conflicted", update)

    def test_no_post_upgrade_task_in_pins(self):
        for preset in sorted(ROOT.glob("*.json5")):
            for rule in rules(preset.name):
                if "postUpgradeTasks" in rule:
                    self.assertNotEqual(rule.get("groupName"), "pins", preset.name)


if __name__ == "__main__":
    unittest.main()

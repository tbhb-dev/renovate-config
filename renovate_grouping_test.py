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
            self.check(DEFAULT, action, "pins")
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
        self.check(DEFAULT, runner, "{{{depName}}} runner")
        engine = {"manager": "github-actions", "packageName": "ghcr.io/renovatebot/renovate",
                  "depName": "ghcr.io/renovatebot/renovate", "datasource": "docker",
                  "updateType": "minor"}
        self.check(DEFAULT, engine, "renovate")
        bazel = {"manager": "bazelisk", "packageName": "bazelbuild/bazel",
                 "depName": "bazel", "datasource": "github-releases",
                 "updateType": "patch"}
        self.check(BAZEL, bazel, "bazel version")
        worker_rules = {"manager": "bazel-module", "packageName": "tbhb_rules",
                        "depName": "tbhb_rules", "datasource": "git-refs",
                        "updateType": "digest"}
        self.assertIn("postUpgradeTasks", self.check(WORKER, worker_rules, "tbhb_rules"))

    def test_no_post_upgrade_task_in_pins(self):
        for preset in sorted(ROOT.glob("*.json5")):
            for rule in rules(preset.name):
                if "postUpgradeTasks" in rule:
                    self.assertNotEqual(rule.get("groupName"), "pins", preset.name)


if __name__ == "__main__":
    unittest.main()

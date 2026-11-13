# SPDX-License-Identifier: Apache-2.0
# Copyright Tony Burns

"""Move every tbhb-dev/renovate-config pin in a caller checkout to one commit.

Usage: renovate_bump_pins.py SHA [ROOT]

Rewrites each full commit hash pin in the checkout's JSON, JSON5 and YAML
files. Exits 1 and names the file when a reference to the repository is left
on anything but SHA, such as a branch name or a short hash. A failed run
leaves a partly changed tree, so callers discard the checkout.
"""

from pathlib import Path
import re
import sys


SUFFIXES = {".json", ".json5", ".yaml", ".yml"}
SKIP = {".git", ".worktrees", ".venv", ".vale", "node_modules"}
# Presets use `//file#ref`, workflows use `/path@ref`. The ref ends at a
# quote, whitespace or comment marker.
REF = re.compile(r"""(tbhb-dev/renovate-config(?:/[^\s"'#@]*)?[@#])([^\s"'#]*)""")
FULL = re.compile(r"[0-9a-fA-F]{40}")


def candidates(root):
    for path in sorted(root.rglob("*")):
        parts = path.relative_to(root).parts
        if path.suffix in SUFFIXES and path.is_file() and not SKIP.intersection(parts):
            yield path


def bump(root, sha):
    moved, left = 0, []
    for path in candidates(root):
        text = path.read_text()

        def replace(match):
            nonlocal moved
            if match[2] == sha:
                return match[0]
            if FULL.fullmatch(match[2]):
                moved += 1
                return match[1] + sha
            left.append((path.relative_to(root), match[2]))
            return match[0]

        updated = REF.sub(replace, text)
        if updated != text:
            path.write_text(updated)
    return moved, left


def main(argv):
    if len(argv) not in (2, 3) or not re.fullmatch(r"[0-9a-f]{40}", argv[1]):
        print(__doc__, file=sys.stderr)
        return 2
    moved, left = bump(Path(argv[2] if len(argv) == 3 else "."), argv[1])
    for name, ref in left:
        print(f"left behind: {name}: {ref}", file=sys.stderr)
    if left:
        return 1
    print(f"moved {moved} pins to {argv[1][:7]}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

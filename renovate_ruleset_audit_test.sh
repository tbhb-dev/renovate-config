#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Copyright Tony Burns

set -euo pipefail
exec python3 "$(dirname "$0")/renovate_ruleset_audit_test.py"

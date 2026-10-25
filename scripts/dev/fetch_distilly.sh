#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 jason081112
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# Fetch the pinned Distilly fork into vendor/distilly.
#
# Usage:
#   scripts/dev/fetch_distilly.sh
#   scripts/dev/fetch_distilly.sh /path/to/vendor/distilly
#   ALPHA_GIT_MIRROR=https://v4.gh-proxy.org scripts/dev/fetch_distilly.sh
#
# The repository's vendor/ directory is gitignored: upstream source is not
# committed to Pre_Alpha. This script makes the local checkout reproducible.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DEST="${1:-${ROOT}/vendor/distilly}"
FORK="https://github.com/jason081112/distilly.git"
UPSTREAM="https://github.com/titanwings/distilly.git"
BRANCH="dot-skill"
SHA="0afc45c29c360ba7bc8260f1bb0bf34e9155f4fa"
MIRROR="${ALPHA_GIT_MIRROR:-}"

if [[ -n "${MIRROR}" ]]; then
  REPO="${MIRROR%/}/${FORK}"
else
  REPO="${FORK}"
fi

if [[ ! -d "${DEST}/.git" ]]; then
  rm -rf "${DEST}"
  mkdir -p "$(dirname "${DEST}")"
  echo "Cloning ${REPO} (${BRANCH})"
  git clone --no-tags --depth 1 --branch "${BRANCH}" "${REPO}" "${DEST}"
fi

# The branch may have moved since the fork was pinned. Fetch the exact SHA
# when possible, then detach so the checkout cannot drift silently.
git -C "${DEST}" fetch --no-tags --depth 1 origin "${SHA}" >/dev/null 2>&1 || true
git -C "${DEST}" checkout --detach "${SHA}" >/dev/null

ACTUAL="$(git -C "${DEST}" rev-parse HEAD)"
if [[ "${ACTUAL}" != "${SHA}" ]]; then
  echo "ERROR: expected ${SHA}, got ${ACTUAL}" >&2
  exit 1
fi

if ! git -C "${DEST}" remote get-url upstream >/dev/null 2>&1; then
  git -C "${DEST}" remote add upstream "${UPSTREAM}"
fi

test -f "${DEST}/LICENSE"
test -f "${DEST}/SKILL.md"
grep -q '"name": "@titanwings/distilly"' "${DEST}/package.json"
grep -qi 'MIT License' "${DEST}/LICENSE"

printf 'Distilly ready: %s\n' "${DEST}"
printf '  fork:     %s\n' "${FORK}"
printf '  upstream: %s\n' "${UPSTREAM}"
printf '  branch:   %s\n' "${BRANCH}"
printf '  commit:   %s\n' "${ACTUAL}"

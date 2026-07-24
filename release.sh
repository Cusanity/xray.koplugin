#!/usr/bin/env bash
# release.sh — Interactive wrapper to publish or override a GitHub release.
# Usage: bash release.sh
set -euo pipefail

REPO="Cusanity/xray.koplugin"

# ── helpers ──────────────────────────────────────────────────────────────────
red()    { printf '\033[31m%s\033[0m\n' "$*"; }
green()  { printf '\033[32m%s\033[0m\n' "$*"; }
yellow() { printf '\033[33m%s\033[0m\n' "$*"; }
bold()   { printf '\033[1m%s\033[0m\n' "$*"; }

require() { command -v "$1" &>/dev/null || { red "ERROR: '$1' not found. Install it and retry."; exit 1; }; }
require git
require gh

# ── ensure gh is authenticated and default repo is set ───────────────────────
gh repo set-default "$REPO" 2>/dev/null || true

# ── gather context ────────────────────────────────────────────────────────────
HEAD_COMMIT=$(git rev-parse --short HEAD)
HEAD_MSG=$(git log -1 --format="%s")

echo
bold "=== xray.koplugin Release Helper ==="
echo
echo "  HEAD : $HEAD_COMMIT  —  $HEAD_MSG"
echo

# Fetch latest release info
LATEST_JSON=$(gh release list --limit 1 --json tagName,name,publishedAt 2>/dev/null || echo "[]")
LATEST_TAG=$(echo "$LATEST_JSON" | python -c "import json,sys; r=json.load(sys.stdin); print(r[0]['tagName'] if r else '')" 2>/dev/null || true)
LATEST_NAME=$(echo "$LATEST_JSON" | python -c "import json,sys; r=json.load(sys.stdin); print(r[0]['name'] if r else '')" 2>/dev/null || true)
LATEST_DATE=$(echo "$LATEST_JSON" | python -c "import json,sys; r=json.load(sys.stdin); print(r[0]['publishedAt'][:10] if r else '')" 2>/dev/null || true)

if [[ -n "$LATEST_TAG" ]]; then
    echo "  Latest release : $(bold "$LATEST_TAG")  \"$LATEST_NAME\"  ($LATEST_DATE)"
else
    yellow "  No existing releases found."
fi
echo

# ── choose action ─────────────────────────────────────────────────────────────
if [[ -n "$LATEST_TAG" ]]; then
    echo "Options:"
    echo "  [1] Override existing release  $LATEST_TAG  (default)"
    echo "  [2] Create a new release version"
    echo
    read -rp "Choice [1/2, Enter = 1]: " CHOICE
    CHOICE="${CHOICE:-1}"
else
    CHOICE="2"
fi

# ── determine target tag ──────────────────────────────────────────────────────
if [[ "$CHOICE" == "2" ]]; then
    echo
    # Suggest next patch version if we can parse the latest tag
    SUGGESTED=""
    if [[ "$LATEST_TAG" =~ ^v([0-9]+)\.([0-9]+)\.([0-9]+)$ ]]; then
        MAJOR="${BASH_REMATCH[1]}"
        MINOR="${BASH_REMATCH[2]}"
        PATCH="${BASH_REMATCH[3]}"
        SUGGESTED="v${MAJOR}.${MINOR}.$((PATCH + 1))"
    fi

    PROMPT="New version tag"
    [[ -n "$SUGGESTED" ]] && PROMPT="$PROMPT [$SUGGESTED]"
    read -rp "$PROMPT: " TARGET_TAG
    TARGET_TAG="${TARGET_TAG:-$SUGGESTED}"

    if [[ -z "$TARGET_TAG" ]]; then
        red "No version entered. Aborting."
        exit 1
    fi

    # Normalise: prepend 'v' if missing
    [[ "$TARGET_TAG" == v* ]] || TARGET_TAG="v$TARGET_TAG"

    # Sanity-check format
    if ! [[ "$TARGET_TAG" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
        red "Tag must be in vMAJOR.MINOR.PATCH format. Got: $TARGET_TAG"
        exit 1
    fi

    echo
    green "Will create new release: $TARGET_TAG"
    OVERRIDE=false
else
    TARGET_TAG="$LATEST_TAG"
    green "Will override existing release: $TARGET_TAG"
    OVERRIDE=true
fi

# ── confirm ───────────────────────────────────────────────────────────────────
echo
echo "  Commit : $HEAD_COMMIT  —  $HEAD_MSG"
echo "  Tag    : $TARGET_TAG"
echo
read -rp "Proceed? [Y/n]: " CONFIRM
CONFIRM="${CONFIRM:-y}"
[[ "$CONFIRM" =~ ^[Yy]$ ]] || { yellow "Aborted."; exit 0; }

# ── execute ───────────────────────────────────────────────────────────────────
echo
if $OVERRIDE; then
    echo "Deleting existing release and tag $TARGET_TAG …"
    gh release delete "$TARGET_TAG" --yes --cleanup-tag 2>/dev/null || true
    # Also delete the local tag if it exists
    git tag -d "$TARGET_TAG" 2>/dev/null || true
fi

echo "Creating tag $TARGET_TAG on HEAD ($HEAD_COMMIT) …"
git tag "$TARGET_TAG" HEAD
git push origin "$TARGET_TAG"

echo
green "Done! Tag $TARGET_TAG pushed — CI release workflow triggered."
echo "Monitor progress: https://github.com/$REPO/actions"

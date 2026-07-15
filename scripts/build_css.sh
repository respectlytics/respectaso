#!/usr/bin/env bash
# Build static/css/tailwind.css from the templates/JS/Python sources.
#
# Uses the Tailwind CSS *standalone CLI* (a self-contained binary - no Node/npm
# required). The binary is downloaded once, sha256-verified, and cached in the
# gitignored .tailwind-cli/ directory. Subsequent runs take <1s.
#
# The output file static/css/tailwind.css is COMMITTED to the repo - after any
# change that adds/removes Tailwind class names (templates, static/js, or
# Python files emitting classes), re-run this script and commit the result.
#
# Version is pinned to 3.4.x deliberately: the desktop app supports macOS 12+,
# whose system WebKit (Safari 15.6) cannot parse Tailwind v4 output
# (@property, color-mix, cascade layers - Safari 16.4+). Do not bump to v4
# unless the minimum supported macOS is raised to 13.3 or later.

set -euo pipefail

TAILWIND_VERSION="3.4.17"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CACHE_DIR="$REPO_ROOT/.tailwind-cli"

case "$(uname -s)-$(uname -m)" in
    Darwin-arm64)
        ASSET="tailwindcss-macos-arm64"
        SHA256="a1d0c7985759accca0bf12e51ac1dcbf0f6cf2fffb62e6e0f62d091c477a10a3"
        ;;
    Linux-x86_64)
        ASSET="tailwindcss-linux-x64"
        SHA256="7d24f7fa191d2193b78cd5f5a42a6093e14409521908529f42d80b11fde1f1d4"
        ;;
    *)
        echo "ERROR: unsupported platform: $(uname -s) $(uname -m)" >&2
        exit 1
        ;;
esac

BINARY="$CACHE_DIR/$ASSET-$TAILWIND_VERSION"

verify_sha() {
    echo "$SHA256  $BINARY" | shasum -a 256 --check --status
}

if [[ ! -x "$BINARY" ]] || ! verify_sha; then
    echo "Downloading Tailwind CSS standalone CLI v$TAILWIND_VERSION ($ASSET)..."
    mkdir -p "$CACHE_DIR"
    curl -fsSL -o "$BINARY" \
        "https://github.com/tailwindlabs/tailwindcss/releases/download/v$TAILWIND_VERSION/$ASSET"
    if ! verify_sha; then
        echo "ERROR: sha256 mismatch for downloaded $ASSET - refusing to run it." >&2
        rm -f "$BINARY"
        exit 1
    fi
    chmod +x "$BINARY"
fi

cd "$REPO_ROOT"
"$BINARY" \
    -c tailwind.config.js \
    -i static/css/tailwind.source.css \
    -o static/css/tailwind.css \
    --minify

echo "Built static/css/tailwind.css ($(du -h static/css/tailwind.css | cut -f1 | tr -d ' '))"
echo "If it changed, commit it: git add static/css/tailwind.css"

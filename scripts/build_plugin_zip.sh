#!/usr/bin/env bash
# Build the installable WordPress plugin zip.
# Runs the plugin's tests first: an untested zip does not ship.
set -euo pipefail
cd "$(dirname "$0")/.."

PHP=${PHP:-$(command -v php || echo /opt/homebrew/bin/php)}
[ -x "$PHP" ] || { echo "no php found; set PHP=/path/to/php" >&2; exit 1; }

echo "== lint"
find wordpress/chatmcd -name '*.php' -print0 | xargs -0 -n1 "$PHP" -l | grep -v "^No syntax errors" || true
find wordpress/chatmcd -name '*.php' -print0 | xargs -0 -n1 "$PHP" -l >/dev/null

echo "== test"
"$PHP" wordpress/tests/test_plugin.php | tail -1

echo "== zip"
VERSION=$(grep -m1 "Version:" wordpress/chatmcd/chatmcd.php | sed 's/.*Version: *//; s/ *$//')
OUT="dist/chatmcd-${VERSION}.zip"
mkdir -p dist
rm -f "$OUT" dist/chatmcd.zip
( cd wordpress && zip -qr "../$OUT" chatmcd \
    -x '*.DS_Store' -x '*/.*' )
cp "$OUT" dist/chatmcd.zip

echo
unzip -l "$OUT" | tail -n +4 | head -20
echo
echo "  $OUT  ($(du -h "$OUT" | cut -f1))"
echo "  dist/chatmcd.zip is a copy at a stable name"

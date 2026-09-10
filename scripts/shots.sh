#!/usr/bin/env bash
# Screenshot both surfaces in both themes against a locally running app.
# ?theme= is a real feature (the WordPress shortcode's `theme` attribute), so
# the captures use it rather than poking localStorage.
set -uo pipefail
CH="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
BASE="${BASE:-http://127.0.0.1:8010}"
OUT="${OUT:-docs/screenshots}"
mkdir -p "$OUT"

shot() { # url out w h
  "$CH" --headless=new --disable-gpu --hide-scrollbars --force-device-scale-factor=2 \
        --window-size="$3,$4" --virtual-time-budget=3000 --screenshot="$2" "$1" \
        >/dev/null 2>&1
  printf "  %-34s %s bytes\n" "$2" "$(stat -f%z "$2" 2>/dev/null)"
}

shot "$BASE/?theme=light"      "$OUT/app-light.png"     1280 880
shot "$BASE/?theme=dark"       "$OUT/app-dark.png"      1280 880
shot "$BASE/embed?theme=light" "$OUT/widget-light.png"   400 620
shot "$BASE/embed?theme=dark"  "$OUT/widget-dark.png"    400 620
shot "$BASE/?theme=light"      "$OUT/app-mobile.png"     390 780

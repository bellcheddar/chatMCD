#!/usr/bin/env python3
"""Drive the running chatMCD app in a real browser and check what it renders.

Screenshots prove the page lays out. They do not prove the streaming path works,
that the markdown renderer turns model output into markup, that the stop control
stops anything, or that the theme switch persists. Those need a browser that is
actually running the JavaScript, so this speaks the Chrome DevTools Protocol
over a real-time session.

Two things it deliberately does not do:

  * `--virtual-time-budget`, which pauses requestAnimationFrame and makes every
    animation measure as frozen. The session is real time.
  * zero-delay synthetic clicks. A press and release in the same millisecond
    passes tests that a real 100 ms click fails, so clicks hold for 120 ms.

    python3 scripts/browser_check.py --base http://127.0.0.1:8010
"""

from __future__ import annotations

import argparse
import base64
import json
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

import websocket

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"


class Tab:
    """A minimal CDP client: evaluate, click, screenshot."""

    def __init__(self, ws_url: str):
        self.ws = websocket.create_connection(ws_url, timeout=30)
        self.n = 0
        self.send("Runtime.enable")
        self.send("Page.enable")
        self.console: list[str] = []

    def send(self, method: str, **params) -> dict:
        self.n += 1
        self.ws.send(json.dumps({"id": self.n, "method": method, "params": params}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("method") == "Runtime.consoleAPICalled":
                args = " ".join(str(a.get("value", a.get("description", "")))
                                for a in msg["params"].get("args", []))
                self.console.append(f"{msg['params']['type']}: {args}")
                continue
            if msg.get("method") == "Runtime.exceptionThrown":
                d = msg["params"]["exceptionDetails"]
                self.console.append(f"EXCEPTION: {d.get('text')} "
                                    f"{d.get('exception', {}).get('description', '')[:200]}")
                continue
            if msg.get("id") == self.n:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})

    def goto(self, url: str) -> None:
        self.send("Page.navigate", url=url)
        for _ in range(200):
            if self.js("document.readyState") == "complete":
                return
            time.sleep(0.1)

    def js(self, expr: str):
        r = self.send("Runtime.evaluate", expression=expr, returnByValue=True,
                      awaitPromise=True)
        return r.get("result", {}).get("value")

    def click(self, selector: str, hold_ms: int = 120) -> bool:
        """Click at the element's centre with a real press-hold-release.

        A zero-delay press/release pair passes where a real 100 ms click fails,
        and the target is resolved at press time so a moving layout cannot make
        the release land somewhere else.
        """
        box = self.js(f"""(() => {{
            const el = document.querySelector({selector!r});
            if (!el) return null;
            el.scrollIntoView({{block:'center'}});
            const r = el.getBoundingClientRect();
            return {{x: r.left + r.width/2, y: r.top + r.height/2}};
        }})()""")
        if not box:
            return False
        for kind in ("mousePressed", "mouseReleased"):
            self.send("Input.dispatchMouseEvent", type=kind, x=box["x"], y=box["y"],
                      button="left", clickCount=1, buttons=1 if kind == "mousePressed" else 0)
            if kind == "mousePressed":
                time.sleep(hold_ms / 1000)
        return True

    def shot(self, path: Path) -> None:
        r = self.send("Page.captureScreenshot", format="png")
        path.write_bytes(base64.b64decode(r["data"]))

    def close(self) -> None:
        try:
            self.ws.close()
        except Exception:
            pass


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def launch(port: int, profile: str):
    return subprocess.Popen(
        [CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars",
         f"--remote-debugging-port={port}", f"--user-data-dir={profile}",
         # Chrome rejects a CDP WebSocket whose Origin it does not recognise, and
         # websocket-client always sends one. Loopback-only debugging port.
         "--remote-allow-origins=*",
         "--window-size=1280,900", "--no-first-run", "--no-default-browser-check",
         "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def ws_url(port: int) -> str:
    for _ in range(80):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list",
                                        timeout=2) as r:
                for t in json.load(r):
                    if t.get("type") == "page" and t.get("webSocketDebuggerUrl"):
                        return t["webSocketDebuggerUrl"]
        except Exception:
            pass
        time.sleep(0.25)
    raise RuntimeError("Chrome did not expose a debugging target")


PASS, FAIL = [], []


def check(name: str, cond, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(f"  {'ok  ' if cond else 'FAIL'}  {name}"
          + (f"\n          {detail}" if not cond and detail else ""))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", default="http://127.0.0.1:8010")
    ap.add_argument("--out", default="docs/screenshots")
    args = ap.parse_args()

    if not Path(CHROME).exists():
        sys.exit(f"no Chrome at {CHROME}")

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    profile = tempfile.mkdtemp()
    port = free_port()
    proc = launch(port, profile)
    tab = None
    try:
        tab = Tab(ws_url(port))

        # ---------------------------------------------------------- the app
        print("\nfull app")
        tab.goto(args.base + "/")
        check("page loads with the light theme by default",
              tab.js("document.documentElement.dataset.theme") == "light")
        check("the greeting is rendered", "chatMCD" in (tab.js(
            "document.querySelector('#transcript .msg.bot .md').textContent") or ""))
        # Three rows' worth, mixing professional and light-hearted. Asserted
        # against the server's own list rather than a hardcoded number, so
        # adding a preset cannot silently fail to reach the page.
        n_presets = len(json.loads(urllib.request.urlopen(
            args.base + "/api/presets", timeout=20).read())["presets"])
        check(f"all {n_presets} preset chips render", tab.js(
            "document.querySelectorAll('#chips .chip').length") == n_presets,
            f"page shows {tab.js("document.querySelectorAll('#chips .chip').length")}")
        check("the chips wrap onto more than one row", tab.js(
            "(() => {const c=[...document.querySelectorAll('#chips .chip')];"
            " return new Set(c.map(e => Math.round(e.getBoundingClientRect().top))).size;})()") >= 2)
        check("the first-run hint is shown", tab.js(
            "!!document.getElementById('hint')"))

        # --- streaming ---------------------------------------------------
        print("\nstreaming a real answer through /api/chat")
        tab.click("#chips .chip")           # "Marc's quick résumé"
        # Wait for the answer to FINISH, and take that from the app rather than
        # from a heuristic. The actions bar is appended exactly once the stream
        # ends, so its arrival is the app's own "done" signal.
        #
        # This used to wait for the text to stop growing for 1.5 s. Against a
        # local mock that is the same thing; against the live site it is not,
        # because a network stall mid-stream looks identical to a finished
        # answer. The checks then ran on a half-written response and reported
        # three failures for a copy button that simply had not been added yet.
        done = False
        for _ in range(400):                      # 100 s
            time.sleep(0.25)
            if tab.js("!!document.querySelector('#transcript .msg.bot:last-child "
                      ".actions')"):
                done = True
                break
        check("the answer completed (the app added its actions bar)", done,
              "timed out waiting for the stream to finish")

        check("the hint is removed once a conversation starts",
              not tab.js("!!document.getElementById('hint')"))
        check("a user turn was added", tab.js(
            "document.querySelectorAll('#transcript .msg.user').length") == 1)
        answer = tab.js("(document.querySelector('#transcript .msg.bot:last-child "
                        ".md')||{}).textContent || ''") or ""
        check("tokens streamed in", len(answer) > 200, f"got {len(answer)} chars")
        check("the answer mentions Elora", "Elora" in answer, answer[:160])

        # --- markdown ----------------------------------------------------
        # The renderer is exercised with a FIXED string, not with whatever the
        # model happened to write. Asserting that <strong> appears in a live
        # answer only holds when the model emits bold, so it reported two
        # failures against a renderer that was working: the answer simply had no
        # bold and no list in it. What the live answer can honestly be asked is
        # whether anything was left unrendered or unescaped.
        print("\nmarkdown rendering")
        html = tab.js("(document.querySelector('#transcript .msg.bot:last-child "
                      ".md')||{}).innerHTML || ''") or ""
        # The ordered list is LOOSE -- a blank line between items -- and one item
        # wraps onto an indented continuation line, because that is exactly what
        # the model writes. Both used to end the list, so each item got its own
        # <ol> and every browser numbered it from 1: a six-step answer rendered
        # as "1." six times.
        fixture = ("**bold** here\n\n- one\n- two\n\n"
                   "1. first\n\n2. second\n   wrapped on\n\n3. third\n\n"
                   "after the list\n\n-\n\n"
                   # Nested: bullets under numbered steps, which is what the
                   # model writes for "what has he built, and what does each do".
                   "1. First tool\n   - does one thing\n   - and another\n"
                   "2. Second tool\n   - does a third\n\n"
                   "## A heading\n\n"
                   "| Role | Where |\n|---|---:|\n| CSO | Elora |\n| Scientist | Oxford |\n\n"
                   "> the one thing to remember\n\n"
                   "---\n\n"
                   "`code` and <script>x</script>")
        rendered = tab.js(f"window.chatmcdRenderMarkdown({json.dumps(fixture)})") or ""
        check("the renderer is reachable", bool(rendered), repr(rendered)[:120])
        check("**bold** became <strong>", "<strong>bold</strong>" in rendered, rendered[:160])
        check("a bullet list became <ul><li>",
              "<ul>" in rendered and "<li>one</li>" in rendered, rendered[:160])
        # Asserted on the exact markup rather than on a count: the fixture now
        # contains two deliberate numbered lists, so counting <ol> would pass
        # for the wrong reason once and fail for the wrong reason after.
        check("a loose numbered list stays ONE <ol>",
              "<ol><li>first</li><li>second wrapped on</li><li>third</li></ol>"
              in rendered, rendered[:300])
        check("all three numbered items are in it",
              rendered.count("<li>") == 10, f"{rendered.count('<li>')} <li>: {rendered[:220]}")
        check("nested bullets do not break the numbered list they sit under",
              "<li>First tool<ul><li>does one thing</li><li>and another</li></ul></li>"
              in rendered, rendered[:700])
        check("the nested list closes before the next numbered item",
              "</ul></li><li>Second tool<ul>" in rendered, rendered[:700])
        check("a wrapped line joins its item, not a new block",
              "<li>second wrapped on</li>" in rendered, rendered[:220])
        check("a real paragraph after the list still closes it",
              "</ol><p>after the list</p>" in rendered, rendered[-220:])
        check("a table became a real <table>",
              "<table>" in rendered and "<th>Role</th>" in rendered
              and rendered.count("<tr>") == 3, rendered[-400:])
        check("the table scrolls in its own box, not the message",
              '<div class="md-table">' in rendered, rendered[-300:])
        check("a right-aligned column keeps its alignment",
              'style="text-align:right"' in rendered, rendered[-400:])
        check("a heading became a real heading, not a bold paragraph",
              '<h4 class="md-h">A heading</h4>' in rendered, rendered[-500:])
        check("a > line became a callout",
              '<div class="md-note">the one thing to remember</div>' in rendered,
              rendered[-300:])
        check("--- became a rule, and did not eat the table",
              "<hr>" in rendered and rendered.count("<table>") == 1, rendered[-260:])
        check("every list opened is closed",
              rendered.count("<ol>") == rendered.count("</ol>")
              and rendered.count("<ul>") == rendered.count("</ul>")
              and rendered.count("<li>") == rendered.count("</li>"),
              f"ol {rendered.count('<ol>')}/{rendered.count('</ol>')} "
              f"ul {rendered.count('<ul>')}/{rendered.count('</ul>')}")
        check("a bare list marker leaves no stray dash",
              "<p>-</p>" not in rendered and "<li></li>" not in rendered, rendered[:400])
        # App names are linked deterministically, first mention only.
        auto = tab.js("""(() => {
          const d = document.createElement('div');
          d.innerHTML = window.chatmcdRenderMarkdown(
            'AlphaFraud watches the PDB, and AlphaFraud again. See ' +
            '[BoltzMaker](https://boltzmaker.mdeller.com) and BoltzMaker. ' +
            '`ChemSage` stays code. PANTSuit is not an app. Elora Therapeutics.');
          window.chatmcdAutolink(d);
          const hrefs = [...d.querySelectorAll('a')].map(a => a.href);
          return {
            alpha: hrefs.filter(h => h.includes('alphafraud')).length,
            boltz: hrefs.filter(h => h.includes('boltzmaker')).length,
            chem: hrefs.filter(h => h.includes('chemsage')).length,
            pants: hrefs.filter(h => h.includes('pants')).length,
            elora: hrefs.filter(h => h.includes('/elora')).length,
            rel: [...d.querySelectorAll('a')].every(a => a.rel.includes('noopener')),
          };
        })()""") or {}
        check("an app name is linked to its live app", auto.get("alpha") == 1, str(auto))
        check("only the first mention is linked", auto.get("alpha") == 1, str(auto))
        check("an app the model already linked is not linked twice", auto.get("boltz") == 1, str(auto))
        check("names inside code are left alone", auto.get("chem") == 0, str(auto))
        check("a name inside a longer word is not linked", auto.get("pants") == 0, str(auto))
        check("Elora Therapeutics links to its page", auto.get("elora") == 1, str(auto))
        check("every inserted link opens safely", auto.get("rel") is True, str(auto))
        check("`code` became <code>", "<code>code</code>" in rendered, rendered[:160])
        check("a <script> tag from the model is escaped",
              "<script" not in rendered.lower() and "&lt;script" in rendered.lower(),
              rendered[-160:])
        check("no raw asterisks left in the live answer", "**" not in answer, answer[:200])
        check("no unescaped markup in the live answer",
              "<script" not in html.lower())

        # --- the actions bar ---------------------------------------------
        print("\nanswer actions")
        check("a copy button appeared", tab.js(
            "!!document.querySelector('#transcript .actions button')"))
        check("thumbs up and down are present", tab.js(
            "document.querySelectorAll('#transcript .actions [aria-pressed]').length") == 2)

        tab.click("#transcript .msg.bot:last-child .actions [aria-label='Helpful']")
        time.sleep(0.6)
        check("voting marks the button pressed", tab.js(
            "document.querySelector('#transcript .actions "
            "[aria-label=\\'Helpful\\']').getAttribute('aria-pressed')") == "true")

        tab.shot(out / "app-conversation.png")

        # --- the actions bar must be ON SCREEN, not merely present -----------
        # The bar is appended AFTER the last token, so it lands below the last
        # scroll of the streaming loop. On a conversation long enough to fill the
        # panel it sits under the fold: present, focusable and invisible, which
        # is exactly how it shipped and what a screenshot from a real laptop
        # showed. "The element exists" was already true while nobody could see
        # it, so this measures the rectangle instead.
        #
        # The conversation has to actually overflow or the check is vacuous --
        # the first version of it passed against the unfixed code at 1280x900,
        # because one answer did not fill the panel. So: ask until it does, and
        # refuse to report a pass if it never did.
        print("\nthe actions bar stays on screen")
        for i in range(1, 5):
            if tab.js("(() => {const t = document.getElementById('transcript');"
                      " return t.scrollHeight > t.clientHeight + 120;})()"):
                break
            tab.click(f"#chips .chip:nth-child({i + 1})")
            for _ in range(120):
                time.sleep(0.25)
                if tab.js("document.querySelectorAll('#transcript .msg.bot .actions')"
                          ".length") >= i + 1:
                    break
        time.sleep(1.2)   # the scroll is smooth; let it settle

        overflowed = tab.js("(() => {const t = document.getElementById('transcript');"
                            " return t.scrollHeight - t.clientHeight;})()")
        check("the panel actually overflows, so this check means something",
              isinstance(overflowed, (int, float)) and overflowed > 120,
              f"only {overflowed}px of overflow")
        check("the last actions bar is visible, not below the fold", tab.js(
            "(() => {"
            " const bars = document.querySelectorAll('#transcript .msg.bot .actions');"
            " const b = bars[bars.length - 1];"
            " const t = document.getElementById('transcript');"
            " if (!b || !t) return 'missing';"
            " const br = b.getBoundingClientRect(), tr = t.getBoundingClientRect();"
            " return br.bottom <= tr.bottom + 1 ? 'visible'"
            "   : 'clipped by ' + Math.round(br.bottom - tr.bottom) + 'px';"
            "})()") == "visible", tab.js(
            "(() => {"
            " const bars = document.querySelectorAll('#transcript .msg.bot .actions');"
            " const b = bars[bars.length - 1];"
            " const t = document.getElementById('transcript');"
            " if (!b || !t) return 'missing';"
            " const br = b.getBoundingClientRect(), tr = t.getBoundingClientRect();"
            " return 'bar.bottom ' + Math.round(br.bottom) + ' vs panel.bottom '"
            "   + Math.round(tr.bottom);"
            "})()"))
        tab.shot(out / "app-actions-visible.png")

        # --- the status light ------------------------------------------------
        # It reports whether the model is answering. The failure that matters is
        # a green dot on a broken model, so this checks it reached a REAL state
        # rather than merely existing.
        print("\nstatus light")
        check("the status dot is present", tab.js("!!document.getElementById('status-dot')"))
        state = tab.js("document.getElementById('status-dot').dataset.health")
        check("it resolved to a real state, not 'unknown'",
              state in ("ok", "degraded", "down"), f"state={state!r}")
        check("a conversation that worked leaves it green", state == "ok",
              f"state={state!r} after a successful answer")
        check("it is painted, not just labelled", tab.js(
            "getComputedStyle(document.getElementById('status-dot')).backgroundColor")
            not in ("", "rgba(0, 0, 0, 0)"))
        check("it explains itself to a screen reader", bool(tab.js(
            "document.getElementById('status-dot').getAttribute('aria-label')")))
        check("the green state animates", tab.js(
            "getComputedStyle(document.getElementById('status-dot')).animationName")
            not in ("", "none"))

        # --- theme switch --------------------------------------------------
        print("\ntheme switch")
        tab.click("[data-theme-set='dark']")
        time.sleep(0.4)
        check("switching sets data-theme=dark",
              tab.js("document.documentElement.dataset.theme") == "dark")
        check("the choice is persisted", tab.js(
            "localStorage.getItem('chatmcd-theme')") == "dark")
        # Assert the transcript's ground, not the body's: at desktop width
        # body.is-app paints --surf-2 behind the framed console, so the body is
        # legitimately a different colour from the conversation ground.
        bg = tab.js("getComputedStyle(document.querySelector('.shell')).backgroundColor")
        ink = tab.js("getComputedStyle(document.querySelector('.msg.bot')).color")
        check("the conversation ground repainted dark", bg == "rgb(11, 14, 23)", bg)
        check("message text repainted light", ink == "rgb(231, 234, 242)", ink)
        tab.shot(out / "app-conversation-dark.png")

        tab.goto(args.base + "/")
        check("the stored choice survives a reload",
              tab.js("document.documentElement.dataset.theme") == "dark")
        tab.js("localStorage.setItem('chatmcd-theme','light')")

        # --- query override ------------------------------------------------
        tab.goto(args.base + "/?theme=dark")
        check("?theme=dark overrides the stored light choice",
              tab.js("document.documentElement.dataset.theme") == "dark")

        # ---------------------------------------------------------- widget
        print("\nwidget")
        tab.goto(args.base + "/embed")
        check("widget uses the embed body class",
              tab.js("document.body.classList.contains('is-embed')"))
        # The widget does not render the link row at all: it is replaced by the
        # overflow menu. Assert absence, not display:none — an earlier version of
        # this check fell back to document.body and asserted nothing.
        check("the widget renders no link row", tab.js(
            "document.querySelectorAll('.hdr-links').length") == 0)
        check("the widget header stays on one row", tab.js(
            "getComputedStyle(document.querySelector('.hdr')).flexWrap") == "nowrap")
        check("the overflow menu is present", tab.js("!!document.getElementById('menu')"))
        tab.click("#menu > button")
        time.sleep(0.4)
        check("the overflow menu opens", tab.js(
            "!document.querySelector('#menu ul').hidden"))

        # --- keyboard ------------------------------------------------------
        # --- a phone ---------------------------------------------------------
        # Checked at a real phone size because the bug was only visible there:
        # fifteen chips wrapped onto nine rows and the header links onto three,
        # squeezing the transcript to a sliver so the answer a visitor had just
        # asked for was the one thing they could not see. Emulated rather than
        # windowed, because headless Chrome clamps a window to 500 px wide.
        print("\nphone (390 x 844)")
        tab.send("Emulation.setDeviceMetricsOverride", width=390, height=844,
                 deviceScaleFactor=3, mobile=True)
        tab.goto(args.base + "/")
        time.sleep(2.0)
        rows = lambda sel: tab.js(
            f"new Set([...document.querySelectorAll('{sel}')]"
            ".map(e => Math.round(e.getBoundingClientRect().top))).size")
        check("the preset chips sit on one row", rows("#chips .chip") == 1,
              f"{rows('#chips .chip')} rows")
        check("the header links sit on one row", rows(".hdr-links a") == 1,
              f"{rows('.hdr-links a')} rows")
        check("the chip row scrolls sideways instead of wrapping", tab.js(
            "(() => {const c=document.getElementById('chips');"
            " return c.scrollWidth > c.clientWidth + 20;})()"))
        share = tab.js("(() => {const t=document.getElementById('transcript');"
                       " return t.getBoundingClientRect().height / window.innerHeight;})()")
        check("the conversation gets most of the screen",
              isinstance(share, (int, float)) and share >= 0.5,
              f"transcript is {share:.0%} of the viewport" if isinstance(share, (int, float))
              else str(share))
        check("nothing overflows the page sideways", tab.js(
            "document.documentElement.scrollWidth <= window.innerWidth + 1"))
        # Scroll-snapping aligns items to the snapport, which ignores padding, so
        # the first chip once sat flush against the screen edge.
        lefts = tab.js("[document.querySelector('#chips .chip'), document.querySelector('.hdr-links a')]"
                       ".map(e => Math.round(e.getBoundingClientRect().left))")
        check("the first chip and link line up with the page, not the screen edge",
              isinstance(lefts, list) and all(isinstance(x, (int, float)) and x >= 10 for x in lefts),
              f"left edges {lefts}")
        tab.shot(out / "app-phone.png")
        tab.send("Emulation.clearDeviceMetricsOverride")
        tab.goto(args.base + "/")
        time.sleep(1.0)

        print("\nkeyboard")
        tab.goto(args.base + "/")
        check("the composer takes focus on load",
              tab.js("document.activeElement && document.activeElement.id") == "q")

        print("\nconsole")
        errors = [c for c in tab.console
                  if c.startswith("error") or c.startswith("EXCEPTION")]
        check("no console errors or exceptions", not errors,
              "\n          ".join(errors[:5]))

        print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
        for f in FAIL:
            print(f"  FAILED: {f}")
        return 1 if FAIL else 0
    finally:
        if tab:
            tab.close()
        proc.terminate()
        shutil.rmtree(profile, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())

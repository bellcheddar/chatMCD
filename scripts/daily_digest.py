#!/usr/bin/env python3
"""Email a daily digest of what visitors asked chatMCD, and what it told them.

Every question, the answer it got, where the visitor was, and how long it took.
Run from cron on the droplet once a day.

    python3 scripts/daily_digest.py --dry-run          # write the HTML, send nothing
    python3 scripts/daily_digest.py --hours 24         # build and send
    python3 scripts/daily_digest.py --hours 168 --subject "chatMCD: the week"

WHY AN HTTP API AND NOT SMTP: DigitalOcean blocks outbound 25, 465, 587 and 2525
on this droplet, so no mail client of any kind can send from it. Ports 443 are
open, so this posts to a transactional email API instead. Measured, not assumed:
all four SMTP ports refuse a TCP connection, api.resend.com:443 accepts one.

Configuration, all from the environment (or /opt/chatmcd/.env under systemd):

    DIGEST_TO           where to send it
    DIGEST_FROM         the from address; must be on a domain the provider has verified
    MAIL_PROVIDER       resend (default) | mailgun | none
    RESEND_API_KEY      for resend
    MAILGUN_API_KEY     for mailgun
    MAILGUN_DOMAIN      for mailgun
    GEO_LOOKUP          1 to resolve addresses to a country (default 1)

Addresses are resolved to a country and city ONLY at digest time, in one batch
call, for the handful of distinct addresses that actually asked something. The
raw addresses never leave the droplet except in the digest itself.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from base64 import b64encode
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

DEFAULT_DB = "/opt/chatmcd/var/chatmcd.sqlite"
GEO_BATCH = "http://ip-api.com/batch?fields=status,country,countryCode,city,query"

BRAND = "#0E7C86"
INK = "#0B0E17"
MUTED = "#4B5670"
LINE = "#D8DFEC"
SOFT = "#F1F4FA"


# ------------------------------------------------------------------- the data

def rows(db: str, hours: float) -> list[dict]:
    if not Path(db).exists():
        sys.exit(f"no database at {db}")
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    since = time.time() - hours * 3600
    # The extra columns arrived after the table did, so an older database may not
    # have them. Ask the table what it actually has rather than assuming.
    have = {r[1] for r in conn.execute("PRAGMA table_info(questions)")}
    cols = [c for c in ("id", "ts", "source", "question", "turn", "said_idk",
                        "ip", "user_agent", "answer", "latency", "ref") if c in have]
    q = conn.execute(f"SELECT {', '.join(cols)} FROM questions "
                     f"WHERE ts >= ? ORDER BY ts", (since,))
    out = [dict(r) for r in q.fetchall()]
    votes = conn.execute("SELECT question, vote FROM feedback WHERE ts >= ?",
                         (since,)).fetchall()
    conn.close()
    return out, {v["question"]: v["vote"] for v in votes}


def geolocate(ips: list[str]) -> dict[str, dict]:
    """Country and city for each distinct address, in one call per 100.

    Free, keyless, and rate limited to 15 batches a minute, which a personal
    site will never approach. A failure here must not lose the digest, so every
    error degrades to "unknown" rather than raising.
    """
    out: dict[str, dict] = {}
    real = [ip for ip in ips if ip and not ip.startswith(("127.", "10.", "192.168.", "::1"))]
    for i in range(0, len(real), 100):
        chunk = real[i:i + 100]
        try:
            req = urllib.request.Request(
                GEO_BATCH, data=json.dumps(chunk).encode(),
                headers={"Content-Type": "application/json"})
            for r in json.loads(urllib.request.urlopen(req, timeout=20).read()):
                if r.get("status") == "success":
                    out[r["query"]] = {"country": r.get("country", ""),
                                       "cc": r.get("countryCode", ""),
                                       "city": r.get("city", "")}
        except Exception as e:  # noqa: BLE001 - a digest without geo still ships
            print(f"  geolocation failed ({e}); continuing without it", file=sys.stderr)
            break
        time.sleep(1.5)
    return out


def device(ua: str) -> str:
    ua = ua or ""
    if not ua:
        return ""
    if "bot" in ua.lower() or "spider" in ua.lower() or "crawl" in ua.lower():
        return "bot"
    if "iPhone" in ua or "Android" in ua or "Mobile" in ua:
        return "mobile"
    if "iPad" in ua or "Tablet" in ua:
        return "tablet"
    return "desktop"


# -------------------------------------------------------------------- the mail

def esc(x) -> str:
    return html.escape(str(x or ""))


def as_email_html(text: str) -> str:
    """Turn the answer's markdown into something readable in a mail client.

    The answer is stored exactly as the model wrote it, which is markdown, so a
    plain escape shows literal ``**Elora Therapeutics**`` in the digest. Mail
    clients strip <style> and much of <head>, so every tag here carries its own
    inline style, and only the handful of constructs the model actually uses are
    handled: bold, bullets, numbered items, tables collapsed to lines, headings.
    """
    import re as _re
    out, in_list = [], False

    def spans(t: str) -> str:
        t = esc(t)
        t = _re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", t)
        t = _re.sub(r"`([^`]+)`", r"<code>\1</code>", t)
        return t

    for line in (text or "").split("\n"):
        row = line.strip()
        if not row:
            if in_list:
                out.append("</ul>"); in_list = False
            continue
        # A table row becomes a middot-separated line: a real table in an email
        # is a fight with every client, and the digest only needs it legible.
        if row.startswith("|"):
            cells_ = [c.strip() for c in row.strip("|").split("|")]
            if all(_re.fullmatch(r":?-{2,}:?", c or "") for c in cells_):
                continue
            row = " · ".join(c for c in cells_ if c)
            out.append(f'<div style="font-size:13px">{spans(row)}</div>')
            continue
        m = _re.match(r"^[-*+]\s+(.*)$", row) or _re.match(r"^\d+[.)]\s+(.*)$", row)
        if m:
            if not in_list:
                out.append('<ul style="margin:4px 0 6px;padding-left:18px">'); in_list = True
            out.append(f"<li>{spans(m.group(1))}</li>")
            continue
        if in_list:
            out.append("</ul>"); in_list = False
        h = _re.match(r"^#{1,4}\s+(.*)$", row)
        if h:
            out.append(f'<div style="font-weight:600;color:{INK};margin:8px 0 2px">'
                       f"{spans(h.group(1))}</div>")
            continue
        out.append(f'<div style="margin:0 0 4px">{spans(row.lstrip("&gt; "))}</div>')
    if in_list:
        out.append("</ul>")
    return "".join(out) or "<i>(no answer recorded)</i>"


def build_html(qs: list[dict], votes: dict, geo: dict, hours: float,
               limit: int = 60) -> str:
    when = datetime.now(timezone.utc).strftime("%A %d %B %Y")
    n = len(qs)
    declined = sum(1 for q in qs if q.get("said_idk"))
    people = len({q.get("ip") for q in qs if q.get("ip")})
    lat = [q["latency"] for q in qs if q.get("latency")]
    countries = Counter(geo.get(q.get("ip"), {}).get("country") or "unknown" for q in qs)
    sources = Counter(q.get("source") or "app" for q in qs)

    def stat(label, value):
        return (f'<td style="padding:0 18px 0 0"><div style="font:600 22px/1.2 '
                f'-apple-system,Segoe UI,sans-serif;color:{BRAND}">{esc(value)}</div>'
                f'<div style="font:400 11px/1.4 -apple-system,Segoe UI,sans-serif;'
                f'color:{MUTED};text-transform:uppercase;letter-spacing:.06em">'
                f'{esc(label)}</div></td>')

    head = "".join([
        stat("questions", n),
        stat("visitors", people),
        stat("declined", declined),
        stat("median", f"{sorted(lat)[len(lat) // 2]:.1f}s" if lat else "n/a"),
    ])

    if not qs:
        body = (f'<p style="font:400 15px/1.6 -apple-system,Segoe UI,sans-serif;'
                f'color:{MUTED}">Nobody asked chatMCD anything in the last '
                f'{hours:.0f} hours.</p>')
    else:
        # Gmail clips a message over about 102 KB behind a "view entire message"
        # link, which would hide the end of a busy day. Newest first, capped, and
        # the cut is stated rather than silent.
        shown = list(reversed(qs))[:limit]
        cards = []
        if len(qs) > limit:
            cards.append(
                f'<tr><td style="padding:0 0 10px"><div style="font:400 12px/1.5 '
                f'-apple-system,Segoe UI,sans-serif;color:{MUTED}">Showing the '
                f'{limit} most recent of {n}. The rest are in the database on the '
                f'droplet.</div></td></tr>')
        for q in shown:
            g = geo.get(q.get("ip") or "", {})
            place = ", ".join(x for x in (g.get("city"), g.get("country")) if x) or "unknown"
            t = datetime.fromtimestamp(q["ts"], timezone.utc).strftime("%H:%M")
            meta = " · ".join(x for x in [
                t + " UTC", place, q.get("ip") or "", device(q.get("user_agent")),
                q.get("source") or "", f"{q['latency']:.1f}s" if q.get("latency") else "",
                f"turn {q['turn'] + 1}" if q.get("turn") else "",
            ] if x)
            vote = votes.get(q["question"])
            flags = []
            if q.get("said_idk"):
                flags.append(f'<span style="background:#FAEBD8;color:#B4650F;'
                             f'border-radius:4px;padding:1px 6px;font-size:11px">'
                             f'declined</span>')
            if vote:
                flags.append(f'<span style="background:#DFF1F2;color:{BRAND};'
                             f'border-radius:4px;padding:1px 6px;font-size:11px">'
                             f'{"thumbs up" if vote > 0 else "thumbs down"}</span>')
            answer = as_email_html((q.get("answer") or "").strip())
            cards.append(
                f'<tr><td style="padding:14px 0;border-bottom:1px solid {LINE}">'
                f'<div style="font:400 11px/1.5 ui-monospace,SFMono-Regular,Menlo,'
                f'monospace;color:{MUTED}">{esc(meta)} {" ".join(flags)}</div>'
                f'<div style="font:600 15px/1.5 -apple-system,Segoe UI,sans-serif;'
                f'color:{INK};margin:6px 0 4px">{esc(q["question"])}</div>'
                f'<div style="font:400 14px/1.6 -apple-system,Segoe UI,sans-serif;'
                f'color:{MUTED}">{answer}</div>'
                f'</td></tr>')
        body = f'<table width="100%" cellpadding="0" cellspacing="0">{"".join(cards)}</table>'

    def tally(title, counter):
        if not counter:
            return ""
        items = " · ".join(f"{esc(k)} {v}" for k, v in counter.most_common(8))
        return (f'<div style="font:400 12px/1.6 -apple-system,Segoe UI,sans-serif;'
                f'color:{MUTED}"><b style="color:{INK}">{esc(title)}</b> {items}</div>')

    return f"""<!doctype html>
<html><body style="margin:0;padding:0;background:{SOFT}">
<table width="100%" cellpadding="0" cellspacing="0" style="background:{SOFT};padding:24px 12px">
<tr><td align="center">
<table width="640" cellpadding="0" cellspacing="0" style="max-width:640px;background:#fff;
       border:1px solid {LINE};border-radius:12px;padding:24px">
  <tr><td>
    <div style="font:400 11px/1.4 ui-monospace,SFMono-Regular,Menlo,monospace;
                color:{MUTED};text-transform:uppercase;letter-spacing:.1em">chatMCD</div>
    <div style="font:600 20px/1.3 -apple-system,Segoe UI,sans-serif;color:{INK};
                margin:4px 0 2px">Daily digest</div>
    <div style="font:400 13px/1.5 -apple-system,Segoe UI,sans-serif;color:{MUTED}">
      {esc(when)} · the last {hours:.0f} hours</div>
    <table cellpadding="0" cellspacing="0" style="margin:18px 0 6px"><tr>{head}</tr></table>
    {tally("Where", countries)}
    {tally("Surface", sources)}
    <div style="height:1px;background:{LINE};margin:16px 0"></div>
    {body}
    <div style="font:400 11px/1.6 -apple-system,Segoe UI,sans-serif;color:{MUTED};
                margin-top:18px">
      <a href="https://chatmcd.mdeller.com" style="color:{BRAND}">chatmcd.mdeller.com</a>
      · questions and answers are stored on the droplet; email addresses and
      phone numbers are stripped before anything is written
    </div>
  </td></tr>
</table>
</td></tr></table>
</body></html>"""


# Cloudflare sits in front of these APIs and blocks urllib's default
# "Python-urllib/3.x" outright: the first real send came back HTTP 403 with
# Cloudflare error 1010, "banned based on your browser's signature", which reads
# like a rejected API key and is nothing of the sort. A plain, honest agent
# string is enough.
UA = "chatMCD-digest/1.0 (+https://chatmcd.mdeller.com)"


def send(subject: str, body: str, to: str, sender: str,
         reply_to: str = "") -> bool:
    """Post the digest to the provider.

    reply_to matters because the From address has to live on a domain the
    provider has verified, which is not necessarily the address you actually
    read. Sending as marc@mdeller.com with a reply-to of marc@marcdeller.com
    means the mail looks right and a reply still lands somewhere real.
    """
    provider = os.environ.get("MAIL_PROVIDER", "resend").lower()

    if provider == "resend":
        key = os.environ.get("RESEND_API_KEY", "")
        if not key:
            print("RESEND_API_KEY is not set; nothing sent", file=sys.stderr)
            return False
        req = urllib.request.Request(
            "https://api.resend.com/emails",
            data=json.dumps({k: v for k, v in {
                "from": sender, "to": [to], "subject": subject, "html": body,
                "reply_to": reply_to or None}.items() if v is not None}).encode(),
            headers={"Authorization": f"Bearer {key}",
                     "Content-Type": "application/json", "User-Agent": UA})
    elif provider == "mailgun":
        # Both of these are imported at module level. Importing urllib.parse
        # HERE made `urllib` a local name for the whole function, so the resend
        # branch above -- which never runs this line -- died on
        # UnboundLocalError the first time it was given a real key.
        key = os.environ.get("MAILGUN_API_KEY", "")
        domain = os.environ.get("MAILGUN_DOMAIN", "")
        if not (key and domain):
            print("MAILGUN_API_KEY / MAILGUN_DOMAIN not set; nothing sent", file=sys.stderr)
            return False
        auth = b64encode(f"api:{key}".encode()).decode()
        req = urllib.request.Request(
            f"https://api.mailgun.net/v3/{domain}/messages",
            data=urllib.parse.urlencode({k: v for k, v in {
                "from": sender, "to": to, "subject": subject, "html": body,
                "h:Reply-To": reply_to or None}.items() if v is not None}).encode(),
            headers={"Authorization": f"Basic {auth}", "User-Agent": UA})
    else:
        print(f"MAIL_PROVIDER={provider}; nothing sent", file=sys.stderr)
        return False

    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read()
            # A 200 means the provider ACCEPTED it, not that it arrived. Print
            # the id so a bounce can be traced afterwards.
            try:
                mid = json.loads(raw).get("id") or json.loads(raw).get("message", "")
            except Exception:  # noqa: BLE001
                mid = ""
            print(f"sent: HTTP {r.status}" + (f"  id={mid}" if mid else ""))
            return True
    except urllib.error.HTTPError as e:
        print(f"send failed: HTTP {e.code} {e.read()[:300].decode(errors='replace')}",
              file=sys.stderr)
    except Exception as e:  # noqa: BLE001
        print(f"send failed: {e}", file=sys.stderr)
    return False


def load_env(path: str) -> int:
    """Read /opt/chatmcd/.env without a shell.

    cron has almost no environment, and the app's .env is a systemd
    EnvironmentFile rather than a shell script: `RATE_LIMIT=20 per minute` is
    legal there and `source` would try to run `per`. So this parses it directly,
    strips the quotes systemd would strip, and never overwrites a variable that
    is already set, so a one-off run can still override anything on the command
    line.
    """
    f = Path(path)
    if not f.exists():
        return 0
    n = 0
    for line in f.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        if k and k not in os.environ:
            os.environ[k] = v
            n += 1
    return n


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--env", default="/opt/chatmcd/.env",
                    help="environment file to read before anything else")
    ap.add_argument("--db", default=None)
    ap.add_argument("--hours", type=float, default=24)
    ap.add_argument("--to", default=None)
    ap.add_argument("--from", dest="sender", default=None)
    ap.add_argument("--reply-to", default=None)
    ap.add_argument("--subject", default="")
    ap.add_argument("--dry-run", action="store_true",
                    help="write the HTML to a file and send nothing")
    ap.add_argument("--out", default="/tmp/chatmcd-digest.html")
    ap.add_argument("--limit", type=int, default=60,
                    help="how many questions to show in full (newest first)")
    ap.add_argument("--empty-ok", action="store_true",
                    help="send even when nobody asked anything")
    args = ap.parse_args()

    # Parsed first: every default below can come from the environment file.
    load_env(args.env)
    args.db = args.db or os.environ.get("DB_PATH", DEFAULT_DB)
    args.to = args.to or os.environ.get("DIGEST_TO", "")
    args.sender = args.sender or os.environ.get(
        "DIGEST_FROM", "chatMCD <onboarding@resend.dev>")
    args.reply_to = args.reply_to or os.environ.get("DIGEST_REPLY_TO", "")

    qs, votes = rows(args.db, args.hours)
    print(f"{len(qs)} questions in the last {args.hours:.0f}h")

    geo = {}
    if qs and os.environ.get("GEO_LOOKUP", "1") in {"1", "true", "on"}:
        geo = geolocate(sorted({q.get("ip") for q in qs if q.get("ip")}))
        print(f"{len(geo)} addresses resolved")

    body = build_html(qs, votes, geo, args.hours, args.limit)
    subject = args.subject or (
        f"chatMCD: {len(qs)} question{'s' if len(qs) != 1 else ''} "
        f"{datetime.now(timezone.utc):%d %b}")

    if args.dry_run:
        Path(args.out).write_text(body)
        print(f"wrote {args.out} ({len(body) / 1024:.1f} KB), sent nothing")
        return 0

    if not qs and not args.empty_ok:
        print("nothing to report; not sending (use --empty-ok to send anyway)")
        return 0
    if not args.to:
        print("DIGEST_TO is not set; nothing sent", file=sys.stderr)
        return 1
    return 0 if send(subject, body, args.to, args.sender, args.reply_to) else 1


if __name__ == "__main__":
    sys.exit(main())

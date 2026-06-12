#!/usr/bin/env python3
"""
Sagrada Familia ticket monitor — Fast-Track Entry + Audio Guide.

Two independent sources, both checked headlessly (no browser, no reCAPTCHA):

1. HEADOUT — open JSON inventory API. tourId 21525 / variantId 68373 is the
   "Fast-Track Entry + Audio Guide" product. Returns exact remaining-ticket counts
   per time slot, so the target hour (e.g. 9:00 AM) is checked precisely.

2. OFFICIAL SITE (tickets.sagradafamilia.org, powered by Clorian) — its per-day
   availability map for the whole month is reachable with an anonymous token
   (client "frontend", secretKey baked into the site's JS). This is DAY-LEVEL only:
   the per-hour events endpoint is gated behind reCAPTCHA, so the official check
   tells us when the target DATE opens up at all; the exact hour is then confirmed
   on Headout (and on the official page manually).

GetYourGuide stays manual (DataDome bot protection blocks headless access).

No external dependencies — Python standard library only.

Usage:
  python3 sagrada_familia_monitor.py --once   # single check (good for testing)
  python3 sagrada_familia_monitor.py          # loop every CHECK_INTERVAL_MIN minutes
"""

import json
import os
import smtplib
import ssl
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText

# ── Configuration ────────────────────────────────────────────────────────────
TARGET_DATE = "2026-06-27"          # YYYY-MM-DD
TARGET_TIME = "09:00:00"            # 24h format (slot times are local Spain time)
TOUR_GROUP_ID = 17925               # Headout: Sagrada Familia Fast-Track Tickets
TOUR_ID = 21525                     # Headout: Fast-Track Entry + Audio Guide
CHECK_INTERVAL_MIN = 5

# Test mode sends an email+push on EVERY check (to verify the plumbing works).
# It is active only while BOTH are true:
#   - the TEST_MODE env var / repo variable is not "false"
#   - the current time is before TEST_MODE_UNTIL_UTC
# After the cutoff, notifications fire only when the target slot is bookable.
TEST_MODE_UNTIL_UTC = "2026-06-12T17:11:00+00:00"

# Personal config comes from the environment (GitHub repo secrets) so this file
# is safe to keep in a public repo. Locally, you can export the same vars.
EMAIL_FROM = os.environ.get("EMAIL_FROM", "")
EMAIL_TO = [e.strip() for e in os.environ.get("EMAIL_TO", "").split(",") if e.strip()]
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 465
SMTP_PASSWORD = os.environ["SMTP_PASSWORD"]  # Gmail app password (repo secret)

NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "")  # ntfy push topic (repo secret)

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

# Official site (Clorian booking engine behind tickets.sagradafamilia.org).
# Product 4375 = "Sagrada Familia & Free Audioguide" (matches Fast-Track + Audio Guide).
CLORIAN_BASE = "https://services.clorian.com"
CLORIAN_API_KEY = "thesagradafamiliafrontendoftomorrow"  # public key from the site's JS
CLORIAN_POS = "649"
CLORIAN_SALES_GROUP = "1"
CLORIAN_PRODUCT = "4375"
CLORIAN_ORIGIN = "https://tickets.sagradafamilia.org"
OFFICIAL_BOOKING_URL = "https://tickets.sagradafamilia.org/"

# Human-readable labels derived from the target (e.g. "June 27, 2026", "9:00 AM")
_d = datetime.strptime(TARGET_DATE, "%Y-%m-%d")
_t = datetime.strptime(TARGET_TIME, "%H:%M:%S")
DATE_LABEL = f"{_d.strftime('%B')} {_d.day}, {_d.year}"
TIME_LABEL = f"{_t.hour % 12 or 12}:{_t.minute:02d} {'AM' if _t.hour < 12 else 'PM'}"

BOOKING_URL = (
    f"https://www.headout.com/book/{TOUR_GROUP_ID}/select/"
    f"?date={TARGET_DATE}&tourId={TOUR_ID}&variantId=68373"
)
GYG_LINK = (
    "https://www.getyourguide.com/barcelona-l45/"
    f"sagrada-familia-skip-the-line-ticket-t50027?date={TARGET_DATE}"
)


def test_mode_active():
    if os.environ.get("TEST_MODE", "true").strip().lower() == "false":
        return False
    cutoff = datetime.fromisoformat(TEST_MODE_UNTIL_UTC)
    return datetime.now(timezone.utc) < cutoff


# ── Checks ───────────────────────────────────────────────────────────────────
def check_headout():
    """Return (slots_for_target_date, target_slot_remaining)."""
    day_after = (
        datetime.strptime(TARGET_DATE, "%Y-%m-%d") + timedelta(days=1)
    ).strftime("%Y-%m-%d")
    url = (
        f"https://api.headout.com/api/v7/tour-groups/{TOUR_GROUP_ID}/inventories"
        f"?from-date={TARGET_DATE}&to-date={day_after}"
    )
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": _UA,
            "Accept-Language": "en-US,en;q=0.9",
            "Origin": "https://www.headout.com",
            "Referer": "https://www.headout.com/",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.load(resp)

    slots = []
    target_remaining = 0
    for a in data.get("availabilities", []):
        if a.get("tourId") != TOUR_ID or a.get("startDate") != TARGET_DATE:
            continue
        remaining = max(
            (p.get("remaining", 0) for p in a.get("paxAvailability", [])),
            default=0,
        )
        slots.append({"time": a["startTime"], "remaining": remaining})
        if a["startTime"] == TARGET_TIME:
            target_remaining = remaining
    return slots, target_remaining


def check_official():
    """Return the official site's day-level status for TARGET_DATE.

    'availability' / 'no-availability' / None (None if the date isn't in the map).
    Day-level only — see module docstring.
    """
    common = {"User-Agent": _UA, "Origin": CLORIAN_ORIGIN, "Accept": "application/json"}
    tok_req = urllib.request.Request(
        f"{CLORIAN_BASE}/user/api/oauth/token?secretKey={CLORIAN_API_KEY}",
        data=b"",
        method="POST",
        headers=common,
    )
    with urllib.request.urlopen(tok_req, timeout=30) as r:
        token = json.load(r)["access_token"]

    d = datetime.strptime(TARGET_DATE, "%Y-%m-%d")
    url = (
        f"{CLORIAN_BASE}/catalog/salesGroups/{CLORIAN_SALES_GROUP}"
        f"/product/{CLORIAN_PRODUCT}/availability?month={d.month}&year={d.year}"
    )
    av_req = urllib.request.Request(
        url,
        headers={**common, "Authorization": "Bearer " + token,
                 "pos": CLORIAN_POS, "Accept-Language": "en"},
    )
    with urllib.request.urlopen(av_req, timeout=30) as r:
        month = json.load(r)
    return month.get(TARGET_DATE)


# ── Notifications ────────────────────────────────────────────────────────────
def send_email(subject, body):
    if not (EMAIL_FROM and EMAIL_TO):
        print("  email skipped (EMAIL_FROM/EMAIL_TO not configured)")
        return
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = EMAIL_FROM
    msg["To"] = ", ".join(EMAIL_TO)
    ctx = ssl.create_default_context()
    with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=ctx) as server:
        server.login(EMAIL_FROM, SMTP_PASSWORD)
        server.sendmail(EMAIL_FROM, EMAIL_TO, msg.as_string())
    print(f"  email sent to {', '.join(EMAIL_TO)}")


def send_push(title, message, priority="default"):
    if not NTFY_TOPIC:
        print("  push skipped (NTFY_TOPIC not configured)")
        return
    req = urllib.request.Request(
        f"https://ntfy.sh/{NTFY_TOPIC}",
        data=message.encode(),
        headers={"Title": title, "Priority": priority, "Tags": "ticket"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        resp.read()
    print(f"  push sent: {title}")


# ── Report building ──────────────────────────────────────────────────────────
def _headout_block(slots, remaining, err):
    if err:
        return f"HEADOUT (hour-exact): check failed — {err}"
    slot_lines = (
        "\n".join(f"    {s['time'][:5]} — {s['remaining']} left" for s in slots)
        or "    (no slots listed for this date)"
    )
    status = f"{remaining} ticket(s)" if remaining > 0 else "NOT available"
    return (
        "HEADOUT (hour-exact):\n"
        f"  {TIME_LABEL} slot: {status}\n"
        f"  Book: {BOOKING_URL}\n"
        f"  All {DATE_LABEL} slots:\n{slot_lines}"
    )


def _official_block(status, err):
    if err:
        return f"OFFICIAL SITE (tickets.sagradafamilia.org): check failed — {err}"
    label = {
        "availability": "AVAILABLE (day-level)",
        "no-availability": "no availability",
        None: "date not in calendar yet",
    }.get(status, str(status))
    return (
        "OFFICIAL SITE (tickets.sagradafamilia.org — day-level):\n"
        f"  {DATE_LABEL}: {label}\n"
        f"  Book: {OFFICIAL_BOOKING_URL}\n"
        f"  (Day-level only — open the page and pick the {TIME_LABEL} slot to confirm.)"
    )


# ── Main loop ────────────────────────────────────────────────────────────────
def run_check():
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now}] checking {TARGET_DATE} {TIME_LABEL} (Headout + Official) ...")

    # Headout (hour-exact)
    try:
        slots, remaining = check_headout()
        headout_err = None
    except Exception as e:
        slots, remaining, headout_err = [], 0, str(e)
        print(f"  HEADOUT FAILED: {e}")

    # Official site (day-level)
    try:
        official_status = check_official()
        official_err = None
    except Exception as e:
        official_status, official_err = None, str(e)
        print(f"  OFFICIAL FAILED: {e}")

    print("  " + _headout_block(slots, remaining, headout_err).replace("\n", "\n  "))
    print("  " + _official_block(official_status, official_err).replace("\n", "\n  "))

    if headout_err and official_err:
        try:
            send_push("Monitor error", "Both site checks failed.", "high")
        except Exception:
            pass
        return

    headout_found = remaining > 0
    official_found = official_status == "availability"
    found = headout_found or official_found

    blocks = (
        f"{_headout_block(slots, remaining, headout_err)}\n\n"
        f"{_official_block(official_status, official_err)}\n\n"
        f"GetYourGuide (manual — bot-protected):\n  {GYG_LINK}"
    )

    if found:
        which = []
        if headout_found:
            which.append(f"Headout ({remaining} at {TIME_LABEL})")
        if official_found:
            which.append("Official site (day open)")
        print(f"  *** AVAILABLE: {', '.join(which)} ***")
        body = (
            f"SAGRADA FAMILIA — TICKET AVAILABILITY!\n\n"
            f"Date: {DATE_LABEL}\n"
            f"Looking for: {TIME_LABEL} — Fast-Track Entry + Audio Guide\n"
            f"Available on: {', '.join(which)}\n\n"
            f"{blocks}\n\nChecked at {now}\n"
        )
        send_email(f"** SAGRADA FAMILIA {DATE_LABEL} AVAILABLE - BOOK NOW! **", body)
        send_push(
            "Sagrada Familia AVAILABLE!",
            f"{' + '.join(which)} — book now!",
            "urgent",
        )
    elif test_mode_active():
        body = (
            f"SAGRADA FAMILIA MONITOR — TEST MODE status report\n\n"
            f"Checked at: {now}\n"
            f"Searching for: {DATE_LABEL} at {TIME_LABEL} — Fast-Track Entry + Audio Guide\n\n"
            f"{blocks}\n\n"
            f"This is a TEST MODE email sent on every check. Test mode switches off\n"
            f"automatically at {TEST_MODE_UNTIL_UTC} (UTC); after that you will only be\n"
            f"notified when a ticket is found.\n"
        )
        send_email(f"[TEST MODE] Sagrada Familia monitor — nothing for {TIME_LABEL} yet", body)
        send_push(
            f"No {TIME_LABEL} ticket yet",
            f"Headout: {'avail' if headout_found else 'no'} | "
            f"Official {DATE_LABEL}: {official_status or 'n/a'}",
        )
    else:
        print("  not available; test mode off — no notification sent.")


def main():
    print("Sagrada Familia Ticket Monitor")
    print(f"  target : {TARGET_DATE} at {TIME_LABEL} (Fast-Track + Audio Guide)")
    print(f"  sources: Headout API (hour-exact) + Official site (day-level)")
    print(f"  test mode active: {test_mode_active()}")
    if "--once" in sys.argv:
        run_check()
        return
    print(f"  interval: every {CHECK_INTERVAL_MIN} minutes — Ctrl+C to stop\n")
    while True:
        run_check()
        time.sleep(CHECK_INTERVAL_MIN * 60)


if __name__ == "__main__":
    main()

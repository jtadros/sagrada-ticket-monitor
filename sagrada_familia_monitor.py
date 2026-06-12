#!/usr/bin/env python3
"""
Sagrada Familia ticket monitor — Fast-Track Entry + Audio Guide.

How it works (verified 2026-06-12):
- Headout exposes an open JSON inventory API. tourId 21525 / variantId 68373 is the
  "Fast-Track Entry + Audio Guide" product inside tour-group 17925. The API returns
  exact remaining-ticket counts per time slot, matching the website UI exactly.
- GetYourGuide and ticketsagradafamilia.com sit behind bot protection
  (DataDome / Cloudflare), so they cannot be checked headlessly. Alert emails
  include their links for a quick manual double-check.

No external dependencies — uses only the Python standard library.

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
TEST_MODE_UNTIL_UTC = "2026-06-12T09:55:00+00:00"

EMAIL_FROM = "john.tadros85@gmail.com"
EMAIL_TO = ["john.tadros85@gmail.com", "diana.morkos85@gmail.com"]
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 465
SMTP_PASSWORD = os.environ["SMTP_PASSWORD"]  # Gmail app password (repo secret)

NTFY_TOPIC = "sagrada_familia_ticket_monitor"  # subscribe to this in the ntfy app

# Human-readable labels derived from the target (e.g. "July 5, 2026", "9:00 AM")
_d = datetime.strptime(TARGET_DATE, "%Y-%m-%d")
_t = datetime.strptime(TARGET_TIME, "%H:%M:%S")
DATE_LABEL = f"{_d.strftime('%B')} {_d.day}, {_d.year}"
TIME_LABEL = f"{_t.hour % 12 or 12}:{_t.minute:02d} {'AM' if _t.hour < 12 else 'PM'}"

BOOKING_URL = (
    f"https://www.headout.com/book/{TOUR_GROUP_ID}/select/"
    f"?date={TARGET_DATE}&tourId={TOUR_ID}&variantId=68373"
)
MANUAL_LINKS = {
    "GetYourGuide": (
        "https://www.getyourguide.com/barcelona-l45/"
        f"sagrada-familia-skip-the-line-ticket-t50027?date={TARGET_DATE}"
    ),
    "TicketSagradaFamilia (reseller)": "https://ticketsagradafamilia.com/",
}


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
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
            ),
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


# ── Notifications ────────────────────────────────────────────────────────────
def send_email(subject, body):
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
    req = urllib.request.Request(
        f"https://ntfy.sh/{NTFY_TOPIC}",
        data=message.encode(),
        headers={"Title": title, "Priority": priority, "Tags": "ticket"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        resp.read()
    print(f"  push sent: {title}")


def manual_links_text():
    return "\n".join(f"  {name}: {url}" for name, url in MANUAL_LINKS.items())


# ── Main loop ────────────────────────────────────────────────────────────────
def run_check():
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now}] checking Headout for {TARGET_DATE} {TIME_LABEL} ...")
    try:
        slots, remaining = check_headout()
    except Exception as e:
        print(f"  CHECK FAILED: {e}")
        try:
            send_push("Monitor error", f"Headout check failed: {e}", "high")
        except Exception:
            pass
        return

    slot_lines = (
        "\n".join(f"  {s['time'][:5]} — {s['remaining']} left" for s in slots)
        or "  (no slots listed for this date)"
    )
    print(slot_lines)

    if remaining > 0:
        print(f"  *** TARGET SLOT AVAILABLE: {remaining} ticket(s) at {TIME_LABEL} ***")
        body = f"""SAGRADA FAMILIA — {TIME_LABEL} TICKET AVAILABLE!

Date: {DATE_LABEL}
Time: {TIME_LABEL}
Type: Fast-Track Entry + Audio Guide
Tickets remaining: {remaining}

BOOK NOW (Headout):
{BOOKING_URL}

Also worth a quick manual check (bot-protected, can't verify automatically):
{manual_links_text()}

All {DATE_LABEL} slots on Headout right now:
{slot_lines}

Checked at {now}
"""
        send_email(
            f"** SAGRADA FAMILIA {TIME_LABEL} TICKET AVAILABLE - BOOK NOW! **", body
        )
        send_push(
            f"Sagrada Familia {TIME_LABEL} AVAILABLE!",
            f"{remaining} ticket(s) left on Headout - book now!",
            "urgent",
        )
    elif test_mode_active():
        body = f"""SAGRADA FAMILIA MONITOR — TEST MODE status report

Checked at: {now}
Searching for: {DATE_LABEL} at {TIME_LABEL} — Fast-Track Entry + Audio Guide

Headout (checked automatically via API):
  URL: {BOOKING_URL}
  {TIME_LABEL} slot: NOT currently available
  All slots for {DATE_LABEL}:
{slot_lines}

Bot-protected sites (cannot check automatically — use links to verify manually):
{manual_links_text()}

This is a TEST MODE email sent on every check. Test mode switches off
automatically at {TEST_MODE_UNTIL_UTC} (UTC); after that you will only be
notified when the ticket is found.
"""
        send_email(
            f"[TEST MODE] Sagrada Familia monitor — no {TIME_LABEL} ticket yet", body
        )
        send_push(
            f"No {TIME_LABEL} ticket yet",
            f"Headout checked at {now}. Slots: "
            + (", ".join(s["time"][:5] for s in slots) or "none"),
        )
    else:
        print("  not available; test mode off — no notification sent.")


def main():
    print("Sagrada Familia Ticket Monitor")
    print(f"  target : {TARGET_DATE} at {TIME_LABEL} (Fast-Track + Audio Guide)")
    print(f"  source : Headout inventory API (tourId {TOUR_ID})")
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

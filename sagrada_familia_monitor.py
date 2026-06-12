#!/usr/bin/env python3
"""
Sagrada Familia ticket monitor — July 5, 2026 @ 1:00 PM, Fast-Track Entry + Audio Guide.

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
from datetime import datetime, timedelta
from email.mime.text import MIMEText

# ── Configuration ────────────────────────────────────────────────────────────
TARGET_DATE = "2026-07-05"          # YYYY-MM-DD
TARGET_TIME = "13:00:00"            # 1:00 PM
TOUR_GROUP_ID = 17925               # Headout: Sagrada Familia Fast-Track Tickets
TOUR_ID = 21525                     # Headout: Fast-Track Entry + Audio Guide
CHECK_INTERVAL_MIN = 5

# TEST_MODE=true → email+push on EVERY check (to verify the plumbing works).
# Set the TEST_MODE env var / repo variable to "false" once confirmed →
# notifications only when the slot is bookable.
TEST_MODE = os.environ.get("TEST_MODE", "true").strip().lower() != "false"

EMAIL_FROM = "john.tadros85@gmail.com"
EMAIL_TO = ["john.tadros85@gmail.com", "diana.morkos85@gmail.com"]
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 465
SMTP_PASSWORD = os.environ["SMTP_PASSWORD"]  # Gmail app password (repo secret)

NTFY_TOPIC = "sagrada_familia_ticket_monitor"  # subscribe to this in the ntfy app

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
    print(f"[{now}] checking Headout for {TARGET_DATE} {TARGET_TIME[:5]} ...")
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
        print(f"  *** TARGET SLOT AVAILABLE: {remaining} ticket(s) at 1:00 PM ***")
        body = f"""SAGRADA FAMILIA — 1:00 PM TICKET AVAILABLE!

Date: July 5, 2026
Time: 1:00 PM
Type: Fast-Track Entry + Audio Guide
Tickets remaining: {remaining}

BOOK NOW (Headout):
{BOOKING_URL}

Also worth a quick manual check (bot-protected, can't verify automatically):
{manual_links_text()}

All July 5 slots on Headout right now:
{slot_lines}

Checked at {now}
"""
        send_email("** SAGRADA FAMILIA 1:00 PM TICKET AVAILABLE - BOOK NOW! **", body)
        send_push(
            "Sagrada Familia 1PM AVAILABLE!",
            f"{remaining} ticket(s) left on Headout - book now!",
            "urgent",
        )
    elif TEST_MODE:
        body = f"""SAGRADA FAMILIA MONITOR — TEST MODE status report

Checked at: {now}
Searching for: July 5, 2026 at 1:00 PM — Fast-Track Entry + Audio Guide

Headout (checked automatically via API):
  URL: {BOOKING_URL}
  1:00 PM slot: NOT currently available
  All slots for July 5:
{slot_lines}

Bot-protected sites (cannot check automatically — use links to verify manually):
{manual_links_text()}

This is a TEST MODE email sent on every check. Set TEST_MODE = False in
sagrada_familia_monitor.py to only get notified when the ticket is found.
"""
        send_email("[TEST MODE] Sagrada Familia monitor — no 1:00 PM ticket yet", body)
        send_push(
            "No 1PM ticket yet",
            f"Headout checked at {now}. Slots: "
            + (", ".join(s["time"][:5] for s in slots) or "none"),
        )


def main():
    print("Sagrada Familia Ticket Monitor")
    print(f"  target : {TARGET_DATE} at {TARGET_TIME[:5]} (Fast-Track + Audio Guide)")
    print(f"  source : Headout inventory API (tourId {TOUR_ID})")
    print(f"  test mode: {TEST_MODE}")
    if "--once" in sys.argv:
        run_check()
        return
    print(f"  interval: every {CHECK_INTERVAL_MIN} minutes — Ctrl+C to stop\n")
    while True:
        run_check()
        time.sleep(CHECK_INTERVAL_MIN * 60)


if __name__ == "__main__":
    main()

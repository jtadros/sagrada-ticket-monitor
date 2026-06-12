# Sagrada Familia Ticket Monitor (cloud)

Runs on GitHub Actions every 5 minutes — no laptop needed.

Checks Headout's inventory API for **July 5, 2026 — 1:00 PM — Fast-Track Entry +
Audio Guide** and notifies by email (john + diana) and ntfy push
(topic `sagrada_familia_ticket_monitor`).

- **TEST_MODE** (repo variable, default `true`): emails on every check, found or not.
  Set the repo variable `TEST_MODE` to `false` to only get alerts when the ticket appears.
- **SMTP_PASSWORD** (repo secret): Gmail app password used to send the emails.
- GetYourGuide and ticketsagradafamilia.com are bot-protected and can't be checked
  headlessly; alert emails include their links for manual double-checking.

Manual run: Actions tab → "Sagrada Familia ticket monitor" → Run workflow.

Note: GitHub disables scheduled workflows after 60 days without repo activity —
not an issue for this monitor's lifespan (needed only until July 5, 2026).

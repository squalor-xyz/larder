# 0025: Conversational channels: email and Matrix

**Status:** Accepted (2026-10-12). The conversation mailbox, Matrix homeserver
and Matrix client library are deployment-specific and decided in the
notifications interview.

## Context

Household members want to talk to larder from wherever they are: add to the
list, ask what's low, and answer larder's questions about pantry stock. The
PWA only works on paired devices on the LAN or VPN (U4), and the planned
notifications (X1, X2) only go one way.

Options considered: a dedicated email account, Matrix, Signal (signal-cli),
Telegram and WhatsApp. Telegram is a hosted service. WhatsApp's official API
goes through a Meta business account, and unofficial clients risk breaking
its terms (unverified for this project; not pursued).

## Decision

- **One command core.** Every channel (PWA, email, Matrix) turns messages into
  the same typed intents (e.g., add item, mark low, what's low, show list,
  answer a question). Only the core changes state, and it checks the sender's
  autonomy level ([ADR 0026](0026-autonomy-levels.md)) first.
- **Channel adapters are plugins**, receiving and sending, like the notifier
  channels ([ADR 0016](0016-future-multi-household.md)). The first two:
  - **Email:** a dedicated account over IMAP and SMTP (stdlib `imaplib`,
    `smtplib`, `email`).
  - **Matrix:** a bot account on a homeserver. The client library is a new
    dependency and needs approval (NF6).
- **Known senders only.** Each email address or Matrix ID maps to a person in
  config. Messages from anyone else are ignored and logged.
- **Inbound text is untrusted.** It is parsed into intents and never executed
  or passed to anything with more privilege than the sender has.
- **Outbound only.** Both adapters are clients that poll or sync, so larder
  still opens no public port (U4, NG3).
- **Separate from the receipts mailbox** ([ADR 0003](0003-dedicated-mailbox.md)),
  so a leak of either credential exposes only that mailbox.

## Consequences

- Several channels can share one set of intents, tests and policy checks.
- Email works with the stdlib; Matrix needs a client library, and household
  members need a Matrix app and account.
- Plain-text replies must stay short and readable on a phone.
- The notifier (X1, X2) and conversation replies use the same outbound
  adapters.

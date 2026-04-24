# Contributing

Thanks for considering a contribution!

## Before you start

- Read [`docs/01-architecture.md`](docs/01-architecture.md) so the moving
  parts make sense.
- Read [`docs/02-servicenow-setup.md`](docs/02-servicenow-setup.md). To
  meaningfully test changes you need:
  - A ServiceNow Personal Developer Instance (PDI) with the **Customer
    Service Management** plugin and **Advanced Work Assignment** active.
  - Two SOW user accounts (one consumer, one agent in your test queue).
  - The bridge running locally and reachable from your PDI (use a dev
    tunnel like `devtunnel host` or `ngrok`).
- Run `tools/probe_e2e.ps1` against your environment first, to confirm
  the baseline works before you change anything.

## Reporting bugs

Open a GitHub issue with:

1. The probe output that demonstrates the failure.
2. Bridge logs around the failure (`docker compose logs bridge`).
3. The relevant ServiceNow records (interaction number, conversation
   sys_id) and any matching `gs.warn` / `gs.error` entries from
   **System Logs > System Log > All**.

## Pull requests

- Keep changes additive — don't reformat large swaths of unrelated code.
- For changes to ServiceNow scripts, copy the relevant block into a PR
  description with a short note on why.
- For new failure modes, add a one-line probe under `tools/` and a row
  to `docs/07-troubleshooting.md`.

## Out of scope (for now)

- Replacing Direct Line with a different chat channel.
- Replacing AWA with a different routing engine.
- File / image attachments.

These would be welcome separate projects, but they're large enough that
the right move is a fork rather than a PR.

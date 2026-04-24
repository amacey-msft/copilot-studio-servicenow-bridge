# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [0.1.0] - 2026-04-24

### Added
- Initial public release.
- ServiceNow Scripted REST resources `open_chat` and `send_message`.
- Outbound Business Rule on `sys_cs_message` that posts agent replies to
  a configurable bridge webhook.
- Flask bridge (`servicenow_bridge.py`) that translates between
  Copilot Studio (Direct Line) and ServiceNow Conversation APIs, with
  per-session state machine and WebSocket / long-poll fan-out to the
  browser.
- Minimal Flask host (`bridge/app.py`) that registers the bridge,
  serves the reference intranet page, and stubs `/directline/token`.
- Reference intranet page (`web/intranet.html`) with self-contained chat
  UI that drives Direct Line directly and switches transparently to the
  ServiceNow rep once a handoff completes.
- End-to-end smoke-test (`tools/probe_e2e.ps1`).
- Documentation suite under `docs/`:
  - `01-architecture.md`, `02-servicenow-setup.md`,
    `03-bridge-backend.md`, `04-copilot-studio.md`,
    `05-browser-webchat.md`, `06-end-to-end-test.md`,
    `07-troubleshooting.md`, `08-api-reference.md`,
    `09-production-hardening.md`.

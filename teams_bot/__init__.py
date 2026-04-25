"""Teams relay bot package.

Adds a Microsoft Teams front-end to the existing Copilot Studio <-> ServiceNow
bridge. The relay bot is the user-facing endpoint inside Teams; it proxies
BOT-mode turns to the Copilot Studio agent over Direct Line and uses the Bot
Framework adapter's `continue_conversation` to push ServiceNow rep replies into
the same 1:1 chat once the session transitions to LIVE.

See ../docs/10-teams-channel-overview.md for architecture (Phase 6).
"""

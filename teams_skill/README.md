# teams_skill/ — v3 spike (CS-callable skill, Python)

**Status: SPIKE. Not wired. Not shipping.**

Phase 1 of v3 plan. Goal: prove Python `microsoft-agents-hosting-aiohttp`
can serve as a Copilot Studio skill (callee) the way the .NET sample
[`skill-handoff`](https://microsoft.github.io/CopilotStudioSamples/contact-center/skill-handoff/)
does.

If green: build out v3 here. If red: archive folder + port to .NET.

## How to spike

1. Reuse `teams_agent/.venv` (same SDK packages).
2. Set env (no `.env` file shipped — use process env or copy from `teams_agent/.env`):
   - `SKILL_APP_ID` — new Azure Bot app reg (NOT the teams_agent one)
   - `SKILL_APP_PASSWORD` — its secret
   - `SKILL_TENANT_ID` — same tenant
   - `SKILL_PUBLIC_URL` — devtunnel URL pointing at port 3979
   - `CS_PARENT_APP_ID` — Copilot Studio agent's auto-created app reg id
3. Run: `python -m teams_skill.app`
4. Verify manifest: `curl http://localhost:3979/skill-manifest.json`
5. Add skill in Copilot Studio: Settings → Skills → Add → paste manifest URL
6. Author topic that calls skill action `endConversation` on phrase "talk to a person"
7. Test in Teams (using existing teams_agent's bot, since CS owns Teams in skill model)

## Spike success criteria

- [ ] CS accepts manifest URL without error
- [ ] CS can invoke `endConversation` action and skill receives the activity
- [ ] Skill can `send_activity` back and CS surfaces it to the user
- [ ] `endOfConversation` activity terminates skill session cleanly

If all four green: Python skill protocol works. Build v3 in Python.
If any red: stop, document failure mode, switch to .NET.

## Spike failure modes to watch

1. **Auth handshake**: skill must validate JWT from CS using CS's app id as audience.
   The SDK may or may not handle this with just `SERVICE_CONNECTION` config.
2. **Allowed callers ACL**: BF skill protocol requires the skill to whitelist
   parent caller app ids. SDK may need explicit `claims_validator`.
3. **Manifest schema**: BF v2.2 schema may not match what current CS expects;
   CS sometimes wants v2.1 or proprietary fields.
4. **`start_agent_process` may not handle skill-protocol activities** the same
   way as channel activities (Bot Framework distinguishes skill vs channel).

"""teams_skill — v3 spike: CS-callable skill (Python M365 Agents SDK).

Phase 1 spike. NOT shipping. NOT wired into bridge yet.

Goal: prove Python `microsoft-agents-hosting-aiohttp` can:
  1. Serve a skill manifest at `/skill-manifest.json`
  2. Accept skill-protocol invocations from Copilot Studio
  3. Handle `endConversation` action + `sendMessage` action
  4. Send proactive messages back through the skill caller (CS) to Teams

If green: build out v3 here.
If red: archive folder, move to .NET in `teams_skill_dotnet/`.
"""

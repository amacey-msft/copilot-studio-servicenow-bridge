# Smoke-test scripts

These PowerShell scripts hit the bridge's HTTP endpoints to verify a
fresh install is working end-to-end. Run them from a machine that can
reach the bridge (locally or via your dev tunnel).

## `probe_e2e.ps1`

Runs the full round-trip: init session → escalate → user message → poll
for the agent's auto-greeting. Use this immediately after installing the
ServiceNow side and bringing the bridge up.

```powershell
./probe_e2e.ps1 -BaseUrl http://localhost:5000
```

Expected: the final `poll` response includes a system message like
`"Thank you for contacting support..."` from your AWA queue's auto-greeting.

If you want a real round-trip with a human agent, leave the script
running, switch to your SOW Agent Workspace, accept the queued chat,
type a reply, and re-run the last `Invoke-RestMethod ... /poll/<sid>`
line that the script prints.

## Adding more probes

When you discover a new failure mode, write a probe for it under
`tools/` rather than re-running ad-hoc commands. This way every
regression has a one-line repro and you build a running record over
time.

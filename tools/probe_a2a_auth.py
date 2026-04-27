"""Synthetic skill-caller test for Phase 6 auth handshake validation.

Mints an MSAL client-credentials token for the skill's own app reg,
constructs a Bot Framework skill-protocol activity, POSTs it to
/api/messages, and reports what the SDK does.

Three test rounds:
  1. No auth         -> expect 401 (proves auth IS validated)
  2. Self token      -> expect 200/202 if SDK accept token from caller=callee
                        (otherwise need allowed-callers ACL config — that
                         tell us what to wire in Phase 6.5)
  3. Garbage token   -> expect 401

If round 2 succeed: Python SDK accept skill-protocol JWT. GREEN.
If round 2 fail with claims-validation message: SDK working, just need
   allowed-callers config. Still GREEN (just SDK config gap to fix).
If round 2 fail with crypto/cert error or unhandled exception: RED.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
import urllib.error
import uuid
from datetime import datetime, timezone

import msal


A2A_APP_ID = os.environ["A2A_APP_ID"]
A2A_APP_PASSWORD = os.environ["A2A_APP_PASSWORD"]
A2A_TENANT_ID = os.environ["A2A_TENANT_ID"]
A2A_PUBLIC_URL = os.environ["A2A_PUBLIC_URL"]


def get_token() -> str:
    app = msal.ConfidentialClientApplication(
        client_id=A2A_APP_ID,
        client_credential=A2A_APP_PASSWORD,
        authority=f"https://login.microsoftonline.com/{A2A_TENANT_ID}",
    )
    # Bot Framework v3 auth: scope is the caller-target's app id /.default
    scope = f"{A2A_APP_ID}/.default"
    res = app.acquire_token_for_client(scopes=[scope])
    if "access_token" not in res:
        print(f"[ERR] token acquire failed: {res}")
        sys.exit(2)
    return res["access_token"]


def make_activity(name: str = "endConversation") -> dict:
    """Construct a skill-protocol event activity."""
    return {
        "type": "event",
        "name": name,
        "id": str(uuid.uuid4()),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "channelId": "test",
        "from": {
            "id": "synthetic-cs-caller",
            "name": "Test CS Agent",
            "role": "skill",
        },
        "conversation": {
            "id": str(uuid.uuid4()),
        },
        "recipient": {
            "id": A2A_APP_ID,
            "role": "skill",
        },
        "value": {
            "userEmail": "alice@example.com",
            "initialQuery": "I want to talk to a person",
        },
        "serviceUrl": "https://test.invalid",
    }


def post(url: str, body: dict, headers: dict) -> tuple[int, str]:
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        return -1, f"<exception: {e!r}>"


def main() -> int:
    url = f"{A2A_PUBLIC_URL.rstrip('/')}/api/messages"
    activity = make_activity()
    print(f"\n==> Target: {url}")

    print("\n--- Round 1: NO auth header (expect 401) ---")
    code, body = post(url, activity, {})
    print(f"HTTP {code}: {body[:400]}")
    r1_pass = code == 401

    print("\n--- Round 2: real client-credentials token (expect 200/202 if green) ---")
    tok = get_token()
    print(f"token acquired (len={len(tok)})")
    code, body = post(url, activity, {"Authorization": f"Bearer {tok}"})
    print(f"HTTP {code}: {body[:600]}")
    r2_pass = code in (200, 201, 202)
    r2_authfail = code == 401

    print("\n--- Round 3: garbage token (expect 401) ---")
    code, body = post(url, activity, {"Authorization": "Bearer not.a.real.jwt"})
    print(f"HTTP {code}: {body[:400]}")
    r3_pass = code == 401

    print("\n=== Verdict ===")
    print(f"  R1 (no auth -> 401):       {'PASS' if r1_pass else 'FAIL'}")
    print(f"  R2 (real token -> 2xx):    {'PASS' if r2_pass else 'FAIL'}")
    print(f"  R3 (garbage -> 401):       {'PASS' if r3_pass else 'FAIL'}")

    if r1_pass and r2_pass and r3_pass:
        print("\nGREEN: Python SDK accepts skill-protocol JWT auth end-to-end.")
        return 0
    if r1_pass and r2_authfail and r3_pass:
        print("\nYELLOW: Auth pipeline works (rejects bad/missing) but rejects valid")
        print("        client-credentials token. Likely need allowed-callers ACL or")
        print("        skill-aware claim validator. Solvable via SDK config — keep going.")
        return 1
    print("\nRED: Auth pipeline broken. Need to dig into SDK internals.")
    return 2


if __name__ == "__main__":
    sys.exit(main())

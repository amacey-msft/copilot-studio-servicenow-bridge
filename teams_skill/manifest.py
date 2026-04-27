"""Skill manifest — v3 spike.

The skill manifest tell CS what action this skill expose. CS read this
URL, see the action shape, then can call the skill from a topic.

Spec: https://learn.microsoft.com/en-us/azure/bot-service/skills-conceptual

For spike, two action mirror MS .NET sample:
  - endConversation: escalate user to live agent (start handoff)
  - sendMessage: relay message between user <-> live agent during handoff
"""
from __future__ import annotations

import os


def build_manifest(public_url: str, ms_app_id: str) -> dict:
    """Build skill manifest JSON.

    Args:
        public_url: full https URL where this skill is reachable
                    (e.g. https://xyz.devtunnels.ms or ACA URL).
        ms_app_id: Azure Bot app reg id of THIS skill (not CS agent).
    """
    base = public_url.rstrip("/")
    return {
        "$schema": "https://schemas.botframework.com/schemas/skills/v2.2/skill-manifest.json",
        "$id": "ServiceNowHandoffSkill",
        "name": "ServiceNow Live-Agent Handoff Skill",
        "version": "0.1.0",
        "description": "Escalate Copilot Studio conversation to a ServiceNow live agent via AWA queue.",
        "publisherName": "internal",
        "endpoints": [
            {
                "name": "default",
                "protocol": "BotFrameworkV3",
                "description": "Default endpoint",
                "endpointUrl": f"{base}/api/messages",
                "msAppId": ms_app_id,
            }
        ],
        "activities": {
            "endConversation": {
                "description": "Start a live-agent handoff. Call when the user wants to talk to a person.",
                "type": "event",
                "name": "endConversation",
                "value": {
                    "$ref": "#/definitions/HandoffRequest"
                },
            },
            "sendMessage": {
                "description": "Relay a message from the user to the live agent during an active handoff.",
                "type": "message",
            },
        },
        "definitions": {
            "HandoffRequest": {
                "type": "object",
                "properties": {
                    "userEmail": {"type": "string", "description": "Teams user email for SN sys_user lookup."},
                    "initialQuery": {"type": "string", "description": "Last user message to seed the SN session."},
                },
                "required": ["userEmail"],
            }
        },
    }

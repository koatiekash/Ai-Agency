"""Agent runtime: prompts, output schemas and the self-improvement memory hook."""
from __future__ import annotations

import json

from . import llm
from .org import AgentSpec, Business
from .store import CommandCenter

NUM = {"type": ["number", "null"]}
STR = {"type": "string"}
STRN = {"type": ["string", "null"]}

REPORT_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": STR,
        "findings": {"type": "array", "items": {
            "type": "object",
            "properties": {"item": STR, "detail": STR, "metric": STR, "value": NUM},
            "required": ["item", "detail", "metric", "value"], "additionalProperties": False}},
        "recommendations": {"type": "array", "items": STR},
        "data_gaps": {"type": "array", "items": STR},
        "confidence": {"type": "number"},
    },
    "required": ["summary", "findings", "recommendations", "data_gaps", "confidence"],
    "additionalProperties": False,
}


def action_schema(b: Business) -> dict:
    """Executive decision schema; the action field only allows that business's permitted actions."""
    action = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": list(b.permitted_actions)},
            "target": STR,               # lead id, product name, symbol…
            "rationale": STR,
            "message": STRN,             # outreach text / listing copy / campaign brief
            "channel": STRN,             # mail | email | call | sms | tiktok | …
            "amount": NUM,               # offer $, price $, budget $
            "symbol": STRN, "contracts": NUM, "entry": NUM, "stop": NUM, "take_profit": NUM,
            "scheduled_for": STRN,       # ISO datetime for appointments
        },
        "required": ["action", "target", "rationale", "message", "channel", "amount", "symbol",
                     "contracts", "entry", "stop", "take_profit", "scheduled_for"],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {
            "actions": {"type": "array", "items": action},
            "marketing_brief": STRN,
            "update_to_ceo": STR,
            "kpis": {"type": "array", "items": {
                "type": "object", "properties": {"name": STR, "value": NUM, "target": NUM},
                "required": ["name", "value", "target"], "additionalProperties": False}},
        },
        "required": ["actions", "marketing_brief", "update_to_ceo", "kpis"],
        "additionalProperties": False,
    }


REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "approved": {"type": "boolean"},
        "issues": {"type": "array", "items": STR},
        "required_changes": {"type": "array", "items": STR},
    },
    "required": ["approved", "issues", "required_changes"],
    "additionalProperties": False,
}

LESSONS_SCHEMA = {
    "type": "object",
    "properties": {"lessons": {"type": "array", "items": {
        "type": "object", "properties": {"agent": STR, "lesson": STR},
        "required": ["agent", "lesson"], "additionalProperties": False}}},
    "required": ["lessons"], "additionalProperties": False,
}

BRIEFING_SCHEMA = {
    "type": "object",
    "properties": {
        "headline": STR,
        "by_business": {"type": "array", "items": {
            "type": "object",
            "properties": {"business": STR, "status": {"type": "string", "enum": ["green", "yellow", "red"]},
                           "summary": STR, "next_step": STR},
            "required": ["business", "status", "summary", "next_step"], "additionalProperties": False}},
        "priorities": {"type": "array", "items": STR},
        "needs_human": {"type": "array", "items": STR},
    },
    "required": ["headline", "by_business", "priorities", "needs_human"], "additionalProperties": False,
}

ASSET_SCHEMA = {
    "type": "object",
    "properties": {
        "format": {"type": "string", "enum": ["short_video", "image_post", "carousel", "text_post", "email"]},
        "platform": STR, "hook": STR, "script": STR,
        "shot_list": {"type": "array", "items": STR},
        "on_screen_text": {"type": "array", "items": STR},
        "caption": STR, "hashtags": {"type": "array", "items": STR},
        "call_to_action": STR, "disclosures": {"type": "array", "items": STR},
        "variants": {"type": "array", "items": STR},
        "notes": STR,
    },
    "required": ["format", "platform", "hook", "script", "shot_list", "on_screen_text", "caption",
                 "hashtags", "call_to_action", "disclosures", "variants", "notes"],
    "additionalProperties": False,
}

HOUSE_RULES = (
    "You are part of a fully automated company. Work only from the data provided; when data is "
    "missing or labelled 'sample' or 'synthetic', say so in data_gaps instead of inventing numbers. "
    "Be concrete and quantitative. Never promise or imply guaranteed profits."
)


def system_prompt(agent: AgentSpec, business_name: str, goal: str, lessons: list[str]) -> str:
    s = (f"You are {agent.name}, the {agent.role} for {business_name}.\n"
         f"Company goal: {goal}\nYour mission: {agent.mission}\n{HOUSE_RULES}")
    lessons = [l for l in lessons if l]
    if lessons:
        s += "\n\nLessons learned from previous cycles (apply them):\n" + "\n".join(f"- {l}" for l in lessons)
    return s


def run(cc: CommandCenter, agent: AgentSpec, b_id: str, b_name: str, goal: str, task: str,
        context: dict, schema: dict, effort: str | None = None) -> dict:
    system = system_prompt(agent, b_name, goal, cc.lessons(b_id, agent.id))
    prompt = f"{task}\n\nCONTEXT (JSON):\n{json.dumps(context, default=str)[:60000]}"
    return llm.ask_json(system, prompt, schema, effort=effort)

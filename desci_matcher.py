"""
DeSci Project Matcher
Given a digested research finding, finds the most relevant DeSci projects
working on that problem area.
"""

import json
import requests
from pathlib import Path


def load_projects(path: str = "desci_projects.json") -> list[dict]:
    """Load the curated DeSci project database."""
    with open(path, encoding='utf-8') as f:
        return json.load(f)


MATCH_SYSTEM_PROMPT = """You are a DeSci (Decentralized Science) expert who knows the Web3 science ecosystem deeply.
Your job is to match a scientific research finding with the most relevant DeSci projects working on that problem.

Be selective. Only recommend projects with a genuine, meaningful connection to the finding.
A vague thematic overlap is NOT enough — the project should be plausibly working on the same problem space.
If no project is a strong match, say so honestly.

Respond ONLY in valid JSON. No markdown, no backticks, no preamble."""

MATCH_USER_PROMPT = """Here is a digested research finding and a list of DeSci projects.

RESEARCH FINDING:
Category: {category}
Key Finding: {key_finding}
Why It Matters: {why_it_matters}
Evidence Strength: {evidence_strength}

DESCI PROJECTS DATABASE:
{projects_json}

Find the 1-3 MOST RELEVANT projects. Respond with this exact JSON:
{{
  "matches": [
    {{
      "name": "exact project name from the list",
      "relevance": "1-2 sentences — specifically WHY this project is relevant to this exact finding. Be concrete.",
      "match_strength": one of ["Strong", "Moderate", "Tangential"]
    }}
  ],
  "no_match_reason": "If zero strong/moderate matches exist, explain why. Otherwise empty string."
}}

Only include "Strong" or "Moderate" matches in the output — drop Tangential matches unless nothing better exists.
Maximum 2 matches. Quality over quantity."""


def match_desci_projects(
    digest: dict,
    api_key: str,
    projects_path: str = "desci_projects.json",
) -> dict:
    """
    Match a digest to relevant DeSci projects.
    Returns a dict with 'matches' list and optional 'no_match_reason'.
    """
    projects = load_projects(projects_path)

    # Build a lean version of projects for the prompt (avoid token bloat)
    projects_lean = [
        {
            "name": p["name"],
            "tagline": p["tagline"],
            "focus_areas": p["focus_areas"],
            "description": p["description"][:200],
        }
        for p in projects
    ]

    prompt = MATCH_USER_PROMPT.format(
        category=digest.get("category", ""),
        key_finding=digest.get("key_finding", ""),
        why_it_matters=digest.get("why_it_matters", ""),
        evidence_strength=digest.get("evidence_strength", ""),
        projects_json=json.dumps(projects_lean, indent=2),
    )

    try:
        response = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": "meta-llama/llama-3.3-70b-instruct",
                "messages": [
                    {"role": "system", "content": MATCH_SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
            },
            timeout=20,
        )
        raw = response.json()["choices"][0]["message"]["content"].strip()
        raw = raw.replace("```json", "").replace("```", "").strip()
        result = json.loads(raw)

        # Enrich matches with full project details
        project_map = {p["name"]: p for p in projects}
        enriched_matches = []
        for match in result.get("matches", []):
            name = match.get("name", "")
            project = project_map.get(name)
            if project:
                enriched_matches.append({
                    **match,
                    "website": project["links"].get("website", ""),
                    "twitter": project["links"].get("twitter", ""),
                    "token": project.get("token"),
                    "chain": project.get("chain", ""),
                })

        return {
            "matches": enriched_matches,
            "no_match_reason": result.get("no_match_reason", ""),
        }

    except Exception as e:
        print(f"  DeSci match error: {e}")
        return {"matches": [], "no_match_reason": f"Matching failed: {e}"}


def _html_escape(text: str) -> str:
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def format_desci_section_telegram(match_result: dict) -> str:
    """Format DeSci matches for a Telegram message (HTML parse mode)."""
    matches = match_result.get("matches", [])
    if not matches:
        return ""

    lines = ["\n🔬 <b>DeSci Projects On This:</b>"]
    for m in matches:
        strength_badge = "🔥" if m["match_strength"] == "Strong" else "🔗"
        token_str = f" <code>${_html_escape(m['token'])}</code>" if m.get("token") else ""
        lines.append(
            f"{strength_badge} <b>{_html_escape(m['name'])}</b>{token_str} — {_html_escape(m['relevance'])}\n"
            f"   🌐 <a href=\"{_html_escape(m['website'])}\">Website</a> | 𝕏 {_html_escape(m['twitter'])}"
        )

    return "\n".join(lines)


def format_desci_section_discord(match_result: dict) -> list[dict]:
    """Format DeSci matches as Discord embed fields."""
    matches = match_result.get("matches", [])
    if not matches:
        return []

    fields = []
    for m in matches:
        strength_badge = "🔥" if m["match_strength"] == "Strong" else "🔗"
        token_str = f" (${m['token']})" if m.get("token") else ""
        fields.append({
            "name": f"{strength_badge} {m['name']}{token_str}",
            "value": f"{m['relevance']}\n[Website]({m['website']}) • 𝕏 {m['twitter']}",
            "inline": False,
        })

    return fields

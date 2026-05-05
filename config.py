"""
Configuration for Mental Health Science Agent.
Secrets are loaded from .env locally; in CI set them as environment variables.
"""

import os
from dotenv import load_dotenv

load_dotenv()

CONFIG = {
    # ── OpenRouter ────────────────────────────────────────────
    # Get yours at: https://openrouter.ai/keys
    "openrouter_api_key": os.getenv("OPENROUTER_API_KEY"),

    # ── Telegram ───────────────────────────────────────────────
    # 1. Message @BotFather on Telegram → /newbot → copy token
    # 2. Add bot to your channel as admin
    # 3. Set chat_id to "@your_channel_name" or the numeric ID
    "telegram_bot_token": os.getenv("TELEGRAM_BOT_TOKEN"),
    "telegram_chat_id":   os.getenv("TELEGRAM_CHAT_ID", "2085012164"),

    # ── Discord ────────────────────────────────────────────────
    # Channel Settings → Integrations → Webhooks → New Webhook → Copy URL
    "discord_webhook_url": os.getenv("DISCORD_WEBHOOK_URL", ""),

    # ── DeSci ─────────────────────────────────────────────────
    # Path to curated project database (relative to agent.py)
    "desci_projects_path": "desci_projects.json",

    # ── Agent Behavior ─────────────────────────────────────────
    "lookback_days": 14,          # How recent should RSS articles be
    "max_papers_per_run": 10,     # Max papers to process per run (cost control)
    "min_relevance_score": 6,     # 1-10, only post findings scoring above this
}

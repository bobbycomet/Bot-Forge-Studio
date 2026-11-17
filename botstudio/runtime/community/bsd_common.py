"""Shared constants and helpers."""
from __future__ import annotations

import logging

import discord

log = logging.getLogger("bot.tasks")

GOLD = discord.Colour.from_rgb(201, 162, 39)
CROWN = "📋"
MAX_TASKS = 20
MAX_PINGED = 10
MAX_ROLE_PINGS = 5


async def report_error(interaction: discord.Interaction, error: Exception) -> None:
    """Log an unexpected error and tell the user something went wrong."""
    log.error("Unhandled interaction error", exc_info=error)
    msg = "⚠️ Something went wrong. Please try again."
    try:
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)
    except discord.HTTPException:
        pass

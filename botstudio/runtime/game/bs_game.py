"""Shared host for the Bot Forge Studio game modules (Quests, Gear & Loot, Dungeons, Profile Cards).

The modules were written against a small dependency-injection surface (db helpers, wallet, admin check,
team lookup).  This file provides it on top of SQLite (aiosqlite) and the same kv store the Economy nodes
use, so currency earned in a dungeon is the same currency a "Give Currency" node hands out.

    from bs_game import host
    host.configure(...)  # done once by whichever game cog loads first (it is idempotent)
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
from pathlib import Path
from typing import Any, Optional

import aiosqlite
import discord

import bs_runtime as bs

log = logging.getLogger("bot.game")
DB_PATH = Path(os.getenv("DATA_DIR", bs.BASE_DIR / "data")) / "game.db"


def _sql(sql: str) -> str:
    """The modules were written for SQLite *and* PostgreSQL; map the few PG spellings."""
    return (sql.replace("LEAST(", "MIN(").replace("GREATEST(", "MAX(")
               .replace("NOW()", "CURRENT_TIMESTAMP").replace("ILIKE", "LIKE"))


class Host:
    def __init__(self):
        self._conn: Optional[aiosqlite.Connection] = None
        self._lock = asyncio.Lock()
        self.wallet = "wallet"          # kv table shared with the Economy nodes
        self.currency_name = "coins"
        self.currency_emoji = "🪙"
        self.teams = ("Team A", "Team B")
        self.ready = False
        self.client: Optional[discord.Client] = None

    # -- database -----------------------------------------------------------------------
    async def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            DB_PATH.parent.mkdir(parents=True, exist_ok=True)
            pending = aiosqlite.connect(DB_PATH)
            pending.daemon = True                  # never keep the process alive after the bot has stopped
            self._conn = await pending
            self._conn.row_factory = aiosqlite.Row
            await self._conn.execute("PRAGMA journal_mode=WAL")
            await self._conn.execute("PRAGMA busy_timeout=5000")
        return self._conn

    async def close(self) -> None:
        """Close the connection (cogs call this on unload; it reopens lazily on next use)."""
        async with self._lock:
            c, self._conn = self._conn, None
        if c is not None:
            try:
                await c.close()
            except Exception:
                pass

    async def db_exec(self, sql: str, params: tuple = ()) -> None:
        async with self._lock:
            c = await self.conn()
            await c.execute(_sql(sql), tuple(params))
            await c.commit()

    async def db_fetch(self, sql: str, params: tuple = ()) -> list:
        async with self._lock:
            c = await self.conn()
            cur = await c.execute(_sql(sql), tuple(params))
            return list(await cur.fetchall())

    async def db_fetch_one(self, sql: str, params: tuple = ()):
        rows = await self.db_fetch(sql, params)
        return rows[0] if rows else None

    async def db_transaction(self, ops: list) -> None:
        if not ops:
            return
        async with self._lock:
            c = await self.conn()
            try:
                for sql, params in ops:
                    await c.execute(_sql(sql), tuple(params))
                await c.commit()
            except Exception:
                await c.rollback()
                raise

    # -- wallet (shared with Economy nodes) ----------------------------------------------------
    async def add_gold(self, guild_id: Any, user_id: Any, amount: float) -> int:
        amt = max(0, round(amount))
        bal = int(await bs.eco_add(self.wallet, guild_id, user_id, amt))
        if amt:
            try:
                await track(guild_id, user_id, "currency_earned", amt)
            except Exception:
                log.exception("quest hook failed (currency still credited)")
        return bal

    async def deduct_gold(self, guild_id: Any, user_id: Any, amount: float) -> bool:
        ok, _ = await bs.eco_spend(self.wallet, guild_id, user_id, round(amount))
        return ok

    async def balance(self, guild_id: Any, user_id: Any) -> int:
        return int(await bs.eco_balance(self.wallet, guild_id, user_id))

    def money(self, n: Any) -> str:
        return f"{self.currency_emoji} {int(n):,} {self.currency_name}"

    # -- members -----------------------------------------------------------------------------------
    def is_admin(self, interaction: discord.Interaction) -> bool:
        p = getattr(interaction.user, "guild_permissions", None)
        return bool(p and (p.manage_guild or p.administrator))

    async def get_team(self, guild_id: Any, user_id: Any) -> Optional[str]:
        """Team/faction a member belongs to (set with the Team nodes), or None."""
        return await bs.kv_get("team", bs.eco_key(guild_id, user_id), None)

    async def set_team(self, guild_id: Any, user_id: Any, team: Optional[str]) -> None:
        await bs.kv_set("team", bs.eco_key(guild_id, user_id), team)


host = Host()

# the event bus lives in bs_runtime so plain nodes can report events without this module being installed
on_event = bs.on_event
off_event = bs.off_event
track = bs.track

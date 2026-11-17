"""Gear & Rarity — equipment templates, rarity tiers, enchanting and evolving (Bot Forge Studio game module).

Every owned item is its own copy with a rarity (normal → basic → rare → legendary → mythic → god) and an optional
enchant, so two "Iron Axe" copies can differ.  Evolving sacrifices matching copies to climb one tier.
Admins manage templates, enchants and per-tier stat multipliers with /gearadmin; players use /gear.
Nodes: Give Gear (reward / shop purchase), Equipped Stats.  Dungeons read these stats for combat.
"""

from __future__ import annotations
import uuid
from datetime import datetime, timezone
from typing import Callable, Optional

import discord
from discord import Embed
from discord.ui import View, Button, Modal, TextInput, Select

from bs_game import host
import bs_runtime as bs

# ══════════════════════════════════════════════════════════════════════════
C_OK, C_ERR, C_INFO = 0x10b981, 0xef4444, 0x8B5CF6


def _ok(msg: str) -> Embed: return Embed(description=f"✅  {msg}", color=C_OK)
def _err(msg: str) -> Embed: return Embed(description=f"❌  {msg}", color=C_ERR)
def _info(title: str, body: str = "", color: int = C_INFO) -> Embed:
    return Embed(title=title, description=body or None, color=color)


RARITY_ORDER = ["normal", "basic", "rare", "legendary", "mythic", "god"]
RARITY_EMOJI = {
    "normal": "⚪", "basic": "🟢", "rare": "🔵",
    "legendary": "🟠", "mythic": "🟣", "god": "🟡",
}
# Default per-tier stat multiplier — overridable per guild in guild_rarity_scaling.
DEFAULT_RARITY_MULT = {
    "normal": 1.0, "basic": 1.3, "rare": 1.8,
    "legendary": 2.6, "mythic": 4.0, "god": 6.5,
}
# Sacrifices REQUIRED (in addition to the base item) to evolve FROM this rarity.
EVOLVE_COST = {"normal": 1, "basic": 2, "rare": 4, "legendary": 10, "mythic": 20}

GEAR_TYPES = ["weapon", "armor", "accessory"]   # matches guild_gear's CHECK constraint

# effect_type is what dungeons.py / combat code actually reads.
# effect_note is admin-authored flavor text describing the effect to players.
ENCHANT_EFFECT_TYPES = {
    "power_pct":     "Flat % boost to attack power",
    "block_pct":     "Flat % boost to block/mitigation (shields)",
    "burn_dot":      "Chance-based damage-over-time on hit",
    "freeze_slow":   "Chance to reduce monster's next attack power",
    "poison_dot":    "Chance-based damage-over-time on hit",
    "paralyze":      "Chance to skip the monster's next attack entirely",
    "spell_block":   "Grants a castable 'Spell' button that fully blocks one monster attack",
    "custom":        "No mechanical effect wired in — flavor / roleplay only",
}


def _next_rarity(rarity: str) -> Optional[str]:
    try:
        idx = RARITY_ORDER.index(rarity)
    except ValueError:
        return None
    return RARITY_ORDER[idx + 1] if idx + 1 < len(RARITY_ORDER) else None


_db_exec = host.db_exec
_db_fetch = host.db_fetch
_db_fetch_one = host.db_fetch_one
_db_transaction = host.db_transaction
_deduct_gold = host.deduct_gold
_is_admin = host.is_admin


def _cur() -> str:
    return host.currency_emoji


# ══════════════════════════════════════════════════════════════════════════
# SCHEMA
# ══════════════════════════════════════════════════════════════════════════
async def init_gear_tables():
    await _db_exec(
        """CREATE TABLE IF NOT EXISTS guild_gear (
            gear_id            TEXT NOT NULL,
            guild_id           TEXT NOT NULL,
            display_name       TEXT NOT NULL,
            emoji              TEXT NOT NULL DEFAULT '⚔️',
            description        TEXT NOT NULL DEFAULT '',
            gear_type          TEXT NOT NULL DEFAULT 'weapon'
                CHECK(gear_type IN ('weapon','armor','accessory')),
            atk_bonus          REAL NOT NULL DEFAULT 0,
            hp_bonus           REAL NOT NULL DEFAULT 0,
            class_restriction  TEXT,
            is_default         INTEGER NOT NULL DEFAULT 0,
            enabled            INTEGER NOT NULL DEFAULT 1,
            PRIMARY KEY (gear_id, guild_id)
        )""", ())
    # guild_gear holds the base templates; rarity/enchant/evolve build on top of it.
    await _db_exec(
        """CREATE TABLE IF NOT EXISTS gear_enchants (
            guild_id      TEXT NOT NULL,
            enchant_id    TEXT NOT NULL,
            display_name  TEXT NOT NULL,
            emoji         TEXT NOT NULL DEFAULT '✨',
            effect_type   TEXT NOT NULL DEFAULT 'power_pct',
            effect_value  REAL NOT NULL DEFAULT 0,
            effect_note   TEXT NOT NULL DEFAULT '',
            cost_gold     INTEGER NOT NULL DEFAULT 0,
            enabled       INTEGER NOT NULL DEFAULT 1,
            PRIMARY KEY (guild_id, enchant_id)
        )""", ())
    await _db_exec(
        """CREATE TABLE IF NOT EXISTS user_gear_instances (
            instance_id  TEXT PRIMARY KEY,
            guild_id     TEXT NOT NULL,
            user_id      TEXT NOT NULL,
            template_id  TEXT NOT NULL,   -- references guild_gear.gear_id
            rarity       TEXT NOT NULL DEFAULT 'normal',
            enchant_id   TEXT,
            equipped     INTEGER NOT NULL DEFAULT 0,
            acquired_at  TEXT NOT NULL
        )""", ())
    await _db_exec(
        """CREATE TABLE IF NOT EXISTS guild_rarity_scaling (
            guild_id   TEXT NOT NULL,
            rarity     TEXT NOT NULL,
            multiplier REAL NOT NULL,
            PRIMARY KEY (guild_id, rarity)
        )""", ())


async def _rarity_mult(guild_id: str, rarity: str) -> float:
    row = await _db_fetch_one(
        "SELECT multiplier FROM guild_rarity_scaling WHERE guild_id=? AND rarity=?",
        (guild_id, rarity))
    if row:
        return float(row["multiplier"])
    return DEFAULT_RARITY_MULT.get(rarity, 1.0)


# ══════════════════════════════════════════════════════════════════════════
# CORE OPS
# ══════════════════════════════════════════════════════════════════════════
async def grant_gear(guild_id: str, user_id: str, template_id: str, rarity: str = "normal") -> Optional[str]:
    """Create a new gear instance for a user (used by shop purchases, dungeon
    loot, and admin grants). template_id is a guild_gear.gear_id."""
    tmpl = await _db_fetch_one(
        "SELECT gear_id FROM guild_gear WHERE guild_id=? AND gear_id=? AND enabled=1",
        (guild_id, template_id))
    if not tmpl:
        return None
    instance_id = "gi_" + uuid.uuid4().hex[:12]
    await _db_exec(
        """INSERT INTO user_gear_instances (instance_id, guild_id, user_id, template_id, rarity, acquired_at)
           VALUES (?,?,?,?,?,?)""",
        (instance_id, guild_id, user_id, template_id, rarity if rarity in RARITY_ORDER else "normal",
         datetime.now(timezone.utc).isoformat()))
    return instance_id


async def get_user_gear(guild_id: str, user_id: str) -> list[dict]:
    rows = await _db_fetch(
        """SELECT gi.*, t.display_name, t.emoji, t.gear_type,
                  t.atk_bonus AS base_atk, t.hp_bonus AS base_hp,
                  e.display_name AS enchant_name, e.emoji AS enchant_emoji,
                  e.effect_type, e.effect_value, e.effect_note
           FROM user_gear_instances gi
           JOIN guild_gear t ON t.guild_id=gi.guild_id AND t.gear_id=gi.template_id
           LEFT JOIN gear_enchants e ON e.guild_id=gi.guild_id AND e.enchant_id=gi.enchant_id
           WHERE gi.guild_id=? AND gi.user_id=?
           ORDER BY gi.equipped DESC, t.gear_type, gi.acquired_at""",
        (guild_id, user_id))
    return [dict(r) for r in rows]


async def get_instance(guild_id: str, user_id: str, instance_id: str) -> Optional[dict]:
    row = await _db_fetch_one(
        """SELECT gi.*, t.display_name, t.emoji, t.gear_type,
                  t.atk_bonus AS base_atk, t.hp_bonus AS base_hp
           FROM user_gear_instances gi
           JOIN guild_gear t ON t.guild_id=gi.guild_id AND t.gear_id=gi.template_id
           WHERE gi.guild_id=? AND gi.user_id=? AND gi.instance_id=?""",
        (guild_id, user_id, instance_id))
    return dict(row) if row else None


async def equip_gear(guild_id: str, user_id: str, instance_id: str) -> tuple[bool, str]:
    inst = await get_instance(guild_id, user_id, instance_id)
    if not inst:
        return False, "You don't own that item."
    # One equipped item per gear_type slot (weapon / armor / accessory)
    await _db_exec(
        """UPDATE user_gear_instances SET equipped=0
           WHERE guild_id=? AND user_id=? AND equipped=1 AND template_id IN (
               SELECT gear_id FROM guild_gear
               WHERE guild_id=? AND gear_type=?)""",
        (guild_id, user_id, guild_id, inst["gear_type"]))
    await _db_exec(
        "UPDATE user_gear_instances SET equipped=1 WHERE guild_id=? AND user_id=? AND instance_id=?",
        (guild_id, user_id, instance_id))
    return True, f"Equipped **{inst['display_name']}** ({inst['rarity']})."


async def unequip_gear(guild_id: str, user_id: str, instance_id: str) -> tuple[bool, str]:
    inst = await get_instance(guild_id, user_id, instance_id)
    if not inst:
        return False, "You don't own that item."
    await _db_exec(
        "UPDATE user_gear_instances SET equipped=0 WHERE guild_id=? AND user_id=? AND instance_id=?",
        (guild_id, user_id, instance_id))
    return True, f"Unequipped **{inst['display_name']}**."


async def get_equipped_stats(guild_id: str, user_id: str) -> dict:
    """Summed atk/hp/block across everything equipped, rarity + enchant applied.
    Used by dungeons.py for combat math. Returns sane defaults (base human
    stats) if the player owns nothing."""
    rows = await get_user_gear(guild_id, user_id)
    atk, hp, block = 5.0, 100.0, 0.0   # unarmed baseline
    spell_charges = 0
    for r in rows:
        if not r["equipped"]:
            continue
        mult = await _rarity_mult(guild_id, r["rarity"])
        item_atk = float(r["base_atk"]) * mult
        item_hp = float(r["base_hp"]) * mult
        item_block = 0.0   # guild_gear has no block stat — block comes purely from enchants
        if r.get("effect_type") == "power_pct":
            item_atk *= (1 + float(r["effect_value"]) / 100.0)
        if r.get("effect_type") == "block_pct":
            item_block += float(r["effect_value"])
        if r.get("effect_type") == "spell_block":
            spell_charges += 1
        atk += item_atk
        hp += item_hp
        block += item_block
    return {"atk": atk, "hp": hp, "block": min(block, 90.0), "spell_charges": spell_charges}


async def enchant_gear(guild_id: str, user_id: str, instance_id: str, enchant_id: str) -> tuple[bool, str]:
    inst = await get_instance(guild_id, user_id, instance_id)
    if not inst:
        return False, "You don't own that item."
    ench = await _db_fetch_one(
        "SELECT * FROM gear_enchants WHERE guild_id=? AND enchant_id=? AND enabled=1",
        (guild_id, enchant_id))
    if not ench:
        return False, "That enchant doesn't exist."
    cost = int(ench["cost_gold"])
    if cost and _deduct_gold:
        paid = await _deduct_gold(guild_id, user_id, cost)
        if not paid:
            return False, f"You need {cost} {_cur()} to apply this enchant."
    await _db_exec(
        "UPDATE user_gear_instances SET enchant_id=? WHERE guild_id=? AND user_id=? AND instance_id=?",
        (enchant_id, guild_id, user_id, instance_id))
    return True, f"{ench['emoji']} **{inst['display_name']}** enchanted with **{ench['display_name']}**! {ench['effect_note']}"


async def evolve_gear(guild_id: str, user_id: str, base_instance_id: str, sacrifice_ids: list[str]) -> tuple[bool, str]:
    base = await get_instance(guild_id, user_id, base_instance_id)
    if not base:
        return False, "Base item not found in your inventory."
    rarity = base["rarity"]
    needed = EVOLVE_COST.get(rarity)
    if needed is None:
        return False, f"**{base['display_name']}** is already max rarity (god tier)."
    if len(sacrifice_ids) != needed:
        return False, f"Evolving from {rarity} needs exactly {needed} sacrifices (you selected {len(sacrifice_ids)})."
    if base_instance_id in sacrifice_ids:
        return False, "You can't sacrifice the item you're evolving."
    sac_rows = []
    for sid in sacrifice_ids:
        s = await get_instance(guild_id, user_id, sid)
        if not s:
            return False, "One of the sacrifice items wasn't found in your inventory."
        if s["template_id"] != base["template_id"] or s["rarity"] != rarity:
            return False, (f"All sacrifices must be the same item AND rarity as the base "
                            f"(**{base['display_name']}**, {rarity}) — "
                            f"a {s['rarity']} {s['display_name']} doesn't qualify.")
        if s.get("equipped"):
            return False, f"**{s['display_name']}** is equipped — unequip it before sacrificing."
        sac_rows.append(s)
    new_rarity = _next_rarity(rarity)
    ops = [("DELETE FROM user_gear_instances WHERE guild_id=? AND user_id=? AND instance_id=?",
             (guild_id, user_id, sid)) for sid in sacrifice_ids]
    ops.append(("UPDATE user_gear_instances SET rarity=? WHERE guild_id=? AND user_id=? AND instance_id=?",
                (new_rarity, guild_id, user_id, base_instance_id)))
    await _db_transaction(ops)
    return True, f"🌟 **{base['display_name']}** evolved: {rarity} → **{new_rarity}**!"


# ══════════════════════════════════════════════════════════════════════════
# PLAYER UI: /gear
# ══════════════════════════════════════════════════════════════════════════
def _gear_line(r: dict) -> str:
    rarity_e = RARITY_EMOJI.get(r["rarity"], "⚪")
    eq = " 🔸*equipped*" if r["equipped"] else ""
    ench = f" · {r['enchant_emoji']} {r['enchant_name']}" if r.get("enchant_name") else ""
    return (f"{r['emoji']} **{r['display_name']}** {rarity_e}{r['rarity'].title()} "
            f"(`{r['instance_id']}`) — {r['gear_type']}{ench}{eq}")


class GearListView(View):
    def __init__(self, guild_id: str, user_id: str, rows: list[dict]):
        super().__init__(timeout=180)
        self.guild_id, self.user_id = guild_id, user_id
        self.rows = rows

    @discord.ui.button(label="⚔️ Equip / Unequip", style=discord.ButtonStyle.primary, row=0)
    async def equip_btn(self, interaction: discord.Interaction, _btn):
        if not self.rows:
            return await interaction.response.send_message(embed=_err("You have no gear."), ephemeral=True)
        opts = [discord.SelectOption(
            label=f"{r['display_name']} ({r['rarity']})"[:100],
            description=("Unequip" if r["equipped"] else "Equip")[:100],
            value=r["instance_id"]) for r in self.rows[:25]]
        view = View(timeout=60)
        sel = Select(placeholder="Choose an item…", options=opts)

        async def cb(i2: discord.Interaction):
            iid = i2.data["values"][0]
            row = next(r for r in self.rows if r["instance_id"] == iid)
            if row["equipped"]:
                ok, msg = await unequip_gear(self.guild_id, self.user_id, iid)
            else:
                ok, msg = await equip_gear(self.guild_id, self.user_id, iid)
            await i2.response.send_message(embed=(_ok(msg) if ok else _err(msg)), ephemeral=True)
        sel.callback = cb
        view.add_item(sel)
        await interaction.response.send_message(embed=_info("⚔️ Equip / Unequip"), view=view, ephemeral=True)

    @discord.ui.button(label="✨ Enchant", style=discord.ButtonStyle.secondary, row=0)
    async def enchant_btn(self, interaction: discord.Interaction, _btn):
        enchants = await _db_fetch(
            "SELECT * FROM gear_enchants WHERE guild_id=? AND enabled=1 ORDER BY display_name",
            (self.guild_id,))
        if not self.rows or not enchants:
            return await interaction.response.send_message(embed=_err("Need at least one item and one available enchant."), ephemeral=True)
        item_opts = [discord.SelectOption(label=f"{r['display_name']} ({r['rarity']})"[:100], value=r["instance_id"]) for r in self.rows[:25]]
        view = View(timeout=90)
        item_sel = Select(placeholder="1. Choose item to enchant…", options=item_opts)

        async def item_cb(i2: discord.Interaction):
            iid = i2.data["values"][0]
            ench_opts = [discord.SelectOption(
                label=f"{e['display_name']} ({e['cost_gold']} {_cur()})"[:100],
                description=e["effect_note"][:100] or ENCHANT_EFFECT_TYPES.get(e["effect_type"], ""),
                value=e["enchant_id"]) for e in enchants[:25]]
            view2 = View(timeout=60)
            ench_sel = Select(placeholder="2. Choose enchant…", options=ench_opts)

            async def ench_cb(i3: discord.Interaction):
                eid = i3.data["values"][0]
                ok, msg = await enchant_gear(self.guild_id, self.user_id, iid, eid)
                await i3.response.send_message(embed=(_ok(msg) if ok else _err(msg)), ephemeral=True)
            ench_sel.callback = ench_cb
            view2.add_item(ench_sel)
            await i2.response.send_message(embed=_info("✨ Choose an enchant"), view=view2, ephemeral=True)
        item_sel.callback = item_cb
        view.add_item(item_sel)
        await interaction.response.send_message(embed=_info("✨ Enchant Gear"), view=view, ephemeral=True)

    @discord.ui.button(label="🌟 Evolve", style=discord.ButtonStyle.success, row=0)
    async def evolve_btn(self, interaction: discord.Interaction, _btn):
        await interaction.response.send_message(
            embed=_info("🌟 Evolve Gear",
                "Use `/gear evolve base:<instance_id> sacrifices:<id1,id2,...>`\n\n"
                "Sacrifices must be the **same item and rarity** as the base. "
                "Costs: normal→basic 1 · basic→rare 2 · rare→legendary 4 · "
                "legendary→mythic 10 · mythic→god 20.\n\n"
                "Your item IDs are shown in parentheses in `/gear`."),
            ephemeral=True)


def _gear_embed(rows: list[dict], display_name: str) -> Embed:
    e = _info(f"🎒 {display_name}'s Gear", color=0x8B5CF6)
    if not rows:
        e.description = "No gear yet — clear a dungeon to find some!"
        return e
    e.description = "\n".join(_gear_line(r) for r in rows[:25])
    if len(rows) > 25:
        e.set_footer(text=f"+{len(rows) - 25} more not shown")
    return e


# ══════════════════════════════════════════════════════════════════════════
# ADMIN CRUD: /gearadmin
# Templates (weapons / armor / accessories), enchants, rarity scaling and direct grants.
# ══════════════════════════════════════════════════════════════════════════
class _GrantModal(Modal, title="Grant Gear to Player"):
    gear_id = TextInput(label="Gear ID (from the gear catalog)", max_length=50)
    rarity = TextInput(label="Rarity", placeholder="normal / basic / rare / legendary / mythic / god", default="normal")

    def __init__(self, guild_id: str, user_id: str):
        super().__init__()
        self.guild_id, self.user_id = guild_id, user_id

    async def on_submit(self, interaction: discord.Interaction):
        gid = self.gear_id.value.strip().lower()
        rar = self.rarity.value.strip().lower() or "normal"
        if rar not in RARITY_ORDER:
            return await interaction.response.send_message(
                embed=_err(f"Rarity must be one of: {', '.join(RARITY_ORDER)}"), ephemeral=True)
        instance_id = await grant_gear(self.guild_id, self.user_id, gid, rar)
        if not instance_id:
            return await interaction.response.send_message(
                embed=_err(f"Gear ID `{gid}` isn't in this server's gear catalog (see /gearadmin → Catalog)."),
                ephemeral=True)
        await interaction.response.send_message(
            embed=_ok(f"Granted <@{self.user_id}> a **{rar}** copy of `{gid}`."), ephemeral=True)


class _EnchantModal(Modal, title="Add/Edit Enchant"):
    enchant_id = TextInput(label="Enchant ID (unique, no spaces)", max_length=40)
    display_name = TextInput(label="Display name", placeholder="Flame", max_length=60)
    value_cost = TextInput(label="effect_value | cost", placeholder="15 | 200")
    effect_note = TextInput(label="What it does (shown to players)", style=discord.TextStyle.paragraph, max_length=300)

    def __init__(self, guild_id: str, effect_type: str):
        super().__init__()
        self.guild_id, self.effect_type = guild_id, effect_type

    async def on_submit(self, interaction: discord.Interaction):
        eid = "".join(c for c in self.enchant_id.value.strip() if c.isalnum() or c == "_")
        if not eid:
            return await interaction.response.send_message(embed=_err("Enchant ID required."), ephemeral=True)
        try:
            parts = [p.strip() for p in self.value_cost.value.split("|")]
            value = float(parts[0]); cost = int(float(parts[1]))
        except (ValueError, IndexError):
            return await interaction.response.send_message(embed=_err("Must be `effect_value | cost`."), ephemeral=True)
        await _db_exec(
            """INSERT INTO gear_enchants (guild_id, enchant_id, display_name, effect_type, effect_value, effect_note, cost_gold)
               VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(guild_id, enchant_id) DO UPDATE SET
                   display_name=excluded.display_name, effect_type=excluded.effect_type,
                   effect_value=excluded.effect_value, effect_note=excluded.effect_note, cost_gold=excluded.cost_gold""",
            (self.guild_id, eid, self.display_name.value.strip(), self.effect_type, value,
             self.effect_note.value.strip(), cost))
        await interaction.response.send_message(embed=_ok(f"Enchant **{eid}** saved."), ephemeral=True)


class _TemplateModal(Modal, title="Add / Edit Gear"):
    gear_id = TextInput(label="Gear ID (unique, no spaces)", placeholder="iron_sword", max_length=50)
    display_name = TextInput(label="Name", placeholder="Iron Sword", max_length=60)
    stats = TextInput(label="Type | attack | hp", placeholder="weapon | 12 | 0   (weapon, armor or accessory)", max_length=40)
    emoji = TextInput(label="Emoji", placeholder="⚔️", required=False, max_length=8)
    description = TextInput(label="Description", style=discord.TextStyle.paragraph, required=False, max_length=200)

    def __init__(self, guild_id: str):
        super().__init__()
        self.guild_id = guild_id

    async def on_submit(self, interaction: discord.Interaction):
        gid = "".join(c for c in self.gear_id.value.strip().lower() if c.isalnum() or c == "_")
        try:
            parts = [p.strip() for p in self.stats.value.split("|")]
            gtype = parts[0].lower()
            atk = float(parts[1]) if len(parts) > 1 and parts[1] else 0.0
            hp = float(parts[2]) if len(parts) > 2 and parts[2] else 0.0
        except (ValueError, IndexError):
            return await interaction.response.send_message(embed=_err("Use `type | attack | hp`, e.g. `weapon | 12 | 0`."), ephemeral=True)
        if not gid or gtype not in GEAR_TYPES:
            return await interaction.response.send_message(embed=_err(f"Need an ID and a type of {', '.join(GEAR_TYPES)}."), ephemeral=True)
        await _db_exec(
            """INSERT INTO guild_gear (gear_id, guild_id, display_name, emoji, description, gear_type, atk_bonus, hp_bonus)
               VALUES (?,?,?,?,?,?,?,?)
               ON CONFLICT(gear_id, guild_id) DO UPDATE SET display_name=excluded.display_name, emoji=excluded.emoji,
                   description=excluded.description, gear_type=excluded.gear_type,
                   atk_bonus=excluded.atk_bonus, hp_bonus=excluded.hp_bonus""",
            (gid, self.guild_id, self.display_name.value.strip(), self.emoji.value.strip() or "⚔️",
             self.description.value.strip(), gtype, atk, hp))
        await interaction.response.send_message(embed=_ok(f"Gear **{gid}** saved."), ephemeral=True)


class GearAdminView(View):
    def __init__(self, guild_id: str):
        super().__init__(timeout=300)
        self.guild_id = guild_id

    @discord.ui.button(label="📋 Gear Catalog", style=discord.ButtonStyle.secondary, row=0)
    async def list_templates(self, interaction: discord.Interaction, _btn):
        rows = await _db_fetch("SELECT * FROM guild_gear WHERE guild_id=? ORDER BY gear_type, display_name", (self.guild_id,))
        if not rows:
            return await interaction.response.send_message(
                embed=_info("No gear yet", "Use ➕ Add Gear to create weapons, armor and accessories."), ephemeral=True)
        lines = [f"{'🟢' if r['enabled'] else '⚪'} {r['emoji']} `{r['gear_id']}` **{r['display_name']}** "
                 f"({r['gear_type']}) atk{r['atk_bonus']:g}/hp{r['hp_bonus']:g}" for r in rows]
        await interaction.response.send_message(
            embed=_info(f"📋 Gear Catalog ({len(rows)})",
                        "The base templates every rarity copy is built from.\n\n" +
                        "\n".join(lines)[:3800]),
            ephemeral=True)

    @discord.ui.button(label="➕ Add Gear", style=discord.ButtonStyle.primary, row=0)
    async def add_template(self, interaction: discord.Interaction, _btn):
        await interaction.response.send_modal(_TemplateModal(self.guild_id))

    @discord.ui.button(label="🎁 Grant to Player", style=discord.ButtonStyle.primary, row=0)
    async def grant_to_player(self, interaction: discord.Interaction, _btn):
        view = View(timeout=60)
        user_select = discord.ui.UserSelect(placeholder="Choose a player…")

        async def cb(i2: discord.Interaction):
            member = user_select.values[0]
            await i2.response.send_modal(_GrantModal(self.guild_id, str(member.id)))
        user_select.callback = cb
        view.add_item(user_select)
        await interaction.response.send_message(
            embed=_info("🎁 Grant Gear to Player", "Same as `/gear grant` — pick a player, then enter the gear ID and rarity."),
            view=view, ephemeral=True)

    @discord.ui.button(label="✨ Add Enchant", style=discord.ButtonStyle.primary, row=1)
    async def add_enchant(self, interaction: discord.Interaction, _btn):
        view = View(timeout=60)
        opts = [discord.SelectOption(label=v[:100], description=k, value=k) for k, v in ENCHANT_EFFECT_TYPES.items()]
        sel = Select(placeholder="Choose underlying effect_type…", options=opts)

        async def cb(i2: discord.Interaction):
            await i2.response.send_modal(_EnchantModal(self.guild_id, i2.data["values"][0]))
        sel.callback = cb
        view.add_item(sel)
        await interaction.response.send_message(
            embed=_info("✨ Add Enchant", "Pick the mechanical effect type, then write the exact number and flavor text in the form."),
            view=view, ephemeral=True)

    @discord.ui.button(label="📋 List Enchants", style=discord.ButtonStyle.secondary, row=1)
    async def list_enchants(self, interaction: discord.Interaction, _btn):
        rows = await _db_fetch("SELECT * FROM gear_enchants WHERE guild_id=? ORDER BY display_name", (self.guild_id,))
        if not rows:
            return await interaction.response.send_message(embed=_info("No enchants yet."), ephemeral=True)
        lines = [f"{'🟢' if r['enabled'] else '⚪'} {r['emoji']} `{r['enchant_id']}` **{r['display_name']}** "
                 f"[{r['effect_type']}={r['effect_value']:g}] {r['cost_gold']}{_cur()} — {r['effect_note']}" for r in rows]
        await interaction.response.send_message(embed=_info(f"✨ Enchants ({len(rows)})", "\n".join(lines)[:4000]), ephemeral=True)

    @discord.ui.button(label="🗑️ Delete Enchant", style=discord.ButtonStyle.danger, row=1)
    async def del_enchant(self, interaction: discord.Interaction, _btn):
        rows = await _db_fetch("SELECT enchant_id, display_name FROM gear_enchants WHERE guild_id=? LIMIT 25", (self.guild_id,))
        if not rows:
            return await interaction.response.send_message(embed=_err("Nothing to delete."), ephemeral=True)
        opts = [discord.SelectOption(label=r["display_name"][:100], value=r["enchant_id"]) for r in rows]
        view = View(timeout=60)
        sel = Select(placeholder="Choose enchant to delete…", options=opts)

        async def cb(i2):
            eid = i2.data["values"][0]
            await _db_exec("DELETE FROM gear_enchants WHERE guild_id=? AND enchant_id=?", (self.guild_id, eid))
            await i2.response.send_message(embed=_ok(f"Deleted `{eid}`."), ephemeral=True)
        sel.callback = cb
        view.add_item(sel)
        await interaction.response.send_message(embed=_info("🗑️ Delete Enchant"), view=view, ephemeral=True)

    @discord.ui.button(label="⚖️ Rarity Scaling", style=discord.ButtonStyle.secondary, row=2)
    async def rarity_scaling(self, interaction: discord.Interaction, _btn):
        view = View(timeout=60)
        sel = Select(placeholder="Choose rarity to retune…",
                      options=[discord.SelectOption(label=f"{RARITY_EMOJI[r]} {r.title()} (default ×{DEFAULT_RARITY_MULT[r]:g})", value=r) for r in RARITY_ORDER])

        async def cb(i2: discord.Interaction):
            rarity = i2.data["values"][0]
            cur = await _rarity_mult(self.guild_id, rarity)

            async def modal_cb(i3, mult_str):
                try:
                    mult = float(mult_str)
                except ValueError:
                    return await i3.response.send_message(embed=_err("Must be a number."), ephemeral=True)
                await _db_exec(
                    """INSERT INTO guild_rarity_scaling (guild_id, rarity, multiplier) VALUES (?,?,?)
                       ON CONFLICT(guild_id, rarity) DO UPDATE SET multiplier=excluded.multiplier""",
                    (self.guild_id, rarity, mult))
                await i3.response.send_message(embed=_ok(f"{rarity.title()} multiplier set to ×{mult:g}"), ephemeral=True)

            from discord.ui import Modal as _M
            modal = _M(title=f"{rarity.title()} multiplier")
            ti = TextInput(label="Multiplier", default=str(cur))
            modal.add_item(ti)

            async def on_submit(self_modal, i3):
                await modal_cb(i3, ti.value)
            modal.on_submit = on_submit.__get__(modal)
            await i2.response.send_modal(modal)
        sel.callback = cb
        view.add_item(sel)
        await interaction.response.send_message(embed=_info("⚖️ Rarity Scaling", "Tune the stat multiplier per tier."), view=view, ephemeral=True)


# ══════════════════════════════════════════════════════════════════════════
# COMMAND REGISTRATION
# ══════════════════════════════════════════════════════════════════════════
def register_gear_commands(tree, gear_group=None, names=("gear", "gearadmin")):
    """Registers the /gear group (view, evolve) and the admin panel on *tree*."""
    owns_group = gear_group is None
    if owns_group:
        gear_group = discord.app_commands.Group(name=names[0], description="Your gear — equip, enchant, evolve")

    @gear_group.command(name="view", description="View your gear inventory")
    async def gear_view(interaction: discord.Interaction):
        if not interaction.guild:
            return await interaction.response.send_message("Server only.", ephemeral=True)
        gid, uid = str(interaction.guild.id), str(interaction.user.id)
        await interaction.response.defer(ephemeral=True, thinking=True)
        rows = await get_user_gear(gid, uid)
        view = GearListView(gid, uid, rows)
        await interaction.followup.send(embed=_gear_embed(rows, interaction.user.display_name), view=view, ephemeral=True)

    @gear_group.command(name="evolve", description="Evolve gear by sacrificing matching copies")
    @discord.app_commands.describe(base="Instance ID of the item to evolve", sacrifices="Comma-separated instance IDs to sacrifice")
    async def gear_evolve(interaction: discord.Interaction, base: str, sacrifices: str):
        if not interaction.guild:
            return await interaction.response.send_message("Server only.", ephemeral=True)
        gid, uid = str(interaction.guild.id), str(interaction.user.id)
        sac_ids = [s.strip() for s in sacrifices.split(",") if s.strip()]
        ok, msg = await evolve_gear(gid, uid, base.strip(), sac_ids)
        await interaction.response.send_message(embed=(_ok(msg) if ok else _err(msg)), ephemeral=True)

    if owns_group:
        tree.add_command(gear_group)

    @tree.command(name=names[1], description="Manage gear templates, enchants and rarity scaling (Manage Server only)")
    async def gearadmin_cmd(interaction: discord.Interaction):
        if _is_admin and not _is_admin(interaction):
            return await interaction.response.send_message(embed=_err("You need **Manage Server** permission."), ephemeral=True)
        if not interaction.guild:
            return await interaction.response.send_message("Server only.", ephemeral=True)
        gid = str(interaction.guild.id)
        view = GearAdminView(gid)
        await interaction.response.send_message(
            embed=_info("⚔️ Gear Admin", "Templates, enchants, and rarity multipliers for this server."),
            view=view, ephemeral=True)

    return gear_group, gearadmin_cmd


# Public alias for external callers (e.g. user_panel.py).
build_gear_embed = _gear_embed


# ══════════════════════════════════════════════════════════════════════════
# COG ENTRY POINT (Bot Forge Studio loads this file as an extension)
# ══════════════════════════════════════════════════════════════════════════
from discord.ext import commands  # noqa: E402

GEAR_COMMAND = %%gear_command%%
ADMIN_COMMAND = %%admin_command%%
host.wallet = %%wallet%%
host.currency_name = %%currency_name%%
host.currency_emoji = %%currency_emoji%%


async def _svc_grant(guild, user, template_id, rarity="normal"):
    return await grant_gear(bs._id_of(guild), bs._id_of(user), str(template_id).strip().lower(), str(rarity).strip().lower() or "normal")


async def _svc_stats(guild, user):
    return await get_equipped_stats(bs._id_of(guild), bs._id_of(user))


class GearCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._names: list[str] = []

    async def cog_load(self):
        await init_gear_tables()
        group, admin = register_gear_commands(self.bot.tree, None, (GEAR_COMMAND, ADMIN_COMMAND))
        self._names = [group.name, admin.name]
        bs.register_service("gear.grant", _svc_grant)
        bs.register_service("gear.stats", _svc_stats)

    async def cog_unload(self):
        for n in self._names:
            self.bot.tree.remove_command(n)
        bs.unregister_service("gear.grant")
        bs.unregister_service("gear.stats")
        await host.close()


async def setup(bot: commands.Bot):
    await bot.add_cog(GearCog(bot))

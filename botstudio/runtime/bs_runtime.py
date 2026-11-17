"""Bot Forge Studio runtime — helpers used by generated cogs, plus the live-update control cog.

This file is copied next to bot.py in every build. Generated cogs call it as `bs`.
"""
from __future__ import annotations

import asyncio
import datetime as _dt
import hashlib
import json
import logging
import logging.handlers
import os
import re
import shutil
import sqlite3
import sys
import threading
import time
from pathlib import Path
from typing import Any, Optional

import discord
from discord.ext import commands, tasks

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("DATA_DIR", BASE_DIR / "data"))
log = logging.getLogger("bot")


# ---------------------------------------------------------------- logging

def setup_logging(level: str = "INFO") -> None:
    (BASE_DIR / "logs").mkdir(exist_ok=True)
    fmt = logging.Formatter("%(asctime)s | %(levelname)-8s | %(name)s | %(message)s", "%Y-%m-%d %H:%M:%S")
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    for h in list(root.handlers):
        root.removeHandler(h)
    out = logging.StreamHandler(sys.stdout)
    out.setFormatter(fmt)
    root.addHandler(out)
    fh = logging.handlers.RotatingFileHandler(BASE_DIR / "logs" / "bot.log", maxBytes=2_000_000,
                                              backupCount=3, encoding="utf-8")
    fh.setFormatter(fmt)
    root.addHandler(fh)


# ---------------------------------------------------------------- messages

def _color(value: Any) -> discord.Colour:
    try:
        return discord.Colour(int(str(value).lstrip("#"), 16))
    except (TypeError, ValueError):
        return discord.Colour.blurple()


def embed(title: str = "", description: str = "", color: str = "5865F2", footer: str = "") -> Optional[discord.Embed]:
    if not (title or description or footer):
        return None
    e = discord.Embed(title=title or None, description=description or None, colour=_color(color))
    if footer:
        e.set_footer(text=str(footer)[:2048])
    return e


async def respond(interaction: discord.Interaction, content: Optional[str] = None,
                  embed: Optional[discord.Embed] = None, ephemeral: bool = False) -> None:
    kwargs: dict = {"ephemeral": ephemeral}
    if content:
        kwargs["content"] = str(content)[:2000]
    if embed is not None:
        kwargs["embed"] = embed
    if len(kwargs) == 1:
        kwargs["content"] = "\u200b"
    if interaction.response.is_done():
        await interaction.followup.send(**kwargs)
    else:
        await interaction.response.send_message(**kwargs)


async def send(bot: commands.Bot, target: Any, content: Optional[str] = None,
               embed: Optional[discord.Embed] = None) -> Optional[discord.Message]:
    """Send to a channel object or a channel ID. Returns the message, or None on failure."""
    ch = target
    if isinstance(target, (int, str)):
        cid = int(target)
        ch = bot.get_channel(cid)
        if ch is None:
            try:
                ch = await bot.fetch_channel(cid)
            except discord.HTTPException:
                log.warning("send: cannot access channel %s", cid)
                return None
    if ch is None:
        return None
    try:
        return await ch.send(content=str(content)[:2000] if content else None, embed=embed)
    except discord.HTTPException as e:
        log.warning("send failed in %s: %s", getattr(ch, "id", "?"), e)
        return None


async def dm(user: Any, content: Optional[str] = None, embed: Optional[discord.Embed] = None) -> bool:
    try:
        await user.send(content=str(content)[:2000] if content else None, embed=embed)
        return True
    except (discord.Forbidden, discord.HTTPException, AttributeError):
        return False


def mention(user: Any) -> str:
    return getattr(user, "mention", f"<@{user}>")


# ---------------------------------------------------------------- members & moderation

async def get_member(guild: Optional[discord.Guild], who: Any) -> Optional[discord.Member]:
    if isinstance(who, discord.Member):
        return who
    if guild is None:
        guild = getattr(who, "guild", None)
    if guild is None:
        return None
    uid = getattr(who, "id", who)
    try:
        uid = int(uid)
    except (TypeError, ValueError):
        return None
    m = guild.get_member(uid)
    if m is None:
        try:
            m = await guild.fetch_member(uid)
        except discord.HTTPException:
            return None
    return m


def _find_role(guild: discord.Guild, role: Any) -> Optional[discord.Role]:
    r = str(role).strip()
    if r.isdigit():
        return guild.get_role(int(r))
    return discord.utils.find(lambda x: x.name.lower() == r.lower(), guild.roles)


async def set_role(guild: Optional[discord.Guild], who: Any, role: Any, add: bool = True) -> bool:
    member = await get_member(guild, who)
    if member is None:
        return False
    r = _find_role(member.guild, role)
    if r is None:
        log.warning("set_role: role %r not found in %s", role, member.guild.id)
        return False
    try:
        if add:
            await member.add_roles(r, reason="Bot Forge Studio")
        else:
            await member.remove_roles(r, reason="Bot Forge Studio")
        return True
    except discord.HTTPException as e:
        log.warning("set_role failed: %s", e)
        return False


async def timeout(who: Any, minutes: float, reason: str = "") -> bool:
    member = await get_member(None, who)
    if member is None:
        return False
    try:
        await member.timeout(_dt.timedelta(minutes=float(minutes)), reason=reason or None)
        return True
    except (discord.HTTPException, ValueError) as e:
        log.warning("timeout failed: %s", e)
        return False


async def kick(who: Any, reason: str = "") -> bool:
    member = await get_member(None, who)
    try:
        await member.kick(reason=reason or None)
        return True
    except (discord.HTTPException, AttributeError) as e:
        log.warning("kick failed: %s", e)
        return False


async def ban(who: Any, reason: str = "") -> bool:
    member = await get_member(None, who)
    try:
        await member.ban(reason=reason or None)
        return True
    except (discord.HTTPException, AttributeError) as e:
        log.warning("ban failed: %s", e)
        return False


async def delete_message(msg: Any) -> bool:
    try:
        await msg.delete()
        return True
    except (discord.HTTPException, AttributeError):
        return False


def has_perm(user: Any, perm: str) -> bool:
    perms = getattr(user, "guild_permissions", None)
    return bool(perms and (getattr(perms, perm, False) or perms.administrator))


def has_role(user: Any, role: Any) -> bool:
    r = str(role).strip().lower()
    for x in getattr(user, "roles", []):
        if str(x.id) == r or x.name.lower() == r:
            return True
    return False


# ---------------------------------------------------------------- application emojis

_EMOJI_NAME = re.compile(r"^[A-Za-z0-9_]{2,32}$")
_EMOJI_TYPES = ("image/png", "image/jpeg", "image/gif", "image/webp")


async def app_emoji_add(bot: commands.Bot, name: Any, image: Any) -> tuple[str, str]:
    """Upload an attachment as an application emoji. Returns (emoji_markup, error)."""
    name = re.sub(r"[^A-Za-z0-9_]", "_", str(name or "").strip())
    if not _EMOJI_NAME.match(name):
        return "", "The name must be 2-32 letters, numbers or underscores."
    if image is None or not hasattr(image, "read"):
        return "", "No image was provided."
    ctype = (getattr(image, "content_type", None) or "").lower()
    if ctype and ctype not in _EMOJI_TYPES:
        return "", "The image must be PNG, JPG, GIF or WEBP."
    if getattr(image, "size", 0) > 256 * 1024:
        return "", "The image must be 256 KB or smaller."
    try:
        emoji = await bot.create_application_emoji(name=name, image=await image.read())
    except discord.HTTPException as e:
        return "", f"Discord refused it: {getattr(e, 'text', '') or e}"
    return str(emoji), ""


async def app_emoji_remove(bot: commands.Bot, name: Any) -> tuple[bool, str]:
    try:
        for e in await bot.fetch_application_emojis():
            if e.name.lower() == str(name).strip().lower():
                await e.delete()
                return True, ""
    except discord.HTTPException as ex:
        return False, f"Discord refused it: {getattr(ex, 'text', '') or ex}"
    return False, f"No application emoji named {name!r}."


async def app_emoji_list(bot: commands.Bot) -> tuple[str, int]:
    try:
        emojis = await bot.fetch_application_emojis()
    except discord.HTTPException:
        return "", 0
    return "\n".join(f"{e} `:{e.name}:`" for e in emojis), len(emojis)


# ---------------------------------------------------------------- HTTP

async def http_get(url: str, timeout_s: float = 15.0) -> tuple[int, str]:
    import aiohttp
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout_s)) as s:
            async with s.get(url, headers={"User-Agent": "BotStudio/0.1"}) as r:
                return r.status, (await r.text())[:200_000]
    except Exception as e:  # network errors are data, not crashes
        log.warning("http_get %s failed: %s", url, e)
        return 0, ""


def try_json(text: str) -> Any:
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------- key/value store (SQLite)

_db: Optional[sqlite3.Connection] = None
_db_lock = threading.Lock()


def _conn() -> sqlite3.Connection:
    global _db
    if _db is None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        _db = sqlite3.connect(DATA_DIR / "bot.db", check_same_thread=False)
        _db.execute("CREATE TABLE IF NOT EXISTS kv (ns TEXT, k TEXT, v TEXT, PRIMARY KEY (ns, k))")
        _db.commit()
    return _db


def _kv_get(ns: str, key: str, default: Any) -> Any:
    with _db_lock:
        row = _conn().execute("SELECT v FROM kv WHERE ns=? AND k=?", (ns, key)).fetchone()
    return default if row is None else json.loads(row[0])


def _kv_set(ns: str, key: str, value: Any) -> None:
    with _db_lock:
        c = _conn()
        c.execute("INSERT INTO kv (ns,k,v) VALUES (?,?,?) ON CONFLICT(ns,k) DO UPDATE SET v=excluded.v",
                  (ns, key, json.dumps(value, default=str)))
        c.commit()


def _kv_incr(ns: str, key: str, amount: float) -> Any:
    with _db_lock:
        c = _conn()
        row = c.execute("SELECT v FROM kv WHERE ns=? AND k=?", (ns, key)).fetchone()
        cur = json.loads(row[0]) if row else 0
        try:
            new = cur + amount
        except TypeError:
            new = amount
        if isinstance(new, float) and new.is_integer():
            new = int(new)
        c.execute("INSERT INTO kv (ns,k,v) VALUES (?,?,?) ON CONFLICT(ns,k) DO UPDATE SET v=excluded.v",
                  (ns, key, json.dumps(new)))
        c.commit()
    return new


async def kv_get(ns: str, key: Any, default: Any = None) -> Any:
    return await asyncio.to_thread(_kv_get, str(ns), str(key), default)


async def kv_set(ns: str, key: Any, value: Any) -> None:
    await asyncio.to_thread(_kv_set, str(ns), str(key), value)


async def kv_incr(ns: str, key: Any, amount: float = 1) -> Any:
    return await asyncio.to_thread(_kv_incr, str(ns), str(key), amount)


async def kv_top(ns: str, limit: int = 10) -> list[tuple[str, Any]]:
    def run():
        with _db_lock:
            rows = _conn().execute("SELECT k, v FROM kv WHERE ns=?", (ns,)).fetchall()
        items = [(k, json.loads(v)) for k, v in rows]
        items = [(k, v) for k, v in items if isinstance(v, (int, float))]
        return sorted(items, key=lambda kv: kv[1], reverse=True)[:limit]
    return await asyncio.to_thread(run)


# ---------------------------------------------------------------- command sync

async def sync_tree(bot: commands.Bot) -> int:
    gid = os.getenv("GUILD_ID", "").strip()
    if gid.isdigit():
        guild = discord.Object(id=int(gid))
        bot.tree.clear_commands(guild=guild)
        bot.tree.copy_global_to(guild=guild)
        synced = await bot.tree.sync(guild=guild)
    else:
        synced = await bot.tree.sync()
    log.info("Synced %d application command(s)", len(synced))
    return len(synced)


# ---------------------------------------------------------------- live update + status

def _file_hash(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()[:16]


class DashboardRunner:
    """Runs dashboard/server.js (the discord.js web dashboard) as a child process of the bot.

    discord.py stays the bot; the dashboard is a separate Node process that shares data/bot.db and
    status.json with it and talks to Discord over REST. The bot starts it, restarts it if it crashes
    (at most 5 starts per minute) or when Bot Forge Studio ships a new server.js, and stops it on shutdown.
    """

    def __init__(self, base: Path = BASE_DIR):
        self.base = Path(base)
        self.dir = self.base / "dashboard"
        self.proc: Optional[asyncio.subprocess.Process] = None
        self.sig: Optional[str] = None
        self.starts: list[float] = []
        self._warned: set[str] = set()
        self._log = None

    def _warn_once(self, key: str, msg: str) -> None:
        if key not in self._warned:
            self._warned.add(key)
            log.warning(msg)

    def node_path(self) -> Optional[str]:
        for name in ("node_path", "node_path.txt"):
            f = self.dir / ".runtime" / name
            if f.is_file():
                p = f.read_text(encoding="utf-8", errors="replace").strip()
                if p and Path(p).exists():
                    return p
        return shutil.which("node")

    @property
    def running(self) -> bool:
        return self.proc is not None and self.proc.returncode is None

    async def ensure(self, want: bool, sig: Optional[str]) -> None:
        if not want:
            await self.stop()
            return
        if self.running and sig == self.sig:
            return
        if self.running:
            log.info("Dashboard server files changed - restarting it")
            await self.stop()
        now = time.time()
        self.starts = [t for t in self.starts if now - t < 60]
        if len(self.starts) >= 5:
            self._warn_once("loop", "Dashboard keeps crashing - not restarting it again until the bot restarts. See logs/dashboard.log")
            return
        server = self.dir / "server.js"
        if not server.is_file():
            return
        node = self.node_path()
        if not node:
            self._warn_once("node", "Dashboard needs Node.js: run dashboard/deps.sh (Deploy does this automatically).")
            return
        if not (self.dir / "node_modules").is_dir():
            self._warn_once("deps", "Dashboard dependencies are not installed yet: run dashboard/deps.sh (Deploy does this automatically).")
            return
        (self.base / "logs").mkdir(parents=True, exist_ok=True)
        if self._log is None or self._log.closed:
            self._log = open(self.base / "logs" / "dashboard.log", "ab")
        try:
            self.proc = await asyncio.create_subprocess_exec(
                node, "--disable-warning=ExperimentalWarning", "server.js", cwd=str(self.dir),
                stdout=self._log, stderr=asyncio.subprocess.STDOUT,
                env={**os.environ, "BS_BASE_DIR": str(self.base)})
        except OSError as e:
            self._warn_once("spawn", f"Could not start the dashboard: {e}")
            return
        self.sig = sig
        self.starts.append(now)
        log.info("Dashboard started (pid %s) - see logs/dashboard.log", self.proc.pid)

    async def stop(self) -> None:
        p, self.proc = self.proc, None
        if p is not None and p.returncode is None:
            try:
                p.terminate()
                await asyncio.wait_for(p.wait(), 5)
            except asyncio.TimeoutError:
                p.kill()
                await p.wait()
            except ProcessLookupError:
                pass
        if self._log is not None and not self._log.closed:
            self._log.close()


class StudioControl(commands.Cog):
    """Watches manifest.json (written by Bot Forge Studio) and hot-reloads changed cogs.

    Also publishes status.json so Bot Forge Studio can show health without a restart.
    """

    def __init__(self, bot: commands.Bot, hot_reload: bool = True):
        self.bot = bot
        self.hot = hot_reload
        self.manifest_path = BASE_DIR / "manifest.json"
        self.status_path = BASE_DIR / "status.json"
        self.loaded: dict[str, str] = {}
        self.errors: list[str] = []
        self.boot_restart_sig: Optional[str] = None
        self.commands_sig: Optional[str] = None
        self.last_reload: Optional[float] = None
        self.started = time.time()
        self.restart_required = False
        self.dash = DashboardRunner()
        self.boot_dash: tuple[bool, Optional[str]] = (False, None)
        self.watch.start()
        self.dash_events.start()

    async def cog_unload(self):
        self.watch.cancel()
        self.dash_events.cancel()
        await self.dash.stop()

    # -- web dashboard -> bot: events the dashboard's Web Routes queued in the shared database -------------
    @tasks.loop(seconds=2)
    async def dash_events(self):
        if not (BASE_DIR / "dashboard" / "server.js").exists():
            return
        for key, val in await kv_top_any("dash_events", 50):
            await kv_set("dash_events", key, None)          # consume first: an event must never run twice
            if isinstance(val, dict) and val.get("name"):
                self.bot.dispatch("bs_dashboard_event", str(val["name"]), val.get("data") or {}, val.get("guild") or "", val.get("user") or "")

    @dash_events.before_loop
    async def _dash_events_wait(self):
        await self.bot.wait_until_ready()

    async def stop_dashboard(self) -> None:
        await self.dash.stop()

    def _read_manifest(self) -> Optional[dict]:
        try:
            return json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    async def initial_load(self) -> None:
        m = self._read_manifest() or {"cogs": {}}
        self.boot_restart_sig = m.get("restart_sig")
        self.commands_sig = m.get("commands_sig")
        self.boot_dash = (bool(m.get("dashboard")), m.get("dashboard_sig"))
        for path, digest in m.get("cogs", {}).items():
            await self._load(path, digest, reload=False)

    @staticmethod
    def _ext(path: str) -> str:
        return path[:-3].replace("/", ".")

    async def _load(self, path: str, digest: str, reload: bool) -> bool:
        ext = self._ext(path)
        try:
            if reload and ext in self.bot.extensions:
                await self.bot.reload_extension(ext)
            else:
                await self.bot.load_extension(ext)
            self.loaded[path] = digest
            self.errors = [e for e in self.errors if not e.startswith(path)]
            log.info("%s %s", "Reloaded" if reload else "Loaded", ext)
            return True
        except Exception as e:
            msg = f"{path}: {type(e).__name__}: {e}"
            self.errors = [x for x in self.errors if not x.startswith(path)] + [msg]
            log.error("Failed to load %s: %s", ext, e)
            return False

    @tasks.loop(seconds=2.0)
    async def watch(self):
        m = self._read_manifest() if self.hot else None
        changed = False
        if m is not None:
            cogs = m.get("cogs", {})
            for path, digest in cogs.items():
                if self.loaded.get(path) != digest:
                    if await self._load(path, digest, reload=path in self.loaded):
                        changed = True
            for path in list(self.loaded):
                if path not in cogs:
                    try:
                        await self.bot.unload_extension(self._ext(path))
                    except Exception as e:
                        log.warning("unload %s: %s", path, e)
                    self.loaded.pop(path, None)
                    changed = True
            self.restart_required = m.get("restart_sig") != self.boot_restart_sig
        # the web dashboard: always follow the manifest when hot reload is on, else only what we booted with
        try:
            want, sig = (bool(m.get("dashboard")), m.get("dashboard_sig")) if m is not None else self.boot_dash
            await self.dash.ensure(want, sig)
        except Exception as e:
            log.error("Dashboard supervisor: %s", e)
        if m is not None:
            if changed:
                self.last_reload = time.time()
                if m.get("commands_sig") != self.commands_sig:
                    try:
                        await sync_tree(self.bot)
                        self.commands_sig = m.get("commands_sig")
                    except Exception as e:
                        log.error("Command sync failed: %s", e)
        self._write_status()

    @watch.before_loop
    async def _before(self):
        await self.bot.wait_until_ready()

    def _write_status(self) -> None:
        data = {
            "pid": os.getpid(),
            "user": str(self.bot.user) if self.bot.user else None,
            "started": self.started,
            "now": time.time(),
            "latency_ms": round(self.bot.latency * 1000) if self.bot.latency == self.bot.latency else None,
            "guilds": len(self.bot.guilds),
            "cogs": sorted(self.loaded),
            "errors": self.errors,
            "restart_required": self.restart_required,
            "last_reload": self.last_reload,
            "dashboard": self.dash.running,
        }
        tmp = self.status_path.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps(data), encoding="utf-8")
            os.replace(tmp, self.status_path)
        except OSError:
            pass


async def setup_control(bot: commands.Bot, hot_reload: bool = True) -> StudioControl:
    ctl = StudioControl(bot, hot_reload)
    await bot.add_cog(ctl)
    await ctl.initial_load()
    return ctl


def json_get(obj, path, default=""):
    """Read a dotted path (list indexes are numbers) out of parsed JSON — used by the Get JSON Field node."""
    cur = obj
    for seg in str(path or "").split("."):
        if not seg:
            continue
        if isinstance(cur, dict):
            cur = cur.get(seg)
        elif isinstance(cur, list) and seg.lstrip("-").isdigit() and -len(cur) <= int(seg) < len(cur):
            cur = cur[int(seg)]
        else:
            return default
        if cur is None:
            return default
    return default if cur is None else cur


# ======================================================================================
# Interactive UI helpers (buttons, dropdowns, forms, paged / tabbed menus, wizards)
# Used by the Interact nodes.  The "ask_*" helpers block the flow until the user answers and
# hand back the *fresh* interaction, so the nodes after them keep replying correctly.
# ======================================================================================

_BTN_STYLE = {"primary": discord.ButtonStyle.primary, "blurple": discord.ButtonStyle.primary,
              "secondary": discord.ButtonStyle.secondary, "grey": discord.ButtonStyle.secondary,
              "gray": discord.ButtonStyle.secondary, "success": discord.ButtonStyle.success,
              "green": discord.ButtonStyle.success, "danger": discord.ButtonStyle.danger,
              "red": discord.ButtonStyle.danger}


def _emoji(v: Any):
    v = (str(v).strip() if v else "")
    return v or None


async def _post(interaction: discord.Interaction, content, embed_, view, ephemeral: bool):
    """Send (or follow up) with a view and return the message so it can be edited later."""
    kwargs: dict = {"view": view, "ephemeral": ephemeral}
    if content:
        kwargs["content"] = str(content)[:2000]
    if embed_ is not None:
        kwargs["embed"] = embed_
    if len(kwargs) == 2:
        kwargs["content"] = "\u200b"
    try:
        if interaction.response.is_done():
            return await interaction.followup.send(wait=True, **kwargs)
        await interaction.response.send_message(**kwargs)
        return await interaction.original_response()
    except discord.HTTPException as e:
        log.warning("UI post failed: %s", e)
        return None


async def _strip(msg) -> None:
    if msg is not None:
        try:
            await msg.edit(view=None)
        except discord.HTTPException:
            pass


class _AskView(discord.ui.View):
    def __init__(self, owner_id: Optional[int], timeout: float):
        super().__init__(timeout=timeout)
        self.owner_id, self.result, self.inter = owner_id, None, None

    async def interaction_check(self, i: discord.Interaction) -> bool:
        if self.owner_id is not None and i.user.id != self.owner_id:
            await i.response.send_message("This isn't your menu.", ephemeral=True)
            return False
        return True

    def done(self, i: discord.Interaction, result: Any) -> None:
        self.result, self.inter = result, i
        self.stop()


def parse_rows(text: Any) -> list[list[str]]:
    """'a | b | c' per line -> [['a','b','c'], ...] (blank lines dropped)."""
    return [[c.strip() for c in ln.split("|")] for ln in str(text or "").splitlines() if ln.strip()]


async def ask_buttons(interaction, content, embed_, buttons, *, only_user: bool = True, timeout: float = 60,
                      ephemeral: bool = False):
    """buttons: [(label, id, style, emoji)].  -> (id, label, interaction) or (None, None, None) on timeout."""
    view = _AskView(interaction.user.id if only_user else None, float(timeout))
    for label, cid, style, emoji in buttons[:25]:
        b = discord.ui.Button(label=label[:80] or None, style=_BTN_STYLE.get(str(style).lower(), discord.ButtonStyle.secondary),
                              emoji=_emoji(emoji), custom_id=None)

        async def _cb(i, _cid=cid, _label=label):
            view.done(i, (_cid, _label))
        b.callback = _cb
        view.add_item(b)
    msg = await _post(interaction, content, embed_, view, ephemeral)
    await view.wait()
    if view.inter is None:
        await _strip(msg)
        return None, None, None
    try:
        await view.inter.response.edit_message(view=None)
    except discord.HTTPException:
        pass
    return view.result[0], view.result[1], view.inter


async def ask_menu(interaction, content, embed_, options, *, placeholder: str = "Choose…", min_values: int = 1,
                   max_values: int = 1, only_user: bool = True, timeout: float = 60, ephemeral: bool = False):
    """options: [(label, value, description, emoji)].  -> (values list, interaction) or (None, None)."""
    view = _AskView(interaction.user.id if only_user else None, float(timeout))
    opts = [discord.SelectOption(label=l[:100], value=(v or l)[:100], description=(d or None) and d[:100], emoji=_emoji(e))
            for l, v, d, e in options[:25]]
    sel = discord.ui.Select(placeholder=placeholder[:150], options=opts, min_values=max(1, min(int(min_values), len(opts))),
                            max_values=max(1, min(int(max_values), len(opts))))

    async def _cb(i):
        view.done(i, list(sel.values))
    sel.callback = _cb
    view.add_item(sel)
    msg = await _post(interaction, content, embed_, view, ephemeral)
    await view.wait()
    if view.inter is None:
        await _strip(msg)
        return None, None
    try:
        await view.inter.response.edit_message(view=None)
    except discord.HTTPException:
        pass
    return view.result, view.inter


class _FormModal(discord.ui.Modal):
    def __init__(self, title: str, fields: list[list[str]]):
        super().__init__(title=title[:45] or "Form", timeout=600)
        self.keys, self.inter = [], None
        for row in fields[:5]:
            row = (row + [""] * 7)[:7]
            label, key, style, req, ph, default, mx = row
            ti = discord.ui.TextInput(label=label[:45] or key or "Field",
                                      style=discord.TextStyle.paragraph if style.lower().startswith(("l", "p")) else discord.TextStyle.short,
                                      required=req.lower() not in ("no", "false", "0", "optional"),
                                      placeholder=ph[:100] or None, default=default[:4000] or None,
                                      max_length=int(mx) if mx.isdigit() and 1 <= int(mx) <= 4000 else None)
            self.add_item(ti)
            self.keys.append(key or label)

    async def on_submit(self, i: discord.Interaction) -> None:
        self.inter = i
        self.stop()


async def ask_form(interaction, title, fields, *, button_label: str = "Open form"):
    """Show a modal. fields rows: label | id | short/long | required | placeholder | default | max.
    -> (answers dict, interaction) or (None, None) if the user closed it."""
    modal = _FormModal(str(title), fields)
    if interaction.response.is_done():                  # a modal can only answer a fresh interaction
        _id, _l, interaction = await ask_buttons(interaction, "Press the button to fill in the form.", None,
                                                 [(button_label, "open", "primary", "")], timeout=120, ephemeral=True)
        if interaction is None:
            return None, None
    await interaction.response.send_modal(modal)
    await modal.wait()
    if modal.inter is None:
        return None, None
    return {k: ti.value for k, ti in zip(modal.keys, [c for c in modal.children if isinstance(c, discord.ui.TextInput)])}, modal.inter


async def wizard(interaction, steps, *, button_label: str = "Continue ▶"):
    """Multi-step form. steps: [(title, [field rows])]. -> (answers, interaction) or (None, None)."""
    answers: dict = {}
    cur = interaction
    for n, (title, fields) in enumerate(steps, 1):
        if n > 1:
            _id, _l, cur2 = await ask_buttons(cur, f"Step {n} of {len(steps)}: **{title}**", None,
                                              [(button_label, "next", "primary", "")], timeout=300, ephemeral=True)
            if cur2 is None:
                return None, None
            cur = cur2
        got, cur3 = await ask_form(cur, f"{title} ({n}/{len(steps)})", fields)
        if got is None:
            return None, None
        answers.update(got)
        cur = cur3
    return answers, cur


class _PageView(discord.ui.View):
    def __init__(self, pages, title, color, owner_id, timeout):
        super().__init__(timeout=timeout)
        self.pages, self.title, self.color, self.owner_id, self.i, self.msg = pages, title, color, owner_id, 0, None
        self._sync()

    def _embed(self):
        e = discord.Embed(title=self.title or None, description=self.pages[self.i][:4000], colour=_color(self.color))
        e.set_footer(text=f"Page {self.i + 1}/{len(self.pages)}")
        return e

    def _sync(self):
        self.first.disabled = self.prev.disabled = self.i == 0
        self.next.disabled = self.last.disabled = self.i >= len(self.pages) - 1

    async def interaction_check(self, i):
        if self.owner_id is not None and i.user.id != self.owner_id:
            await i.response.send_message("This isn't your menu.", ephemeral=True)
            return False
        return True

    async def _go(self, i, to):
        self.i = max(0, min(len(self.pages) - 1, to))
        self._sync()
        await i.response.edit_message(embed=self._embed(), view=self)

    @discord.ui.button(label="⏮", style=discord.ButtonStyle.secondary)
    async def first(self, i, b): await self._go(i, 0)

    @discord.ui.button(label="◀", style=discord.ButtonStyle.primary)
    async def prev(self, i, b): await self._go(i, self.i - 1)

    @discord.ui.button(label="▶", style=discord.ButtonStyle.primary)
    async def next(self, i, b): await self._go(i, self.i + 1)

    @discord.ui.button(label="⏭", style=discord.ButtonStyle.secondary)
    async def last(self, i, b): await self._go(i, len(self.pages) - 1)

    async def on_timeout(self):
        await _strip(self.msg)


def split_pages(text: Any) -> list[str]:
    parts = [p.strip() for p in re.split(r"(?m)^\s*---+\s*$", str(text or ""))]
    return [p for p in parts if p] or ["(empty)"]


async def paginate(interaction, pages, title="", color="5865F2", *, only_user: bool = False, timeout: float = 180,
                   ephemeral: bool = False):
    """Paged embed with first/prev/next/last buttons. Returns immediately; the buttons keep working."""
    view = _PageView(list(pages), str(title), color, interaction.user.id if only_user else None, float(timeout))
    view.msg = await _post(interaction, None, view._embed(), view, ephemeral)
    return view.msg


class _TabView(discord.ui.View):
    def __init__(self, tabs, color, owner_id, timeout):
        super().__init__(timeout=timeout)
        self.tabs, self.color, self.owner_id, self.msg = tabs, color, owner_id, None
        opts = [discord.SelectOption(label=t[0][:100], value=str(n), description=(t[3][:100] or None) if len(t) > 3 else None,
                                     emoji=_emoji(t[4]) if len(t) > 4 else None, default=n == 0) for n, t in enumerate(tabs[:25])]
        self.sel = discord.ui.Select(placeholder="Choose a section…", options=opts)
        self.sel.callback = self._pick
        self.add_item(self.sel)

    def embed_for(self, n: int):
        t = self.tabs[n]
        return discord.Embed(title=t[0] or None, description=(t[1] if len(t) > 1 else "")[:4000], colour=_color(self.color))

    async def interaction_check(self, i):
        if self.owner_id is not None and i.user.id != self.owner_id:
            await i.response.send_message("This isn't your menu.", ephemeral=True)
            return False
        return True

    async def _pick(self, i):
        n = int(self.sel.values[0])
        for o in self.sel.options:
            o.default = o.value == str(n)
        await i.response.edit_message(embed=self.embed_for(n), view=self)

    async def on_timeout(self):
        await _strip(self.msg)


async def tabbed_menu(interaction, tabs, color="5865F2", *, only_user: bool = False, timeout: float = 300,
                      ephemeral: bool = False):
    """Dropdown that switches between embeds. tabs rows: title | text | (unused) | description | emoji."""
    tabs = [t for t in tabs if t and t[0]]
    if not tabs:
        return None
    view = _TabView(tabs, color, interaction.user.id if only_user else None, float(timeout))
    view.msg = await _post(interaction, None, view.embed_for(0), view, ephemeral)
    return view.msg


async def post_panel(bot, target, content, embed_, buttons=(), menu=None):
    """Persistent panel: components carry fixed custom_ids that 'On Component Used' triggers listen for."""
    view = discord.ui.View(timeout=None)
    for label, cid, style, emoji in list(buttons)[:25]:
        view.add_item(discord.ui.Button(label=label[:80] or None, custom_id=cid[:100] or None,
                                        style=_BTN_STYLE.get(str(style).lower(), discord.ButtonStyle.secondary), emoji=_emoji(emoji)))
    if menu:
        cid, placeholder, options, mn, mx = menu
        opts = [discord.SelectOption(label=l[:100], value=(v or l)[:100], description=(d or None) and d[:100], emoji=_emoji(e))
                for l, v, d, e in options[:25]]
        if opts:
            view.add_item(discord.ui.Select(custom_id=cid[:100], placeholder=placeholder[:150], options=opts,
                                            min_values=max(1, min(int(mn), len(opts))), max_values=max(1, min(int(mx), len(opts)))))
    ch = target
    if isinstance(target, (int, str)):
        ch = bot.get_channel(int(target)) or await bot.fetch_channel(int(target))
    try:
        return await ch.send(content=str(content)[:2000] if content else None, embed=embed_, view=view)
    except discord.HTTPException as e:
        log.warning("post_panel failed: %s", e)
        return None


# ======================================================================================
# Economy helpers (wallets, items, cooldowns, boards) — all on the shared kv store, so the
# web dashboard can show them too.  A wallet is one kv table ("account"); keys are "guild:user".
# ======================================================================================

def _id_of(x: Any) -> str:
    return str(getattr(x, "id", x) if x is not None else 0)


def eco_key(guild: Any, user: Any) -> str:
    return f"{_id_of(guild)}:{_id_of(user)}"


def _kv_spend(ns: str, key: str, amount: float, floor: bool) -> tuple[bool, Any]:
    with _db_lock:
        c = _conn()
        row = c.execute("SELECT v FROM kv WHERE ns=? AND k=?", (ns, key)).fetchone()
        cur = json.loads(row[0]) if row else 0
        cur = cur if isinstance(cur, (int, float)) else 0
        if cur < amount and not floor:
            return False, cur
        new = max(0, cur - amount) if floor else cur - amount
        if isinstance(new, float) and new.is_integer():
            new = int(new)
        c.execute("INSERT INTO kv (ns,k,v) VALUES (?,?,?) ON CONFLICT(ns,k) DO UPDATE SET v=excluded.v", (ns, key, json.dumps(new)))
        c.commit()
    return True, new


async def eco_balance(account: str, guild: Any, user: Any) -> Any:
    v = await kv_get(account, eco_key(guild, user), 0)
    return v if isinstance(v, (int, float)) else 0


async def eco_add(account: str, guild: Any, user: Any, amount: float) -> Any:
    return await kv_incr(account, eco_key(guild, user), amount)


async def eco_spend(account: str, guild: Any, user: Any, amount: float, floor: bool = False) -> tuple[bool, Any]:
    return await asyncio.to_thread(_kv_spend, str(account), eco_key(guild, user), float(amount), floor)


async def eco_transfer(account: str, guild: Any, src: Any, dst: Any, amount: float) -> tuple[bool, Any]:
    if amount <= 0 or _id_of(src) == _id_of(dst):
        return False, await eco_balance(account, guild, src)
    ok, bal = await eco_spend(account, guild, src, amount)
    if ok:
        await eco_add(account, guild, dst, amount)
    return ok, bal


def human_duration(seconds: float) -> str:
    s = int(max(0, seconds))
    d, s = divmod(s, 86400)
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    parts = [f"{d}d" if d else "", f"{h}h" if h else "", f"{m}m" if m else "", f"{s}s" if s and not (d or h) else ""]
    return " ".join(p for p in parts if p) or "0s"


def _claim(ns: str, key: str, seconds: float) -> tuple[bool, float]:
    now = time.time()
    with _db_lock:
        c = _conn()
        row = c.execute("SELECT v FROM kv WHERE ns=? AND k=?", (ns, key)).fetchone()
        last = json.loads(row[0]) if row else 0
        if now - last < seconds:
            return False, seconds - (now - last)
        c.execute("INSERT INTO kv (ns,k,v) VALUES (?,?,?) ON CONFLICT(ns,k) DO UPDATE SET v=excluded.v", (ns, key, json.dumps(now)))
        c.commit()
    return True, 0.0


async def cooldown_claim(name: str, guild: Any, user: Any, seconds: float) -> tuple[bool, float]:
    """True once per *seconds* per member.  -> (ready, seconds_remaining)"""
    return await asyncio.to_thread(_claim, f"cooldown:{name}", eco_key(guild, user), float(seconds))


async def eco_board(account: str, guild: Any, limit: int = 10, symbol: str = "") -> str:
    prefix = f"{_id_of(guild)}:"

    def run():
        with _db_lock:
            rows = _conn().execute("SELECT k, v FROM kv WHERE ns=? AND substr(k,1,?)=?", (account, len(prefix), prefix)).fetchall()
        items = [(k[len(prefix):], json.loads(v)) for k, v in rows]
        return sorted([(k, v) for k, v in items if isinstance(v, (int, float))], key=lambda kv: kv[1], reverse=True)[:limit]
    top = await asyncio.to_thread(run)
    if not top:
        return "Nobody here yet."
    medals = {1: "🥇", 2: "🥈", 3: "🥉"}
    return "\n".join(f"{medals.get(n, f'`{n}.`')} <@{uid}> — {v:,}{(' ' + symbol) if symbol else ''}" for n, (uid, v) in enumerate(top, 1))


async def item_add(account: str, guild: Any, user: Any, item: str, qty: int = 1) -> int:
    return int(await kv_incr(f"items:{account}", f"{eco_key(guild, user)}:{item}", qty))


async def item_take(account: str, guild: Any, user: Any, item: str, qty: int = 1) -> tuple[bool, int]:
    ok, left = await asyncio.to_thread(_kv_spend, f"items:{account}", f"{eco_key(guild, user)}:{item}", float(qty), False)
    return ok, int(left)


async def item_count(account: str, guild: Any, user: Any, item: str) -> int:
    return int(await kv_get(f"items:{account}", f"{eco_key(guild, user)}:{item}", 0) or 0)


async def inventory_text(account: str, guild: Any, user: Any) -> str:
    prefix = f"{eco_key(guild, user)}:"

    def run():
        with _db_lock:
            rows = _conn().execute("SELECT k, v FROM kv WHERE ns=? AND substr(k,1,?)=?", (f"items:{account}", len(prefix), prefix)).fetchall()
        return sorted((k[len(prefix):], json.loads(v)) for k, v in rows)
    items = [(k, v) for k, v in await asyncio.to_thread(run) if isinstance(v, (int, float)) and v > 0]
    return "\n".join(f"• **{k}** × {int(v)}" for k, v in items) or "Nothing yet."


# ======================================================================================
# Event bus — nodes and game modules report things that happened ("messages", "wins" …);
# Quests (and anything else that listens) count them.
# ======================================================================================
_event_listeners: list = []


def on_event(fn) -> None:
    if fn not in _event_listeners:
        _event_listeners.append(fn)


def off_event(fn) -> None:
    if fn in _event_listeners:
        _event_listeners.remove(fn)


async def track(guild: Any, user: Any, event: str, amount: float = 1) -> None:
    """Report an event for a member.  Listeners must be async fn(guild_id, user_id, event, amount)."""
    for fn in list(_event_listeners):
        try:
            await fn(_id_of(guild), _id_of(user), str(event), amount)
        except Exception:
            log.exception("event listener failed for %r", event)


# ======================================================================================
# Service registry — game modules publish functions ("gear.grant", "dungeon.start" …) that nodes call
# without importing the module; a node whose module is not installed warns instead of crashing.
# ======================================================================================
_services: dict = {}


def register_service(name: str, fn) -> None:
    _services[name] = fn


def unregister_service(name: str) -> None:
    _services.pop(name, None)


async def service(name: str, *args, **kwargs):
    fn = _services.get(name)
    if fn is None:
        log.warning("service %r is not available — add the matching module node to your project", name)
        return None
    return await fn(*args, **kwargs)


async def kv_top_any(ns: str, limit: int = 1000) -> list[tuple[str, Any]]:
    """Every (key, value) of a kv table, whatever the value type (used for team membership)."""
    def run():
        with _db_lock:
            rows = _conn().execute("SELECT k, v FROM kv WHERE ns=? ORDER BY k LIMIT ?", (str(ns), int(limit))).fetchall()
        return [(k, json.loads(v)) for k, v in rows]
    return await asyncio.to_thread(run)


def live_emit(guild: Any, event: str, data: Any = None) -> None:
    """Bot -> dashboard: append a live event; the dashboard server tails the file and pushes it over its WebSocket."""
    path = Path(os.getenv("DATA_DIR", BASE_DIR / "data")) / "live_events.jsonl"
    line = json.dumps({"event": str(event)[:64], "data": data if data is not None else {}, "guild": _id_of(guild) if guild else None,
                       "ts": time.time()}, default=str)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        if path.stat().st_size > 1_000_000:                  # keep the file small: retain the newest lines
            keep = path.read_text(encoding="utf-8").splitlines()[-200:]
            path.write_text("\n".join(keep) + "\n", encoding="utf-8")
    except OSError as e:
        log.warning("live_emit failed: %s", e)


def weighted_choice(pairs: list) -> str:
    """pairs: [(label, weight)] -> one label, chosen with probability proportional to its weight."""
    import random as _r
    labels = [p[0] for p in pairs]
    weights = [max(0.0, float(p[1])) for p in pairs]
    return _r.choices(labels, weights=weights, k=1)[0] if labels and sum(weights) > 0 else (labels[0] if labels else "")


# ======================================================================================
# Community tools: private channels (tickets), channel deletion, polls
# ======================================================================================

async def create_private_channel(guild, name: str, opener, staff_role: str = "", category_id: str = ""):
    """A text channel only `opener`, the staff role and the bot can see.  Returns the channel, or None (logged) when Discord refuses."""
    try:
        ov = {guild.default_role: discord.PermissionOverwrite(view_channel=False),
              guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True, read_message_history=True)}
        if opener is not None:
            ov[opener] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, attach_files=True)
        sr = str(staff_role or "").strip()
        role = (guild.get_role(int(sr)) if sr.isdigit() else discord.utils.get(guild.roles, name=sr)) if sr else None
        if role is not None:
            ov[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, manage_messages=True)
        cat = guild.get_channel(int(category_id)) if str(category_id or "").isdigit() else None
        slug = re.sub(r"[^a-z0-9-]+", "-", str(name).lower()).strip("-")[:90] or "ticket"
        return await guild.create_text_channel(slug, overwrites=ov, category=cat if isinstance(cat, discord.CategoryChannel) else None,
                                               reason="Created by Bot Forge Studio ticket flow")
    except (discord.HTTPException, ValueError, AttributeError) as e:
        log.warning("create_private_channel failed: %s", e)
        return None


async def delete_channel(channel) -> bool:
    try:
        await channel.delete(reason="Closed by Bot Forge Studio flow")
        return True
    except (discord.HTTPException, AttributeError) as e:
        log.warning("delete_channel failed: %s", e)
        return False


_NUM = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]


async def poll(interaction, question: str, options: str, ephemeral: bool = False):
    """Post a reaction poll (2-10 options separated by |) and seed the number reactions."""
    opts = [o.strip() for o in str(options or "").split("|") if o.strip()][:10]
    if len(opts) < 2:
        await respond(interaction, "A poll needs at least two options separated by |.", None, ephemeral=True)
        return None
    e = discord.Embed(title=f"📊 {str(question)[:250]}", colour=discord.Colour.blurple(),
                      description="\n".join(f"{_NUM[i]}  {o[:200]}" for i, o in enumerate(opts)))
    e.set_footer(text=f"Poll by {interaction.user.display_name} · react to vote")
    await respond(interaction, "", e, ephemeral=ephemeral)
    try:
        msg = await interaction.original_response()
        for i in range(len(opts)):
            await msg.add_reaction(_NUM[i])
        return msg
    except discord.HTTPException as ex:
        log.warning("poll reactions failed: %s", ex)
        return None

"""SQLite storage (aiosqlite). Every row is scoped by guild so one bot serves many servers.

Model
  lists      – named checklists (optionally archived, optionally with a live progress message)
  tasks      – static checklist items (name, description, completed flag)
  reminders  – timers that post a message in a channel and optionally ping people/roles.
               One-shot or recurring. Can stand alone, or be linked to a task / whole list.
  activity   – optional audit trail of completions and list changes.
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Optional

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS guild_settings (
    guild_id            INTEGER PRIMARY KEY,
    reminder_channel_id INTEGER,
    digest_channel_id   INTEGER
);

CREATE TABLE IF NOT EXISTS user_settings (
    user_id  INTEGER PRIMARY KEY,
    timezone TEXT NOT NULL DEFAULT 'UTC'
);

CREATE TABLE IF NOT EXISTS lists (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id             INTEGER NOT NULL,
    owner_id             INTEGER NOT NULL,
    channel_id           INTEGER,
    name                 TEXT NOT NULL,
    created_at           INTEGER NOT NULL,
    archived             INTEGER NOT NULL DEFAULT 0,
    progress_channel_id  INTEGER,
    progress_message_id  INTEGER
);
CREATE INDEX IF NOT EXISTS idx_lists_guild ON lists(guild_id, owner_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_lists_unique
    ON lists(guild_id, owner_id, name COLLATE NOCASE);

CREATE TABLE IF NOT EXISTS tasks (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    list_id      INTEGER NOT NULL REFERENCES lists(id) ON DELETE CASCADE,
    position     INTEGER NOT NULL,
    name         TEXT NOT NULL,
    description  TEXT NOT NULL DEFAULT '',
    completed_at INTEGER,
    completed_by INTEGER
);
CREATE INDEX IF NOT EXISTS idx_tasks_list ON tasks(list_id, position);

CREATE TABLE IF NOT EXISTS reminders (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id        INTEGER NOT NULL,
    owner_id        INTEGER NOT NULL,
    channel_id      INTEGER,
    message         TEXT NOT NULL DEFAULT '',
    kind            TEXT NOT NULL,                       -- 'date' | 'timer'
    due_at          INTEGER NOT NULL,                    -- unix seconds (UTC)
    notify_ids      TEXT NOT NULL DEFAULT '[]',          -- JSON array of user ids
    notify_roles    TEXT NOT NULL DEFAULT '[]',          -- JSON array of role ids
    ping_everyone   INTEGER NOT NULL DEFAULT 0,
    status          TEXT NOT NULL DEFAULT 'pending',     -- 'pending' | 'sent' | 'cancelled'
    sent_at         INTEGER,
    created_at      INTEGER NOT NULL,
    list_id         INTEGER REFERENCES lists(id) ON DELETE CASCADE,
    task_id         INTEGER REFERENCES tasks(id) ON DELETE CASCADE,
    interval_seconds INTEGER,                            -- NULL = one-shot; else recurring
    recur_until     INTEGER                              -- optional end timestamp for recurrence
);
CREATE INDEX IF NOT EXISTS idx_rem_due   ON reminders(status, due_at);
CREATE INDEX IF NOT EXISTS idx_rem_owner ON reminders(guild_id, owner_id, status);
CREATE INDEX IF NOT EXISTS idx_rem_list  ON reminders(list_id, task_id);

CREATE TABLE IF NOT EXISTS activity (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id   INTEGER NOT NULL,
    actor_id   INTEGER NOT NULL,
    list_id    INTEGER,
    task_id    INTEGER,
    action     TEXT NOT NULL,          -- 'complete' | 'reopen' | 'create_list' | 'delete_list' | 'archive' …
    detail     TEXT NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_activity_guild ON activity(guild_id, created_at DESC);
"""

_REMINDER_COLUMNS = {
    "message", "kind", "due_at", "notify_ids", "notify_roles", "ping_everyone",
    "channel_id", "status", "sent_at", "interval_seconds", "recur_until",
}

_REMINDER_JOIN = (
    "SELECT r.*, l.name AS list_name, t.name AS task_name, t.position AS task_position "
    "FROM reminders r LEFT JOIN lists l ON l.id = r.list_id LEFT JOIN tasks t ON t.id = r.task_id "
)


class DuplicateListName(Exception):
    """You already have a list with that name in this server."""


class Database:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.conn: Optional[aiosqlite.Connection] = None

    async def connect(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = await aiosqlite.connect(self.path)
        self.conn.row_factory = aiosqlite.Row
        await self.conn.execute("PRAGMA journal_mode=WAL")
        await self.conn.execute("PRAGMA foreign_keys=ON")
        await self._migrate()
        await self.conn.executescript(SCHEMA)
        await self._migrate_columns()
        await self.conn.commit()

    async def _migrate(self) -> None:
        """v1 stored a time on every task. Move those times into linked reminders."""
        async with self.conn.execute("PRAGMA table_info(tasks)") as cur:
            cols = {r["name"] for r in await cur.fetchall()}
        if "due_at" not in cols:
            return
        await self.conn.executescript(
            "BEGIN; DROP INDEX IF EXISTS idx_tasks_due; DROP INDEX IF EXISTS idx_tasks_list; "
            "ALTER TABLE tasks RENAME TO tasks_old; COMMIT;"
        )
        await self.conn.executescript(SCHEMA)
        await self.conn.executescript(
            """
            BEGIN;
            INSERT INTO tasks (id, list_id, position, name, description)
                SELECT id, list_id, position, name, description FROM tasks_old;
            INSERT INTO reminders (guild_id, owner_id, channel_id, message, kind, due_at,
                                   notify_ids, status, sent_at, created_at, list_id, task_id)
                SELECT l.guild_id, l.owner_id, l.channel_id, '', o.kind, o.due_at, o.notify_ids,
                       o.status, o.sent_at, CAST(strftime('%s','now') AS INTEGER), o.list_id, o.id
                FROM tasks_old o JOIN lists l ON l.id = o.list_id;
            DROP TABLE tasks_old;
            COMMIT;
            """
        )

    async def _migrate_columns(self) -> None:
        """Add columns introduced after the initial schema without breaking existing DBs."""
        async with self.conn.execute("PRAGMA table_info(lists)") as cur:
            list_cols = {r["name"] for r in await cur.fetchall()}
        for col, decl in (
            ("archived", "INTEGER NOT NULL DEFAULT 0"),
            ("progress_channel_id", "INTEGER"),
            ("progress_message_id", "INTEGER"),
        ):
            if col not in list_cols:
                await self.conn.execute(f"ALTER TABLE lists ADD COLUMN {col} {decl}")

        async with self.conn.execute("PRAGMA table_info(reminders)") as cur:
            rem_cols = {r["name"] for r in await cur.fetchall()}
        for col, decl in (
            ("notify_roles", "TEXT NOT NULL DEFAULT '[]'"),
            ("ping_everyone", "INTEGER NOT NULL DEFAULT 0"),
            ("interval_seconds", "INTEGER"),
            ("recur_until", "INTEGER"),
        ):
            if col not in rem_cols:
                await self.conn.execute(f"ALTER TABLE reminders ADD COLUMN {col} {decl}")

        async with self.conn.execute("PRAGMA table_info(guild_settings)") as cur:
            gs_cols = {r["name"] for r in await cur.fetchall()}
        if "digest_channel_id" not in gs_cols:
            await self.conn.execute(
                "ALTER TABLE guild_settings ADD COLUMN digest_channel_id INTEGER"
            )

    async def close(self) -> None:
        if self.conn:
            await self.conn.close()
            self.conn = None

    # ---- low-level helpers -------------------------------------------------
    async def _one(self, sql: str, params: tuple = ()) -> Optional[dict[str, Any]]:
        async with self.conn.execute(sql, params) as cur:
            row = await cur.fetchone()
        return dict(row) if row else None

    async def _all(self, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        async with self.conn.execute(sql, params) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def _run(self, sql: str, params: tuple = ()) -> aiosqlite.Cursor:
        cur = await self.conn.execute(sql, params)
        await self.conn.commit()
        return cur

    # ---- user / guild settings ---------------------------------------------
    async def get_timezone(self, user_id: int) -> Optional[str]:
        row = await self._one("SELECT timezone FROM user_settings WHERE user_id=?", (user_id,))
        return row["timezone"] if row else None

    async def set_timezone(self, user_id: int, tz: str) -> None:
        await self._run(
            "INSERT INTO user_settings (user_id, timezone) VALUES (?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET timezone=excluded.timezone",
            (user_id, tz),
        )

    async def get_channel(self, guild_id: int) -> Optional[int]:
        row = await self._one(
            "SELECT reminder_channel_id FROM guild_settings WHERE guild_id=?", (guild_id,)
        )
        return row["reminder_channel_id"] if row else None

    async def set_channel(self, guild_id: int, channel_id: Optional[int]) -> None:
        await self._run(
            "INSERT INTO guild_settings (guild_id, reminder_channel_id) VALUES (?, ?) "
            "ON CONFLICT(guild_id) DO UPDATE SET reminder_channel_id=excluded.reminder_channel_id",
            (guild_id, channel_id),
        )

    async def get_digest_channel(self, guild_id: int) -> Optional[int]:
        row = await self._one(
            "SELECT digest_channel_id FROM guild_settings WHERE guild_id=?", (guild_id,)
        )
        return row["digest_channel_id"] if row else None

    async def set_digest_channel(self, guild_id: int, channel_id: Optional[int]) -> None:
        await self._run(
            "INSERT INTO guild_settings (guild_id, digest_channel_id) VALUES (?, ?) "
            "ON CONFLICT(guild_id) DO UPDATE SET digest_channel_id=excluded.digest_channel_id",
            (guild_id, channel_id),
        )

    # ---- activity log ------------------------------------------------------
    async def log_activity(
        self, guild_id: int, actor_id: int, action: str, *,
        list_id: Optional[int] = None, task_id: Optional[int] = None, detail: str = "",
    ) -> None:
        await self._run(
            "INSERT INTO activity (guild_id, actor_id, list_id, task_id, action, detail, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (guild_id, actor_id, list_id, task_id, action, detail, int(time.time())),
        )

    async def recent_activity(self, guild_id: int, limit: int = 25) -> list[dict]:
        return await self._all(
            "SELECT * FROM activity WHERE guild_id=? ORDER BY created_at DESC LIMIT ?",
            (guild_id, limit),
        )

    # ---- lists -------------------------------------------------------------
    async def _insert_reminder(
        self, guild_id: int, owner_id: int, channel_id: Optional[int],
        r: dict, list_id: Optional[int], task_id: Optional[int],
    ) -> int:
        cur = await self.conn.execute(
            "INSERT INTO reminders (guild_id, owner_id, channel_id, message, kind, due_at, "
            "notify_ids, notify_roles, ping_everyone, created_at, list_id, task_id, "
            "interval_seconds, recur_until) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                guild_id, owner_id, channel_id,
                r.get("message", ""), r["kind"], r["due_at"],
                json.dumps(r.get("notify_ids", [])),
                json.dumps(r.get("notify_roles", [])),
                1 if r.get("ping_everyone") else 0,
                int(time.time()), list_id, task_id,
                r.get("interval_seconds"),
                r.get("recur_until"),
            ),
        )
        return cur.lastrowid

    async def create_list(
        self, guild_id: int, owner_id: int, channel_id: Optional[int], name: str,
        tasks: list[dict], list_reminder: Optional[dict] = None,
    ) -> int:
        """tasks: [{name, description, reminder?: {message, kind, due_at, notify_ids, …}}]"""
        try:
            cur = await self.conn.execute(
                "INSERT INTO lists (guild_id, owner_id, channel_id, name, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (guild_id, owner_id, channel_id, name, int(time.time())),
            )
            list_id = cur.lastrowid
            for pos, t in enumerate(tasks, 1):
                cur = await self.conn.execute(
                    "INSERT INTO tasks (list_id, position, name, description) VALUES (?, ?, ?, ?)",
                    (list_id, pos, t["name"], t["description"]),
                )
                if t.get("reminder"):
                    await self._insert_reminder(
                        guild_id, owner_id, channel_id, t["reminder"], list_id, cur.lastrowid
                    )
            if list_reminder:
                await self._insert_reminder(
                    guild_id, owner_id, channel_id, list_reminder, list_id, None
                )
            await self.conn.commit()
            await self.log_activity(guild_id, owner_id, "create_list", list_id=list_id, detail=name)
            return list_id
        except sqlite3.IntegrityError as e:
            await self.conn.rollback()
            raise DuplicateListName from e

    async def get_list(self, list_id: int) -> Optional[dict]:
        return await self._one("SELECT * FROM lists WHERE id=?", (list_id,))

    async def find_list_by_name(self, guild_id: int, owner_id: int, name: str) -> Optional[dict]:
        return await self._one(
            "SELECT * FROM lists WHERE guild_id=? AND owner_id=? AND name=? COLLATE NOCASE "
            "AND archived=0",
            (guild_id, owner_id, name),
        )

    async def list_name_taken(
        self, guild_id: int, owner_id: int, name: str, exclude_id: Optional[int] = None
    ) -> bool:
        row = await self._one(
            "SELECT id FROM lists WHERE guild_id=? AND owner_id=? AND name=? COLLATE NOCASE "
            "AND id != ?",
            (guild_id, owner_id, name, exclude_id or -1),
        )
        return row is not None

    async def lists_for(
        self, guild_id: int, owner_id: Optional[int] = None, *, include_archived: bool = False
    ) -> list[dict]:
        sql = (
            "SELECT l.*, COUNT(t.id) AS task_count, "
            "COUNT(CASE WHEN t.completed_at IS NOT NULL THEN 1 END) AS done_count "
            "FROM lists l LEFT JOIN tasks t ON t.list_id = l.id WHERE l.guild_id = ? "
        )
        params: tuple = (guild_id,)
        if not include_archived:
            sql += "AND l.archived = 0 "
        if owner_id is not None:
            sql += "AND l.owner_id = ? "
            params += (owner_id,)
        sql += "GROUP BY l.id ORDER BY l.archived, l.name COLLATE NOCASE"
        return await self._all(sql, params)

    async def rename_list(self, list_id: int, name: str) -> None:
        try:
            await self._run("UPDATE lists SET name=? WHERE id=?", (name, list_id))
        except sqlite3.IntegrityError as e:
            await self.conn.rollback()
            raise DuplicateListName from e

    async def set_archived(self, list_id: int, archived: bool, actor_id: int) -> None:
        lst = await self.get_list(list_id)
        await self._run("UPDATE lists SET archived=? WHERE id=?", (1 if archived else 0, list_id))
        if lst:
            await self.log_activity(
                lst["guild_id"], actor_id, "archive" if archived else "unarchive",
                list_id=list_id, detail=lst["name"],
            )

    async def delete_list(self, list_id: int, actor_id: int) -> None:
        lst = await self.get_list(list_id)
        await self._run("DELETE FROM lists WHERE id=?", (list_id,))  # tasks + reminders cascade
        if lst:
            await self.log_activity(
                lst["guild_id"], actor_id, "delete_list", list_id=list_id, detail=lst["name"]
            )

    async def set_progress_message(
        self, list_id: int, channel_id: Optional[int], message_id: Optional[int]
    ) -> None:
        await self._run(
            "UPDATE lists SET progress_channel_id=?, progress_message_id=? WHERE id=?",
            (channel_id, message_id, list_id),
        )

    # ---- tasks -------------------------------------------------------------
    async def tasks_for(self, list_id: int) -> list[dict]:
        return await self._all("SELECT * FROM tasks WHERE list_id=? ORDER BY position", (list_id,))

    async def count_tasks(self, list_id: int) -> int:
        row = await self._one("SELECT COUNT(*) AS n FROM tasks WHERE list_id=?", (list_id,))
        return row["n"] if row else 0

    _TASK_JOIN = (
        "SELECT t.*, l.guild_id, l.owner_id, l.name AS list_name "
        "FROM tasks t JOIN lists l ON l.id = t.list_id "
    )

    async def get_task(self, task_id: int) -> Optional[dict]:
        return await self._one(self._TASK_JOIN + "WHERE t.id=?", (task_id,))

    async def get_task_by_position(self, list_id: int, position: int) -> Optional[dict]:
        return await self._one(
            self._TASK_JOIN + "WHERE t.list_id=? AND t.position=?", (list_id, position)
        )

    async def add_task(self, list_id: int, name: str, description: str) -> int:
        cur = await self._run(
            "INSERT INTO tasks (list_id, position, name, description) "
            "SELECT ?, COALESCE(MAX(position), 0) + 1, ?, ? FROM tasks WHERE list_id = ?",
            (list_id, name, description, list_id),
        )
        return cur.lastrowid

    async def update_task(self, task_id: int, name: str, description: str) -> None:
        await self._run(
            "UPDATE tasks SET name=?, description=? WHERE id=?", (name, description, task_id)
        )

    async def delete_task(self, task_id: int) -> None:
        row = await self._one("SELECT list_id, position FROM tasks WHERE id=?", (task_id,))
        if not row:
            return
        await self.conn.execute("DELETE FROM tasks WHERE id=?", (task_id,))
        await self.conn.execute(
            "UPDATE tasks SET position = position - 1 WHERE list_id=? AND position > ?",
            (row["list_id"], row["position"]),
        )
        await self.conn.commit()

    async def set_task_done(self, task_id: int, user_id: int, done: bool) -> dict:
        """Mark complete/incomplete. Completing cancels the task's pending one-shot timer;
        finishing the last task cancels the list countdown (one-shot only)."""
        task = await self.get_task(task_id)
        result = {
            "task_timer_cancelled": False,
            "list_timer_cancelled": False,
            "all_done": False,
            "done": 0,
            "total": 0,
        }
        if not task:
            return result
        if done:
            await self.conn.execute(
                "UPDATE tasks SET completed_at=?, completed_by=? WHERE id=?",
                (int(time.time()), user_id, task_id),
            )
            # Only cancel one-shot timers; recurring ones keep running
            cur = await self.conn.execute(
                "UPDATE reminders SET status='cancelled' "
                "WHERE task_id=? AND status='pending' AND interval_seconds IS NULL",
                (task_id,),
            )
            result["task_timer_cancelled"] = cur.rowcount > 0
        else:
            await self.conn.execute(
                "UPDATE tasks SET completed_at=NULL, completed_by=NULL WHERE id=?", (task_id,)
            )
        async with self.conn.execute(
            "SELECT COUNT(*) AS total, COUNT(completed_at) AS done FROM tasks WHERE list_id=?",
            (task["list_id"],),
        ) as cur:
            row = await cur.fetchone()
        result.update(
            total=row["total"], done=row["done"], all_done=row["total"] == row["done"]
        )
        if done and result["all_done"]:
            cur = await self.conn.execute(
                "UPDATE reminders SET status='cancelled' "
                "WHERE list_id=? AND task_id IS NULL AND status='pending' "
                "AND interval_seconds IS NULL",
                (task["list_id"],),
            )
            result["list_timer_cancelled"] = cur.rowcount > 0
        await self.conn.commit()
        await self.log_activity(
            task["guild_id"], user_id, "complete" if done else "reopen",
            list_id=task["list_id"], task_id=task_id, detail=task["name"],
        )
        return result

    async def bulk_set_done(
        self, list_id: int, positions: list[int], user_id: int, done: bool
    ) -> dict:
        """Complete or reopen several tasks by position. Returns summary."""
        tasks = await self.tasks_for(list_id)
        by_pos = {t["position"]: t for t in tasks}
        changed = 0
        for pos in positions:
            t = by_pos.get(pos)
            if not t:
                continue
            is_done = t["completed_at"] is not None
            if is_done == done:
                continue
            await self.set_task_done(t["id"], user_id, done)
            changed += 1
        total = len(tasks)
        done_n = sum(1 for t in await self.tasks_for(list_id) if t["completed_at"])
        return {"changed": changed, "done": done_n, "total": total, "all_done": done_n == total}

    # ---- reminders ---------------------------------------------------------
    async def add_reminder(
        self, guild_id: int, owner_id: int, channel_id: Optional[int],
        data: dict, list_id: Optional[int] = None, task_id: Optional[int] = None,
    ) -> int:
        rid = await self._insert_reminder(guild_id, owner_id, channel_id, data, list_id, task_id)
        await self.conn.commit()
        return rid

    async def set_timer(
        self, guild_id: int, owner_id: int, channel_id: Optional[int],
        data: dict, list_id: int, task_id: Optional[int],
    ) -> int:
        """Attach a timer to a task (task_id) or whole list (task_id None), replacing any old one."""
        await self.conn.execute(
            "DELETE FROM reminders WHERE list_id=? AND task_id IS ?", (list_id, task_id)
        )
        rid = await self._insert_reminder(guild_id, owner_id, channel_id, data, list_id, task_id)
        await self.conn.commit()
        return rid

    async def clear_timer(self, list_id: int, task_id: Optional[int]) -> int:
        cur = await self._run(
            "DELETE FROM reminders WHERE list_id=? AND task_id IS ?", (list_id, task_id)
        )
        return cur.rowcount

    async def clear_all_timers(self, list_id: int) -> int:
        cur = await self._run("DELETE FROM reminders WHERE list_id=?", (list_id,))
        return cur.rowcount

    async def get_link_reminder(self, list_id: int, task_id: Optional[int]) -> Optional[dict]:
        return await self._one(
            _REMINDER_JOIN + "WHERE r.list_id=? AND r.task_id IS ? AND r.status='pending'",
            (list_id, task_id),
        )

    async def reminders_for_list(self, list_id: int) -> list[dict]:
        return await self._all(
            _REMINDER_JOIN + "WHERE r.list_id=? AND r.status='pending'", (list_id,)
        )

    async def get_reminder(self, reminder_id: int) -> Optional[dict]:
        return await self._one(_REMINDER_JOIN + "WHERE r.id=?", (reminder_id,))

    async def reminders_for(
        self, guild_id: int, owner_id: Optional[int] = None, limit: int = 25
    ) -> list[dict]:
        sql = _REMINDER_JOIN + "WHERE r.guild_id=? AND r.status='pending' "
        params: tuple = (guild_id,)
        if owner_id is not None:
            sql += "AND r.owner_id=? "
            params += (owner_id,)
        sql += "ORDER BY r.due_at LIMIT ?"
        return await self._all(sql, params + (limit,))

    async def update_reminder(self, reminder_id: int, **fields: Any) -> None:
        bad = set(fields) - _REMINDER_COLUMNS
        if bad:
            raise ValueError(f"Unknown reminder columns: {bad}")
        if "notify_ids" in fields and not isinstance(fields["notify_ids"], str):
            fields["notify_ids"] = json.dumps(fields["notify_ids"])
        if "notify_roles" in fields and not isinstance(fields["notify_roles"], str):
            fields["notify_roles"] = json.dumps(fields["notify_roles"])
        if "ping_everyone" in fields:
            fields["ping_everyone"] = 1 if fields["ping_everyone"] else 0
        sets = ", ".join(f"{k}=?" for k in fields)
        await self._run(f"UPDATE reminders SET {sets} WHERE id=?", (*fields.values(), reminder_id))

    async def due_reminders(self, now: int, limit: int = 50) -> list[dict]:
        return await self._all(
            "SELECT r.*, l.name AS list_name, t.name AS task_name, t.position AS task_position, "
            "(SELECT COUNT(*) FROM tasks WHERE list_id = r.list_id) AS list_total, "
            "(SELECT COUNT(completed_at) FROM tasks WHERE list_id = r.list_id) AS list_done "
            "FROM reminders r LEFT JOIN lists l ON l.id = r.list_id "
            "LEFT JOIN tasks t ON t.id = r.task_id "
            "WHERE r.status='pending' AND r.due_at <= ? ORDER BY r.due_at LIMIT ?",
            (now, limit),
        )

    async def mark_sent_or_reschedule(self, reminder_id: int, now: int) -> None:
        """One-shot → sent. Recurring → advance due_at (or cancel if past recur_until)."""
        r = await self.get_reminder(reminder_id)
        if not r:
            return
        interval = r.get("interval_seconds")
        if not interval:
            await self._run(
                "UPDATE reminders SET status='sent', sent_at=? WHERE id=?", (now, reminder_id)
            )
            return
        next_due = r["due_at"] + interval
        # Catch up if we were offline for a long time
        while next_due <= now:
            next_due += interval
        until = r.get("recur_until")
        if until and next_due > until:
            await self._run(
                "UPDATE reminders SET status='sent', sent_at=? WHERE id=?", (now, reminder_id)
            )
        else:
            await self._run(
                "UPDATE reminders SET due_at=?, sent_at=? WHERE id=?", (next_due, now, reminder_id)
            )

    async def due_soon(self, guild_id: int, within_seconds: int, now: int) -> list[dict]:
        """Pending reminders due within the next within_seconds window."""
        return await self._all(
            _REMINDER_JOIN
            + "WHERE r.guild_id=? AND r.status='pending' AND r.due_at > ? AND r.due_at <= ? "
            "ORDER BY r.due_at",
            (guild_id, now, now + within_seconds),
        )

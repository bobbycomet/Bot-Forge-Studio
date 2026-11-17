"""
Per-guild SQLite storage for the post router bot.

Each Discord server gets its own folder and its own SQLite file:

    data/
      <guild_id>/
        router.db

Mapping IDs are the file's own AUTOINCREMENT column, so they're sequential
and independent per server: a brand-new server's first mapping is always
#1, never #11 because some other server already has ten. No guild_id column
is needed anywhere in here — a Database instance is already scoped to one
guild by which file it opened.

Game profiles scope bracket roles/channels and per-user game UIDs so multiple
games on the same server never mix brackets or UIDs.
"""

import json
import os
from datetime import datetime, timezone

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    hub_channel_id INTEGER,
    bracket_channel_id INTEGER
);

CREATE TABLE IF NOT EXISTS mappings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    destination_channel_id INTEGER NOT NULL,
    user_ids TEXT NOT NULL DEFAULT '[]',
    source_ids TEXT NOT NULL DEFAULT '[]',
    keyword_mode INTEGER NOT NULL DEFAULT 0,
    keywords TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS game_profiles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL COLLATE NOCASE,
    created_at TEXT NOT NULL,
    UNIQUE(name)
);

CREATE TABLE IF NOT EXISTS bracket_roles (
    level INTEGER NOT NULL,
    game_id INTEGER NOT NULL,
    role_id INTEGER NOT NULL UNIQUE,
    channel_id INTEGER,
    created_at TEXT NOT NULL,
    PRIMARY KEY (game_id, level),
    FOREIGN KEY (game_id) REFERENCES game_profiles(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS user_uids (
    user_id INTEGER NOT NULL,
    game_id INTEGER NOT NULL,
    uid TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (user_id, game_id),
    FOREIGN KEY (game_id) REFERENCES game_profiles(id) ON DELETE CASCADE
);
"""

# Lightweight migration for existing DBs.
MIGRATIONS = [
    "ALTER TABLE settings ADD COLUMN bracket_channel_id INTEGER",
    "ALTER TABLE bracket_roles ADD COLUMN channel_id INTEGER",
    "ALTER TABLE bracket_roles ADD COLUMN game_id INTEGER",
]

MAX_USER_IDS = 10
MAX_SOURCE_IDS = 20
MAX_UID_LEN = 64
MAX_GAME_NAME_LEN = 50

# Bracket levels: 10, 20, ..., 200 (inclusive). Level 200 reserved for future use.
BRACKET_LEVELS = list(range(10, 201, 10))


class Database:
    """One connection, scoped to a single guild's SQLite file."""

    def __init__(self, path: str):
        self.path = path
        self._conn: aiosqlite.Connection | None = None

    async def connect(self):
        pending = aiosqlite.connect(self.path)
        pending.daemon = True        # never keep the process alive after the bot stopped
        self._conn = await pending
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA foreign_keys = ON")
        await self._conn.executescript(SCHEMA)
        for sql in MIGRATIONS:
            try:
                await self._conn.execute(sql)
            except aiosqlite.OperationalError:
                pass  # column already exists
        await self._migrate_legacy_brackets()
        await self._conn.commit()

    async def _migrate_legacy_brackets(self):
        """Attach any pre-game-profile bracket rows to a 'Legacy' game profile.

        Old schema had PRIMARY KEY (level) only. New schema is
        PRIMARY KEY (game_id, level). If rows exist with NULL game_id, create
        a Legacy profile and assign them, then rebuild the table so the
        composite PK is enforced.
        """
        cur = await self._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='bracket_roles'"
        )
        if not await cur.fetchone():
            return

        cur = await self._conn.execute("PRAGMA table_info(bracket_roles)")
        cols = {row["name"]: row for row in await cur.fetchall()}
        if "game_id" not in cols:
            return

        cur = await self._conn.execute(
            "SELECT COUNT(*) AS n FROM bracket_roles WHERE game_id IS NULL"
        )
        orphan_count = (await cur.fetchone())["n"]
        if orphan_count == 0:
            cur = await self._conn.execute("PRAGMA table_info(bracket_roles)")
            info = await cur.fetchall()
            pk_cols = [r["name"] for r in info if r["pk"]]
            if pk_cols == ["level"]:
                await self._rebuild_bracket_roles_table()
            return

        cur = await self._conn.execute(
            "SELECT id FROM game_profiles WHERE name = 'Legacy' COLLATE NOCASE"
        )
        row = await cur.fetchone()
        if row:
            legacy_id = row["id"]
        else:
            cur = await self._conn.execute(
                "INSERT INTO game_profiles (name, created_at) VALUES (?, ?)",
                ("Legacy", datetime.now(timezone.utc).isoformat()),
            )
            legacy_id = cur.lastrowid

        await self._conn.execute(
            "UPDATE bracket_roles SET game_id = ? WHERE game_id IS NULL",
            (legacy_id,),
        )
        await self._rebuild_bracket_roles_table()

    async def _rebuild_bracket_roles_table(self):
        """Recreate bracket_roles with the composite PRIMARY KEY (game_id, level)."""
        await self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS bracket_roles_new (
                level INTEGER NOT NULL,
                game_id INTEGER NOT NULL,
                role_id INTEGER NOT NULL UNIQUE,
                channel_id INTEGER,
                created_at TEXT NOT NULL,
                PRIMARY KEY (game_id, level),
                FOREIGN KEY (game_id) REFERENCES game_profiles(id) ON DELETE CASCADE
            );
            INSERT OR IGNORE INTO bracket_roles_new (level, game_id, role_id, channel_id, created_at)
                SELECT level, game_id, role_id, channel_id, created_at
                FROM bracket_roles
                WHERE game_id IS NOT NULL;
            DROP TABLE bracket_roles;
            ALTER TABLE bracket_roles_new RENAME TO bracket_roles;
            """
        )

    async def close(self):
        if self._conn:
            await self._conn.close()

    # ---------------- settings ----------------

    async def set_hub_channel(self, channel_id: int):
        await self._conn.execute(
            "INSERT INTO settings (id, hub_channel_id) VALUES (1, ?) "
            "ON CONFLICT(id) DO UPDATE SET hub_channel_id = excluded.hub_channel_id",
            (channel_id,),
        )
        await self._conn.commit()

    async def get_hub_channel(self) -> int | None:
        cur = await self._conn.execute("SELECT hub_channel_id FROM settings WHERE id = 1")
        row = await cur.fetchone()
        return row["hub_channel_id"] if row else None

    async def set_bracket_channel(self, channel_id: int):
        await self._conn.execute(
            "INSERT INTO settings (id, bracket_channel_id) VALUES (1, ?) "
            "ON CONFLICT(id) DO UPDATE SET bracket_channel_id = excluded.bracket_channel_id",
            (channel_id,),
        )
        await self._conn.commit()

    async def get_bracket_channel(self) -> int | None:
        cur = await self._conn.execute("SELECT bracket_channel_id FROM settings WHERE id = 1")
        row = await cur.fetchone()
        return row["bracket_channel_id"] if row else None

    # ---------------- mappings ----------------

    async def add_mapping(
        self,
        name: str,
        destination_channel_id: int,
        user_ids: list[int],
        keyword_mode: bool,
        keywords: list[str],
        source_ids: list[int] | None = None,
    ) -> int:
        cur = await self._conn.execute(
            "INSERT INTO mappings "
            "(name, destination_channel_id, user_ids, source_ids, keyword_mode, keywords, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                name,
                destination_channel_id,
                json.dumps(list(user_ids)[:MAX_USER_IDS]),
                json.dumps(list(source_ids or [])[:MAX_SOURCE_IDS]),
                int(keyword_mode),
                json.dumps(keywords),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        await self._conn.commit()
        return cur.lastrowid

    async def update_mapping(self, mapping_id: int, **fields):
        if not fields:
            return
        cols, vals = [], []
        for key, value in fields.items():
            if key == "user_ids":
                value = json.dumps(list(value)[:MAX_USER_IDS])
            elif key == "source_ids":
                value = json.dumps(list(value)[:MAX_SOURCE_IDS])
            elif key == "keywords":
                value = json.dumps(list(value))
            elif key == "keyword_mode":
                value = int(value)
            cols.append(f"{key} = ?")
            vals.append(value)
        vals.append(mapping_id)
        await self._conn.execute(f"UPDATE mappings SET {', '.join(cols)} WHERE id = ?", vals)
        await self._conn.commit()

    async def remove_mapping(self, mapping_id: int):
        await self._conn.execute("DELETE FROM mappings WHERE id = ?", (mapping_id,))
        await self._conn.commit()

    async def get_mapping(self, mapping_id: int):
        cur = await self._conn.execute("SELECT * FROM mappings WHERE id = ?", (mapping_id,))
        return await cur.fetchone()

    async def list_mappings(self):
        cur = await self._conn.execute("SELECT * FROM mappings ORDER BY id")
        return await cur.fetchall()

    # ---------------- bulk user-ID edits ----------------

    async def bulk_add_user_id(self, user_id: int, mapping_ids: list[int] | None = None) -> list[int]:
        rows = await self.list_mappings()
        if mapping_ids is not None:
            wanted = set(mapping_ids)
            rows = [r for r in rows if r["id"] in wanted]

        changed = []
        for row in rows:
            ids = json.loads(row["user_ids"])
            if user_id in ids:
                continue
            ids.append(user_id)
            await self.update_mapping(row["id"], user_ids=ids)
            changed.append(row["id"])
        return changed

    async def bulk_remove_user_id(self, user_id: int, mapping_ids: list[int] | None = None) -> list[int]:
        rows = await self.list_mappings()
        if mapping_ids is not None:
            wanted = set(mapping_ids)
            rows = [r for r in rows if r["id"] in wanted]

        changed = []
        for row in rows:
            ids = json.loads(row["user_ids"])
            if user_id not in ids:
                continue
            ids = [i for i in ids if i != user_id]
            await self.update_mapping(row["id"], user_ids=ids)
            changed.append(row["id"])
        return changed

    # ---------------- game profiles ----------------

    async def add_game_profile(self, name: str) -> int:
        name = name.strip()
        if not name or len(name) > MAX_GAME_NAME_LEN:
            raise ValueError(f"Game name must be 1–{MAX_GAME_NAME_LEN} characters")
        cur = await self._conn.execute(
            "INSERT INTO game_profiles (name, created_at) VALUES (?, ?)",
            (name, datetime.now(timezone.utc).isoformat()),
        )
        await self._conn.commit()
        return cur.lastrowid

    async def rename_game_profile(self, game_id: int, new_name: str) -> bool:
        new_name = new_name.strip()
        if not new_name or len(new_name) > MAX_GAME_NAME_LEN:
            raise ValueError(f"Game name must be 1–{MAX_GAME_NAME_LEN} characters")
        cur = await self._conn.execute(
            "UPDATE game_profiles SET name = ? WHERE id = ?",
            (new_name, game_id),
        )
        await self._conn.commit()
        return cur.rowcount > 0

    async def remove_game_profile(self, game_id: int) -> bool:
        """Cascade-deletes bracket roles and user UIDs for this game."""
        cur = await self._conn.execute("DELETE FROM game_profiles WHERE id = ?", (game_id,))
        await self._conn.commit()
        return cur.rowcount > 0

    async def get_game_profile(self, game_id: int):
        cur = await self._conn.execute("SELECT * FROM game_profiles WHERE id = ?", (game_id,))
        return await cur.fetchone()

    async def get_game_profile_by_name(self, name: str):
        cur = await self._conn.execute(
            "SELECT * FROM game_profiles WHERE name = ? COLLATE NOCASE",
            (name.strip(),),
        )
        return await cur.fetchone()

    async def list_game_profiles(self):
        cur = await self._conn.execute("SELECT * FROM game_profiles ORDER BY name COLLATE NOCASE")
        return await cur.fetchall()

    async def ensure_legacy_and_migrate(self) -> dict:
        """Create a 'Legacy' game profile if needed and attach any orphaned
        bracket rows (NULL game_id) to it. Safe to run repeatedly.

        Returns a summary dict:
          created_legacy: bool
          legacy_id: int
          brackets_migrated: int
          total_legacy_brackets: int
        """
        cur = await self._conn.execute(
            "SELECT id FROM game_profiles WHERE name = 'Legacy' COLLATE NOCASE"
        )
        row = await cur.fetchone()
        created = False
        if row:
            legacy_id = row["id"]
        else:
            cur = await self._conn.execute(
                "INSERT INTO game_profiles (name, created_at) VALUES (?, ?)",
                ("Legacy", datetime.now(timezone.utc).isoformat()),
            )
            await self._conn.commit()
            legacy_id = cur.lastrowid
            created = True

        # Count orphans before update (column may not exist on brand-new DBs that
        # already have the composite schema with NOT NULL game_id — then 0).
        migrated = 0
        try:
            cur = await self._conn.execute(
                "SELECT COUNT(*) AS n FROM bracket_roles WHERE game_id IS NULL"
            )
            orphans = (await cur.fetchone())["n"]
            if orphans:
                await self._conn.execute(
                    "UPDATE bracket_roles SET game_id = ? WHERE game_id IS NULL",
                    (legacy_id,),
                )
                await self._conn.commit()
                migrated = orphans
                # Rebuild PK shape if still on the old single-column primary key.
                cur = await self._conn.execute("PRAGMA table_info(bracket_roles)")
                info = await cur.fetchall()
                pk_cols = [r["name"] for r in info if r["pk"]]
                if pk_cols == ["level"]:
                    await self._rebuild_bracket_roles_table()
                    await self._conn.commit()
        except Exception:
            # Table shape already fully migrated — nothing left to do.
            pass

        cur = await self._conn.execute(
            "SELECT COUNT(*) AS n FROM bracket_roles WHERE game_id = ?",
            (legacy_id,),
        )
        total = (await cur.fetchone())["n"]
        return {
            "created_legacy": created,
            "legacy_id": legacy_id,
            "brackets_migrated": migrated,
            "total_legacy_brackets": total,
        }

    async def migrate_legacy_to_game(self, target_game_id: int) -> dict:
        """Move bracket roles and user UIDs from the Legacy profile onto target_game_id.

        Levels that already exist on the target are left on Legacy and reported
        as conflicts. UIDs that already exist on the target are left alone
        (target wins). Safe to run repeatedly.

        Returns:
          legacy_id, target_id,
          brackets_moved, brackets_conflicted,
          uids_moved, uids_skipped,
          legacy_remaining_brackets
        """
        cur = await self._conn.execute(
            "SELECT id FROM game_profiles WHERE name = 'Legacy' COLLATE NOCASE"
        )
        row = await cur.fetchone()
        if not row:
            return {
                "legacy_id": None,
                "target_id": target_game_id,
                "brackets_moved": 0,
                "brackets_conflicted": 0,
                "uids_moved": 0,
                "uids_skipped": 0,
                "legacy_remaining_brackets": 0,
                "error": "No Legacy game profile exists. Run /game migrate once with no target, or /game add name:Legacy first.",
            }
        legacy_id = row["id"]
        if legacy_id == target_game_id:
            return {
                "legacy_id": legacy_id,
                "target_id": target_game_id,
                "brackets_moved": 0,
                "brackets_conflicted": 0,
                "uids_moved": 0,
                "uids_skipped": 0,
                "legacy_remaining_brackets": 0,
                "error": "Target is Legacy itself — pick a different game profile.",
            }

        target = await self.get_game_profile(target_game_id)
        if target is None:
            return {
                "legacy_id": legacy_id,
                "target_id": target_game_id,
                "brackets_moved": 0,
                "brackets_conflicted": 0,
                "uids_moved": 0,
                "uids_skipped": 0,
                "legacy_remaining_brackets": 0,
                "error": "Target game profile not found.",
            }

        # --- brackets ---
        cur = await self._conn.execute(
            "SELECT level FROM bracket_roles WHERE game_id = ?", (target_game_id,)
        )
        target_levels = {r["level"] for r in await cur.fetchall()}

        cur = await self._conn.execute(
            "SELECT level, role_id FROM bracket_roles WHERE game_id = ?", (legacy_id,)
        )
        legacy_brackets = await cur.fetchall()

        moved_b = 0
        conflicted = 0
        for br in legacy_brackets:
            if br["level"] in target_levels:
                conflicted += 1
                continue
            await self._conn.execute(
                "UPDATE bracket_roles SET game_id = ? WHERE game_id = ? AND level = ?",
                (target_game_id, legacy_id, br["level"]),
            )
            moved_b += 1

        # --- UIDs ---
        cur = await self._conn.execute(
            "SELECT user_id FROM user_uids WHERE game_id = ?", (target_game_id,)
        )
        target_users = {r["user_id"] for r in await cur.fetchall()}

        cur = await self._conn.execute(
            "SELECT user_id, uid FROM user_uids WHERE game_id = ?", (legacy_id,)
        )
        legacy_uids = await cur.fetchall()

        moved_u = 0
        skipped_u = 0
        for uu in legacy_uids:
            if uu["user_id"] in target_users:
                skipped_u += 1
                continue
            await self._conn.execute(
                "UPDATE user_uids SET game_id = ? WHERE user_id = ? AND game_id = ?",
                (target_game_id, uu["user_id"], legacy_id),
            )
            moved_u += 1

        await self._conn.commit()

        cur = await self._conn.execute(
            "SELECT COUNT(*) AS n FROM bracket_roles WHERE game_id = ?", (legacy_id,)
        )
        remaining = (await cur.fetchone())["n"]

        return {
            "legacy_id": legacy_id,
            "target_id": target_game_id,
            "target_name": target["name"],
            "brackets_moved": moved_b,
            "brackets_conflicted": conflicted,
            "uids_moved": moved_u,
            "uids_skipped": skipped_u,
            "legacy_remaining_brackets": remaining,
            "error": None,
        }



    # ---------------- bracket roles (scoped by game) ----------------

    async def set_bracket_role(
        self, level: int, role_id: int, game_id: int, channel_id: int | None = None
    ):
        """Register (or replace) the Discord role for a bracket level under a game.

        channel_id links that level to its own destination. Passing None keeps
        whatever destination the level already had.
        """
        if level not in BRACKET_LEVELS:
            raise ValueError(f"Level must be one of {BRACKET_LEVELS}")
        await self._conn.execute(
            "INSERT INTO bracket_roles (level, game_id, role_id, channel_id, created_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(game_id, level) DO UPDATE SET role_id = excluded.role_id, "
            "channel_id = COALESCE(excluded.channel_id, bracket_roles.channel_id), "
            "created_at = excluded.created_at",
            (level, game_id, role_id, channel_id, datetime.now(timezone.utc).isoformat()),
        )
        await self._conn.commit()

    async def set_bracket_level_channel(self, level: int, game_id: int, channel_id: int) -> bool:
        """Point an already-registered level at its own destination.
        Returns False if no role is registered for that level+game yet."""
        cur = await self._conn.execute(
            "UPDATE bracket_roles SET channel_id = ? WHERE level = ? AND game_id = ?",
            (channel_id, level, game_id),
        )
        await self._conn.commit()
        return cur.rowcount > 0

    async def get_bracket_destination(self, level: int, game_id: int) -> int | None:
        """The level's own destination if set, else the server-wide default."""
        row = await self.get_bracket_role(level, game_id)
        if row is not None and row["channel_id"] is not None:
            return row["channel_id"]
        return await self.get_bracket_channel()

    async def remove_bracket_role(self, level: int, game_id: int) -> bool:
        cur = await self._conn.execute(
            "DELETE FROM bracket_roles WHERE level = ? AND game_id = ?",
            (level, game_id),
        )
        await self._conn.commit()
        return cur.rowcount > 0

    async def get_bracket_role(self, level: int, game_id: int):
        cur = await self._conn.execute(
            "SELECT * FROM bracket_roles WHERE level = ? AND game_id = ?",
            (level, game_id),
        )
        return await cur.fetchone()

    async def list_bracket_roles(self, game_id: int | None = None):
        if game_id is None:
            cur = await self._conn.execute(
                "SELECT br.*, gp.name AS game_name FROM bracket_roles br "
                "JOIN game_profiles gp ON gp.id = br.game_id "
                "ORDER BY gp.name COLLATE NOCASE, br.level"
            )
        else:
            cur = await self._conn.execute(
                "SELECT br.*, gp.name AS game_name FROM bracket_roles br "
                "JOIN game_profiles gp ON gp.id = br.game_id "
                "WHERE br.game_id = ? ORDER BY br.level",
                (game_id,),
            )
        return await cur.fetchall()

    async def get_all_bracket_role_ids(self, game_id: int | None = None) -> dict:
        """Return {(game_id, level): role_id} or {level: role_id} when game_id given."""
        rows = await self.list_bracket_roles(game_id)
        if game_id is not None:
            return {r["level"]: r["role_id"] for r in rows}
        return {(r["game_id"], r["level"]): r["role_id"] for r in rows}

    # ---------------- user game UIDs ----------------

    async def set_user_uid(self, user_id: int, game_id: int, uid: str) -> None:
        uid = uid.strip()
        if not uid or len(uid) > MAX_UID_LEN:
            raise ValueError(f"UID must be 1–{MAX_UID_LEN} characters")
        await self._conn.execute(
            "INSERT INTO user_uids (user_id, game_id, uid, updated_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(user_id, game_id) DO UPDATE SET uid = excluded.uid, "
            "updated_at = excluded.updated_at",
            (user_id, game_id, uid, datetime.now(timezone.utc).isoformat()),
        )
        await self._conn.commit()

    async def remove_user_uid(self, user_id: int, game_id: int) -> bool:
        cur = await self._conn.execute(
            "DELETE FROM user_uids WHERE user_id = ? AND game_id = ?",
            (user_id, game_id),
        )
        await self._conn.commit()
        return cur.rowcount > 0

    async def get_user_uid(self, user_id: int, game_id: int) -> str | None:
        cur = await self._conn.execute(
            "SELECT uid FROM user_uids WHERE user_id = ? AND game_id = ?",
            (user_id, game_id),
        )
        row = await cur.fetchone()
        return row["uid"] if row else None

    async def list_user_uids(self, user_id: int):
        """All game UIDs for one Discord user: rows with game_id, uid, game_name."""
        cur = await self._conn.execute(
            "SELECT uu.*, gp.name AS game_name FROM user_uids uu "
            "JOIN game_profiles gp ON gp.id = uu.game_id "
            "WHERE uu.user_id = ? ORDER BY gp.name COLLATE NOCASE",
            (user_id,),
        )
        return await cur.fetchall()

    async def get_uids_for_users(self, user_ids: list[int], game_id: int) -> dict[int, str]:
        """Bulk lookup: {discord_user_id: uid} for a single game."""
        if not user_ids:
            return {}
        placeholders = ",".join("?" * len(user_ids))
        cur = await self._conn.execute(
            f"SELECT user_id, uid FROM user_uids WHERE game_id = ? AND user_id IN ({placeholders})",
            (game_id, *user_ids),
        )
        rows = await cur.fetchall()
        return {r["user_id"]: r["uid"] for r in rows}


class DatabaseManager:
    """Owns one Database per guild, opened lazily on first use and cached.

    A guild's file lives at ``<data_root>/<guild_id>/router.db``; the folder
    is created automatically the first time that guild touches the bot. This
    is the only thing that makes the bot multi-server-safe — matching and
    delivery in utils.py don't change at all, since each connection only ever
    sees one guild's rows to begin with.
    """

    def __init__(self, data_root: str = "data"):
        self.data_root = data_root
        self._dbs: dict[int, Database] = {}

    def _path_for(self, guild_id: int) -> str:
        guild_dir = os.path.join(self.data_root, str(guild_id))
        os.makedirs(guild_dir, exist_ok=True)
        return os.path.join(guild_dir, "router.db")

    async def get(self, guild_id: int) -> Database:
        db = self._dbs.get(guild_id)
        if db is None:
            db = Database(self._path_for(guild_id))
            await db.connect()
            self._dbs[guild_id] = db
        return db

    async def close_all(self):
        for db in self._dbs.values():
            await db.close()
        self._dbs.clear()



# ---------------------------------------------------------------- Bot Forge Studio glue
def ensure_db(bot) -> "DatabaseManager":
    """One DatabaseManager per bot, shared by every router / bracket / game-profile module."""
    if getattr(bot, "db_manager", None) is None:
        bot.db_manager = DatabaseManager(os.path.join(os.getenv("DATA_DIR", "data"), "router"))
    return bot.db_manager


async def release_db(bot) -> None:
    mgr = getattr(bot, "db_manager", None)
    if mgr is not None:
        await mgr.close_all()          # reopens lazily on next use, so other modules keep working

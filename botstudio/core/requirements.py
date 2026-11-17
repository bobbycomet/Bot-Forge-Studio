"""What a project needs from Discord: privileged intents and bot permissions, derived from the nodes it uses."""
from __future__ import annotations

PERMS = {
    "action.add_role": "Manage Roles", "action.remove_role": "Manage Roles", "mod.add_role": "Manage Roles", "mod.remove_role": "Manage Roles",
    "mod.timeout": "Moderate Members", "mod.untimeout": "Moderate Members", "mod.kick": "Kick Members", "mod.ban": "Ban Members",
    "mod.purge": "Manage Messages", "mod.delete": "Manage Messages", "mod.nickname": "Manage Nicknames", "mod.slowmode": "Manage Channels",
    "mod.lock_channel": "Manage Channels", "action.create_ticket": "Manage Channels", "action.delete_channel": "Manage Channels",
    "action.poll": "Add Reactions", "action.react": "Add Reactions", "module.birthdays": "Manage Roles", "module.dungeons": "Create Public Threads",
    "module.arena": "Create Public Threads", "module.emotes": "Attach Files", "module.cards": "Attach Files",
    "module.role_brackets": "Manage Roles", "action.dm": "(members must allow DMs)",
}
PRIV = {"message_content": "Message Content Intent", "members": "Server Members Intent", "presences": "Presence Intent"}
BASE = ("View Channels", "Send Messages", "Embed Links")


def needs(graph, res) -> tuple[list[str], list[str]]:
    intents = sorted(getattr(res, "intents", []) or [])
    priv = [PRIV[i] for i in intents if i in PRIV]
    perms = set(BASE)
    for n in graph.nodes.values():
        p = PERMS.get(n.type_id)
        if p and not p.startswith("("):
            perms.add(p)
    return priv, sorted(perms)


def needs_markdown(graph, res) -> str:
    priv, perms = needs(graph, res)
    return ("**Privileged intents** (Developer Portal → Bot tab): " + (", ".join(f"**{p}**" for p in priv) if priv else "none needed") + "\n\n"
            "**Bot permissions** (invite link): " + ", ".join(f"**{p}**" for p in perms) + "\n\n"
            "**Scopes:** `bot` and `applications.commands`.")

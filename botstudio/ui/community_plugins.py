"""Community / official plugin browser (convenience layer over local install).

Catalog roots (GitHub, private for now):
  https://github.com/bobbycomet/Bot-Studio/tree/main/Plugins
  https://github.com/bobbycomet/Bot-Studio/tree/main/Plugins/official
  https://github.com/bobbycomet/Bot-Studio/tree/main/Plugins/community

License: Bot Forge Studio is GPLv3. Community plugins must be GPLv3 or MIT.
Official plugins are maintained by Comet UX Labs (lead: Bobby Comet).
"""
from __future__ import annotations

PLUGIN_HUB = "https://github.com/bobbycomet/Bot-Studio/tree/main/Plugins"
OFFICIAL_URL = "https://github.com/bobbycomet/Bot-Studio/tree/main/Plugins/official"
COMMUNITY_URL = "https://github.com/bobbycomet/Bot-Studio/tree/main/Plugins/community"

LICENSE_NOTE = (
    "Bot Forge Studio is licensed under GPLv3. "
    "Official plugins are developed by Comet UX Labs. "
    "Community plugins must be licensed GPLv3 or MIT."
)

# Static catalog for offline / private-repo friendliness. Expand as packages are published.
OFFICIAL_CATALOG = [
    {
        "id": "phrase_scoring",
        "title": "Phrase Scoring",
        "kind": "module-folder",
        "summary": "Dual-system phrase engine, gold, alignment (runtime + graph nodes).",
        "license": "GPLv3",
        "author": "Comet UX Labs",
        "url": OFFICIAL_URL + "/phrase_scoring",
    },
    {
        "id": "feature_graph_nodes",
        "title": "Feature Graph Nodes",
        "kind": "py-plugin",
        "summary": "Connectable Trigger/Action/Flow/Data nodes for scoring, gold, quests.",
        "license": "GPLv3",
        "author": "Comet UX Labs",
        "url": OFFICIAL_URL + "/feature_graph_nodes.py",
    },
]

COMMUNITY_CATALOG = [
    # Populated as the community repo grows
]


def all_catalog() -> list[dict]:
    out = []
    for row in OFFICIAL_CATALOG:
        out.append({**row, "channel": "official"})
    for row in COMMUNITY_CATALOG:
        out.append({**row, "channel": "community"})
    return out

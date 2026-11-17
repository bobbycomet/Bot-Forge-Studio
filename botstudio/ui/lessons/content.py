"""The guided lessons.  Each lesson is an ordered list of Steps (see engine.py); nothing here touches Qt except opening a URL.

Writing a lesson: one Step per thing the learner does.  `add_node` -> `select`/`set_prop` -> `connect` is the core loop; every
`set_prop` carries a validator from validators.py whose error message teaches *why* a value is wrong.  Steps with `when=` only
appear on a branch the learner chose.
"""
from __future__ import annotations

from PyQt6.QtCore import QUrl
from PyQt6.QtGui import QDesktopServices

from . import validators as V
from .engine import Lesson, Step

PORTAL = "https://discord.com/developers/applications"


def _open(url: str):
    return lambda win, run: QDesktopServices.openUrl(QUrl(url))


# ----------------------------------------------------------------------------------------------- computed text
def needs_text(run) -> str:
    from ...core.requirements import needs
    priv, perms = needs(run.win.graph, run.win.build_result)
    return (
        "### What *your* bot needs\n\n"
        "**Privileged intents** (switch these on under **Bot → Privileged Gateway Intents**): "
        + (", ".join(f"**{p}**" for p in priv) if priv else "none — your bot only uses default intents") + ".\n\n"
        "**Bot permissions** for the invite link: " + ", ".join(f"**{p}**" for p in perms) + ".\n\n"
        "**Scopes:** `bot` and `applications.commands` (the second one is what makes slash commands appear)."
    )


def project_files_text(run) -> str:
    res = run.win.build_result
    if res is None:
        return "Press **F5** to build, then come back."
    files = sorted(res.files)
    shown = "\n".join(f"- `{f}`" for f in files[:14]) + (f"\n- … and {len(files) - 14} more" if len(files) > 14 else "")
    return f"Your project just produced **{len(files)} files**:\n\n{shown}"


# ----------------------------------------------------------------------------------------------- shared step groups
def launch_steps(extra: str = "") -> list[Step]:
    return [
        Step("info", "What happens when you build", lambda run: (
            "**Build (F5)** compiles your graph into a real project. Studio walks every trigger, asks each node for its code, "
            "resolves the data wires into expressions, and then **syntax-checks the generated Python** so mistakes show up here, "
            "not at 3 a.m. on your server.\n\n" + project_files_text(run) + "\n\n"
            "• `bot.py` — the entry point (regenerated on every build)\n• `cogs/…` — one file per connected group of nodes\n"
            "• `bs_runtime.py` — helpers every cog shares\n• `requirements.txt` — dependencies (installed into a virtual environment on deploy)\n"
            "• `.env.example` — the settings you must provide, **the token is never stored in the project**\n"
            "• `manifest.json` — hashes that let Live Sync push only what changed\n" + extra),
             target="bottom"),
        Step("confirm", "Deploy the bot", (
            "Open the **Bot** tab (right panel).\n\n"
            "1. **Target** — *local* runs it on this machine; *ssh* installs it on a Linux server over key authentication.\n"
            "2. Press **Deploy** (F6). Studio uploads the files, creates a **virtual environment**, installs `requirements.txt`, "
            "writes your `.env` and registers a service (systemd, runit, OpenRC or a plain process).\n"
            "3. It asks for the **bot token** once; it goes into `.env` on the target with permissions `600` and is never saved in the project.\n"
            "4. Press **Start**. In the log you should see `Logged in as <your bot>` — that line means the gateway connection worked.\n\n"
            "**No token yet?** Do the next steps (Discord Developer Portal) first, then come back and deploy."),
             target="manager"),
        Step("info", "If it doesn't start", (
            "Read the log (Bot tab → Logs) and match the last line:\n\n"
            "| Message | Meaning | Fix |\n|---|---|---|\n"
            "| `DISCORD_TOKEN is not set` | the `.env` file has no token | paste the token, restart |\n"
            "| `Invalid Discord token` | wrong or **reset** token | copy a fresh one from the portal |\n"
            "| `Privileged intent … not enabled` | your nodes need an intent that is off | enable it in the portal (Bot tab) |\n"
            "| `ModuleNotFoundError` | a dependency did not install | check the install output; add it in Project settings → pip requirements |\n"
            "| commands missing in Discord | global commands take time to propagate | set a **Dev server ID** in Project settings for instant sync |\n"
            "| `Missing Access` / `Missing Permissions` | the bot lacks a permission in that channel | fix the role / channel permissions |")),
    ]


def portal_steps() -> list[Step]:
    return [
        Step("info", "Discord Developer Portal — the big picture", (
            "Everything Discord-side lives in the **Developer Portal**. You will create one *application*, give it a *bot user*, copy its "
            "*token*, decide which *privileged intents* it may use, and generate an *invite link* with the right *permissions*.\n\n"
            "These four things are easy to mix up:\n\n"
            "| | What it is | Where | Secret? |\n|---|---|---|---|\n"
            "| **Application ID / Client ID** | public name tag of your app | General Information | no |\n"
            "| **Bot token** | the password your code logs in with | Bot → Reset Token | **YES — never share it** |\n"
            "| **Gateway intents** | which *kinds of events* Discord sends your bot | Bot → Privileged Gateway Intents (+ code) | no |\n"
            "| **Permissions** | what the bot may *do* in a server (send, kick, manage roles…) | OAuth2 → URL Generator / server roles | no |\n\n"
            "The **client secret** (OAuth2 page) is a *different* secret, only used by the web dashboard's login."),
             button=("Open the Developer Portal", _open(PORTAL))),
        Step("confirm", "Create the application", (
            "1. Open the portal and press **New Application**; name it (this is the name people see).\n"
            "2. Accept the terms and press **Create**.\n"
            "3. Optional: add an icon and description under **General Information**.\n\n"
            "Copy nothing yet — the Application ID is public and Studio doesn't need it for a normal bot."),
             button=("Open the Developer Portal", _open(PORTAL))),
        Step("confirm", "Create the bot and copy its token", (
            "1. Open the **Bot** tab (every application has a bot user).\n"
            "2. Press **Reset Token**, confirm, and copy the token **once** — Discord will not show it again.\n"
            "3. Paste it into Studio's **Deploy** prompt (or `.env` as `DISCORD_TOKEN=`).\n\n"
            "### Keep it secret\n"
            "- Never paste it in chat, screenshots, issues, or a project you share.\n"
            "- Never commit `.env` to Git (Studio's generated `.gitignore`-style advice: only `.env.example` is safe to publish).\n"
            "- Never put it in browser code — anything the browser can read, everyone can.\n"
            "- If it ever leaks: **Reset Token immediately**; the old one stops working.\n\n"
            "Also on this page: *Public Bot* (let others add it) and *Requires OAuth2 Code Grant* (leave **off** for a normal bot).")),
        Step("info", "Privileged gateway intents", lambda run: (
            "A **gateway intent** is a subscription: your bot only receives the kinds of events it asked for. Three are *privileged* — "
            "you must also switch them on in the portal, and verified bots in 100+ servers need Discord's approval:\n\n"
            "- **Presence Intent** — online status, activities\n- **Server Members Intent** — member join/leave/update, full member lists\n"
            "- **Message Content Intent** — the text of messages (needed to read normal chat; slash commands do not need it)\n\n"
            "Studio switches on the matching intents *in the generated code* automatically, but the portal switch is yours. "
            "If they don't match, the bot refuses to log in with *“Privileged intent provided is not enabled”*.\n\n" + needs_text(run))),
        Step("confirm", "Set permissions and generate the invite link", lambda run: (
            "1. Open **OAuth2 → URL Generator**.\n2. Tick the scopes **bot** and **applications.commands**.\n"
            "3. Under *Bot Permissions* tick what your bot needs — keep it to the minimum (never *Administrator* “just in case”).\n"
            "4. Copy the generated URL at the bottom.\n\n" + needs_text(run) + "\n\n"
            "**Permissions vs. roles:** the invite gives the bot a role with those permissions; server admins can still restrict it per channel. "
            "A bot can only manage roles **below** its own highest role — drag the bot's role above any role it must hand out."),
             button=("Open the Developer Portal", _open(PORTAL))),
        Step("confirm", "Invite the bot to your server", (
            "1. Paste the URL into your browser.\n2. Choose your server (you need **Manage Server** there) and press **Authorise**.\n"
            "3. The bot appears **offline** in the member list until you press *Start* in Studio's Bot tab.\n\n"
            "**Testing tip:** make a private test server and put its ID in **Project → Settings → Dev server ID**. Commands then sync "
            "instantly to that server instead of taking up to an hour globally.")),
    ]


# ----------------------------------------------------------------------------------------------- lesson 1
L1 = Lesson(
    "l1", "Build Your First Discord Bot", "Beginner", 45,
    "From an empty canvas to a bot running on your server. You build **/hello** (a reply that greets the user by mention), an optional "
    "**coin flip** that teaches data wiring, and a **welcome message** driven by an event — then build, launch, and invite it.",
    outcomes=["How a Bot Forge Studio project is organised: triggers, actions, flow, data", "Commands vs events, and how each starts a flow",
              "Node properties: names, limits, placeholders, what is rejected and why", "Wiring data between nodes",
              "Build → validate → deploy → read the log", "Discord Developer Portal: application, token, intents, permissions, invite"],
    prerequisites="Nothing. No programming experience needed. You'll need a Discord account and a server you manage to test in.",
    steps=[
        Step("info", "What you will build", (
            "You will build a small but complete bot:\n\n"
            "- **/hello** — a slash *command* that replies with a greeting that mentions the person who ran it\n"
            "- an optional **/flip** command that picks Heads or Tails (to learn *data wiring*)\n"
            "- a **welcome message** sent whenever someone joins (an *event*, not a command)\n\n"
            "### The architecture in one picture\n```\nTrigger (red)  ->  Action (blue)  ->  Action ...\n   starts a flow      does something\n```\n"
            "- **Triggers** (red) start a flow: a slash command, a member joining, a message, a timer.\n"
            "- **Actions** (blue) do work: reply, send, add a role.\n- **Flow** (orange) nodes branch: *if the member has permission…*\n"
            "- **Data** (green) nodes supply values: random choices, text templates, numbers.\n\n"
            "**Execution wires** (white chevrons) say *what runs next*. **Data wires** (coloured) say *where a value comes from*.\n\n"
            "Studio turns each connected group into a real **discord.py** file you can read. Nothing is hidden.")),
        Step("info", "Your workspace", (
            "Three areas matter right now (highlighted in turn):\n\n"
            "1. **The canvas** (centre) — where the graph lives. Drag empty space to pan, scroll to zoom.\n"
            "2. **The Nodes palette** (left) — every node, grouped by colour. Search filters by name.\n"
            "3. **Properties** (right) — what you configure on the selected node.\n\n"
            "Next you'll pick a node from the palette."), target="palette"),
        Step("add_node", "Add the Slash Command trigger", (
            "A **Slash Command** is a *trigger*: it starts a flow when someone types `/yourcommand`.\n\n"
            "**Drag** the highlighted **Slash Command** entry from the palette onto the canvas (double-clicking it also works).\n\n"
            "*Why a trigger first?* A flow with no trigger never runs — Studio warns you about orphaned nodes."),
             key="slash", type_id="trigger.slash"),
        Step("select", "Select it", (
            "Click the node on the canvas. Selecting a node loads its settings in the **Properties** panel on the right.\n\n"
            "Notice the ports: the white chevron on the right (**next**) is where the flow continues; the coloured dots are *data outputs* "
            "(user, channel, guild) that later nodes can use."), key="slash"),
        Step("set_prop", "Name the command", (
            "Find the **Name** property (highlighted) and change it to **`greet`**.\n\n"
            "### Discord's rules for command names\n"
            "- only **lowercase** letters, digits, `-` and `_`\n- **no spaces**\n- **1–32** characters\n\n"
            "Discord rejects anything else when the bot starts, so Studio checks it now. Examples: `greet`, `daily-reward`, `roll_dice`.\n\n"
            "*Common mistake:* `Greet Me` — capitals and a space. Use `greet-me`."),
             key="slash", prop="name", check=V.regex(r"[a-z0-9_-]{1,32}", "Command names must be lowercase letters, digits, - or _ only, 1–32 characters, no spaces."),
             hint="Type a new name, for example greet."),
        Step("set_prop", "Describe the command", (
            "Change **Description**. This is the grey text people see in Discord's command picker, so say what the command does.\n\n"
            "Limit: **1–100 characters**. Example: `Get a friendly greeting`."),
             key="slash", prop="description", check=V.length(1, 100, "The description")),
        Step("add_node", "Add a Reply action", (
            "**Reply** answers the person who used the command. It's an *action* (blue).\n\n"
            "Drag **Reply** onto the canvas, **to the right** of the Slash Command so the flow reads left to right."),
             key="reply", type_id="action.reply"),
        Step("connect", "Connect the flow", (
            "Make the Reply run after the command:\n\n"
            "Drag from the **next** port (white chevron, right side of Slash Command) to the **in** port (left side of Reply).\n\n"
            "This is an **execution wire**. Without it Reply would never run — Studio would show *“not reachable from a trigger”*."),
             src=("slash", "next"), dst=("reply", "in")),
        Step("set_prop", "Write the reply with a placeholder", (
            "Select the Reply and edit **Content**.\n\n"
            "### Placeholders\n"
            "Text can contain `{placeholders}` that Studio fills in when the command runs:\n\n"
            "| Write | You get |\n|---|---|\n| `{user.mention}` | a clickable @mention of the person |\n| `{user.name}` | their username |\n"
            "| `{user.display_name}` | their server nickname |\n| `{guild.name}` | the server's name |\n\n"
            "Try: `Hello {user.mention}, welcome to {guild.name}!`\n\n"
            "### What you can't write\n"
            "Placeholders are **plain names with dots** — no function calls, no maths, no code. `{user.mention()}` or `{1+1}` are rejected. "
            "That restriction is deliberate: anyone can share a project file, and text fields must never be able to smuggle in code. "
            "(For real logic, Studio has the *Custom Python* node — you'll meet it in a later lesson.)\n\n"
            "*Unknown names* such as `{nope}` stay as literal text, so check your spelling."),
             key="reply", prop="content", check=V.placeholders(known=("user", "channel", "guild")),
             hint="Include {user.mention} or another placeholder."),
        Step("validate", "Check the project", (
            "Studio rebuilds as you edit. Look at the **Problems** list (bottom) — it should be empty or contain only warnings.\n\n"
            "### How to read it\n"
            "- **Errors** (red) block the build: a missing required input, a loop, invalid syntax in generated code.\n"
            "- **Warnings** (amber) are allowed but suspicious: an unconnected node, a node that can't be reached.\n"
            "- Click a problem to jump to the node that caused it.\n\n"
            "The lesson continues when the project builds with no errors."), target="bottom"),
        Step("branch", "Next: a little data wiring?", (
            "You now have a working command. Choose where to go:\n\n"
            "- **Add a coin flip** (about 8 minutes) — learn how *data* flows between nodes\n"
            "- **Skip to events** — go straight to the welcome message"),
             key="path", choices=[("coin", "Add a coin flip"), ("events", "Skip to events")]),
        # ---- coin flip branch
        Step("add_node", "Second command: /flip", (
            "Add another **Slash Command** — a new, separate flow. Place it below the first one.\n\n"
            "Each connected group becomes its own cog file; separate flows can't interfere with each other."),
             key="slash2", type_id="trigger.slash", when={"path": "coin"}),
        Step("set_prop", "Name it flip", "Set **Name** to **`flip`** (same rules as before: lowercase, no spaces).",
             key="slash2", prop="name", check=V.regex(r"flip", "For this lesson call the command exactly `flip`."), when={"path": "coin"}),
        Step("add_node", "Add a Random Choice (data) node", (
            "**Data nodes** (green) don't run in sequence — they *supply a value* when something asks for it.\n\n"
            "Drag **Random Choice** onto the canvas below the Slash Command. It picks one item from a list each time it's evaluated."),
             key="rand", type_id="data.random_choice", when={"path": "coin"}),
        Step("set_prop", "Set the choices", (
            "Edit **Items**. Separate choices with the **pipe** character `|`:\n\n`Heads|Tails`\n\n"
            "- Spaces around items are trimmed.\n- At least **two** items are needed for a choice to mean anything.\n"
            "- A pipe inside an item isn't possible — the pipe *is* the separator.\n\n"
            "Make it your own: try `Heads|Tails|Edge`."),
             key="rand", prop="items", check=V.pipe_items(2), when={"path": "coin"}),
        Step("add_node", "Add a Format node", (
            "**Format** builds a sentence from a template. Any `{name}` in the template that isn't a built-in (like `user`) "
            "becomes an **input port** you can wire.\n\nDrag **Format** onto the canvas to the right of Random Choice."),
             key="fmt", type_id="data.format", when={"path": "coin"}),
        Step("set_prop", "Write the template", (
            "Edit **Template**. Use `{side}` as a custom placeholder:\n\n`🪙 {user.mention} flipped **{side}**!`\n\n"
            "Watch the node: a new input port called **side** appears. That is how placeholders become wires.\n\n"
            "Markdown works in Discord messages: `**bold**`, `*italic*`, `` `code` ``."),
             key="fmt", prop="template", check=V.contains_all(["{side}"], "The template must contain {side} — that's the port Random Choice will plug into."),
             when={"path": "coin"}),
        Step("add_node", "Add the second Reply", "Add one more **Reply** for the coin flip, to the right of Format.",
             key="reply2", type_id="action.reply", when={"path": "coin"}),
        Step("connect", "Exec wire: command → reply", "Connect **/flip**'s **next** port to the new Reply's **in** port.",
             src=("slash2", "next"), dst=("reply2", "in"), when={"path": "coin"}),
        Step("connect", "Data wire: choice → template", (
            "Now the data wires (coloured, not white):\n\nDrag from **Random Choice → value** to **Format → side**.\n\n"
            "Read it as: *“when the template needs `side`, ask Random Choice.”*"),
             src=("rand", "value"), dst=("fmt", "side"), when={"path": "coin"}),
        Step("connect", "Data wire: sentence → reply", "Drag **Format → text** into **Reply → content**. A connected data wire overrides the typed value.",
             src=("fmt", "text"), dst=("reply2", "content"), when={"path": "coin"}),
        # ---- events
        Step("info", "Commands vs. events", (
            "So far the user *asked* for something (`/greet`). An **event** happens on its own — someone joins, leaves, posts, reacts.\n\n"
            "| Trigger | Fires when |\n|---|---|\n| Slash Command | someone runs it |\n| **Member Join** | someone joins the server |\n"
            "| Message | a message is posted *(needs the Message Content intent)* |\n| Schedule / Ready | a timer / the bot starts |\n\n"
            "Events need the right **gateway intent**. Member Join needs the **Server Members** intent — Studio switches it on in the "
            "generated code; you'll enable it in the portal later.")),
        Step("add_node", "Add the Member Join trigger", "Drag **Member Join** (red) onto the canvas, below your other flows.",
             key="join", type_id="trigger.member_join"),
        Step("add_node", "Add a Send action", (
            "**Reply** only works for commands (there's someone to reply to). A join event has no conversation, so use **Send**, "
            "which posts to a *channel you choose*."), key="send", type_id="action.send"),
        Step("connect", "Connect join → send", "Wire **next** of Member Join to **in** of Send.", src=("join", "next"), dst=("send", "in")),
        Step("set_prop", "Pick the channel", (
            "Set **Channel** to the channel's **ID** — a long number.\n\n"
            "**How to get one:** Discord → *User Settings → Advanced → Developer Mode* on, then right-click the channel → **Copy Channel ID**.\n\n"
            "- Digits only, roughly **17–20** long.\n- For the lesson you may type a stand-in like `123456789012345678`; replace it before launching.\n\n"
            "*Why not the channel name?* Names change and repeat; IDs never do."),
             key="send", prop="channel", check=V.regex(r"\d{15,20}", "A channel ID is 15–20 digits, e.g. 123456789012345678 (right-click the channel with Developer Mode on → Copy Channel ID).")),
        Step("set_prop", "Write the welcome", "Set **Content**, for example `Welcome {user.mention}! Read the rules and say hi 👋`.",
             key="send", prop="content", check=V.placeholders(known=("user", "guild", "channel"))),
        Step("validate", "Validate everything", (
            "Final check before building for real: the Problems list must show **no errors**. Warnings are fine.\n\n"
            "If you see *“Member Join needs the members intent”*-style notes, that's informational — Studio already enabled it."),
             target="bottom"),
        Step("info", "Project settings", (
            "Open **Project → Settings** and look at the fields:\n\n"
            "- **Bot name** — also names the install folder and service.\n"
            "- **Token variable** — the *name* of the environment variable holding the token (default `DISCORD_TOKEN`). Not the token itself.\n"
            "- **Dev server ID** — instant command sync for one test server.\n- **Status text** — what shows under the bot's name.\n"
            "- **Extra intents / pip requirements** — only if you add custom code that needs them.\n\n"
            "Give your bot a name now; nothing else is required.")),
    ] + launch_steps() + portal_steps() + [
        Step("confirm", "Test it in Discord", (
            "With the bot online and invited:\n\n"
            "1. Type `/` in your server and find your commands — your **description** is shown next to each name.\n"
            "2. Run **/greet** (or whatever you named it). You should get the greeting with a real mention.\n"
            "3. If you built it, run **/flip** a few times.\n"
            "4. Have a second account join the server (or use an invite from an alt) to trigger the welcome message.\n\n"
            "*Nothing shows up?* Re-read “If it doesn't start”. Most issues are a token, an intent, or a missing permission.")),
        Step("finish", "You built a Discord bot", (
            "## What you now understand\n\n"
            "- A project is **flows**: a trigger starts them, actions do the work, data nodes feed values.\n"
            "- **Commands** are asked for; **events** just happen — and events may need privileged intents.\n"
            "- Text fields take **placeholders**, never code. Properties are validated before Discord ever sees them.\n"
            "- **Build** generates real Python; **Deploy** installs it with its own virtual environment; the **token** lives only in `.env`.\n"
            "- The portal gives you an *application*, a *token* (secret), *intents* (events) and *permissions* (actions).\n\n"
            "## Where next\n"
            "- **Lesson 2** — an advanced, modular bot with persistence and error handling.\n"
            "- **Lesson 3** — game and community systems (economy, quests, bosses).\n"
            "- Load an example from **Help → Bot Wizard** and take it apart.")),
    ],
)



# ----------------------------------------------------------------------------------------------- lesson 2
PY_REPORT = '''try:
    ticket_id = await bs.kv_incr("tickets", "counter")   # atomic: two reports at once never share a number
    await bs.kv_set("tickets", f"{guild.id}:{ticket_id}", {"title": title, "details": details, "by": user.id})
except Exception:
    bs.log.exception("could not store ticket")
    ticket_id = 0'''

L2 = Lesson(
    "l2", "Advanced Discord Bot Development", "Advanced", 75,
    "Build a **support desk** the way larger bots are organised: separate flows that talk through shared storage and events, a form, "
    "custom Python with error handling, a permission-gated admin command, and a module that reacts to your events — without a tangle of nodes.",
    outcomes=["Modular architecture: flows, modules, and what each is for", "Event-driven design with the event bus (Track Progress)",
              "Data persistence with the shared key/value store, and atomic counters", "Custom Python nodes with inputs, outputs and error handling",
              "Permissions on commands and why checks belong at the edge", "Keeping a growing project readable and maintainable"],
    prerequisites="Lesson 1 (or equivalent). You should be comfortable adding nodes, setting properties and wiring.",
    steps=[
        Step("info", "The project: a support desk", (
            "Members file reports with **/report** (a form). Staff list them with **/reports**. Every report also counts toward a staff quest.\n\n"
            "```\n/report -> Form -> Custom Python (store) -> Track event -> Reply\n/reports (staff only) -> Custom Python (read) -> Reply\nQuests module  <- listens for the event\n```\n\n"
            "### Why this shape?\n"
            "- **Separate flows, shared storage.** The two commands don't call each other; they meet in the *database*. That's what keeps flows independent — "
            "you can change /reports without touching /report.\n"
            "- **Events instead of wiring.** /report announces *“a report was filed”* (an event). Anything that cares — quests today, a dashboard tomorrow — "
            "listens. /report never needs to know who.\n"
            "- **Modules for whole features.** A quest system is hundreds of lines with its own storage; you add it as one node.")),
        Step("info", "Nodes, modules, and custom code — when to use which", (
            "| Use | When |\n|---|---|\n| **Nodes** | one step of a flow: reply, spend, branch |\n"
            "| **Module** (teal/orange nodes) | a whole feature with its own commands and storage: Quests, Task Lists, Boss Fights |\n"
            "| **Custom Python** | logic no node covers — you get *all* of Python for the logic, discord.py for bot calls |\n\n"
            "**Rule of thumb:** reach for custom Python last, keep each custom node *small and single-purpose*, and give it a clear name through its "
            "inputs/outputs. Ten 6-line nodes you can read beat one 200-line node nobody dares touch.")),
        Step("add_node", "Add the /report command", "Add a **Slash Command** (the member-facing entry point).", key="t1", type_id="trigger.slash"),
        Step("set_prop", "Name it report", "Set **Name** to **`report`**.", key="t1", prop="name", check=V.regex(r"report", "Use exactly `report` for this lesson.")),
        Step("add_node", "Add a Show Form (Modal)", (
            "A **form** (Discord calls it a *modal*) pops up fields for the user. It's an *Interact* node and it **waits** for the answer — "
            "the rest of the flow runs only after they submit.\n\nAdd **Show Form (Modal)** to the right of the command."),
             key="form", type_id="ui.form"),
        Step("connect", "Connect command → form", "Wire the command's **next** to the form's **in**.", src=("t1", "next"), dst=("form", "in")),
        Step("set_prop", "Define the form fields", (
            "Edit **Fields**, one field per line:\n\n`label | id | short/long | required | placeholder | default | max length`\n\n"
            "For this lesson enter exactly two fields whose **ids** are `title` and `details`:\n\n"
            "```\nTitle | title | short | yes | What is wrong?\nDetails | details | long | yes | Tell us more\n```\n\n"
            "- `short` = one line, `long` = paragraph\n- **id** becomes an **output port** (and a variable) — pick lowercase names without spaces\n"
            "- Discord allows **at most 5 fields** per form (use *Multi-step Form* for more)\n- Only the first 3 columns are required."),
             key="form", prop="fields",
             check=V.all_of(V.form_fields(2), V.contains_all(["| title |", "| details |"], "The two field ids must be `title` and `details` (the second column)."))),
        Step("add_node", "Add a Custom Python node", (
            "This node will **store** the report. Add **Custom Python** to the right of the form.\n\n"
            "### How it runs\nYour code is the *body of an async function* inside the command's handler. That means:\n"
            "- you can use `await` anywhere\n- everything wired before the node is **in scope by name** (you'll see the list in the editor)\n"
            "- a bare `return` isn't how you pass data on — assign **output variables** instead"),
             key="py", type_id="action.python"),
        Step("connect", "Connect form → Python", "Wire the form's **next** (the “submitted” path) to the Python node's **in**.", src=("form", "next"), dst=("py", "in")),
        Step("set_prop", "Declare the data inputs", (
            "Set **Data inputs** to `title, details` (comma-separated names).\n\n"
            "Each name becomes an **input port** *and* a variable of the same name in your code. Names must be valid Python identifiers "
            "(letters, digits, underscore; not starting with a digit)."),
             key="py", prop="inputs", check=V.contains_all(["title", "details"], "List both `title` and `details`, separated by a comma.")),
        Step("set_prop", "Declare the data output", (
            "Set **Data outputs** to `ticket_id`.\n\nAn output is a variable your code *assigns*; Studio exposes it as a port so later nodes can use it. "
            "Forgetting to assign it leaves the port empty — the next step's validator checks for that."),
             key="py", prop="outputs", check=V.regex(r"\s*ticket_id\s*", "Outputs must be just `ticket_id` for this lesson.")),
        Step("connect", "Wire the title", "Drag **form → title** into **Custom Python → title**.", src=("form", "title"), dst=("py", "title")),
        Step("connect", "Wire the details", "Drag **form → details** into **Custom Python → details**.", src=("form", "details"), dst=("py", "details")),
        Step("set_prop", "Write the storage code", (
            "Select the node and press **Open in editor…** (the editor shows *what's in scope*, completes `user.` and `bs.` members, "
            "and flags undefined names as you type).\n\nType this — the lesson checks for each idea:\n\n```python\n" + PY_REPORT + "\n```\n\n"
            "### Line by line\n- `bs.kv_incr(table, key)` — **atomically** adds 1 and returns the new value. If two members report in the same millisecond, "
            "they still get different numbers. (Reading a number, adding 1 yourself, and saving it would *race*.)\n"
            "- `bs.kv_set(table, key, value)` — saves any JSON-friendly value. The key `guild.id:ticket_id` namespaces by server, so servers never mix data.\n"
            "- **try / except** — storage can fail (disk full, locked file). Without it one failure aborts the whole command and the user sees *“Something went wrong”*. "
            "With it you log the problem (`bs.log.exception` records the full traceback) and carry on.\n\n"
            "**What not to do:** blocking calls such as `time.sleep()` or `requests.get()` freeze *every* command on the bot — use `await asyncio.sleep()` / an async HTTP node."),
             key="py", prop="code",
             check=V.python_code(must_assign=("ticket_id",), need_try=True,
                                 must_contain=[("bs.kv_incr", "Use bs.kv_incr to get a unique ticket number atomically."),
                                               ("bs.kv_set", "Store the report with bs.kv_set so /reports can read it later.")],
                                 forbid=[("time.sleep", "it blocks the bot's event loop — use await asyncio.sleep()."), ("requests.", "it blocks the event loop — use an async approach.")])),
        Step("add_node", "Announce the event (Track Progress)", (
            "**Track Progress** puts an event on the **event bus**: *“something happened for this member.”* Quests (and any module that listens) count it.\n\n"
            "This is **event-driven design**: /report doesn't know quests exist. Add the node after the Python node."),
             key="trk", type_id="game.track"),
        Step("connect", "Connect Python → Track", "Wire the Python node's **next** to Track Progress's **in**.", src=("py", "next"), dst=("trk", "in")),
        Step("set_prop", "Name the event", (
            "Set **Event name** to **`report_filed`**.\n\nEvent names are your *contract*: the Quests admin picks the same name later. Use lowercase words with underscores, "
            "and keep names stable — renaming one silently breaks everything listening to it."),
             key="trk", prop="event", check=V.regex(r"report_filed", "Use exactly `report_filed` so the quest we add later can count it.")),
        Step("add_node", "Add a Format node for the confirmation", "Add **Format** so the reply can include the ticket number.", key="fmt", type_id="data.format"),
        Step("set_prop", "Template with a custom placeholder", (
            "Set **Template** to something like `🎫 Ticket **#{id}** filed — thanks {user.mention}!`.\n\n`{id}` is not built in, so it becomes an input port named **id**."),
             key="fmt", prop="template", check=V.contains_all(["{id}"], "Include {id}: it becomes the input port that receives the ticket number.")),
        Step("connect", "Ticket number into the template", "Drag **Custom Python → ticket_id** into **Format → id**.", src=("py", "ticket_id"), dst=("fmt", "id")),
        Step("add_node", "Add the Reply", "Add **Reply** after Track Progress.", key="rep", type_id="action.reply"),
        Step("connect", "Track → Reply", "Wire Track Progress's **next** to Reply's **in**.", src=("trk", "next"), dst=("rep", "in")),
        Step("connect", "Template → Reply", "Wire **Format → text** into **Reply → content**.", src=("fmt", "text"), dst=("rep", "content")),
        Step("add_node", "Second flow: /reports (staff)", "Add another **Slash Command**. This one lets staff read the reports.", key="t2", type_id="trigger.slash"),
        Step("set_prop", "Name it reports", "Set **Name** to **`reports`**.", key="t2", prop="name", check=V.regex(r"reports", "Use exactly `reports`.")),
        Step("set_prop", "Restrict who may run it", (
            "Set **Required permission** to **manage_guild** (the *Manage Server* permission).\n\n"
            "### Permissions belong at the edge\n"
            "The check happens **before any of your logic runs**, and Discord hides the command from members who lack the permission. "
            "Never rely on *“nobody knows the command exists”* — and never do the check deep inside a flow where a refactor might skip it."),
             key="t2", prop="require_permission", check=V.regex(r"manage_guild", "Choose manage_guild from the list.")),
        Step("add_node", "Add the reader (Custom Python)", "Add a second **Custom Python** node after /reports.", key="py2", type_id="action.python"),
        Step("connect", "Connect /reports → Python", "Wire **next** to **in**.", src=("t2", "next"), dst=("py2", "in")),
        Step("set_prop", "Read the stored reports", (
            "Open the editor and write code that builds a text summary into **`result`** (the default output). Use `bs.kv_top_any(\"tickets\", 50)` — "
            "it returns `(key, value)` pairs. Keys look like `<guild id>:<number>`; keep only this server's.\n\n"
            "```python\nrows = [(k, v) for k, v in await bs.kv_top_any(\"tickets\", 50)\n        if k.startswith(f\"{guild.id}:\") and isinstance(v, dict)]\n"
            "lines = [f\"#{k.split(':')[1]} — {v['title']}\" for k, v in rows[-10:]]\nresult = \"\\n\".join(lines) or \"No reports yet.\"\n```\n\n"
            "- **Filtering by guild** is the multi-server safety habit: always scope reads *and* writes.\n- `rows[-10:]` limits the output; Discord messages cap at 2000 characters.\n"
            "- You don't need `try` for a read this simple, but you may add one."),
             key="py2", prop="code", check=V.python_code(must_assign=("result",), must_contain=[("kv_top_any", "Read the stored reports with bs.kv_top_any.")])),
        Step("add_node", "Reply with the list", "Add a **Reply** after the reader.", key="rep2", type_id="action.reply"),
        Step("connect", "Reader → Reply", "Wire the reader's **next** to the Reply's **in**.", src=("py2", "next"), dst=("rep2", "in")),
        Step("connect", "Result → content", "Drag **Custom Python → result** into **Reply → content**.", src=("py2", "result"), dst=("rep2", "content")),
        Step("add_node", "Add the Quests module", (
            "**Quests** is a *module*: a full daily-quest system (storage, `/quests`, `/questsadmin`, claim buttons) as one node. "
            "It listens on the event bus — you don't wire it to anything.\n\nAdd the **Quests** module anywhere on the canvas (a spare corner is typical)."),
             key="quests", type_id="module.quests"),
        Step("info", "How modules talk to your flows", (
            "Modules and flows share three things only:\n\n"
            "1. **Storage** — the same key/value store (`bs.kv_*`) and wallet.\n"
            "2. **The event bus** — `Track Progress` / `bs.track(guild, user, event, amount)`.\n"
            "3. **Services** — named functions a module publishes (`gear.grant`, `boss.start`…) that nodes call; a missing module just logs a warning.\n\n"
            "In Discord an admin will run `/questsadmin`, pick **Custom event…**, and type `report_filed`. Staff then earn quest progress for every report — "
            "and /report never changed.")),
        Step("validate", "Validate the whole project", (
            "Everything should build. Look at the warnings too: *“not reachable from a trigger”* usually means a missing exec wire.\n\n"
            "### Maintainability checklist\n- One purpose per flow • descriptive command and event names • stored keys always include the guild id • "
            "custom code short, with `try/except` around I/O • no secrets in nodes."), target="bottom"),
        Step("info", "Error handling and performance — the habits that matter", (
            "- **Errors:** Studio's generated bot already catches command errors and answers politely; *your* `try/except` is for things you can recover from or want to log.\n"
            "- **Never block the loop** — slow code in one command delays all others.\n- **Don't poll** — use triggers and events; a background timer that queries every second wastes the database.\n"
            "- **Bound everything** — limit lists (`[-10:]`), message lengths, loops.\n- **Make writes atomic** — counters via `kv_incr`, spending via the Economy *Spend* node, never read-modify-write.\n"
            "- **Test the unhappy path** — empty form, huge text, two people at once.")),
    ] + [Step("confirm", "Deploy and test", (
            "Deploy exactly as in Lesson 1 (Bot tab → Deploy → Start; the portal steps are the same).\n\n"
            "Then in Discord: run **/report**, fill the form, check the confirmation number; run **/reports** as an admin; run **/questsadmin** → *Add quest* → "
            "*Custom event…* → `report_filed`, then file another report and look at **/quests**."), target="manager"),
         Step("finish", "A modular bot", (
            "## What you built\nTwo independent flows sharing storage, an event that other systems can hear, a module added without wiring, a permission-gated command, and custom "
            "Python with error handling.\n\n**Next:** Lesson 3 builds game and community systems on exactly these foundations.\n"))],
)



# ----------------------------------------------------------------------------------------------- lesson 3
def _two_names(v, node, run):
    names = [x.strip() for x in str(v or "").replace("\n", ",").split(",") if x.strip()]
    return None if len(names) >= 2 else "Give at least two faction names separated by commas, e.g. Sunwardens, Moonwardens."


L3 = Lesson(
    "l3", "Game and Community Bot Development", "Intermediate to Advanced", 90,
    "Build a **pocket RPG community**: factions that score with phrases, community boss fights, a gear shop with safe purchases, daily quests and profile cards — "
    "and learn how the pieces share data without stepping on each other.",
    outcomes=["Player profiles, currency, inventory and equipment as separate systems", "Why persistent data is keyed by member and server IDs",
              "Atomic purchases: how to avoid duplicate-spend and lost-update bugs", "Experience, levels, rankings and leaderboards",
              "Boss and arena sessions; matchmaking by rating", "Gacha-style randomness and rewards", "How modules cooperate (wallet, events, services)"],
    prerequisites="Lessons 1 and 2 recommended.",
    steps=[
        Step("info", "Design before dragging: the systems and their data", (
            "A game bot is **several systems that share a few things**. Decide that *before* building:\n\n"
            "| System | Owns | Reads from |\n|---|---|---|\n| Wallet (Economy) | currency per member | — |\n| Factions & Scoring | scores, levels, team | — |\n"
            "| Gear & Rarity | item copies, equipped stats | wallet (enchant costs) |\n| Boss Fights | boss HP, damage, rewards | scoring hits, gear attack |\n"
            "| Quests | daily progress | **events** from everything |\n| Cards | nothing | scores, wallet, gear (display only) |\n\n"
            "### Why this matters\n"
            "- **Each system owns its data.** The boss module never edits the inventory directly; it asks the gear *service* for a member's stats. "
            "That's why you can swap or remove a system without the others breaking.\n"
            "- **Everything is keyed by IDs**, never names: `server id : member id`. Names change; IDs don't; and the server id keeps communities apart.\n"
            "- **Persistence is the default.** Anything a player earned must survive a restart, so it lives in the database, not in a Python variable.")),
        Step("info", "Concurrency: many players, one bot", (
            "Dozens of members use the bot *at the same moment*. Three classic bugs, and how Studio's nodes avoid them:\n\n"
            "1. **Double spend** — two purchases both read “you have 100”, both succeed. *Spend Currency* checks **and** deducts in one atomic database step.\n"
            "2. **Lost update** — two counters read 5, both write 6. *Add to Counter* / `kv_incr` increments inside the database.\n"
            "3. **Torn state** — a battle half-applied after an error. Game modules write results in one transaction at the end of a session.\n\n"
            "**The rule:** never *read → compute → write* across an `await` for shared numbers. Use the atomic nodes, or wrap changes in one database call.")),
        Step("add_node", "Add Factions & Phrase Scoring", (
            "**Factions & Phrase Scoring** gives every member a **team** and a **score** — they earn points when they use their faction's trigger phrases. "
            "It also publishes the `hits` event that bosses turn into damage.\n\nDrag the module onto the canvas (left side)."),
             key="sc", type_id="module.scoring"),
        Step("set_prop", "Name your factions", (
            "Set **Starting factions** to two or more names separated by commas, for example `Sunwardens, Moonwardens`.\n\n"
            "They're created for each server the first time the bot sees it; admins rename or add more with `/faction create`. "
            "Each faction has its own phrases (`/trigger add`), emoji, colour and optional role."),
             key="sc", prop="side_names_list", check=_two_names),
        Step("add_node", "Add Gear & Rarity", (
            "**Gear & Rarity** owns *items*: templates admins define, and **instances** members own — each copy has its own rarity (normal → god) and enchant. "
            "Two *Iron Swords* can differ, which is why they're separate rows with their own IDs.\n\nAdd the module."),
             key="gear", type_id="module.gear"),
        Step("add_node", "Add Boss Fights", (
            "**Boss Fights** runs timed community bosses. It **listens** to scoring hits (event bus) and asks Gear for each fighter's attack (service). "
            "It never touches the gear tables itself."), key="boss", type_id="module.bosses"),
        Step("add_node", "Add Quests", "**Quests** counts events from every system (hits, boss damage, dungeon clears, currency earned…).", key="quest", type_id="module.quests"),
        Step("add_node", "Add Profile & Rank Cards", "**Cards** draws image profiles from scores, wallet, team and gear — it only *reads*, so it can't corrupt anything.",
             key="cards", type_id="module.cards"),
        Step("info", "One wallet for everyone", (
            "Each module node has **Wallet / currency table**, **Currency name** and **emoji** properties. They default to the same wallet (`wallet`), so quest rewards, "
            "boss rewards, arena wagers and your shop all pay from and into **one balance**.\n\nUse a *different* wallet name only for a separate currency "
            "(say `gems` for premium rewards) — and then set it on every module and node that should use it."), target="props"),
        Step("add_node", "The shop: start with a command", "Add a **Slash Command** — we'll build `/buy`.", key="buy", type_id="trigger.slash"),
        Step("set_prop", "Name it buy", "Set **Name** to **`buy`**.", key="buy", prop="name", check=V.regex(r"buy", "Use exactly `buy`.")),
        Step("add_node", "Ask for confirmation", (
            "**Confirm (Yes/No)** shows buttons and **waits**. Money should never move on one click without a *“are you sure?”*."), key="ask", type_id="ui.confirm"),
        Step("connect", "Command → confirm", "Wire **next** to **in**.", src=("buy", "next"), dst=("ask", "in")),
        Step("add_node", "Spend the currency — safely", (
            "**Spend Currency** takes money **only if the member can afford it**, in one atomic step, and branches: **paid** or **not enough**. "
            "That branch is your *double-spend protection* — you never need a separate “check balance” node (which would reintroduce the race)."),
             key="spend", type_id="eco.spend"),
        Step("connect", "Yes → spend", "Wire the confirm node's **yes** port to Spend Currency's **in**.", src=("ask", "yes"), dst=("spend", "in")),
        Step("set_prop", "Set the price", "Set **Price** to **120** or any whole number above 0.", key="spend", prop="amount",
             check=V.number_between(1, 1_000_000, "The price")),
        Step("add_node", "Hand over the item", (
            "**Give Gear** creates a *new copy* of a gear template for the buyer. It's a **service call**: if Gear & Rarity isn't installed the call logs a warning instead of crashing."),
             key="give", type_id="game.give_gear"),
        Step("connect", "Paid → give", "Wire **paid** to Give Gear's **in**.", src=("spend", "paid"), dst=("give", "in")),
        Step("set_prop", "Which item?", (
            "Set **Gear ID** to **`steel_sword`** — the id of a template you'll create later with `/gearadmin → ➕ Add Gear` (the node starts with a different example id, so change it).\n\n"
            "IDs are lowercase letters, digits and `_`. Using the *id* (not the display name) means you can rename the item without breaking the shop."),
             key="give", prop="template", check=V.regex(r"[a-z0-9_]{2,40}", "Use a lowercase id like steel_sword (letters, digits, underscore).")),
        Step("add_node", "Confirmation message", "Add a **Reply** for the successful purchase.", key="ok", type_id="action.reply"),
        Step("connect", "Give → reply", "Wire Give Gear's **next** to the Reply's **in**.", src=("give", "next"), dst=("ok", "in")),
        Step("set_prop", "Success text", "Set **Content** with a placeholder, e.g. `🛒 {user.mention} bought an Iron Sword!`.", key="ok", prop="content",
             check=V.placeholders(known=("user", "guild", "channel"))),
        Step("add_node", "…and the not-enough branch", "Add a second **Reply** for members who can't afford it.", key="no", type_id="action.reply"),
        Step("connect", "Not enough → reply", "Wire Spend's **insufficient** port to this Reply's **in**.", src=("spend", "insufficient"), dst=("no", "in")),
        Step("set_prop", "Say why", "Set **Content** to a clear message, e.g. `You need more coins for that.`", key="no", prop="content", check=V.length(3, 300, "The message")),
        Step("validate", "Validate the project", (
            "Everything should build. Notice the modules have **no wires** — they connect through the wallet, the event bus and services, which is why a game "
            "can have a dozen systems without a dozen wires."), target="bottom"),
        Step("info", "Progression: XP, levels, rankings", (
            "- **Experience** comes from scoring hits. A member's *lifetime* score decides their **level** (`level n` needs about `50·n²` points).\n"
            "- **Current** score decays slowly if you set `decay_per_day`, which is what makes *leaderboards* reflect recent activity.\n"
            "- **Rankings:** `/scoreboard` per faction and period (daily / weekly / monthly / all time); `/arena leaderboard` for duel ratings (Elo).\n"
            "- **Matchmaking:** in the Arena a member *challenges* another; Elo with a dynamic K-factor makes upsets worth more. A repeat-opponent rule reduces farming.\n"
            "- **Gacha / randomness:** the *Random Choice* and *Weighted Choice* data nodes plus *Spend Currency* and *Give Item* make a pull; store collections with the "
            "Inventory nodes. Keep odds visible to players — hidden odds feel unfair.")),
        Step("info", "Set the game up in Discord", (
            "After deploying (Lesson 1), an admin sets each system up *in Discord* — no code:\n\n"
            "1. `/faction list` then `/trigger bulk` for each faction (phrases with weights)\n2. `/gearadmin` → **Add Gear**, enchants and rarity multipliers\n"
            "3. `/boss create` → `/boss phases` → `/boss rewards` → `/boss start`\n4. `/questsadmin` for daily quests\n5. `/arenaconfig set` for timers and caps\n\n"
            "**Community tools:** reputation and automatic roles come from *levels* + the `game.set_team` / faction role; give bonus rewards with **Timed Reward**.")),
        Step("confirm", "Deploy and play-test", (
            "Deploy and invite the bot (see Lesson 1). Then test **with two accounts** — concurrency bugs only show up with two people:\n\n"
            "- both run `/buy` at the same time with exactly enough coins for one item — only one may succeed\n- type a trigger phrase and watch `/score`\n"
            "- `/boss start`, then fight it from both accounts and check `/boss leaderboard`"), target="manager"),
        Step("finish", "A connected game", (
            "## What you built\nA multi-system game where **each system owns its data**, shares a wallet, announces events, and exposes services — with a purchase flow that "
            "can't double-spend.\n\n**Next:** Lessons 4 and 5 show how to extend any of this with your own nodes and code."))
    ],
)



# ----------------------------------------------------------------------------------------------- lesson 4
def _workshop(win, run):
    from ..plugin_workshop import PluginWorkshop
    win._workshop = PluginWorkshop(win)
    win._workshop.show()


def _d20_registered(win, run):
    from ...core.model import get_spec
    spec = get_spec("custom.dice_roll")
    if spec is None:
        return "Save & load the plugin in the Workshop first — the node isn't registered yet."
    if spec.title != "Roll D20":
        return f"The node is titled “{spec.title}”. Change the second argument of NodeSpec to \"Roll D20\" and save again."
    p = spec.prop("sides")
    if p is None or int(p.default) != 20:
        return "Change the default of the `sides` property (the 4th argument of model.Prop) to 20, then save again."
    return True


def _workshop_tested(win, run):
    return True if getattr(win, "_workshop_tested", False) else "Press “Test generate” in the Workshop — it builds a tiny project with your node."


L4 = Lesson(
    "l4", "Creating and Using Custom File-Based (FS) Nodes", "Advanced", 60,
    "Extend Bot Forge Studio itself: write a **plugin node** — a Python file in the plugins folder that adds a new node to the palette — "
    "then use it in a real command. You'll define its ports, properties and the code it generates, and learn to test and debug it.",
    outcomes=["What a file-based plugin node is and how it differs from built-in nodes, modules and the Custom Python node",
              "NodeSpec: type id, kind, ports, properties, generator", "How the generator turns a node into bot code",
              "Testing and debugging a node before it reaches a bot", "Organising reusable nodes into folders and packages"],
    prerequisites="Lessons 1 and 2. You should be able to read short Python functions.",
    steps=[
        Step("info", "Three ways to extend a bot — pick the right one", (
            "| | Lives in | Best for |\n|---|---|---|\n| **Custom Python** node | your project | one-off logic in *this* flow |\n"
            "| **Module** | bundled with Studio / `.bsmod` | a whole feature with commands and storage |\n"
            "| **File-based plugin node** (this lesson) | a `.py` file in the **plugins folder** | a *reusable node* you want in every project |\n\n"
            "*File-based* means the node is defined by a file on disk, not by the project: drop the file in the plugins folder and **every** project "
            "can use the node — and you can share or version the file like any code.\n\n"
            "**Standard nodes** ship inside Studio. Plugin nodes are identical to them at run time; the difference is only *where their definition lives*.\n\n"
            "> “FS” in the course outline is read here as *file-system / file-based* nodes. If you meant something else, Lesson 5 covers Custom Python and Lesson 6 covers JavaScript.")),
        Step("info", "Anatomy of a node", (
            "A node is a **NodeSpec** — plain data plus one function:\n\n"
            "| Field | Meaning |\n|---|---|\n| `type_id` | unique id, e.g. `custom.dice_roll` (the `custom.` prefix marks plugin nodes in share warnings) |\n"
            "| `title`, `category` | name on the node, palette group |\n| `kind` | `trigger` (starts flows), `action` (runs in a flow), `flow` (branches), `data` (supplies a value) |\n"
            "| `inputs`, `outputs` | **ports**: `Port(name, \"exec\"|\"data\", label, dtype)` — exec ports carry *order*, data ports carry *values* |\n"
            "| `props` | settings in the Properties panel: `Prop(key, label, kind, default)` — kinds `str text int float bool choice any` |\n"
            "| `gen` | a function that **emits the Python** the node runs |\n\n"
            "### The generator\n`gen(ctx)` doesn't *do* the work — it **writes the code that will**, using `ctx.emit(\"…\")`. Useful helpers:\n"
            "- `ctx.prop(\"sides\")` — the property value typed by the user\n- `ctx.val(\"name\")` — an *input*: the wired data, else the typed value\n"
            "- `ctx.var(\"total\")` — a unique variable name for an output port\n- `ctx.scope` — the trigger's variables (`user`, `guild`…)\n\n"
            "**Safety rule:** put user-typed values through `repr()` / `int()` before emitting — never paste raw text into code.")),
        Step("action", "Open the Plugin Workshop", (
            "Press the button to open the **Plugin Workshop**. It shows a complete working example — *Dice Roll* — and lets you save, load and test it without leaving Studio.\n\n"
            "Read the comments in the code first. Where does the plugins folder live? **Open plugins folder** shows it.\n\n"
            "*Don't close the Workshop yet; you'll edit it next.*"),
             button=("Open the Plugin Workshop", _workshop),
             check=lambda win, run: True if getattr(win, "_workshop", None) is not None and win._workshop.isVisible() else "Press the button to open the Workshop."),
        Step("action", "Make it yours: a twenty-sided die", (
            "Edit the code in the Workshop:\n\n1. Change the **title** (2nd argument of `model.NodeSpec`) from `\"Dice Roll\"` to **`\"Roll D20\"`**.\n"
            "2. Change the default of the `sides` property (4th argument of `model.Prop`) from `6` to **`20`**.\n\n"
            "Then press **Save & load into Studio**. The Workshop will tell you if the file has a syntax error, lacks `register_nodes`, or fails to register.\n\n"
            "### Debugging tips\n- **Syntax error** → the Workshop shows the line.\n- **“register_nodes missing”** → the function name must match exactly.\n"
            "- **Node missing from the palette** → check the log under the code; a Python exception while loading is shown there.\n- Keep the same `type_id` while iterating: `replace=True` updates the node in place."),
             check=_d20_registered, hint="Edit the title and default, then Save & load."),
        Step("action", "Test the generator", (
            "A node can load fine and still produce **broken code**. Press **Test generate** in the Workshop: it builds a project containing a Slash Command wired to your node and "
            "compiles the result.\n\nYou'll see the exact line your node emitted, for example\n\n`v_ab12cd_total = __import__('random').randint(1, 20)`\n\n"
            "- A traceback means `gen` itself crashed — fix the Python in the Workshop.\n- *“generated project has errors”* means the **emitted** code is invalid — compare it to the example above."),
             check=_workshop_tested, hint="Press Test generate in the Workshop."),
        Step("add_node", "Use your node on the canvas", (
            "Your node is now in the palette under **Custom → Roll D20** (the palette entry is highlighted). It behaves exactly like a built-in node.\n\n"
            "Close the Workshop if it's in the way, then add the node."), key="die", type_id="custom.dice_roll"),
        Step("add_node", "A command to trigger it", "Add a **Slash Command** to the left of the die.", key="cmd", type_id="trigger.slash"),
        Step("set_prop", "Name it roll", "Set **Name** to **`roll`**.", key="cmd", prop="name", check=V.regex(r"roll", "Use exactly `roll`.")),
        Step("connect", "Command → die", "Wire the command's **next** to the die's **in**.", src=("cmd", "next"), dst=("die", "in")),
        Step("add_node", "Format the result", "Add **Format**: it will build the message from the die's output.", key="fmt", type_id="data.format"),
        Step("set_prop", "Template", "Set **Template** to e.g. `🎲 {user.mention} rolled a **{n}**!` — `{n}` becomes an input port.", key="fmt", prop="template",
             check=V.contains_all(["{n}"], "Include {n} so the die's output has somewhere to go.")),
        Step("connect", "Die output → template", (
            "Drag the die's data output **total** into **Format → n**.\n\nThis is the payoff of ports: you defined `Port(\"total\", \"data\")` in the plugin, "
            "and Studio now lets anyone wire it."), src=("die", "total"), dst=("fmt", "n")),
        Step("add_node", "Reply", "Add a **Reply** after the die.", key="rep", type_id="action.reply"),
        Step("connect", "Die → Reply (order)", "Wire the die's **next** to the Reply's **in**.", src=("die", "next"), dst=("rep", "in")),
        Step("connect", "Format → Reply (text)", "Wire **Format → text** into **Reply → content**.", src=("fmt", "text"), dst=("rep", "content")),
        Step("validate", "Build and look at the code", (
            "The project must build. Then open the **Generated code** tab and find your line in the `roll` cog — that's your plugin's `gen()` at work.\n\n"
            "If you later *remove* the plugin file, projects that use the node keep the node on the canvas (flagged as missing) so nothing is lost; "
            "the share dialog lists which plugins a project needs."), target="bottom"),
        Step("info", "Integrating nodes into existing projects", (
            "- Plugin nodes appear in every project; a project that uses one lists it under *required plugins* when you export a pack.\n"
            "- **Never rename a `type_id`** once projects use it — saved graphs refer to it by that string. Make a new node and deprecate the old one.\n"
            "- Need the node's code to call shared helpers? Put them in a Python module the runtime can import, or emit an `import` inside `gen`."),
             ),
        Step("info", "Organising reusable functionality", (
            "As your collection grows:\n\n"
            "1. **One file per small family** of nodes in the plugins folder (`dice.py`, `inventory_tools.py`).\n"
            "2. A **module folder** (`module.json` + `graph_nodes.py`) bundles a *module* (its own cog/commands) **and** its connectable nodes.\n"
            "3. `packaging/make_bsmod.py` zips a folder into a `.bsmod` you can send to others.\n"
            "4. **Dashboard twins:** set `spec.gen_js` (and `requires_js`) so the same node works inside Web Routes (JavaScript) too.\n\n"
            "### Testing checklist\n- Test generate with default values • with weird values (0, empty, huge) • in a flow where the output is unused • with the node wired twice.")),
        Step("finish", "You extended Bot Forge Studio", (
            "## What you built\nA reusable plugin node: a `NodeSpec` with ports and properties, a generator that emits safe code, tested in the Workshop and used in a real command.\n\n"
            "**Next:** Lesson 5 shows the lighter-weight alternative — the Custom Python node — and when it's the better tool.")),
    ],
)

# ----------------------------------------------------------------------------------------------- lesson 5
PY_STATS = '''import statistics

try:
    values = [float(x) for x in numbers.replace(",", " ").split()]
    mean = statistics.mean(values)
    summary = f"{user.display_name}: n={len(values)}, mean={mean:.2f}, uses so far={add_to_counter_value}"
except (ValueError, statistics.StatisticsError):
    mean = 0
    summary = "Send some numbers, like `3 5 8`."'''

L5 = Lesson(
    "l5", "Creating and Using Custom Python Nodes", "Advanced", 55,
    "Write real Python inside a flow: a **/stats** command that reads numbers, uses another node's output, handles bad input, and returns several values. "
    "You'll learn exactly what the Custom Python environment gives you — and where its boundaries are.",
    outcomes=["How Custom Python is executed (an async function body inside the handler)", "Inputs, outputs and the scope of connected nodes",
              "Imports, functions and reusable code", "Error handling that keeps a command alive", "Debugging with the editor's Problems and the logs",
              "What the node cannot or should not do"],
    prerequisites="Lesson 1. Basic Python helps (variables, if, functions); Lesson 2 is useful but not required.",
    steps=[
        Step("info", "What a Custom Python node really is", (
            "Studio compiles your flow into one `async def` handler. **Your code is pasted into that function**, at the point where the node sits. Consequences:\n\n"
            "- You can use **`await`** directly (call async discord.py or `bs.*` helpers).\n- Everything wired *before* the node is **in scope by name**.\n"
            "- Assign your **output variables**; they become ports. `return` isn't how you hand data on.\n- Indentation is yours: the code is re-indented for you, so write it at column 0.\n\n"
            "```\n[trigger vars]  [earlier nodes' outputs]  [your inputs]\n          \\          |          /\n         your Python  ->  outputs (ports)\n```\n"
            "**Rule of thumb:** *all* of Python works for the **logic** (loops, classes, `statistics`, `json`, `re`, `datetime`…); **discord.py** is for anything that talks to Discord.")),
        Step("add_node", "A command that takes input", "Add a **Slash Command**.", key="cmd", type_id="trigger.slash"),
        Step("set_prop", "Name it stats", "Set **Name** to **`stats`**.", key="cmd", prop="name", check=V.regex(r"stats", "Use exactly `stats`.")),
        Step("set_prop", "Declare a parameter", (
            "Set **Options** (the `params` property) to **`numbers:str`**.\n\n"
            "### The options syntax\n`name:type`, comma-separated. Add `=default` for an optional value or `?` for optional-without-default:\n\n"
            "| Write | Means |\n|---|---|\n| `numbers:str` | required text |\n| `count:int=5` | optional integer, 5 if omitted |\n| `target:user` | a member picker |\n| `note:str?` | optional text |\n\n"
            "Types: `str int float bool user channel role attachment`. The name becomes an **output port** of the command *and* a variable in later custom code."),
             key="cmd", prop="params", check=V.regex(r"\s*numbers\s*:\s*str\s*", "Enter exactly `numbers:str` for this lesson.")),
        Step("add_node", "A node whose output we'll reuse", (
            "Add **Add to Counter** (Storage). It counts how many times each member used the command and **outputs the new count** — a value your Python can read later. "
            "That's *interacting with other nodes*."), key="incr", type_id="store.incr"),
        Step("connect", "Command → counter", "Wire the command's **next** to the counter's **in**.", src=("cmd", "next"), dst=("incr", "in")),
        Step("add_node", "Add the Custom Python node", "Add **Custom Python** after the counter.", key="py", type_id="action.python"),
        Step("connect", "Counter → Python", "Wire the counter's **next** to the Python node's **in**.", src=("incr", "next"), dst=("py", "in")),
        Step("set_prop", "Inputs: pass the numbers in", (
            "Set **Data inputs** to **`numbers`**, then wire the command's **numbers** output into it (next step).\n\n"
            "You don't *have* to declare inputs to read trigger variables — `numbers` is already in scope because the command provides it — "
            "but declaring it makes the dependency **visible on the canvas** and lets you feed the node from *any* source."),
             key="py", prop="inputs", check=V.regex(r"\s*numbers\s*", "Enter exactly `numbers`.")),
        Step("set_prop", "Outputs: two values", (
            "Set **Data outputs** to **`mean, summary`**.\n\nTwo output ports, two variables your code must assign. Output names follow Python identifier rules."),
             key="py", prop="outputs", check=V.contains_all(["mean", "summary"], "List both `mean` and `summary`, comma-separated.")),
        Step("connect", "Wire the input", "Drag **Slash Command → numbers** into **Custom Python → numbers**.", src=("cmd", "numbers"), dst=("py", "numbers")),
        Step("set_prop", "Write the code", (
            "Open the editor. Press **Ctrl+Space** anywhere: it offers `user.`, `bs.`, your variables, and the standard library (`statistics.` lists its functions). "
            "The right-hand list shows **what's in scope** — including `add_to_counter_value`, the counter's output, named after its node.\n\nType:\n\n```python\n" + PY_STATS + "\n```\n\n"
            "### Syntax and rules\n- **Imports** at the top are fine for any installed package; for packages that aren't installed, add them in *Project → Settings → pip requirements* — otherwise the bot fails to start with `ModuleNotFoundError`.\n"
            "- **Functions** are allowed: define them in the node and call them. To share one function between nodes, put it in a *plugin node* (Lesson 4) or a module.\n"
            "- **Error handling:** `float()` raises `ValueError` on `abc`; `statistics.mean([])` raises `StatisticsError`. Without `try/except` either one ends the command with *“Something went wrong”*. "
            "Catch the **specific** exceptions you expect, not a bare `except:`.\n"
            "- **Both outputs must always be assigned** — notice the `except` branch sets `mean` *and* `summary`, so later nodes never see an empty port."),
             key="py", prop="code",
             check=V.python_code(must_assign=("mean", "summary"), need_try=True,
                                 must_contain=[("import statistics", "Import the statistics module (`import statistics`) — it's part of Python's standard library."),
                                               ("add_to_counter_value", "Use the counter's output `add_to_counter_value` so the summary shows how often the member used the command.")],
                                 forbid=[("time.sleep", "it blocks the whole bot. Use `await asyncio.sleep(...)` if you must wait."),
                                         ("input(", "there is no keyboard — the bot can't ask a terminal for input."),
                                         ("os.system", "running shell commands from a command handler is a security risk.")])),
        Step("add_node", "Show the result", "Add a **Reply** after the Python node.", key="rep", type_id="action.reply"),
        Step("connect", "Python → Reply (order)", "Wire the Python node's **next** to the Reply's **in**.", src=("py", "next"), dst=("rep", "in")),
        Step("connect", "Summary → content", "Drag **Custom Python → summary** into **Reply → content**.", src=("py", "summary"), dst=("rep", "content")),
        Step("validate", "Validate", (
            "The project must build. In the editor, the **Problems** list flags syntax errors *and* names that nothing provides (*“undefined name 'x'”*) — "
            "usually a typo or a node that isn't wired before this one."), target="bottom"),
        Step("info", "Boundaries: what the node can't or shouldn't do", (
            "| Don't | Why | Instead |\n|---|---|---|\n| `time.sleep`, `requests.get`, heavy loops | they **freeze every command** (one event loop) | `await asyncio.sleep`, an HTTP node, `asyncio.to_thread` |\n"
            "| read/write the token or `.env` | credentials must stay out of flows | use Project settings |\n| rely on globals between runs | each run is independent; the bot may restart | use `bs.kv_*` storage |\n"
            "| `exec`/`eval` of user text | code injection | never |\n| assume the node runs once | many members run the command *at the same time* | atomic helpers (`kv_incr`) |\n\n"
            "**Execution rules:** the node runs top to bottom in the handler; an exception stops the flow *after* it (the user gets the generic error message and the traceback is in the log); "
            "variables you don't declare as outputs stay local to that handler.")),
        Step("info", "Debugging and testing", (
            "1. **Problems list** in the editor — fix syntax and undefined names before building.\n2. **Generated code** tab — find your lines inside the cog to see exactly what runs, and with which aliases.\n"
            "3. **`bs.log.info(\"value: %s\", x)`** — prints to the bot log (Bot tab → Logs). `bs.log.exception(\"…\")` inside `except` logs the full traceback.\n"
            "4. **Test the unhappy inputs** in Discord: `abc`, an empty string, `1e999`, 500 numbers.\n5. Keep each node short; split long logic into several nodes so errors point at one of them.\n\n"
            "### Integrating into an existing bot\nDrop the node between any two others: wire what it needs *in*, assign what later nodes need *out*. Because inputs/outputs are explicit ports, the node can be moved, copied "
            "and reused without editing its code.")),
        Step("confirm", "Deploy and try it", (
            "Deploy as in Lesson 1, then run `/stats numbers: 3 5 8`, `/stats numbers: abc` (the error branch), and run it again to watch the counter in the summary grow."), target="manager"),
        Step("finish", "Custom code, safely", (
            "## What you built\nA custom node with declared inputs and outputs that reads another node's output, handles bad input, and logs for debugging.\n\n"
            "**Next:** Lesson 6 takes the bot to the web with a live dashboard.")),
    ],
)



# ----------------------------------------------------------------------------------------------- lesson 6
def _webtest(win, run):
    from ..web_test import WebTestDialog
    win._webtest = WebTestDialog(win)
    win._webtest.show()


HTML_L6 = '''<main class="wrap">
  <h1>Server visits</h1>
  <section class="card">
    <p class="big" id="count">…</p>
    <p class="muted" id="live">Waiting for live updates…</p>
  </section>
</main>'''
CSS_L6 = '''.wrap { max-width: 520px; margin: 0 auto; padding: 24px 16px; }
.card { background: var(--panel); border: 1px solid var(--raised); border-radius: 8px; padding: 20px; text-align: center; }
.big { font-size: 48px; margin: 0; color: var(--accent); }
@media (max-width: 480px) { .big { font-size: 36px; } }'''
JS_L6 = '''const count = document.getElementById('count');
const live = document.getElementById('live');

async function load() {
  const data = await BotStudio.api('/api/custom/stats');
  count.textContent = data.visits;
}

BotStudio.live((msg) => {
  if (msg.type === 'event' && msg.event === 'visit') {
    count.textContent = msg.data.visits;
    live.textContent = 'Updated live';
  }
});

load().catch((err) => { count.textContent = '—'; live.textContent = err.message; });'''

L6 = Lesson(
    "l6", "Building a Web Dashboard", "Advanced", 80,
    "Build a working web dashboard for your bot: an **HTML page** styled with **CSS**, driven by **browser JavaScript**, talking to **API routes** on the dashboard server, "
    "updating live over a **WebSocket**, and sending actions back to the bot — with the security model explained at every step. You test it all locally, without Discord.",
    outcomes=["Frontend / backend / bot: three environments and how they talk", "HTML structure, CSS layout and vanilla JavaScript in the browser",
              "API endpoints (Web Routes) and HTTP requests with fetch", "WebSockets for live updates (and when HTTP is enough)",
              "Authentication, authorization and what must never reach the browser", "Dashboard → bot and bot → dashboard communication",
              "Testing locally with the Web test console"],
    prerequisites="Lessons 1 and 2. Basic HTML/CSS/JavaScript helps but every line is explained.",
    steps=[
        Step("info", "What you will build", (
            "A **visits dashboard**: a page showing a counter that a *Web Route* increments, updating **live** on every open page, plus an action that tells the bot something happened.\n\n"
            "```\nBrowser (HTML + CSS + JavaScript)\n     |  HTTP  fetch('/api/…')        WebSocket /ws (live)\n     v                                  ^\nDashboard server (Node + Express)  — login, validation, routes\n     |  shared database + files          ^\n     v                                  |\nDiscord bot (discord.py)  — commands, events, game logic\n```\n"
            "**The one rule:** the browser **never** talks to the bot or to Discord. It talks to *your server's* validated endpoints; the server talks to the bot. "
            "That separation is what keeps your token safe.")),
        Step("info", "Three environments, three node families", (
            "Bot Forge Studio keeps them apart on purpose — they run in *different programs* with different powers:\n\n"
            "| Environment | Language | Nodes | Can do | Cannot do |\n|---|---|---|---|---|\n"
            "| **Discord bot** | Python (discord.py) | Trigger / Action / Flow / Data / Game … | talk to Discord, store data | show a web page |\n"
            "| **Dashboard server** | Node.js (Express) | **Web Route**, **Server JavaScript**, Respond JSON, Broadcast … | check logins, read the database, call Discord's REST API | open a gateway connection, run in a browser |\n"
            "| **Browser** | JavaScript, HTML, CSS | **Web**: HTML Page, CSS Stylesheet, Browser JavaScript | draw the page, react to clicks, `fetch`, WebSocket | read secrets, call discord.js, use `require()` |\n\n"
            "*Server JavaScript* and *Browser JavaScript* are **different nodes** because they are different worlds — the editor's tip bar always says which one you are in.")),
        Step("branch", "Which backend?", (
            "The dashboard backend can be built two ways in general:\n\n"
            "- **Node.js + Express** — *built into Bot Forge Studio*; everything in this lesson works today.\n"
            "- **Python (FastAPI / Flask)** next to a discord.py bot — a valid architecture, **not generated by Studio yet**.\n\n"
            "Pick the one you want to *learn about*; the build in this lesson uses Node either way."),
             key="backend", choices=[("node", "Node.js + Express (build it)"), ("python", "Python backend (explain it)")]),
        Step("info", "The Python-backend equivalent", (
            "Studio's generated dashboard is Node, but the **architecture is identical** with a Python backend. Mapping:\n\n"
            "| This lesson (Node) | Python equivalent |\n|---|---|\n| Express routes (`/api/custom/…`) | FastAPI `@app.get(\"/api/…\")` or Flask routes |\n"
            "| `ws` WebSocket server | FastAPI WebSockets, or `aiohttp` (already installed with discord.py) |\n| session cookie + OAuth login | `authlib` / `itsdangerous` signed cookies |\n"
            "| shared SQLite `bot.db` | the same file via `aiosqlite` |\n| `Send Event to Bot` (kv table) | a direct function call, since the API and the bot share one process |\n\n"
            "**What stays the same:** the browser code (HTML/CSS/JS) doesn't change at all — it only knows URLs. We'll build the Node version now, "
            "so every concept below transfers directly."), when={"backend": "python"}),
        Step("add_node", "Add the Web Dashboard module", (
            "The **Web Dashboard** module hosts the Express server: Discord login, a server picker, built-in pages, and **your** pages and routes. "
            "The bot starts and supervises it as a child process — one thing to deploy.\n\nAdd it anywhere (a spare corner is fine)."), key="dash", type_id="module.dashboard"),
        Step("set_prop", "Title it", "Set **Dashboard title** to your own name (blank titles aren't useful). Max 60 characters; it appears in the header and the browser tab.", key="dash", prop="title",
             check=V.length(3, 60, "The title")),
        Step("add_node", "Add a Web Route (an API endpoint)", (
            "A **Web Route** is an *endpoint*: an address the browser can request, plus the flow that answers. It runs on the **dashboard server** (Node), not in the bot.\n\n"
            "Add a **Web Route** in the web area (below the dashboard module)."), key="rt", type_id="web.route"),
        Step("set_prop", "Choose its path", (
            "Set **Path** to **`/api/custom/stats`**.\n\n### Path rules\n- must start with **`/api/custom`** — your routes live in their own namespace and can't shadow the built-in `/api/me`, `/auth/…`\n"
            "- letters, digits, `-` `_` `.`; `:name` makes a URL parameter (`/api/custom/user/:id`)\n\nLeave **Method** at GET (reading data) and **Require dashboard login** on — "
            "an unauthenticated request gets **401** before your flow runs. Use POST/PUT/DELETE for *changes*; Studio's server refuses cross-site state-changing requests."),
             key="rt", prop="path", check=V.all_of(V.route_path, V.regex(r"/api/custom/stats", "Use /api/custom/stats for this lesson."))),
        Step("add_node", "Count a visit (storage)", "Add **Add to Counter** after the route. It increments a number in the shared database and outputs the new value.", key="vi", type_id="store.incr"),
        Step("connect", "Route → counter", "Wire the route's **next** to the counter's **in**.", src=("rt", "next"), dst=("vi", "in")),
        Step("set_prop", "Counter table", (
            "Set **Table** (namespace) to **`visits`**. Key stays `{guild_id}`-style per server — the route provides **`guild_id`** from the request's `?guild_id=` (which the dashboard adds for you).\n\n"
            "Table names: letters, digits, `_` `:` `-`, up to 64."),
             key="vi", prop="namespace", check=V.regex(r"[A-Za-z0-9_:-]{1,64}", "Letters, digits, _ : - only, up to 64 characters.")),
        Step("set_prop", "Counter key", "Set **Key** to **`{guild_id}`**, so each server has its own counter. Placeholders work in the dashboard exactly as in the bot.", key="vi", prop="key",
             check=V.contains_all(["{guild_id}"], "Use {guild_id} so servers never share a counter.")),
        Step("add_node", "Build the response object", (
            "**Build JSON Object**: each field name becomes an input port. A JSON object is how the server hands structured data to the browser."), key="ob", type_id="data.object"),
        Step("set_prop", "Field names", "Set **Field names** to **`visits`**.", key="ob", prop="keys", check=V.regex(r"\s*visits\s*", "Use exactly `visits`.")),
        Step("connect", "Counter → field", "Drag **Add to Counter → value** into **Build JSON Object → visits**.", src=("vi", "value"), dst=("ob", "visits")),
        Step("add_node", "Broadcast a live update", (
            "**Broadcast Live Update** pushes an event over the **WebSocket** to every page watching this server — that's how pages update *without refreshing or polling*."),
             key="bc", type_id="web.broadcast"),
        Step("set_prop", "Name the event", "Set **Event name** to **`visit`**. The browser will listen for events with this name.", key="bc", prop="event",
             check=V.regex(r"visit", "Use exactly `visit`.")),
        Step("connect", "Counter → broadcast", "Wire the counter's **next** to Broadcast's **in**.", src=("vi", "next"), dst=("bc", "in")),
        Step("connect", "Object → broadcast data", "Wire **Build JSON Object → json** into **Broadcast → data**.", src=("ob", "json"), dst=("bc", "data")),
        Step("add_node", "Answer the request", "**Respond JSON** sends the HTTP answer. Add it after the broadcast.", key="rj", type_id="web.json"),
        Step("connect", "Broadcast → respond", "Wire Broadcast's **next** to Respond JSON's **in**.", src=("bc", "next"), dst=("rj", "in")),
        Step("connect", "Object → respond data", "Wire **Build JSON Object → json** into **Respond JSON → data**. (A data output can feed several inputs.)", src=("ob", "json"), dst=("rj", "data")),
        Step("add_node", "Frontend: the HTML Page", (
            "Now the **browser side**. An **HTML Page** node is a page of your dashboard, served at `/site/<name>.html` behind the dashboard login.\n\nAdd it."), key="page", type_id="web.html"),
        Step("set_prop", "Page file name", "Set **File name** to **`visits`** (letters, digits, dashes). The page will be at `/site/visits.html`.", key="page", prop="slug",
             check=V.regex(r"[a-z0-9][a-z0-9-]{0,40}", "Lowercase letters, digits and dashes only."), allow_default=False),
        Step("set_prop", "Write the HTML", (
            "Open the editor and write the **body** (Studio adds `<html>`, `<head>`, the CSS and JavaScript links):\n\n```html\n" + HTML_L6 + "\n```\n\n"
            "### Structure\n- **Semantic tags** (`<main>`, `<section>`, `<h1>`) describe meaning; screen readers and search rely on them.\n"
            "- **`id`** makes an element findable from JavaScript; ids must be **unique** (the editor flags duplicates).\n- **`class`** hooks styling.\n\n"
            "### Not allowed (and why)\nInline `<script>`, `onclick=…`, `style=…` and external CDNs are **blocked by the dashboard's Content-Security-Policy**. "
            "That policy is a safety net: even if an attacker injected markup, it couldn't run their script. Use the CSS and JavaScript *nodes* instead."),
             key="page", prop="code", check=V.html_page(must_contain=[('id="count"', "Add an element with id=\"count\" — the script will write the number into it.")])),
        Step("add_node", "Style it: CSS Stylesheet", "Add a **CSS Stylesheet** node. Use several (layout, theme, components) if you like — they attach in order.", key="css", type_id="web.css"),
        Step("set_prop", "Write the CSS", (
            "Open the editor:\n\n```css\n" + CSS_L6 + "\n```\n\n"
            "- **Selectors** target elements: `.card` (class), `#count` (id), `main h1` (descendant).\n- **Custom properties** like `var(--panel)` reuse the dashboard theme.\n"
            "- **Responsive design:** `@media (max-width: 480px)` changes the layout on phones — design for narrow screens first.\n"
            "- Flexbox/Grid, animations and transitions all work; **external fonts and `@import` URLs are blocked** (same policy). A missing `;` or `}` is flagged as you type."),
             key="css", prop="code", check=V.css_code(must_contain=[(".card", "Style the .card element from the HTML."), ("@media", "Add an @media rule so the page works on phones.")])),
        Step("add_node", "Behaviour: Browser JavaScript", (
            "**Browser JavaScript** runs in the *visitor's browser*. It is **not** discord.js and **not** the dashboard server. Add it."), key="js", type_id="web.js"),
        Step("set_prop", "Write the script", (
            "Open the editor:\n\n```javascript\n" + JS_L6 + "\n```\n\n"
            "### What each part does\n- `BotStudio.api(path)` — a `fetch` that sends your session cookie and **throws an Error with the server's message** on any non-2xx answer (so a 401 shows *“not logged in”* instead of crashing).\n"
            "- `textContent = …` — puts text into the page **safely**. `innerHTML` with data from users is an XSS hole (the editor warns).\n"
            "- `BotStudio.live(handler)` — opens a **WebSocket** to `/ws`, subscribes to the server you picked and **reconnects automatically**.\n- `async/await` — the page stays responsive while waiting for the network.\n\n"
            "**Not available here:** `require()`, `process`, discord.js, other websites (blocked by `connect-src 'self'`), and any secret. The editor says *which* when you try."),
             key="js", prop="code", check=V.browser_js(must_contain=[("/api/custom/stats", "Call your route: BotStudio.api('/api/custom/stats')."), ("textContent", "Write the number with textContent."),
                                                                     ("BotStudio.live", "Listen for live updates with BotStudio.live(…).")])),
        Step("connect", "Attach the CSS to the page", "Drag **CSS Stylesheet → attach to page** into **HTML Page → CSS / JS**. Order of attachment = order of loading.", src=("css", "attach"), dst=("page", "assets")),
        Step("connect", "Attach the script to the page", "Drag **Browser JavaScript → attach to page** into the same **CSS / JS** input. A page can take several of each.", src=("js", "attach"), dst=("page", "assets")),
        Step("validate", "Validate the web project", (
            "Studio checks all three web languages at build time: unclosed HTML tags, CSS syntax, browser-JS syntax, **forbidden features** (inline scripts, `require`, external requests) and — importantly — "
            "**secrets** (a string shaped like a Discord token is an *error* in frontend code).\n\nErrors look like `site/visits.html:7: …` — file and line."), target="bottom"),
        Step("info", "HTTP or WebSocket?", (
            "| Use **HTTP** (`fetch`) when… | Use a **WebSocket** when… |\n|---|---|\n| the page *asks* for data once (load, submit, refresh) | the server needs to *tell* the page something unprompted |\n"
            "| the result is the answer to one request | updates arrive often: scores, active games, moderation feed, bot status |\n| you want caching, simple errors, easy testing | many pages watch the same thing — one push beats many polls |\n\n"
            "In your dashboard: loading the counter is **HTTP**; the `visit` event is a **WebSocket** message. The server also sends **bot status** every 3 seconds to every subscribed page.\n\n"
            "**WebSocket security:** the upgrade is accepted only with a valid **session cookie** and a **same-origin** `Origin` header (blocks cross-site WebSocket hijacking); a client can only subscribe to servers it manages; "
            "messages are size-limited and rate-limited. On HTTPS the browser uses `wss://` automatically.")),
        Step("action", "Test it locally — no bot, no Discord", (
            "Open the **Web test console**. It builds your dashboard and runs it in **test mode**: a *fake Discord* (so there's no token or OAuth), automatic login, loopback address only. "
            "**Your bot is not started.**\n\n"
            "1. Press **Build & start** (the first run installs the web packages; needs Node.js 22.13+ and a network once).\n2. Press **Open in browser**, then the *Open /site/visits.html* button.\n"
            "3. Open **API console**: `GET /api/custom/stats?guild_id=123456789012345678` → see the JSON and the status code. Try without `?guild_id=` and read the error.\n"
            "4. Open the page in **two** browser tabs; reload one repeatedly — the other updates **live**.\n5. **Live events** tab: push an event and watch the page react.\n6. **HTTP requests** and **WebSocket** tabs show every request and message.\n\n"
            "*Edit → Rebuild (hot reload)* updates pages, routes and config without restarting."),
             button=("Open the Web test console", _webtest),
             check=lambda win, run: True if getattr(win, "_webtest_started", False) else "Open the console and press Build & start."),
        Step("confirm", "Check the live behaviour", (
            "Before moving on, confirm in the test console that: the counter shows a number, **two tabs update together**, a pushed live event reaches the page, and a request without login "
            "(open the API URL in a *private window*) is answered **401**.\n\n**Debugging checklist:** browser **DevTools → Console** shows JavaScript errors and CSP violations; **Network** shows each request and its status; "
            "the console's *Server log* shows route errors (a route that throws answers with an error, never a stack trace).")),
        Step("add_node", "Dashboard → bot: a second route", (
            "How does a dashboard make the **bot** do something? **Never directly.** The route validates and authorises, then queues an *event*; the bot picks it up. Add a second **Web Route**."),
             key="rt2", type_id="web.route"),
        Step("set_prop", "POST /api/custom/ping", "Set **Path** to **`/api/custom/ping`**, and **Method** to **POST** (this one *changes* something). Use the choice list.", key="rt2", prop="method",
             check=V.regex(r"POST", "Choose POST.")),
        Step("set_prop", "Path of the second route", "Set **Path** to **`/api/custom/ping`**.", key="rt2", prop="path", check=V.all_of(V.route_path, V.regex(r"/api/custom/ping", "Use /api/custom/ping."))),
        Step("add_node", "Send Event to Bot", (
            "**Send Event to Bot** writes the event into the shared database. The bot's listener picks it up within about two seconds and deletes it first, so an event can **never run twice**."),
             key="eb", type_id="web.emit_bot"),
        Step("connect", "Route → Send Event", "Wire the second route's **next** to **Send Event to Bot → in**.", src=("rt2", "next"), dst=("eb", "in")),
        Step("add_node", "…and the bot side: On Dashboard Event", "Now a **bot** trigger (Python world): **On Dashboard Event**. Add it near your other bot flows.", key="de", type_id="trigger.dashboard_event"),
        Step("add_node", "Do something when it arrives", "Add a **Log** action (in the bot). In a real bot this could be a role change or a channel message.", key="lg", type_id="action.log"),
        Step("connect", "Event → log", "Wire the event trigger's **next** to Log's **in**.", src=("de", "next"), dst=("lg", "in")),
        Step("info", "Security: what the dashboard enforces — and what is on you", (
            "**Enforced by the generated server**\n- Bot token stays on the server (read from `.env`); the browser never receives it.\n- Session cookie is **HttpOnly**, **SameSite=Lax**, and **Secure** on HTTPS.\n"
            "- **Discord OAuth2 login**; only members with **Manage Server** in a server that has the bot see it (plus an optional allow-list of user IDs).\n"
            "- State-changing requests must come from **your own origin** (CSRF/CORS) and are **rate-limited** per session.\n- A strict **Content-Security-Policy** (scripts, styles, connections only from your site) and `Referrer-Policy: same-origin`.\n"
            "- WebSocket: session + origin checks, per-server subscription checks, size and rate limits.\n- Pages can require login; routes can require login, *server admin* or *bot owner*.\n\n"
            "**Your responsibility**\n- **Authorise every action** in the route (set *Caller must manage the server*, or check `user` in your flow) — logged in ≠ allowed.\n"
            "- **Validate input** (type, length, allowed values) before storing or acting; never build code or SQL from request data.\n- **Never put secrets in HTML/CSS/browser JS** — the build refuses token-shaped strings, but not your passwords.\n"
            "- Put **HTTPS** in front when the dashboard is public (a reverse proxy such as Caddy or nginx), and set `DASHBOARD_URL` to the public address.\n"
            "- Keep routes **cheap**: there is no per-route rate limiter beyond the write limit.\n\n"
            "**The dashboard is never a trusted environment** just because it belongs to your project. Treat every request as coming from a stranger until the route says otherwise.")),
        Step("validate", "Validate again", "Everything — web pages, routes and the bot trigger — must build without errors.", target="bottom"),
        Step("confirm", "Launch with the real Discord login", (
            "To use the dashboard for real (not test mode):\n\n1. In the **Developer Portal → OAuth2**, copy the **Client Secret** (a different secret from the bot token — keep it private) into `DISCORD_CLIENT_SECRET` in `.env`.\n"
            "2. Add **`<DASHBOARD_URL>/auth/callback`** (default `http://localhost:3000/auth/callback`) under **OAuth2 → Redirects**.\n3. Set `DASHBOARD_URL`, `DASHBOARD_HOST` (127.0.0.1 locally; 0.0.0.0 behind a proxy) and `DASHBOARD_PORT`.\n"
            "4. **Deploy** (Bot tab). The deploy installs a private Node.js if the server has none, runs `npm install`, and the bot starts the dashboard.\n5. Open the URL, log in with Discord, and open your page from the menu."), target="manager"),
        Step("finish", "A dashboard you understand", (
            "## What you built\n- A page (**HTML + CSS + browser JavaScript**) served behind login.\n- A **Web Route** answering `fetch`, with storage and a **live WebSocket** broadcast.\n"
            "- A validated, queued path from the dashboard to the **bot**, and the test console to debug it all offline.\n\n"
            "## Where next\n- Replace the counter with player data from your RPG (`Get Faction Score`, the Economy tables).\n- Add a **Push to Dashboard** action in the bot for live game events.\n"
            "- Use **Server JavaScript** nodes when a route needs logic the nodes don't cover (still server-side, still validated).")),
    ],
)

LESSONS: list = [L1, L2, L3, L4, L5, L6]

"""Interactive UI nodes: buttons, dropdowns, forms (modals), confirm dialogs, paged and tabbed menus,
multi-step wizards and persistent panels.

Everything here compiles to calls into ``bs_runtime`` (see the "Interactive UI helpers" section).  The
"Ask" nodes pause the flow until the user answers, then continue with the fresh interaction — so a
slash command can read like a conversation:  Slash Command -> Show Form -> Reply with the answers.
"""
from __future__ import annotations

import re

from .codegen import GenError, IDENT
from .model import NodeSpec, Prop, register
from .nodes import D, EX, _msg_ports, _msg_props

STYLES = ("primary", "secondary", "success", "danger")


# ----------------------------------------------------------------------------- text mini-languages

def rows(text, width: int = 0) -> list[list[str]]:
    """'a | b | c' per line -> [['a','b','c']].  Lines starting with # or // are comments."""
    out = []
    for ln in str(text or "").splitlines():
        if not ln.strip() or ln.strip().startswith(("#", "//")):
            continue
        cells = [c.strip() for c in ln.split("|")]
        out.append((cells + [""] * width)[:max(width, len(cells))] if width else cells)
    return out


def key_of(text: str, fallback: str) -> str:
    k = re.sub(r"[^a-z0-9]+", "_", str(text or "").lower()).strip("_")
    if not k or not k[0].isalpha():
        k = fallback
    return k[:32]


def field_rows(text) -> list[list[str]]:
    """Form fields:  label | id | short/long | required | placeholder | default | max length"""
    out, seen = [], set()
    for i, r in enumerate(rows(text, 7)):
        r = r[:7]
        k = key_of(r[1] or r[0], f"field{i + 1}")
        while k in seen:
            k += "_2"
        seen.add(k)
        r[1] = k
        out.append(r)
    return out[:5]


def sections(text) -> list[tuple[str, list[str]]]:
    """'## Title' headings followed by body lines -> [(title, [lines])]."""
    out: list[tuple[str, list[str]]] = []
    for ln in str(text or "").splitlines():
        if ln.startswith("##"):
            out.append((ln.lstrip("#").strip(), []))
        elif out:
            out[-1][1].append(ln)
    return out


def wizard_steps(text) -> list[tuple[str, list[list[str]]]]:
    steps, seen = [], set()
    for t, body in sections(text):
        fields = []
        for i, r in enumerate(rows("\n".join(body), 7)):
            r = r[:7]
            k = key_of(r[1] or r[0], f"field{i + 1}")
            while k in seen:
                k += "_2"
            seen.add(k)
            r[1] = k
            fields.append(r)
        if fields:
            steps.append((t or f"Step {len(steps) + 1}", fields[:5]))
    return steps[:10]


def _need_interaction(ctx, what: str) -> bool:
    if ctx.has("interaction"):
        return True
    ctx.warn(f"{what} needs a Slash Command or Component trigger (it answers an interaction)")
    ctx.emit("pass")
    return False


def _rebind(ctx, var: str):
    ctx.emit(f"interaction = {var}")


# ----------------------------------------------------------------------------- Ask: Buttons

def gen_ask_buttons(ctx):
    if not _need_interaction(ctx, "Ask Buttons"):
        return
    btns = [(r + [""] * 4)[:4] for r in rows(ctx.prop("buttons"))]
    if not btns:
        raise GenError("Ask Buttons needs at least one button (label | id | style | emoji)")
    for b in btns:
        b[1] = b[1] or key_of(b[0], "choice")
    v = ctx.var
    ctx.emit(f"{v('choice')}, {v('label')}, {v('interaction')} = await bs.ask_buttons(interaction, {ctx.val('content')}, "
             f"{ctx.embed_expr()}, {btns!r}, only_user={bool(ctx.prop('only_user'))}, timeout={float(ctx.prop('timeout') or 60)!r}, "
             f"ephemeral={bool(ctx.prop('ephemeral'))})")
    ctx.emit(f"if {v('interaction')} is None:")
    with ctx.block("timeout"):
        pass
    ctx.emit("else:")
    with ctx.block():
        _rebind(ctx, v("interaction"))
        ctx.chain("next")


register(NodeSpec(
    "ui.ask_buttons", "Ask: Buttons", "Interact", "flow",
    inputs=[EX("in")] + _msg_ports(),
    outputs=[EX("next", "answered"), EX("timeout"), D("choice", "str"), D("label", "str"), D("interaction", "interaction")],
    props=_msg_props() + [
        Prop("buttons", "Buttons (one per line)", "text", "Yes | yes | success | ✅\nNo | no | danger | ❌",
             help="label | id | style | emoji.  Styles: primary secondary success danger."),
        Prop("only_user", "Only the person who ran it can click", "bool", True),
        Prop("timeout", "Wait (seconds)", "float", 60),
        Prop("ephemeral", "Only visible to the user", "bool", False)],
    gen=gen_ask_buttons,
    description="Shows buttons and waits for a click. 'choice' is the clicked button's id; 'timeout' runs if nobody clicks."))


# ----------------------------------------------------------------------------- Ask: Dropdown

def _opt_rows(text) -> list[list[str]]:
    out = []
    for r in rows(text, 4):
        r = r[:4]
        r[1] = r[1] or r[0]
        out.append(r)
    return out[:25]


def gen_ask_menu(ctx):
    if not _need_interaction(ctx, "Ask Dropdown"):
        return
    opts = _opt_rows(ctx.prop("options"))
    if not opts:
        raise GenError("Ask Dropdown needs at least one option (label | value | description | emoji)")
    v = ctx.var
    ctx.emit(f"{v('values')}, {v('interaction')} = await bs.ask_menu(interaction, {ctx.val('content')}, {ctx.embed_expr()}, "
             f"{opts!r}, placeholder={str(ctx.prop('placeholder') or 'Choose…')!r}, min_values={int(ctx.prop('min_values') or 1)}, "
             f"max_values={int(ctx.prop('max_values') or 1)}, only_user={bool(ctx.prop('only_user'))}, "
             f"timeout={float(ctx.prop('timeout') or 60)!r}, ephemeral={bool(ctx.prop('ephemeral'))})")
    ctx.emit(f"{v('value')} = {v('values')}[0] if {v('values')} else None")
    ctx.emit(f"if {v('interaction')} is None:")
    with ctx.block("timeout"):
        pass
    ctx.emit("else:")
    with ctx.block():
        _rebind(ctx, v("interaction"))
        ctx.chain("next")


register(NodeSpec(
    "ui.ask_menu", "Ask: Dropdown", "Interact", "flow",
    inputs=[EX("in")] + _msg_ports(),
    outputs=[EX("next", "answered"), EX("timeout"), D("value", "str"), D("values"), D("interaction", "interaction")],
    props=_msg_props() + [
        Prop("options", "Options (one per line)", "text", "Red | red | The red team | 🔴\nBlue | blue | The blue team | 🔵",
             help="label | value | description | emoji  (max 25)"),
        Prop("placeholder", "Placeholder", "str", "Choose…"),
        Prop("min_values", "Minimum choices", "int", 1), Prop("max_values", "Maximum choices", "int", 1),
        Prop("only_user", "Only the person who ran it can answer", "bool", True),
        Prop("timeout", "Wait (seconds)", "float", 60),
        Prop("ephemeral", "Only visible to the user", "bool", False)],
    gen=gen_ask_menu,
    description="Shows a dropdown and waits for a choice. 'value' is the first pick, 'values' is the whole list."))


# ----------------------------------------------------------------------------- Confirm

def gen_confirm(ctx):
    if not _need_interaction(ctx, "Confirm"):
        return
    v = ctx.var
    yes, no = str(ctx.prop("yes_label") or "Yes"), str(ctx.prop("no_label") or "No")
    ctx.emit(f"{v('choice')}, _unused, {v('interaction')} = await bs.ask_buttons(interaction, {ctx.val('content')}, "
             f"{ctx.embed_expr()}, [({yes!r}, 'yes', 'success', ''), ({no!r}, 'no', 'danger', '')], "
             f"only_user={bool(ctx.prop('only_user'))}, timeout={float(ctx.prop('timeout') or 30)!r}, "
             f"ephemeral={bool(ctx.prop('ephemeral'))})")
    ctx.emit(f"if {v('interaction')} is not None:")
    with ctx.block():
        _rebind(ctx, v("interaction"))
        ctx.emit(f"if {v('choice')} == 'yes':")
        with ctx.block("yes"):
            pass
        ctx.emit("else:")
        with ctx.block("no"):
            pass
    if ctx.g.outgoing(ctx.node.id, "timeout"):
        ctx.emit("else:")
        with ctx.block("timeout"):
            pass


register(NodeSpec(
    "ui.confirm", "Confirm (Yes / No)", "Interact", "flow",
    inputs=[EX("in")] + _msg_ports(),
    outputs=[EX("yes"), EX("no"), EX("timeout"), D("interaction", "interaction")],
    props=_msg_props() + [
        Prop("yes_label", "Yes button", "str", "Yes"), Prop("no_label", "No button", "str", "No"),
        Prop("only_user", "Only the person who ran it can answer", "bool", True),
        Prop("timeout", "Wait (seconds)", "float", 30),
        Prop("ephemeral", "Only visible to the user", "bool", False)],
    gen=gen_confirm, description="Asks 'are you sure?' and branches on the answer."))


# ----------------------------------------------------------------------------- Forms

def _form_ports(props):
    return [], [D(r[1]) for r in field_rows(props.get("fields", ""))] + [D("interaction", "interaction")]


def gen_form(ctx):
    if not _need_interaction(ctx, "Show Form"):
        return
    fields = field_rows(ctx.prop("fields"))
    if not fields:
        raise GenError("Show Form needs at least one field (label | id | short/long | required | placeholder | default | max)")
    v = ctx.var
    ctx.emit(f"_form, {v('interaction')} = await bs.ask_form(interaction, {str(ctx.prop('title') or 'Form')!r}, {fields!r})")
    ctx.emit(f"if _form is None:")
    with ctx.block("cancel"):
        pass
    ctx.emit("else:")
    with ctx.block():
        for r in fields:
            ctx.emit(f"{v(r[1])} = _form.get({r[1]!r}, '')")
        _rebind(ctx, v("interaction"))
        ctx.chain("next")


register(NodeSpec(
    "ui.form", "Show Form (Modal)", "Interact", "flow",
    inputs=[EX("in")], outputs=[EX("next", "submitted"), EX("cancel")],
    props=[Prop("title", "Form title", "str", "Tell us about you"),
           Prop("fields", "Fields (one per line, max 5)", "text",
                "Name | name | short | yes | Your name\nAbout you | about | long | no | A few words",
                help="label | id | short/long | required | placeholder | default | max length.  Each id becomes an output.")],
    dynamic_ports=_form_ports, gen=gen_form,
    description="Pops up a form. Every field becomes an output you can wire; 'cancel' runs if the form is closed."))


def _wizard_ports(props):
    outs = []
    for _t, fields in wizard_steps(props.get("steps", "")):
        outs += [D(f[1]) for f in fields]
    return [], outs + [D("interaction", "interaction")]


def gen_wizard(ctx):
    if not _need_interaction(ctx, "Multi-step Form"):
        return
    steps = wizard_steps(ctx.prop("steps"))
    if not steps:
        raise GenError("Multi-step Form needs steps: a '## Title' line, then field rows underneath")
    v = ctx.var
    ctx.emit(f"_wiz, {v('interaction')} = await bs.wizard(interaction, {steps!r})")
    ctx.emit("if _wiz is None:")
    with ctx.block("cancel"):
        pass
    ctx.emit("else:")
    with ctx.block():
        for _t, fields in steps:
            for f in fields:
                ctx.emit(f"{v(f[1])} = _wiz.get({f[1]!r}, '')")
        _rebind(ctx, v("interaction"))
        ctx.chain("next")


register(NodeSpec(
    "ui.wizard", "Multi-step Form", "Interact", "flow",
    inputs=[EX("in")], outputs=[EX("next", "finished"), EX("cancel")],
    props=[Prop("steps", "Steps", "text",
                "## About you\nName | name | short | yes\nAge | age | short | no\n## Preferences\nFavourite colour | colour | short | yes\nWhy | why | long | no",
                help="'## Step title' then one field per line (label | id | short/long | required | placeholder | default | max). "
                     "Up to 5 fields per step — Discord's modal limit — and any number of steps.")],
    dynamic_ports=_wizard_ports, gen=gen_wizard,
    description="A form with several pages (chained modals). Every field of every step becomes an output."))


# ----------------------------------------------------------------------------- Paged / tabbed menus

def gen_paged(ctx):
    if not _need_interaction(ctx, "Paged Message"):
        return
    ctx.emit(f"{ctx.var('message')} = await bs.paginate(interaction, bs.split_pages({str(ctx.prop('pages') or '')!r}), "
             f"{ctx.val('title')}, {str(ctx.prop('color') or '5865F2')!r}, only_user={bool(ctx.prop('only_user'))}, "
             f"timeout={float(ctx.prop('timeout') or 180)!r}, ephemeral={bool(ctx.prop('ephemeral'))})")


register(NodeSpec(
    "ui.paged", "Paged Message", "Interact", "action",
    inputs=[EX("in"), D("title", "str")], outputs=[EX(), D("message", "message")],
    props=[Prop("title", "Title", "str", "Guide", fmt=True, port=True),
           Prop("pages", "Pages (separate with a line of ---)", "text", "Welcome to the guide.\n---\nPage two goes here.\n---\nPage three."),
           Prop("color", "Colour", "str", "5865F2"),
           Prop("only_user", "Only the person who ran it can flip pages", "bool", False),
           Prop("timeout", "Buttons stay active (seconds)", "float", 180),
           Prop("ephemeral", "Only visible to the user", "bool", False)],
    gen=gen_paged, description="An embed with first / previous / next / last buttons."))


def gen_tabs(ctx):
    if not _need_interaction(ctx, "Tabbed Menu"):
        return
    tabs = [[t, "\n".join(b).strip(), "", "", ""] for t, b in sections(ctx.prop("tabs"))]
    if not tabs:
        raise GenError("Tabbed Menu needs sections: a '## Title' line, then the text under it")
    ctx.emit(f"{ctx.var('message')} = await bs.tabbed_menu(interaction, {tabs[:25]!r}, {str(ctx.prop('color') or '5865F2')!r}, "
             f"only_user={bool(ctx.prop('only_user'))}, timeout={float(ctx.prop('timeout') or 300)!r}, "
             f"ephemeral={bool(ctx.prop('ephemeral'))})")


register(NodeSpec(
    "ui.tabs", "Tabbed Menu", "Interact", "action",
    inputs=[EX("in")], outputs=[EX(), D("message", "message")],
    props=[Prop("tabs", "Sections", "text", "## Rules\nBe kind.\n## Roles\nPick a role in #roles.\n## Help\nAsk a moderator.",
                help="'## Title' starts a section; the lines under it are its text. A dropdown switches between them."),
           Prop("color", "Colour", "str", "5865F2"),
           Prop("only_user", "Only the person who ran it can switch", "bool", False),
           Prop("timeout", "Menu stays active (seconds)", "float", 300),
           Prop("ephemeral", "Only visible to the user", "bool", False)],
    gen=gen_tabs, description="One message with a dropdown that switches between several embeds."))


# ----------------------------------------------------------------------------- Persistent panels

def gen_panel(ctx):
    if ctx.is_blank("channel"):
        target = ctx.scope.get("channel", "None")
    else:
        target = ctx.val("channel")
    btns = [(r + [""] * 4)[:4] for r in rows(ctx.prop("buttons"))]
    for b in btns:
        b[1] = b[1] or key_of(b[0], "button")
    menu = "None"
    opts = _opt_rows(ctx.prop("menu_options"))
    mid = str(ctx.prop("menu_id") or "").strip()
    if opts and mid:
        menu = repr((mid, str(ctx.prop("menu_placeholder") or "Choose…"), opts,
                     int(ctx.prop("menu_min") or 1), int(ctx.prop("menu_max") or 1)))
    ctx.emit(f"{ctx.var('sent')} = await bs.post_panel(self.bot, {target}, {ctx.val('content')}, {ctx.embed_expr()}, "
             f"{btns!r}, {menu})")


register(NodeSpec(
    "ui.panel", "Post Panel", "Interact", "action",
    inputs=[EX("in"), D("channel", "channel")] + _msg_ports(), outputs=[EX(), D("sent", "message")],
    props=[Prop("channel", "Channel ID (blank = current)", "str", "")] + _msg_props() + [
        Prop("buttons", "Buttons (one per line)", "text", "Open ticket | ticket_open | primary | 🎫",
             help="label | id | style | emoji.  Each id is what 'On Component Used' listens for. Panels survive restarts."),
        Prop("menu_id", "Dropdown id (blank = no dropdown)", "str", ""),
        Prop("menu_placeholder", "Dropdown placeholder", "str", "Choose…"),
        Prop("menu_options", "Dropdown options", "text", "", help="label | value | description | emoji"),
        Prop("menu_min", "Dropdown minimum", "int", 1), Prop("menu_max", "Dropdown maximum", "int", 1)],
    gen=gen_panel,
    description="Posts a message with permanent buttons / a dropdown. Handle them with 'On Component Used'."))


def gen_component(ctx):
    cid = str(ctx.prop("custom_id") or "").strip()
    if not cid:
        raise GenError("On Component Used needs the id of a button or dropdown")
    ctx.caps |= {"interaction", "guild_ctx"}
    ctx.outs = {"user": "interaction.user", "channel": "interaction.channel", "guild": "interaction.guild",
                "interaction": "interaction", "message": "interaction.message", "custom_id": "custom_id",
                "values": "values", "value": "value"}
    ctx.scope = ctx.outs
    ctx.decorators = ['@commands.Cog.listener("on_interaction")']
    ctx.signature = "self, interaction: discord.Interaction"
    kind = str(ctx.prop("kind") or "any")
    p = ctx.preamble
    p.append("if interaction.type != discord.InteractionType.component: return")
    p.append("custom_id = (interaction.data or {}).get('custom_id', '')")
    p.append(f"if not custom_id.startswith({cid[:-1]!r}): return" if cid.endswith("*") else f"if custom_id != {cid!r}: return")
    if kind == "button":
        p.append("if (interaction.data or {}).get('component_type') != 2: return")
    elif kind == "dropdown":
        p.append("if (interaction.data or {}).get('component_type') not in (3, 5, 6, 7, 8): return")
    p.append("values = list((interaction.data or {}).get('values', []))")
    p.append("value = values[0] if values else None")
    if ctx.prop("guild_only"):
        p.append("if interaction.guild is None: return")
    perm = str(ctx.prop("require_permission") or "none")
    if perm != "none":
        p.append(f"if not bs.has_perm(interaction.user, {perm!r}):")
        p.append("    await bs.respond(interaction, \"You can't use this.\", ephemeral=True); return")


register(NodeSpec(
    "trigger.component", "On Component Used", "Trigger", "trigger",
    outputs=[EX(), D("user", "user"), D("channel", "channel"), D("guild", "guild"), D("message", "message"),
             D("custom_id", "str"), D("value", "str"), D("values")],
    props=[Prop("custom_id", "Component id", "str", "ticket_open", help="End with * to match a prefix, e.g. shop_*"),
           Prop("kind", "Kind", "choice", "any", choices=("any", "button", "dropdown")),
           Prop("guild_only", "Server only", "bool", True),
           Prop("require_permission", "Required permission", "choice", "none",
                choices=("none", "manage_guild", "administrator", "manage_messages", "manage_roles", "manage_channels",
                         "kick_members", "ban_members", "moderate_members"))],
    gen=gen_component,
    description="Runs when someone presses a button or picks from a dropdown posted by 'Post Panel'. "
                "Reply with Reply, or swap the message with 'Update Panel Message'."))


def gen_edit_panel(ctx):
    if not ctx.has("interaction"):
        ctx.warn("Update Panel Message needs a Component trigger")
        ctx.emit("pass")
        return
    content, emb = ctx.val("content"), ctx.embed_expr()
    ctx.emit(f"await interaction.response.edit_message(content={content} or None, embed={emb}"
             + (", view=None" if ctx.prop("remove_components") else "") + ")")


register(NodeSpec(
    "ui.edit_panel", "Update Panel Message", "Interact", "action",
    inputs=[EX("in")] + _msg_ports(), outputs=[EX()],
    props=_msg_props() + [Prop("remove_components", "Remove the buttons / dropdown", "bool", False)],
    gen=gen_edit_panel, description="Edits the message a component was clicked on (answers the interaction)."))

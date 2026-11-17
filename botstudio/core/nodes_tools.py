"""Community tool nodes: private ticket channels, channel deletion and reaction polls."""
from __future__ import annotations

from .codegen import GenError
from .model import NodeSpec, Prop, register
from .nodes import D, EX


def _need_guild(ctx) -> str:
    g = ctx.scope.get("guild")
    if not g:
        raise GenError(f"{ctx.node.spec.title} [{ctx.node.id}]: needs a trigger that provides a server (Slash Command, Component, Member Join …)")
    return g


def gen_create_ticket(ctx):
    guild = _need_guild(ctx)
    user = ctx.val("user", ctx.default_user() or "None")
    v = ctx.var
    ctx.emit(f"{v('channel')} = await bs.create_private_channel({guild}, {ctx.val('name')}, {user}, {ctx.val('staff_role')}, {ctx.val('category_id')})")
    ctx.emit(f"{v('ok')} = {v('channel')} is not None")
    ctx.emit(f"{v('mention')} = {v('channel')}.mention if {v('channel')} is not None else ''")


register(NodeSpec(
    "action.create_ticket", "Create Private Channel", "Action", "action",
    inputs=[EX("in"), D("user", "user"), D("name", "str")], outputs=[EX(), D("channel", "channel"), D("mention", "str"), D("ok", "bool")],
    props=[Prop("name", "Channel name", "str", "ticket-{user.name}", fmt=True, port=True,
                help="Lowercased and cleaned automatically (letters, digits, dashes)."),
           Prop("staff_role", "Staff role (name or ID, blank = none)", "str", "", help="This role can also see and write in the channel."),
           Prop("category_id", "Category ID (blank = top level)", "str", "")],
    gen=gen_create_ticket,
    description="Creates a text channel only the member, the staff role and the bot can see — the core of a ticket system. "
                "Needs the Manage Channels permission. 'channel' feeds Send / Delete Channel, 'mention' is a clickable #link, 'ok' is false when Discord refused."))


def gen_delete_channel(ctx):
    edge = ctx.g.incoming(ctx.node.id, "channel")
    target = ctx.expr_of(edge.src, edge.src_port) if edge else ctx.scope.get("channel", "None")
    ctx.emit(f"{ctx.var('ok')} = await bs.delete_channel({target})")


register(NodeSpec(
    "action.delete_channel", "Delete Channel", "Action", "action",
    inputs=[EX("in"), D("channel", "channel")], outputs=[EX(), D("ok", "bool")],
    gen=gen_delete_channel,
    description="Deletes a channel (the one the flow runs in, unless you wire another). Needs Manage Channels. Permanent — confirm first."))


def gen_poll(ctx):
    if not ctx.has("interaction"):
        ctx.warn("Create Poll needs a Slash Command trigger")
        ctx.emit("pass")
        return
    ctx.emit(f"{ctx.var('message')} = await bs.poll(interaction, {ctx.val('question')}, {ctx.val('options')}, {bool(ctx.prop('ephemeral'))})")


register(NodeSpec(
    "action.poll", "Create Poll", "Action", "action",
    inputs=[EX("in"), D("question", "str"), D("options", "str")], outputs=[EX(), D("message", "message")],
    props=[Prop("question", "Question", "str", "Pizza tonight?", fmt=True, port=True),
           Prop("options", "Options (2–10, separated by |)", "str", "Yes|No|Maybe", port=True),
           Prop("ephemeral", "Only visible to the user", "bool", False)],
    gen=gen_poll,
    description="Posts a reaction poll with numbered options (needs the Add Reactions permission)."))

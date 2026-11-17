"""Frontend files for the Web Dashboard (served from dashboard/web/public by server.js).

Plain HTML/CSS/JS with no build step and no external requests (the server's CSP only allows its
own files and Discord avatars). All dynamic text goes through ``textContent`` — never innerHTML.
The page layout comes from ``/api/config`` (built from the graph), so changing the bot and pressing
Live Sync updates the dashboard without a restart.
"""

INDEX_HTML = '''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__BS_TITLE__</title>
<link rel="stylesheet" href="/style.css">
<script src="/app.js" defer></script>
</head>
<body>
<div id="app"><p class="muted center">Loading…</p></div>
<noscript>This dashboard needs JavaScript.</noscript>
</body>
</html>
'''

STYLE_CSS = '''/* Griffin Dark — same palette as Bot Forge Studio */
:root { --bg:#171a21; --panel:#1b2838; --raised:#2a475e; --canvas:#10161f; --text:#c7d5e0; --muted:#8f98a0;
        --accent:#66c0f4; --good:#5ba32b; --bad:#d9534f; --warn:#f0ad4e; }
* { box-sizing: border-box; }
html, body { margin: 0; height: 100%; }
body { background: var(--bg); color: var(--text); font: 14px/1.5 "Noto Sans", "Segoe UI", Ubuntu, sans-serif; }
a { color: var(--accent); }
.muted { color: var(--muted); }
.center { text-align: center; margin-top: 20vh; }
.shell { display: grid; grid-template-columns: 230px 1fr; grid-template-rows: 52px 1fr; height: 100%; }
header { grid-column: 1 / 3; display: flex; align-items: center; gap: 12px; padding: 0 16px; background: var(--panel);
         border-bottom: 1px solid var(--raised); }
header h1 { font-size: 16px; margin: 0; color: #fff; flex: 1; }
header img { width: 28px; height: 28px; border-radius: 50%; }
nav { background: var(--panel); border-right: 1px solid var(--raised); padding: 12px 8px; overflow-y: auto; }
nav button { display: block; width: 100%; text-align: left; margin: 2px 0; padding: 8px 12px; border: 0; border-radius: 3px;
             background: transparent; color: var(--text); cursor: pointer; font: inherit; }
nav button:hover { background: var(--raised); }
nav button.active { background: var(--raised); color: #fff; box-shadow: inset 3px 0 0 var(--accent); }
main { padding: 20px 24px; overflow: auto; }
main h2 { margin-top: 0; color: #fff; }
select, input, textarea { background: var(--canvas); color: var(--text); border: 1px solid var(--raised); border-radius: 3px;
                          padding: 6px 8px; font: inherit; }
textarea { width: 100%; min-height: 110px; resize: vertical; }
select:focus, input:focus, textarea:focus { outline: none; border-color: var(--accent); }
button.btn { background: var(--raised); color: var(--text); border: 1px solid #3d6c8d; border-radius: 3px; padding: 6px 14px;
             cursor: pointer; font: inherit; }
button.btn:hover { background: #35607d; }
button.primary { background: linear-gradient(#1a9fff, #0f7bc7); border-color: var(--accent); color: #fff; }
button.danger { background: #7a2d2b; border-color: var(--bad); }
.cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(170px, 1fr)); gap: 12px; }
.card { background: var(--panel); border: 1px solid var(--raised); border-radius: 4px; padding: 14px; }
.card .n { font-size: 24px; color: #fff; }
table { border-collapse: collapse; width: 100%; background: var(--panel); border: 1px solid var(--raised); }
th, td { padding: 7px 10px; border-bottom: 1px solid var(--raised); text-align: left; vertical-align: top; word-break: break-word; }
th { color: var(--accent); font-weight: 600; }
.row { display: flex; gap: 8px; align-items: center; margin: 10px 0; flex-wrap: wrap; }
.msg { padding: 8px 12px; border-radius: 3px; margin: 10px 0; }
.msg.ok { background: #1f3a1a; border: 1px solid var(--good); }
.msg.err { background: #3a1f1f; border: 1px solid var(--bad); }
.login { max-width: 380px; margin: 18vh auto; text-align: center; background: var(--panel); border: 1px solid var(--raised);
         border-radius: 6px; padding: 28px; }
.login h1 { color: #fff; margin-top: 0; }
.login a.btn { display: inline-block; text-decoration: none; margin-top: 12px; }
pre { background: var(--canvas); border: 1px solid var(--raised); padding: 10px; overflow: auto; border-radius: 3px; }
@media (max-width: 720px) { .shell { grid-template-columns: 1fr; grid-template-rows: 52px auto 1fr; }
  header { grid-column: 1; } nav { display: flex; overflow-x: auto; border-right: 0; } nav button { width: auto; white-space: nowrap; } }
'''

APP_JS = r'''(() => {
'use strict';
const root = document.getElementById('app');
const state = { cfg: null, me: null, guild: null, page: null };

function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (k === 'class') el.className = v;
    else if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
    else if (v !== false && v !== null && v !== undefined) el.setAttribute(k, v === true ? '' : v);
  }
  for (const kid of kids.flat()) {
    if (kid === null || kid === undefined || kid === false) continue;
    el.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  }
  return el;
}

async function api(path, opts = {}) {
  const init = { credentials: 'same-origin', ...opts };
  if (opts.body !== undefined) { init.headers = { 'Content-Type': 'application/json' }; init.body = JSON.stringify(opts.body); }
  const r = await fetch(path, init);
  let data = null;
  try { data = await r.json(); } catch (_) { /* empty body */ }
  if (!r.ok) throw new Error((data && data.error) || `HTTP ${r.status}`);
  return data;
}

const gpath = (suffix) => `/api/guilds/${state.guild}${suffix}`;
const tpath = (page, suffix) => (page.scope === 'global' ? `/api/owner${suffix}` : gpath(suffix));
const note = (kind, text) => h('div', { class: `msg ${kind}` }, text);

function renderLogin(error) {
  const msgs = { login_failed: 'Login failed. Please try again.', not_allowed: 'This dashboard is restricted to specific users.' };
  root.replaceChildren(h('div', { class: 'login' },
    h('h1', {}, state.cfg.title),
    h('p', { class: 'muted' }, `Sign in with Discord to manage ${state.cfg.botName}.`),
    error ? note('err', msgs[error] || 'Something went wrong.') : null,
    state.cfg.loginReady
      ? h('a', { class: 'btn primary', href: '/auth/login' }, 'Log in with Discord')
      : note('err', 'Login is not configured yet: the bot owner must set DISCORD_CLIENT_SECRET.')));
}

async function renderApp() {
  state.me = await api('/api/me');
  const saved = localStorage.getItem('bs_guild');
  state.guild = state.me.guilds.find((g) => g.id === saved)?.id ?? state.me.guilds[0]?.id ?? null;
  if (!state.page) state.page = state.cfg.pages[0]?.id ?? null;
  draw();
}

function draw() {
  const me = state.me;
  const sel = h('select', { onchange: (e) => { state.guild = e.target.value; localStorage.setItem('bs_guild', state.guild); draw(); } },
    me.guilds.map((g) => h('option', { value: g.id, selected: g.id === state.guild }, g.name)));
  const nav = h('nav', {}, state.cfg.pages.map((p) => h('button', {
    class: p.id === state.page ? 'active' : '', onclick: () => { state.page = p.id; draw(); },
  }, p.title)));
  const header = h('header', {},
    h('h1', {}, state.cfg.title),
    me.guilds.length ? sel : null,
    h('img', { src: me.user.avatar_url, alt: '' }),
    h('span', {}, me.user.global_name || me.user.username),
    h('button', { class: 'btn', onclick: async () => { await api('/auth/logout', { method: 'POST', body: {} }); location.href = '/'; } }, 'Log out'));
  const main = h('main', {});
  root.replaceChildren(h('div', { class: 'shell' }, header, nav, main));
  if (!state.guild) {
    main.append(h('h2', {}, 'No servers yet'),
      h('p', {}, 'You do not manage any server that has this bot. Add it to one:'),
      ...me.invites.map((g) => h('p', {}, h('a', { href: g.url, target: '_blank', rel: 'noopener' }, `Invite to ${g.name}`))));
    return;
  }
  const page = state.cfg.pages.find((p) => p.id === state.page);
  if (page) renderPage(main, page).catch((e) => main.append(note('err', e.message)));
}

async function renderPage(main, page) {
  main.append(h('h2', {}, page.title));
  const fn = { overview, commands, leaderboard, kv, announce, api: apiPage, text, site }[page.type];
  if (fn) await fn(main, page);
}

async function overview(main) {
  const o = await api(gpath('/overview'));
  const up = o.bot.uptime_s ?? 0;
  const card = (label, n) => h('div', { class: 'card' }, h('div', { class: 'muted' }, label), h('div', { class: 'n' }, n));
  main.append(h('div', { class: 'cards' },
    card('Members', o.member_count.toLocaleString()), card('Channels', o.channel_count), card('Roles', o.role_count),
    card('Boosts', o.boosts), card('Bot latency', o.bot.ping === null ? '—' : `${o.bot.ping} ms`),
    card('Bot uptime', o.bot.uptime_s === null ? '—' : `${Math.floor(up / 3600)}h ${Math.floor((up % 3600) / 60)}m`),
    card('Bot servers', o.bot.servers), card('Bot status', o.bot.online ? 'online' : 'offline')));
}

async function commands(main) {
  const list = state.cfg.commands || [];
  main.append(list.length ? table(['Command', 'What it does', 'Options', 'Needs'],
    list.map((c) => [`/${c.name}`, c.description, c.params, c.permission])) : h('p', { class: 'muted' }, 'This bot has no slash commands.'));
}

function table(cols, rows) {
  return h('table', {}, h('tr', {}, cols.map((c) => h('th', {}, c))),
    rows.map((r) => h('tr', {}, r.map((c) => h('td', {}, c)))));
}

async function leaderboard(main, page) {
  const rows = await api(tpath(page, `/leaderboard?ns=${encodeURIComponent(page.arg)}`));
  main.append(rows.length ? table(['#', 'Member', 'Value'], rows.map((r) => [r.rank, r.name, r.value]))
    : h('p', { class: 'muted' }, 'Nothing here yet.'));
}

async function kv(main, page) {
  const ns = encodeURIComponent(page.arg);
  const box = h('div', {});
  main.append(box);
  const load = async () => {
    const rows = await api(tpath(page, `/kv?ns=${ns}`));
    const key = h('input', { placeholder: 'key' });
    const val = h('input', { placeholder: 'value (JSON or text)', size: 36 });
    const out = h('div', {});
    box.replaceChildren(
      rows.length ? table(['Key', 'Value', ''], rows.map((r) => [r.key, JSON.stringify(r.value),
        page.edit ? h('button', { class: 'btn danger', onclick: async () => { await api(tpath(page, `/kv?ns=${ns}&key=${encodeURIComponent(r.key)}`), { method: 'DELETE' }); load(); } }, 'Delete') : ''] ))
        : h('p', { class: 'muted' }, 'No entries yet.'),
      page.edit ? h('div', { class: 'row' }, key, val, h('button', { class: 'btn primary', onclick: async () => {
        let v = val.value;
        try { v = JSON.parse(v); } catch (_) { /* keep as text */ }
        try { await api(tpath(page, '/kv'), { method: 'PUT', body: { ns: page.arg, key: key.value, value: v } }); load(); }
        catch (e) { out.replaceChildren(note('err', e.message)); }
      } }, 'Save')) : null, out);
  };
  await load();
}

async function announce(main) {
  const chans = await api(gpath('/channels'));
  const ch = h('select', {}, chans.map((c) => h('option', { value: c.id }, `#${c.name}`)));
  const text = h('textarea', { maxlength: 2000, placeholder: 'Message to post as the bot…' });
  const out = h('div', {});
  main.append(h('div', { class: 'row' }, ch), text, h('div', { class: 'row' }, h('button', { class: 'btn primary', onclick: async () => {
    try { await api(gpath('/send'), { method: 'POST', body: { channel_id: ch.value, content: text.value } }); text.value = ''; out.replaceChildren(note('ok', 'Posted.')); }
    catch (e) { out.replaceChildren(note('err', e.message)); }
  } }, 'Send')), out);
}

async function apiPage(main, page) {
  const sep = page.arg.includes('?') ? '&' : '?';
  const data = await api(`${page.arg}${sep}guild_id=${state.guild}`);
  if (Array.isArray(data) && data.length && typeof data[0] === 'object') {
    const cols = [...new Set(data.flatMap((r) => Object.keys(r)))];
    main.append(table(cols, data.map((r) => cols.map((c) => (typeof r[c] === 'object' ? JSON.stringify(r[c]) : r[c] ?? '')))));
  } else if (data && typeof data === 'object' && !Array.isArray(data)) {
    main.append(table(['Field', 'Value'], Object.entries(data).map(([k, v]) => [k, typeof v === 'object' ? JSON.stringify(v) : v])));
  } else main.append(h('pre', {}, JSON.stringify(data, null, 2)));
}

async function text(main, page) {
  for (const para of String(page.arg).split(/\n{2,}|\\n/)) main.append(h('p', {}, para));
}

async function site(main, page) {
  main.append(h('p', {}, 'This page is a standalone site served by your dashboard.'),
    h('a', { class: 'btn primary', href: `/site/${encodeURIComponent(page.arg)}.html` }, 'Open page'));
}

async function boot() {
  try { state.cfg = await api('/api/config'); } catch (e) { root.replaceChildren(h('p', { class: 'muted center' }, e.message)); return; }
  document.documentElement.style.setProperty('--accent', `#${String(state.cfg.accent).replace('#', '')}`);
  document.title = state.cfg.title;
  const err = new URLSearchParams(location.search).get('error');
  if (!state.cfg.loggedIn) return renderLogin(err);
  try { await renderApp(); } catch (e) { renderLogin(e.message); }
}
boot();
})();
'''


LIVE_JS = r'''/* Bot Forge Studio browser helper — loaded automatically by every HTML Page.
 * BotStudio.api(path, {method, body})  -> parsed JSON (throws Error(message) on a non-2xx answer)
 * BotStudio.guild()                    -> the server picked in the dashboard (or the first one you manage)
 * BotStudio.live(handler, opts)        -> WebSocket with auto-reconnect; handler gets {type:'hello'|'status'|'event'|'error', ...}
 * Nothing here holds a secret: the session cookie is HttpOnly and the bot token never leaves the server. */
(function () {
  'use strict';
  async function api(path, opts) {
    const init = Object.assign({ credentials: 'same-origin' }, opts || {});
    if (init.body !== undefined && typeof init.body !== 'string') {
      init.headers = Object.assign({ 'Content-Type': 'application/json' }, init.headers || {});
      init.body = JSON.stringify(init.body);
    }
    const r = await fetch(path, init);
    let data = null;
    try { data = await r.json(); } catch (_) { /* empty body */ }
    if (!r.ok) throw new Error((data && data.error) || ('HTTP ' + r.status));
    return data;
  }
  function guild() {
    try { return localStorage.getItem('bs_guild') || ''; } catch (_) { return ''; }
  }
  function live(handler, opts) {
    const o = Object.assign({ guild: null }, opts || {});
    let ws = null, closed = false, delay = 1000, timer = null;
    async function connect() {
      if (closed) return;
      let gid = o.guild || guild();
      if (!gid) { try { const me = await api('/api/me'); gid = me.guilds[0] && me.guilds[0].id; } catch (e) { handler({ type: 'error', message: e.message }); } }
      const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
      ws = new WebSocket(proto + '//' + location.host + '/ws');
      ws.onopen = () => { delay = 1000; if (gid) ws.send(JSON.stringify({ type: 'subscribe', guild: gid })); };
      ws.onmessage = (ev) => { try { handler(JSON.parse(ev.data)); } catch (_) { /* ignore malformed */ } };
      ws.onclose = () => { if (!closed) { timer = setTimeout(connect, delay); delay = Math.min(delay * 2, 15000); } };
      ws.onerror = () => { try { ws.close(); } catch (_) { /* already closing */ } };
    }
    connect();
    return {
      send(obj) { if (ws && ws.readyState === 1) ws.send(JSON.stringify(obj)); },
      close() { closed = true; clearTimeout(timer); if (ws) ws.close(); },
    };
  }
  window.BotStudio = { api, guild, live };
})();
'''

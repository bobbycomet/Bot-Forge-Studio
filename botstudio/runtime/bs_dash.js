'use strict';
/**
 * Bot Forge Studio dashboard helpers (discord.js side).
 *
 * The bot is discord.py; this process only serves the web dashboard. It never connects to the Discord
 * gateway: server/channel/member data comes from Discord's REST API (discord.js `REST`) using the bot
 * token, and bot data comes from the files the bot already writes — data/bot.db (shared SQLite key/value
 * store, same schema bs_runtime.py uses) and status.json.
 *
 * Generated Web Routes load this file as `bs`.
 */
const fs = require('node:fs');
const path = require('node:path');
const net = require('node:net');
const dns = require('node:dns').promises;
const crypto = require('node:crypto');
const { EventEmitter } = require('node:events');
const { REST, Routes } = require('discord.js');

const BASE_DIR = process.env.BS_BASE_DIR ? path.resolve(process.env.BS_BASE_DIR) : path.resolve(__dirname, '..');
const DATA_DIR = process.env.DATA_DIR ? path.resolve(process.env.DATA_DIR) : path.join(BASE_DIR, 'data');

// ------------------------------------------------------------------ logging (stdout; the bot redirects it to logs/dashboard.log)

const LEVELS = { debug: 10, info: 20, warning: 30, error: 40 };
const threshold = LEVELS[String(process.env.LOG_LEVEL || 'info').toLowerCase()] ?? 20;
const stamp = () => {
  const d = new Date();
  const p = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
};
const mk = (name) => (...a) => { if (LEVELS[name] >= threshold) process.stdout.write(`${stamp()} | ${name.toUpperCase().padEnd(8)} | dashboard | ${require('node:util').format(...a)}\n`); };
const log = { debug: mk('debug'), info: mk('info'), warning: mk('warning'), warn: mk('warning'), error: mk('error') };

// ------------------------------------------------------------------ small utilities (same names bs_runtime.py users know)

const s = (v) => (v === null || v === undefined ? '' : String(v));
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const truthy = (v) => {
  if (Array.isArray(v)) return v.length > 0;
  if (v && typeof v === 'object') return true;
  if (typeof v === 'string') return v.length > 0 && !['false', '0'].includes(v.toLowerCase());
  return Boolean(v);
};
const randInt = (lo, hi) => {
  lo = Math.ceil(Number(lo)); hi = Math.floor(Number(hi));
  if (hi < lo) [lo, hi] = [hi, lo];
  return Math.floor(Math.random() * (hi - lo + 1)) + lo;
};
const choice = (arr) => arr[Math.floor(Math.random() * arr.length)];
const mod = (a, b) => ((a % b) + b) % b;
const title = (t) => String(t).toLowerCase().replace(/(^|[^A-Za-z0-9'])([a-z])/g, (_, p, c) => p + c.toUpperCase());
const replaceAll = (t, find, repl) => String(t).split(String(find)).join(String(repl));
const coalesce = (a, b) => (a !== null && a !== undefined && a !== '' ? a : b);
const numeric = (v) => (typeof v === 'number') || (typeof v === 'string' && v.trim() !== '' && Number.isFinite(Number(v)));

function eq(a, b) {
  if (a === b) return true;
  if (numeric(a) && numeric(b)) return Number(a) === Number(b);
  if ((a === null || a === undefined) && (b === null || b === undefined)) return true;
  return s(a) === s(b);
}
function cmp(a, b, op) {
  const num = numeric(a) && numeric(b);
  const x = num ? Number(a) : s(a);
  const y = num ? Number(b) : s(b);
  return { '>': x > y, '<': x < y, '>=': x >= y, '<=': x <= y }[op] ?? x === y;
}
const contains = (a, b) => s(a).toLowerCase().includes(s(b).toLowerCase());
const snowflakeTime = (id) => { try { return Math.floor(Number((BigInt(id) >> 22n) + 1420070400000n) / 1000); } catch (_) { return 0; } };
const status = (n) => { const c = Math.trunc(Number(n)); return c >= 200 && c <= 599 ? c : 200; };

function jsonGet(obj, p, def = '') {
  let cur = obj;
  for (const seg of String(p ?? '').split('.').filter(Boolean)) {
    if (cur === null || cur === undefined) return def;
    cur = cur[seg];
  }
  return cur === undefined || cur === null ? def : cur;
}
const tryJson = (t) => { try { return JSON.parse(t); } catch (_) { return null; } };

/** Only same-site paths or http(s) URLs may be redirect targets (never javascript: etc.). */
function safeUrl(u) {
  const t = String(u ?? '').trim();
  if (/^\/(?![/\\])/.test(t)) return t;
  try { const x = new URL(t); if (['http:', 'https:'].includes(x.protocol)) return x.toString(); } catch (_) { /* fall through */ }
  return '/';
}

// templates: {user.name}, {guild_id} … — the dashboard user is a plain object with Python-style names
const ALIAS = {
  mention: (o) => (o.id ? `<@${o.id}>` : undefined),
  name: (o) => o.username ?? o.name,
  display_name: (o) => o.global_name ?? o.username ?? o.name,
  avatar: (o) => o.avatar_url,
};
function attr(obj, p) {
  let cur = obj;
  for (const seg of String(p).split('.')) {
    if (cur === null || cur === undefined) return undefined;
    let next = ALIAS[seg] ? ALIAS[seg](cur) : undefined;
    if (next === undefined || next === null) next = cur[seg];
    if (typeof next === 'function') return undefined;
    cur = next;
  }
  return cur;
}

// ------------------------------------------------------------------ HTTP GET (SSRF-guarded, for the HTTP GET node)

function isPrivateIp(ip) {
  if (net.isIPv4(ip)) {
    const [a, b] = ip.split('.').map(Number);
    return a === 10 || a === 127 || a === 0 || (a === 169 && b === 254) || (a === 172 && b >= 16 && b <= 31)
      || (a === 192 && b === 168) || (a === 100 && b >= 64 && b <= 127) || a >= 224;
  }
  const t = ip.toLowerCase();
  if (t.startsWith('::ffff:')) return isPrivateIp(t.slice(7));
  return t === '::1' || t === '::' || t.startsWith('fc') || t.startsWith('fd') || t.startsWith('fe80');
}
async function assertPublicUrl(raw) {
  const u = new URL(raw);
  if (!['http:', 'https:'].includes(u.protocol)) throw new Error('only http(s) URLs are allowed');
  if (process.env.BS_ALLOW_PRIVATE_HTTP === '1') return u;
  const host = u.hostname.replace(/^\[|\]$/g, '');
  const addrs = net.isIP(host) ? [{ address: host }] : await dns.lookup(host, { all: true });
  if (addrs.some((a) => isPrivateIp(a.address))) throw new Error('refusing to contact a private/internal address');
  return u;
}
async function httpGet(url, timeoutMs = 15_000) {
  try {
    let target = s(url);
    for (let hop = 0; hop < 4; hop++) {
      await assertPublicUrl(target);
      const r = await fetch(target, { headers: { 'User-Agent': 'BotStudio/0.2' }, redirect: 'manual', signal: AbortSignal.timeout(timeoutMs) });
      if (r.status >= 300 && r.status < 400 && r.headers.get('location')) { target = new URL(r.headers.get('location'), target).toString(); continue; }
      return { status: r.status, text: (await r.text()).slice(0, 200_000) };
    }
    return { status: 0, text: '' };
  } catch (e) {
    log.warning('httpGet %s failed: %s', url, e && e.message || e);
    return { status: 0, text: '' };
  }
}

// ------------------------------------------------------------------ shared key/value store (data/bot.db, written by the bot too)

let _db = null;
function db() {
  if (!_db) {
    const { DatabaseSync } = require('node:sqlite');
    fs.mkdirSync(DATA_DIR, { recursive: true });
    _db = new DatabaseSync(path.join(DATA_DIR, 'bot.db'));
    _db.exec('PRAGMA busy_timeout = 5000');            // the bot writes to the same file
    _db.exec('CREATE TABLE IF NOT EXISTS kv (ns TEXT, k TEXT, v TEXT, PRIMARY KEY (ns, k))');
  }
  return _db;
}
const UPSERT = 'INSERT INTO kv (ns,k,v) VALUES (?,?,?) ON CONFLICT(ns,k) DO UPDATE SET v=excluded.v';

async function kvGet(ns, key, def = null) {
  const row = db().prepare('SELECT v FROM kv WHERE ns=? AND k=?').get(s(ns), s(key));
  return row === undefined ? def : JSON.parse(row.v);
}
async function kvSet(ns, key, value) {
  if (value === null || value === undefined) { db().prepare('DELETE FROM kv WHERE ns=? AND k=?').run(s(ns), s(key)); return; }
  db().prepare(UPSERT).run(s(ns), s(key), JSON.stringify(value));
}
async function kvIncr(ns, key, amount = 1) {
  const row = db().prepare('SELECT v FROM kv WHERE ns=? AND k=?').get(s(ns), s(key));
  const cur = row ? JSON.parse(row.v) : 0;
  let next = (typeof cur === 'number' ? cur : 0) + Number(amount);
  if (Number.isInteger(next)) next = Math.trunc(next);
  db().prepare(UPSERT).run(s(ns), s(key), JSON.stringify(next));
  return next;
}
async function kvTop(ns, limit = 10, prefix = '') {
  const rows = db().prepare('SELECT k, v FROM kv WHERE ns=? AND substr(k, 1, ?) = ?').all(s(ns), prefix.length, prefix);
  return rows.map((r) => [r.k, JSON.parse(r.v)]).filter(([, v]) => typeof v === 'number').sort((a, b) => b[1] - a[1]).slice(0, limit);
}
async function kvList(ns, prefix = '', limit = 500) {
  const rows = db().prepare('SELECT k, v FROM kv WHERE ns=? AND substr(k, 1, ?) = ? ORDER BY k LIMIT ?').all(s(ns), prefix.length, prefix, limit);
  return rows.map((r) => [r.k, JSON.parse(r.v)]);
}

// ------------------------------------------------------------------ bot status (status.json, written by the bot every ~2 s)

function botStatus() {
  try {
    const st = JSON.parse(fs.readFileSync(path.join(BASE_DIR, 'status.json'), 'utf8'));
    const age = Date.now() / 1000 - Number(st.now || 0);
    return { online: age < 30, latency_ms: st.latency_ms ?? null, started: st.started ?? null, guilds: st.guilds ?? null, user: st.user ?? null };
  } catch (_) { return { online: false, latency_ms: null, started: null, guilds: null, user: null }; }
}

// ------------------------------------------------------------------ Discord REST (no gateway)

let _tokenEnv = process.env.BS_TOKEN_ENV || 'DISCORD_TOKEN';
const setTokenEnv = (name) => { if (name) _tokenEnv = name; };
let _rest = null;
function rest() {
  if (!_rest) {
    const token = process.env[_tokenEnv];
    if (!token) throw new Error(`${_tokenEnv} is not set (the dashboard reads the bot token from the bot's .env)`);
    const opts = { version: '10', timeout: 15_000 };
    if (process.env.BS_DISCORD_API) opts.api = process.env.BS_DISCORD_API;
    _rest = new REST(opts).setToken(token);
  }
  return _rest;
}
const apiBase = () => `${process.env.BS_DISCORD_API || 'https://discord.com/api'}/v10`;

const cache = new Map();
async function cached(key, ttlMs, fn) {
  const hit = cache.get(key);
  if (hit && hit.exp > Date.now()) return hit.v;
  const v = await fn();
  cache.set(key, { v, exp: Date.now() + ttlMs });
  if (cache.size > 2000) for (const [k, e] of cache) if (e.exp < Date.now()) cache.delete(k);
  return v;
}

/** The bot's application: id (for OAuth) and the people who own it. */
const botApp = () => cached('app', 600_000, async () => {
  const a = await rest().get(Routes.oauth2CurrentApplication());
  const owners = new Set();
  if (a.owner?.id) owners.add(a.owner.id);
  for (const m of a.team?.members ?? []) if (m.user?.id) owners.add(m.user.id);
  return { id: a.id, name: a.name, owners };
});
const isOwner = async (userId) => (await botApp()).owners.has(String(userId));

/** Ids of every server the bot is in. */
const botGuildIds = () => cached('guilds', 60_000, async () => {
  const ids = new Set();
  let after;
  for (let i = 0; i < 25; i++) {
    const page = await rest().get(Routes.userGuilds(), { query: new URLSearchParams({ limit: '200', ...(after ? { after } : {}) }) });
    for (const g of page) ids.add(g.id);
    if (page.length < 200) break;
    after = page[page.length - 1].id;
  }
  return ids;
});
const getGuild = (id) => cached(`g:${id}`, 30_000, () => rest().get(Routes.guild(id), { query: new URLSearchParams({ with_counts: 'true' }) }));
const getChannels = (id) => cached(`c:${id}`, 30_000, () => rest().get(Routes.guildChannels(id)));

async function memberName(gid, uid) {
  return cached(`m:${gid}:${uid}`, 600_000, async () => {
    try { const m = await rest().get(Routes.guildMember(gid, uid)); return m.nick ?? m.user?.global_name ?? m.user?.username ?? uid; } catch (_) { /* not a member / no access */ }
    try { const u = await rest().get(Routes.user(uid)); return u.global_name ?? u.username ?? uid; } catch (_) { return uid; }
  });
}

/** Post a message (no mentions) to a text channel of the server in the request. */
async function sendChannelMessage(gid, channelId, content) {
  const channels = await getChannels(gid);
  const ch = channels.find((c) => c.id === String(channelId) && [0, 5].includes(c.type));
  if (!ch) throw new Error('unknown channel');
  const text = s(content).trim();
  if (!text || text.length > 2000) throw new Error('message must be 1-2000 characters');
  return rest().post(Routes.channelMessages(ch.id), { body: { content: text, allowed_mentions: { parse: [] } } });
}

// ------------------------------------------------------------------ helpers for generated Web Routes

function guildId(req) {
  const raw = req.params?.guild_id ?? req.params?.gid ?? req.query?.guild_id ?? req.body?.guild_id ?? null;
  return /^\d{5,25}$/.test(String(raw ?? '')) ? String(raw) : null;
}
async function guild(req) {
  const id = guildId(req);
  if (!id || !(await botGuildIds()).has(id)) return null;
  return getGuild(id).catch(() => null);
}
/** Route node "Send to Channel": same rule as the Announce page — only channels of the request's server. */
async function sendMessage(req, channel, content) {
  const gid = guildId(req);
  if (!gid || !(await botGuildIds()).has(gid)) { log.warning('sendMessage: no valid guild_id in the request'); return null; }
  try { return await sendChannelMessage(gid, channel, content); } catch (e) { log.warning('sendMessage failed: %s', e && e.message || e); return null; }
}

// ------------------------------------------------------------------ live updates & the dashboard -> bot bridge

const live = new EventEmitter();           // the server forwards 'event' to subscribed WebSocket clients
live.setMaxListeners(0);
/** Push a live event to every dashboard page watching `guild` (null = every server). */
function broadcast(event, data, guild) {
  live.emit('event', { event: String(event ?? 'update').slice(0, 64), data: data ?? {}, guild: guild ? String(guild) : null });
}
/** Hand an event to the running bot: it lands in the shared kv table 'dash_events' and the bot's listener picks it up. */
async function emitBot(name, data, guild, userId) {
  const n = String(name ?? '');
  if (!/^[A-Za-z0-9_.:-]{1,64}$/.test(n)) throw new Error('bad event name');
  const payload = JSON.stringify(data ?? {});
  if (payload.length > 10_000) throw new Error('event payload too large');
  const key = `${Date.now()}-${crypto.randomBytes(4).toString('hex')}`;
  await kvSet('dash_events', key, { name: n, data: JSON.parse(payload), guild: guild ? String(guild) : null, user: userId ? String(userId) : null, ts: Date.now() / 1000 });
}
/** Events the bot appends to data/live_events.jsonl (bs.live_emit) are forwarded to the dashboard. */
function startLiveTail() {
  const file = path.join(DATA_DIR, 'live_events.jsonl');
  let offset = 0;
  try { offset = fs.statSync(file).size; } catch (_) { /* not written yet */ }
  const t = setInterval(() => {
    try {
      const size = fs.statSync(file).size;
      if (size < offset) offset = 0;                       // rotated
      if (size === offset) return;
      const fd = fs.openSync(file, 'r');
      const buf = Buffer.alloc(Math.min(size - offset, 256 * 1024));
      fs.readSync(fd, buf, 0, buf.length, offset);
      fs.closeSync(fd);
      const text = buf.toString('utf8');
      const cut = text.lastIndexOf('\n') + 1;             // only whole lines
      offset += Buffer.byteLength(text.slice(0, cut));
      for (const line of text.slice(0, cut).split('\n')) {
        if (!line.trim()) continue;
        try { const e = JSON.parse(line); broadcast(e.event, e.data, e.guild); } catch (_) { /* bad line */ }
      }
    } catch (_) { /* file missing: nothing to forward */ }
  }, 1000);
  t.unref();
  return t;
}

module.exports = {
  live, broadcast, emitBot, startLiveTail,
  BASE_DIR, DATA_DIR, log, s, sleep, truthy, randInt, choice, mod, title, replaceAll, coalesce, eq, cmp, contains, snowflakeTime,
  status, jsonGet, tryJson, safeUrl, attr, httpGet, kvGet, kvSet, kvIncr, kvTop, kvList, botStatus,
  setTokenEnv, rest, apiBase, botApp, isOwner, botGuildIds, getGuild, getChannels, memberName, sendChannelMessage,
  guildId, guild, sendMessage,
};

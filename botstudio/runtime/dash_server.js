'use strict';
/**
 * Bot Forge Studio web dashboard server (shipped as dashboard/server.js, started by the Python bot).
 *
 * Discord OAuth2 login -> in-memory sessions -> JSON APIs for the generated frontend, plus the
 * Web Route flows from routes/*.js. config.json (the translation of the bot into pages) and routes/
 * are re-read when they change, so Live Sync updates a running dashboard without a restart.
 *
 * Environment (from the bot's .env): DASHBOARD_PORT (3000), DASHBOARD_HOST (127.0.0.1), DASHBOARD_URL,
 * DISCORD_CLIENT_SECRET, DASHBOARD_TRUST_PROXY, and the bot token under the name config.json says.
 */
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const crypto = require('node:crypto');

const ROOT = __dirname;
const BASE_DIR = process.env.BS_BASE_DIR ? path.resolve(process.env.BS_BASE_DIR) : path.resolve(ROOT, '..');

// minimal .env loader (no dependency); real environment variables win
try {
  for (const line of fs.readFileSync(path.join(BASE_DIR, '.env'), 'utf8').split(/\r?\n/)) {
    const m = line.match(/^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$/);
    if (!m || line.trim().startsWith('#')) continue;
    let v = m[2].replace(/\s+#.*$/, '');
    if (/^(['"]).*\1$/.test(v)) v = v.slice(1, -1);
    if (process.env[m[1]] === undefined) process.env[m[1]] = v;
  }
} catch (_) { /* no .env */ }

const express = require('express');
const bs = require('./bs_dash');

const log = bs.log;
const PUBLIC_DIR = path.join(ROOT, 'web', 'public');
const ROUTES_DIR = path.join(ROOT, 'routes');
const CONFIG_FILE = path.join(ROOT, 'config.json');
const COOKIE = 'bs_sid';
const OAUTH_COOKIE = 'bs_oauth';
const SESSION_TTL_MS = 6 * 3600 * 1000;
const MANAGE_MASK = 0x8n | 0x20n;   // ADMINISTRATOR | MANAGE_GUILD
const METHODS = ['get', 'post', 'put', 'delete', 'patch'];

const TEST = process.env.BS_TEST_MODE === '1';   // local test console: fake Discord, instant login, request / socket logging
const state = { sessions: new Map(), oauth: new Map(), limits: new Map(), router: null, routeSig: '', cfg: null, cfgMtime: 0 };

// ------------------------------------------------------------------ config & routes (live-reloaded)

function config() {
  try {
    const st = fs.statSync(CONFIG_FILE);
    if (st.mtimeMs !== state.cfgMtime) {
      state.cfg = JSON.parse(fs.readFileSync(CONFIG_FILE, 'utf8'));
      state.cfgMtime = st.mtimeMs;
      bs.setTokenEnv(state.cfg.tokenEnv);
      log.info('Loaded config.json (%d pages)', state.cfg.pages.length);
    }
  } catch (e) { if (!state.cfg) throw e; log.error('config.json is unreadable, keeping the previous one: %s', e.message); }
  return state.cfg;
}

function loadRoutes() {
  let files = [];
  try { files = fs.readdirSync(ROUTES_DIR).filter((f) => f.endsWith('.js')).sort(); } catch (_) { /* no routes */ }
  const sig = files.map((f) => `${f}:${fs.statSync(path.join(ROUTES_DIR, f)).mtimeMs}`).join('|');
  if (sig === state.routeSig && state.router) return;
  const router = express.Router();
  try {
    for (const f of files) {
      const abs = path.join(ROUTES_DIR, f);
      delete require.cache[require.resolve(abs)];
      for (const route of require(abs)) {
        const m = String(route.method || 'get').toLowerCase();
        if (!METHODS.includes(m)) throw new Error(`unsupported HTTP method ${route.method}`);
        router[m](route.path, wrapRoute(route, f));
      }
    }
    state.router = router;
    state.routeSig = sig;
    log.info('Loaded %d route file(s)', files.length);
  } catch (e) {
    state.routeSig = sig;   // do not retry the same broken files every tick
    if (!state.router) state.router = express.Router();
    log.error('Failed to load routes (keeping the previous routes): %s', e && e.stack || e);
  }
}

// ------------------------------------------------------------------ helpers

const port = () => Number(process.env.DASHBOARD_PORT || 3000);
const host = () => process.env.DASHBOARD_HOST || '127.0.0.1';
const baseUrl = () => String(process.env.DASHBOARD_URL || `http://localhost:${port()}`).replace(/\/+$/, '');
const isHttps = () => baseUrl().startsWith('https://');

function parseCookies(req) {
  const out = {};
  for (const part of String(req.headers.cookie || '').split(';')) {
    const i = part.indexOf('=');
    if (i < 0) continue;
    try { out[part.slice(0, i).trim()] = decodeURIComponent(part.slice(i + 1).trim()); } catch (_) { /* bad cookie */ }
  }
  return out;
}
function setCookie(res, name, value, maxAgeS) {
  const flags = [`${name}=${encodeURIComponent(value)}`, 'Path=/', 'HttpOnly', 'SameSite=Lax', `Max-Age=${maxAgeS}`];
  if (isHttps()) flags.push('Secure');
  const prev = res.getHeader('Set-Cookie');
  res.setHeader('Set-Cookie', prev ? [].concat(prev, flags.join('; ')) : [flags.join('; ')]);
}
const clearCookie = (res, name) => setCookie(res, name, '', 0);

function limited(key, max, windowMs) {
  const now = Date.now();
  const e = state.limits.get(key);
  if (!e || e.reset < now) { state.limits.set(key, { n: 1, reset: now + windowMs }); return false; }
  e.n += 1;
  return e.n > max;
}

const avatarUrl = (u) => (u.avatar
  ? `https://cdn.discordapp.com/avatars/${u.id}/${u.avatar}.png?size=128`
  : `https://cdn.discordapp.com/embed/avatars/${Number((BigInt(u.id) >> 22n) % 6n)}.png`);
const iconUrl = (g) => (g.icon ? `https://cdn.discordapp.com/icons/${g.id}/${g.icon}.png?size=64` : null);

const wrapAsync = (fn) => (req, res, next) => Promise.resolve(fn(req, res, next)).catch((err) => {
  log.error('%s %s failed: %s', req.method, req.path, err && err.message || err);
  if (!res.headersSent) res.status(502).json({ error: 'Discord request failed' });
});

// ------------------------------------------------------------------ sessions & middleware

function createSession(res, data) {
  const id = crypto.randomBytes(32).toString('base64url');
  state.sessions.set(id, { ...data, exp: Date.now() + SESSION_TTL_MS });
  setCookie(res, COOKIE, id, Math.floor(SESSION_TTL_MS / 1000));
}
function attachSession(req, res, next) {
  const id = parseCookies(req)[COOKIE];
  const sess = id ? state.sessions.get(id) : null;
  if (sess && sess.exp > Date.now()) { req.session = sess; req.sessionId = id; req.user = sess.user; } else if (sess) state.sessions.delete(id);
  next();
}
function securityHeaders(req, res, next) {
  const h = String(req.headers.host || '');
  const ws = /^[A-Za-z0-9.:\[\]-]{1,100}$/.test(h) ? ` ws://${h} wss://${h}` : '';      // some browsers do not treat ws: as 'self'
  res.setHeader('Content-Security-Policy', "default-src 'self'; img-src 'self' https://cdn.discordapp.com data:; "
    + `style-src 'self'; script-src 'self'; connect-src 'self'${ws}; frame-ancestors 'none'; base-uri 'none'; form-action 'self'`);
  res.setHeader('X-Content-Type-Options', 'nosniff');
  res.setHeader('Referrer-Policy', 'same-origin');
  res.setHeader('X-Frame-Options', 'DENY');
  next();
}
/** State-changing requests that carry our session cookie must come from our own origin. */
function csrfGuard(req, res, next) {
  if (['GET', 'HEAD', 'OPTIONS'].includes(req.method) || !req.session) return next();
  const origin = req.headers.origin;
  if (origin) {
    let ok = false;
    try { ok = new URL(origin).host === req.headers.host || new URL(origin).origin === new URL(baseUrl()).origin; } catch (_) { /* bad origin */ }
    if (!ok) return res.status(403).json({ error: 'cross-origin request blocked' });
  }
  if (limited(`w:${req.sessionId}`, 60, 60_000)) return res.status(429).json({ error: 'too many requests' });
  return next();
}
const needLogin = (req, res, next) => (req.session ? next() : res.status(401).json({ error: 'login required' }));
const needOwner = wrapAsync(async (req, res, next) => ((await bs.isOwner(req.user.id)) ? next() : res.status(403).json({ error: 'bot owner only' })));

const guildAccess = wrapAsync(async (req, res, next) => {
  const gid = bs.guildId(req);
  if (!gid || !req.session.guilds.has(gid) || !(await bs.botGuildIds()).has(gid)) return res.status(404).json({ error: 'unknown server' });
  req.guildId = gid;
  return next();
});

function wrapRoute(route, file) {
  return async (req, res) => {
    try {
      if (route.login && !req.session) return res.status(401).json({ error: 'login required' });
      if (route.ownerOnly && !(await bs.isOwner(req.user.id))) return res.status(403).json({ error: 'bot owner only' });
      if (route.guildAdmin) {
        const gid = bs.guildId(req);
        if (!gid || !req.session.guilds.has(gid)) return res.status(403).json({ error: 'you do not manage that server' });
      }
      await route.run(req, res);
      if (!res.headersSent) res.status(204).end();
    } catch (err) {
      log.error('Web route %s %s (%s) failed: %s', route.method, route.path, file, err && err.stack || err);
      if (!res.headersSent) res.status(500).json({ error: 'internal error' });
    }
  };
}

// ------------------------------------------------------------------ OAuth2 login

function authRouter() {
  const r = express.Router();

  r.get('/login', wrapAsync(async (req, res) => {
    if (TEST) {          // TEST MODE: skip Discord entirely
      const g = { id: state.mock.GUILD, name: 'Test Server', icon_url: null };
      createSession(res, { user: { ...state.mock.USER, avatar_url: avatarUrl(state.mock.USER) }, guilds: new Map([[g.id, g]]), invites: [] });
      const next = String(req.query.next || '/');
      return res.redirect(/^\/(?!\/)[A-Za-z0-9_\-./]{0,100}$/.test(next) ? next : '/');
    }
    if (!process.env.DISCORD_CLIENT_SECRET) {
      return res.status(500).type('text').send('Dashboard login is not configured: set DISCORD_CLIENT_SECRET in .env and restart the bot.');
    }
    const st = crypto.randomBytes(24).toString('base64url');
    state.oauth.set(st, Date.now() + 10 * 60_000);
    setCookie(res, OAUTH_COOKIE, st, 600);
    const q = new URLSearchParams({
      client_id: (await bs.botApp()).id, response_type: 'code', scope: 'identify guilds',
      redirect_uri: `${baseUrl()}/auth/callback`, state: st, prompt: 'none',
    });
    return res.redirect(`https://discord.com/oauth2/authorize?${q}`);
  }));

  r.get('/callback', async (req, res) => {
    const st = String(req.query.state || '');
    const okState = st && state.oauth.has(st) && state.oauth.get(st) > Date.now() && parseCookies(req)[OAUTH_COOKIE] === st;
    state.oauth.delete(st);
    clearCookie(res, OAUTH_COOKIE);
    if (!okState || !req.query.code) return res.redirect('/?error=login_failed');
    try {
      const body = new URLSearchParams({
        client_id: (await bs.botApp()).id, client_secret: process.env.DISCORD_CLIENT_SECRET,
        grant_type: 'authorization_code', code: String(req.query.code), redirect_uri: `${baseUrl()}/auth/callback`,
      });
      const tr = await fetch(`${bs.apiBase()}/oauth2/token`, {
        method: 'POST', headers: { 'Content-Type': 'application/x-www-form-urlencoded' }, body, signal: AbortSignal.timeout(10_000),
      });
      if (!tr.ok) throw new Error(`token exchange failed (${tr.status})`);
      const auth = { Authorization: `Bearer ${(await tr.json()).access_token}` };
      const [me, gl] = await Promise.all([
        fetch(`${bs.apiBase()}/users/@me`, { headers: auth, signal: AbortSignal.timeout(10_000) }).then((x) => x.json()),
        fetch(`${bs.apiBase()}/users/@me/guilds`, { headers: auth, signal: AbortSignal.timeout(10_000) }).then((x) => x.json()),
      ]);
      if (!me?.id || !Array.isArray(gl)) throw new Error('unexpected Discord response');
      const allow = config().restrictUserIds || [];
      if (allow.length && !allow.includes(me.id)) return res.redirect('/?error=not_allowed');
      const present = await bs.botGuildIds();
      const guilds = new Map();
      const invites = [];
      for (const g of gl) {
        let manage = Boolean(g.owner);
        try { manage = manage || (BigInt(g.permissions || 0) & MANAGE_MASK) !== 0n; } catch (_) { /* bad perms */ }
        if (!manage) continue;
        if (present.has(g.id)) guilds.set(g.id, { id: g.id, name: g.name, icon_url: iconUrl(g) });
        else if (invites.length < 25) invites.push({ id: g.id, name: g.name });
      }
      const user = { id: me.id, username: me.username, global_name: me.global_name ?? null, avatar_url: avatarUrl(me) };
      createSession(res, { user, guilds, invites });
      log.info('Login: %s (%s) manages %d server(s) with the bot', me.username, me.id, guilds.size);
      return res.redirect('/');
    } catch (err) {
      log.warning('Login failed: %s', err && err.message || err);
      return res.redirect('/?error=login_failed');
    }
  });

  r.post('/logout', (req, res) => {
    if (req.sessionId) state.sessions.delete(req.sessionId);
    clearCookie(res, COOKIE);
    res.json({ ok: true });
  });
  return r;
}

// ------------------------------------------------------------------ built-in API

function apiRouter() {
  const r = express.Router();
  const allowed = (list, ns, scope) => (config().allow?.[list] ?? []).find((e) => e.ns === ns && e.scope === scope);

  r.get('/config', wrapAsync(async (req, res) => {
    const c = config();
    const owner = req.user ? await bs.isOwner(req.user.id) : false;
    res.json({
      title: c.title, accent: c.accent, botName: c.botName, commands: c.commands,
      pages: c.pages.filter((p) => !p.ownerOnly || owner),
      loggedIn: Boolean(req.session), loginReady: Boolean(process.env.DISCORD_CLIENT_SECRET),
    });
  }));

  r.get('/me', needLogin, wrapAsync(async (req, res) => {
    const app = await bs.botApp();
    const base = `https://discord.com/oauth2/authorize?client_id=${app.id}&scope=bot%20applications.commands&permissions=0`;
    res.json({
      user: req.session.user, owner: await bs.isOwner(req.user.id),
      guilds: [...req.session.guilds.values()],
      invites: req.session.invites.map((g) => ({ ...g, url: `${base}&guild_id=${g.id}` })),
    });
  }));

  // table handlers shared by per-server and owner (all-servers) scopes
  const leaderboard = (scope) => wrapAsync(async (req, res) => {
    const ns = String(req.query.ns || '');
    if (!allowed('leaderboards', ns, scope)) return res.status(404).json({ error: 'unknown leaderboard' });
    const prefix = scope === 'guild' ? `${req.guildId}:` : '';
    const rows = await bs.kvTop(ns, 25, prefix);
    res.json(await Promise.all(rows.map(async ([k, v], i) => {
      const uid = k.slice(prefix.length);
      const label = scope === 'guild' && /^\d+$/.test(uid) ? await bs.memberName(req.guildId, uid) : uid;
      return { rank: i + 1, id: uid, name: label, value: v };
    })));
  });
  const kvList = (scope) => wrapAsync(async (req, res) => {
    const ns = String(req.query.ns || '');
    if (!allowed('kv', ns, scope)) return res.status(404).json({ error: 'unknown table' });
    const prefix = scope === 'guild' ? `${req.guildId}:` : '';
    res.json((await bs.kvList(ns, prefix, 500)).map(([k, v]) => ({ key: k.slice(prefix.length), value: v })));
  });
  const kvPut = (scope) => wrapAsync(async (req, res) => {
    const { ns, key, value } = req.body ?? {};
    const a = allowed('kv', ns, scope);
    if (!a || !a.edit) return res.status(404).json({ error: 'this table is read-only' });
    if (typeof key !== 'string' || !key.length || key.length > 200 || /[\u0000-\u001f]/.test(key)) return res.status(400).json({ error: 'bad key' });
    if (value === undefined || JSON.stringify(value).length > 10_000) return res.status(400).json({ error: 'bad value' });
    await bs.kvSet(ns, `${scope === 'guild' ? `${req.guildId}:` : ''}${key}`, value);
    log.info('%s set %s/%s', req.user.id, ns, key);
    res.json({ ok: true });
  });
  const kvDelete = (scope) => wrapAsync(async (req, res) => {
    const ns = String(req.query.ns || '');
    const key = String(req.query.key || '');
    const a = allowed('kv', ns, scope);
    if (!a || !a.edit) return res.status(404).json({ error: 'this table is read-only' });
    if (!key || key.length > 200) return res.status(400).json({ error: 'bad key' });
    await bs.kvSet(ns, `${scope === 'guild' ? `${req.guildId}:` : ''}${key}`, null);
    log.info('%s deleted %s/%s', req.user.id, ns, key);
    res.json({ ok: true });
  });

  const owner = express.Router();
  r.use('/owner', needLogin, needOwner, owner);
  owner.get('/leaderboard', leaderboard('global'));
  owner.get('/kv', kvList('global'));
  owner.put('/kv', kvPut('global'));
  owner.delete('/kv', kvDelete('global'));

  const g = express.Router({ mergeParams: true });
  r.use('/guilds/:gid', needLogin, guildAccess, g);

  g.get('/overview', wrapAsync(async (req, res) => {
    const [gd, channels] = await Promise.all([bs.getGuild(req.guildId), bs.getChannels(req.guildId)]);
    const st = bs.botStatus();
    res.json({
      id: gd.id, name: gd.name, icon_url: iconUrl(gd), member_count: gd.approximate_member_count ?? 0,
      channel_count: channels.length, role_count: (gd.roles ?? []).length, boosts: gd.premium_subscription_count ?? 0,
      bot: { name: st.user, ping: st.latency_ms, uptime_s: st.started ? Math.max(0, Math.floor(Date.now() / 1000 - st.started)) : null,
        servers: st.guilds ?? (await bs.botGuildIds()).size, online: st.online },
    });
  }));

  g.get('/channels', wrapAsync(async (req, res) => {
    const list = (await bs.getChannels(req.guildId)).filter((c) => [0, 5].includes(c.type))
      .map((c) => ({ id: c.id, name: c.name })).sort((a, b) => a.name.localeCompare(b.name));
    res.json(list);
  }));

  g.get('/leaderboard', leaderboard('guild'));
  g.get('/kv', kvList('guild'));
  g.put('/kv', kvPut('guild'));
  g.delete('/kv', kvDelete('guild'));

  g.post('/send', wrapAsync(async (req, res) => {
    if (!config().allow?.announce) return res.status(404).json({ error: 'announce is not enabled' });
    const { channel_id: cid, content } = req.body ?? {};
    try { await bs.sendChannelMessage(req.guildId, cid, content); } catch (e) {
      const msg = e && e.message || 'failed';
      return res.status(/^(unknown channel|message must)/.test(msg) ? 400 : 502).json({ error: msg });
    }
    log.info('%s posted in channel %s of %s', req.user.id, cid, req.guildId);
    return res.json({ ok: true });
  }));
  return r;
}

// ------------------------------------------------------------------ server

function buildApp() {
  const app = express();
  app.disable('x-powered-by');
  if (process.env.DASHBOARD_TRUST_PROXY) app.set('trust proxy', Number(process.env.DASHBOARD_TRUST_PROXY) || true);
  app.use(securityHeaders);
  app.use(express.json({ limit: '100kb' }));
  app.use(attachSession);
  if (TEST) {
    app.use((req, res, next) => {
      const t0 = Date.now();
      res.on('finish', () => { if (!req.path.startsWith('/__test')) log.info('HTTP %s %s -> %d (%dms)', req.method, req.originalUrl.slice(0, 120), res.statusCode, Date.now() - t0); });
      next();
    });
  }
  app.use(csrfGuard);
  // HTML Pages: served from web/public/site, behind login unless the page opted out
  app.use('/site', (req, res, next) => {
    const slug = (req.path.match(/^\/([a-z0-9-]+)\./) || [])[1];
    const pageCfg = (config().sitePages || {})[slug];
    if (pageCfg && pageCfg.login !== false && !req.session) return res.redirect('/');
    return next();
  });
  if (TEST) {
    app.post('/__test/live', (req, res) => {
      const { event = 'test', data = {} } = req.body ?? {};
      bs.broadcast(event, data, state.mock.GUILD);
      res.json({ ok: true, clients: state.wsClients ? state.wsClients() : 0 });
    });
    app.get('/__test/state', (req, res) => res.json({ clients: state.wsClients ? state.wsClients() : 0, sessions: state.sessions.size, posted: state.mock.posted }));
  }
  app.use('/auth', authRouter());
  app.use('/api', apiRouter());
  app.use((req, res, next) => { loadRoutes(); state.router(req, res, next); });
  app.use('/api', (req, res) => res.status(404).json({ error: 'not found' }));
  app.use(express.static(PUBLIC_DIR, { index: 'index.html', maxAge: 0 }));
  app.get('*', (req, res) => res.sendFile(path.join(PUBLIC_DIR, 'index.html')));
  // eslint-disable-next-line no-unused-vars
  app.use((err, req, res, next) => {
    if (err && err.type === 'entity.parse.failed') return res.status(400).json({ error: 'invalid JSON' });
    log.error('Web error: %s', err && err.stack || err);
    return res.status(500).json({ error: 'internal error' });
  });
  return app;
}

function attachWebSocket(server) {
  const { WebSocketServer } = require('ws');
  const wss = new WebSocketServer({ noServer: true, maxPayload: 4096 });
  const wsLog = (...a) => { if (TEST) log.info(...a); };
  const send = (ws, obj) => { if (ws.readyState === 1) ws.send(JSON.stringify(obj)); wsLog('WS -> %s', JSON.stringify(obj).slice(0, 160)); };
  const sameOrigin = (origin, req) => { try { return new URL(origin).host === req.headers.host || new URL(origin).origin === new URL(baseUrl()).origin; } catch (_) { return false; } };
  state.wsClients = () => wss.clients.size;

  server.on('upgrade', (req, socket, head) => {
    const deny = (code, text) => { socket.write(`HTTP/1.1 ${code} ${text}\r\nConnection: close\r\n\r\n`); socket.destroy(); };
    if (new URL(req.url, 'http://x').pathname !== '/ws') return deny(404, 'Not Found');
    if (!req.headers.origin || !sameOrigin(req.headers.origin, req)) return deny(403, 'Forbidden');       // cross-site WebSocket hijacking guard
    const id = parseCookies(req)[COOKIE];
    const sess = id ? state.sessions.get(id) : null;
    if (!sess || sess.exp < Date.now()) return deny(401, 'Unauthorized');
    return wss.handleUpgrade(req, socket, head, (ws) => { ws.session = sess; ws.guild = null; ws.msgs = 0; ws.t0 = Date.now(); wss.emit('connection', ws, req); });
  });

  wss.on('connection', (ws) => {
    wsLog('WS connected: %s', ws.session.user.username);
    send(ws, { type: 'hello', user: ws.session.user.username, guilds: [...ws.session.guilds.keys()] });
    ws.on('message', async (raw) => {
      wsLog('WS <- %s', String(raw).slice(0, 160));
      if (Date.now() - ws.t0 > 10_000) { ws.t0 = Date.now(); ws.msgs = 0; }
      if (++ws.msgs > 20) return send(ws, { type: 'error', message: 'slow down' });
      let msg;
      try { msg = JSON.parse(String(raw)); } catch (_) { return send(ws, { type: 'error', message: 'invalid JSON' }); }
      if (msg.type === 'ping') return send(ws, { type: 'pong' });
      if (msg.type === 'subscribe') {
        const gid = String(msg.guild ?? '');
        if (!ws.session.guilds.has(gid) || !(await bs.botGuildIds()).has(gid)) return send(ws, { type: 'error', message: 'unknown server' });
        ws.guild = gid;
        return send(ws, { type: 'status', guild: gid, data: bs.botStatus() });
      }
      return send(ws, { type: 'error', message: 'unknown message type' });
    });
    ws.on('close', () => wsLog('WS closed'));
  });

  setInterval(() => {                                   // bot status for everyone who subscribed
    const st = bs.botStatus();
    for (const ws of wss.clients) if (ws.guild) send(ws, { type: 'status', guild: ws.guild, data: st });
  }, 3000).unref();
  bs.live.on('event', ({ event, data, guild }) => {     // routes / the bot push live updates
    for (const ws of wss.clients) if (ws.guild && (!guild || guild === ws.guild)) send(ws, { type: 'event', event, data });
  });
  bs.startLiveTail();
  return wss;
}

async function main() {
  if (TEST && !['127.0.0.1', 'localhost', '::1'].includes(host())) {
    log.error('BS_TEST_MODE logs everyone in automatically, so it only starts on a loopback address (DASHBOARD_HOST=%s). Refusing.', host());
    process.exit(1);
  }
  if (TEST) {
    const mock = await require('./dash_mock').startMock();
    state.mock = mock;
    process.env.BS_DISCORD_API = `http://127.0.0.1:${mock.port}/api`;
    process.env.DISCORD_TOKEN = process.env.DISCORD_TOKEN || 'test-token';
    process.env.DISCORD_CLIENT_SECRET = process.env.DISCORD_CLIENT_SECRET || 'test-secret';
    log.info('TEST MODE: fake Discord on port %d — nothing here touches the real Discord API', mock.port);
    const writeStatus = () => {   // pretend to be the bot so the status cards and the WebSocket have something to show
      try { fs.writeFileSync(path.join(BASE_DIR, 'status.json'), JSON.stringify({ now: Date.now() / 1000, started: state.started ??= Date.now() / 1000, latency_ms: 30 + Math.floor(Math.random() * 20), guilds: 1, user: 'Test Bot#0000' })); } catch (_) { /* read-only */ }
    };
    writeStatus();
    setInterval(writeStatus, 2000).unref();
  }
  config();
  loadRoutes();
  const server = http.createServer(buildApp());
  attachWebSocket(server);
  server.on('error', (e) => { log.error('Dashboard server error: %s', e && e.message || e); process.exit(1); });
  await new Promise((resolve) => server.listen(port(), host(), resolve));
  log.info('Dashboard listening on http://%s:%d (public URL %s)', host(), port(), baseUrl());
  setInterval(() => {
    const now = Date.now();
    for (const [k, v] of state.sessions) if (v.exp < now) state.sessions.delete(k);
    for (const [k, v] of state.oauth) if (v < now) state.oauth.delete(k);
    for (const [k, v] of state.limits) if (v.reset < now) state.limits.delete(k);
    try { config(); loadRoutes(); } catch (e) { log.error('reload: %s', e && e.message || e); }
  }, 2000).unref();
  const bye = () => server.close(() => process.exit(0));
  process.on('SIGTERM', bye);
  process.on('SIGINT', bye);
}

process.on('unhandledRejection', (e) => log.error('Unhandled rejection: %s', (e && e.stack) || e));
if (require.main === module) main().catch((e) => { log.error('Dashboard failed to start: %s', (e && e.stack) || e); process.exit(1); });
module.exports = { buildApp, state };

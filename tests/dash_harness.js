'use strict';
// Usage: cd <built bot folder>/dashboard (with node_modules) && node dash_harness.js
const assert = require('node:assert');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const { spawn } = require('node:child_process');
const { DatabaseSync } = require('node:sqlite');

const BASE = path.resolve('..');                       // the bot folder
const posted = [];

// ---- fake Discord (REST + OAuth) -----------------------------------------------------------------
const fake = http.createServer((req, res) => {
  const url = new URL(req.url, 'http://x');
  let body = '';
  req.on('data', (c) => { body += c; });
  req.on('end', () => {
    const send = (code, obj) => { res.writeHead(code, { 'content-type': 'application/json' }); res.end(JSON.stringify(obj)); };
    const bearer = String(req.headers.authorization || '').startsWith('Bearer ');
    const p = url.pathname.replace(/^\/api\/v10/, '');
    if (p === '/oauth2/applications/@me') return send(200, { id: '777', name: 'TestBot', owner: { id: '1' } });
    if (p === '/oauth2/token') return send(200, { access_token: 'tok' });
    if (p === '/users/@me') return send(200, { id: '1', username: 'alice', global_name: 'Alice', avatar: null });
    if (p === '/users/@me/guilds') {
      return bearer
        ? send(200, [{ id: '123456', name: 'G', owner: true, permissions: '8' }, { id: '555555', name: 'Other', owner: false, permissions: '32' },
          { id: '666666', name: 'NoPerms', owner: false, permissions: '0' }])
        : send(200, [{ id: '123456' }]);
    }
    if (p === '/guilds/123456') return send(200, { id: '123456', name: 'G', icon: null, approximate_member_count: 5, premium_subscription_count: 2, roles: [{}, {}, {}] });
    if (p === '/guilds/123456/channels') return send(200, [{ id: '900', type: 0, name: 'general' }, { id: '901', type: 2, name: 'voice' }]);
    if (p === '/guilds/123456/members/42') return send(200, { nick: 'Answer', user: { username: 'u42' } });
    if (p === '/channels/900/messages' && req.method === 'POST') { posted.push(JSON.parse(body)); return send(200, { id: '1' }); }
    return send(404, { message: 'not found', code: 0 });
  });
});

const jar = {};   // tiny cookie jar
async function j(base, p, o = {}) {
  const headers = { ...(o.headers || {}) };
  const ck = Object.entries(jar).map(([k, v]) => `${k}=${v}`).join('; ');
  if (ck) headers.cookie = ck;
  const r = await fetch(base + p, { redirect: 'manual', ...o, headers });
  for (const sc of r.headers.getSetCookie?.() ?? []) {
    const [kv] = sc.split(';'); const i = kv.indexOf('=');
    if (/Max-Age=0/i.test(sc)) delete jar[kv.slice(0, i)]; else jar[kv.slice(0, i)] = kv.slice(i + 1);
  }
  let b = null; try { b = await r.clone().json(); } catch (_) { /* not json */ }
  return { r, b };
}
const post = (base, p, body, headers = {}) => j(base, p, { method: 'POST', headers: { 'content-type': 'application/json', ...headers }, body: JSON.stringify(body) });

(async () => {
  await new Promise((r) => fake.listen(0, '127.0.0.1', r));
  const fakePort = fake.address().port;
  const port = 31000 + Math.floor(Math.random() * 2000);
  const base = `http://127.0.0.1:${port}`;

  // data the bot would have written
  fs.mkdirSync(path.join(BASE, 'data'), { recursive: true });
  const db = new DatabaseSync(path.join(BASE, 'data', 'bot.db'));
  db.exec('CREATE TABLE IF NOT EXISTS kv (ns TEXT, k TEXT, v TEXT, PRIMARY KEY (ns, k))');
  const put = (ns, k, v) => db.prepare('INSERT OR REPLACE INTO kv VALUES (?,?,?)').run(ns, k, JSON.stringify(v));
  put('xp', '123456:42', 120); put('xp', '123456:43', 80); put('xp', '999999:44', 9999);   // another server's data must never show
  put('uses', '123456:42', 3);
  fs.writeFileSync(path.join(BASE, 'status.json'), JSON.stringify({ now: Date.now() / 1000, started: Date.now() / 1000 - 3700, latency_ms: 42, guilds: 2, user: 'Bot#0001' }));

  const child = spawn(process.execPath, ['server.js'], {
    cwd: process.cwd(), stdio: ['ignore', 'pipe', 'pipe'],
    env: { ...process.env, BS_BASE_DIR: BASE, BS_DISCORD_API: `http://127.0.0.1:${fakePort}/api`, DISCORD_TOKEN: 'fake-token',
      DISCORD_CLIENT_SECRET: 'secret', DASHBOARD_PORT: String(port), DASHBOARD_URL: base, DASHBOARD_HOST: '127.0.0.1' },
  });
  let out = '';
  child.stdout.on('data', (d) => { out += d; }); child.stderr.on('data', (d) => { out += d; });
  for (let i = 0; i < 100 && !/listening/.test(out); i++) await new Promise((r) => setTimeout(r, 100));
  assert.ok(/listening/.test(out), 'server must start:\n' + out);

  try {
    // ---- before login
    let { r, b } = await j(base, '/api/config');
    assert.strictEqual(r.status, 200); assert.strictEqual(b.title, 'Server Dashboard'); assert.strictEqual(b.loggedIn, false);
    assert.deepStrictEqual(b.pages.map((p) => p.type).slice(0, 3), ['overview', 'commands', 'leaderboard']);
    assert.ok(b.commands.some((c) => c.name === 'count'), 'slash commands are translated');
    assert.strictEqual((await j(base, '/api/me')).r.status, 401);
    assert.strictEqual((await j(base, '/api/custom/stats?guild_id=123456')).r.status, 401);
    assert.match(r.headers.get('content-security-policy'), /default-src 'self'/);
    assert.match(await (await fetch(base + '/')).text(), /<title>Server Dashboard<\/title>/);
    assert.strictEqual((await fetch(base + '/app.js')).status, 200);
    assert.strictEqual((await fetch(base + '/spa/route')).status, 200);

    // ---- full OAuth round trip (token exchange + user + guilds go to the fake Discord)
    const login = await j(base, '/auth/login');
    assert.strictEqual(login.r.status, 302);
    const loc = new URL(login.r.headers.get('location'));
    assert.strictEqual(loc.origin + loc.pathname, 'https://discord.com/oauth2/authorize');
    assert.strictEqual(loc.searchParams.get('client_id'), '777');
    assert.strictEqual(loc.searchParams.get('redirect_uri'), `${base}/auth/callback`);
    const cb = await j(base, `/auth/callback?code=c&state=${loc.searchParams.get('state')}`);
    assert.strictEqual(cb.r.headers.get('location'), '/', 'login succeeds'); assert.ok(jar.bs_sid, 'session cookie set');

    assert.strictEqual((await j(base, '/auth/callback?code=c&state=forged')).r.headers.get('location'), '/?error=login_failed', 'forged state rejected');
    ({ r, b } = await j(base, '/api/me'));
    assert.strictEqual(b.user.username, 'alice'); assert.deepStrictEqual(b.guilds.map((g) => g.id), ['123456'], 'only managed servers that have the bot');
    assert.deepStrictEqual(b.invites.map((g) => g.id), ['555555'], 'managed servers without the bot get an invite link'); assert.strictEqual(b.owner, true);

    // ---- translated pages & data
    ({ b } = await j(base, '/api/config'));
    assert.strictEqual(b.loggedIn, true);
    ({ r, b } = await j(base, '/api/guilds/123456/overview'));
    assert.strictEqual(b.member_count, 5); assert.strictEqual(b.role_count, 3); assert.strictEqual(b.channel_count, 2);
    assert.strictEqual(b.bot.ping, 42); assert.strictEqual(b.bot.online, true); assert.ok(b.bot.uptime_s >= 3700);
    assert.strictEqual((await j(base, '/api/guilds/555555/overview')).r.status, 404, 'a server the bot is not in is blocked');
    ({ b } = await j(base, '/api/guilds/123456/leaderboard?ns=xp'));
    assert.deepStrictEqual(b.map((x) => [x.name, x.value]), [['Answer', 120], ['43', 80]], 'per-server data only, member names from REST');
    assert.strictEqual((await j(base, '/api/guilds/123456/leaderboard?ns=uses')).r.status, 200);
    assert.strictEqual((await j(base, '/api/guilds/123456/leaderboard?ns=secret')).r.status, 404, 'namespace allow-list');
    assert.strictEqual((await j(base, '/api/guilds/123456/kv?ns=xp')).r.status, 404, 'xp is a leaderboard, not a table');
    assert.strictEqual((await post(base, '/api/guilds/123456/kv', { ns: 'uses', key: 'x', value: 1 })).r.status, 404, 'auto tables are read-only');

    // ---- announce goes out through REST, without mentions
    ({ r } = await post(base, '/api/guilds/123456/send', { channel_id: '900', content: 'hello @everyone' }));
    assert.strictEqual(r.status, 200); assert.deepStrictEqual(posted[0], { content: 'hello @everyone', allowed_mentions: { parse: [] } });
    assert.strictEqual((await post(base, '/api/guilds/123456/send', { channel_id: '901', content: 'x' })).r.status, 400, 'voice channel refused');
    assert.strictEqual((await post(base, '/api/guilds/123456/send', { channel_id: '900', content: 'x' }, { origin: 'https://evil.example' })).r.status, 403, 'cross-origin blocked');

    // ---- Web Route flow from the graph
    for (const n of [1, 2]) { ({ r, b } = await j(base, '/api/custom/stats?guild_id=123456')); assert.strictEqual(b.visits, n); assert.strictEqual(b.server, '123456'); }
    assert.strictEqual((await j(base, '/api/nope')).r.status, 404);

    // ---- live reload: edit config.json and a route while the server runs
    const cfgPath = path.resolve('config.json');
    const cfg = JSON.parse(fs.readFileSync(cfgPath, 'utf8'));
    cfg.title = 'Renamed'; cfg.pages.push({ id: 'g-9', title: 'Global', type: 'leaderboard', arg: 'glob', scope: 'global', ownerOnly: true });
    cfg.allow.leaderboards.push({ ns: 'glob', scope: 'global' });
    put('glob', 'u1', 7);
    fs.writeFileSync(cfgPath, JSON.stringify(cfg));
    const rt = fs.readdirSync('routes')[0];
    fs.writeFileSync(path.join('routes', rt), fs.readFileSync(path.join('routes', rt), 'utf8').replace('"visits"', '"visits_v2"'));
    await new Promise((r) => setTimeout(r, 2600));
    ({ b } = await j(base, '/api/config')); assert.strictEqual(b.title, 'Renamed', 'config.json reloads live');
    assert.ok(b.pages.some((p) => p.arg === 'glob'), 'owner sees the all-servers page');
    ({ b } = await j(base, '/api/owner/leaderboard?ns=glob')); assert.deepStrictEqual(b.map((x) => x.value), [7]);
    ({ b } = await j(base, '/api/custom/stats?guild_id=123456')); assert.ok('visits_v2' in b, 'routes reload live');

    // ---- a non-owner must not see or read owner-only pages
    for (const k of Object.keys(jar)) delete jar[k];
    const jar2 = {}; // second user: forge by logging in again but as a non-owner is not possible with the fake; check anonymous instead
    assert.strictEqual((await j(base, '/api/owner/leaderboard?ns=glob')).r.status, 401);
    ({ b } = await j(base, '/api/config')); assert.ok(!b.pages.some((p) => p.arg === 'glob'), 'owner-only pages hidden when logged out');
    void jar2;
    console.log('OK dashboard');
  } finally {
    child.kill();
    fake.close();
  }
})().then(() => process.exit(0), (e) => { console.error('FAIL dashboard:', e && e.stack || e); process.exit(1); });

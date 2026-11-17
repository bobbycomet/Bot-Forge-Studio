'use strict';
// Usage (inside <bot>/dashboard with node_modules): node ws_harness.js <base-url> <session-cookie>
const WebSocket = require('ws');
const assert = require('node:assert');
const [base, cookie] = process.argv.slice(2);
const wsUrl = base.replace('http', 'ws') + '/ws';
const open = (headers) => new Promise((resolve, reject) => {
  const ws = new WebSocket(wsUrl, { headers });
  const msgs = [];
  ws.on('message', (d) => msgs.push(JSON.parse(String(d))));
  ws.on('open', () => resolve({ ws, msgs }));
  ws.on('unexpected-response', (_q, res) => reject(Object.assign(new Error('rejected'), { status: res.statusCode })));
  ws.on('error', () => {});
});
const wait = (ms) => new Promise((r) => setTimeout(r, ms));
(async () => {
  // no cookie / foreign origin are refused before the upgrade completes
  await assert.rejects(open({ origin: base }), (e) => e.status === 401, 'anonymous socket must be 401');
  await assert.rejects(open({ origin: 'https://evil.example', cookie: `bs_sid=${cookie}` }), (e) => e.status === 403, 'foreign origin must be 403');
  await assert.rejects(open({ cookie: `bs_sid=${cookie}` }), (e) => e.status === 403, 'missing origin must be 403');
  const { ws, msgs } = await open({ origin: base, cookie: `bs_sid=${cookie}` });
  await wait(200);
  assert.strictEqual(msgs[0].type, 'hello');
  ws.send(JSON.stringify({ type: 'subscribe', guild: '999' })); await wait(200);
  assert.strictEqual(msgs.at(-1).type, 'error', 'an unmanaged server cannot be subscribed');
  ws.send(JSON.stringify({ type: 'subscribe', guild: '123456789012345678' })); await wait(300);
  const st = msgs.find((m) => m.type === 'status');
  assert.ok(st && st.data.online === true, 'subscribe answers with the bot status');
  const r = await fetch(`${base}/__test/live`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ event: 'score', data: { n: 7 } }) });
  assert.strictEqual(r.status, 200);
  await wait(300);
  assert.ok(msgs.some((m) => m.type === 'event' && m.event === 'score' && m.data.n === 7), 'live events reach subscribed sockets');
  ws.send('not json'); await wait(150);
  assert.strictEqual(msgs.at(-1).type, 'error');
  ws.close();
  console.log('OK websocket');
  process.exit(0);
})().catch((e) => { console.error('FAIL websocket:', e && e.stack || e); process.exit(1); });

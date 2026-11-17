'use strict';
/**
 * Fake Discord for the dashboard's TEST MODE (BS_TEST_MODE=1): just enough REST for the generated pages to work
 * without a bot token, a real server or OAuth.  Never used in production.
 */
const http = require('node:http');

const GUILD = '123456789012345678';
const USER = { id: '1', username: 'tester', global_name: 'Test User', avatar: null };
const members = { '42': { nick: 'Alice', user: { id: '42', username: 'alice' } }, '43': { nick: 'Bob', user: { id: '43', username: 'bob' } } };
const posted = [];

function startMock() {
  return new Promise((resolve) => {
    const server = http.createServer((req, res) => {
      let body = '';
      req.on('data', (c) => { body += c; });
      req.on('end', () => {
        const send = (code, obj) => { res.writeHead(code, { 'content-type': 'application/json' }); res.end(JSON.stringify(obj)); };
        const p = new URL(req.url, 'http://x').pathname.replace(/^\/api\/v10/, '');
        if (p === '/oauth2/applications/@me') return send(200, { id: '777', name: 'Test Bot', owner: USER });
        if (p === '/users/@me/guilds') return send(200, [{ id: GUILD }]);
        if (p === `/guilds/${GUILD}`) return send(200, { id: GUILD, name: 'Test Server', icon: null, approximate_member_count: 128, premium_subscription_count: 3, roles: [{}, {}, {}, {}] });
        if (p === `/guilds/${GUILD}/channels`) return send(200, [{ id: '900', type: 0, name: 'general' }, { id: '901', type: 0, name: 'announcements' }, { id: '902', type: 2, name: 'voice' }]);
        let m = p.match(new RegExp(`^/guilds/${GUILD}/members/(\\d+)$`));
        if (m) return members[m[1]] ? send(200, members[m[1]]) : send(404, { message: 'Unknown Member' });
        m = p.match(/^\/users\/(\d+)$/);
        if (m) return send(200, { id: m[1], username: `user${m[1]}`, global_name: `User ${m[1]}` });
        if (/^\/channels\/\d+\/messages$/.test(p) && req.method === 'POST') { try { posted.push(JSON.parse(body)); } catch (_) { /* ignore */ } return send(200, { id: String(Date.now()) }); }
        return send(404, { message: 'not found', code: 0 });
      });
    });
    server.listen(0, '127.0.0.1', () => resolve({ port: server.address().port, server, GUILD, USER, posted }));
  });
}
module.exports = { startMock, GUILD, USER };

// bg3-data-mcp bridge for Vortex (optional; installed by the MCP tool bg3_vortex_bridge_install).
// A tiny HTTP server on 127.0.0.1 that runs Vortex's own actions (deploy, purge, enable/disable, remove, and the Nexus
// update check / download / install through Vortex's own Nexus login) for the active game, so the MCP never touches
// Vortex's database, deployment files or Nexus credentials itself.
// Every request must carry the token from bridge.json (written next to this file, readable by your user only).
const http = require('http');
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');

function main(context) {
  context.once(() => {
    const api = context.api;
    let actions = null;
    let selectors = null;
    try { ({ actions, selectors } = require('vortex-api')); } catch (e) { /* older Vortex: enable/disable unavailable */ }
    const cfgPath = path.join(__dirname, 'bridge.json');
    let cfg = {};
    try { cfg = JSON.parse(fs.readFileSync(cfgPath, 'utf8')); } catch (e) { /* first run */ }
    if (!cfg.token) cfg.token = crypto.randomBytes(24).toString('hex');
    cfg.port = cfg.port || 17846;

    const state = () => api.getState();
    const gameId = () => (selectors ? selectors.activeGameId(state()) : state().settings.gameMode.current);
    const profileId = () => (selectors ? selectors.activeProfile(state()) || {} : {}).id;
    const modsOf = (g) => (state().persistent.mods || {})[g] || {};
    const findMod = (g, q) => {
      const mods = modsOf(g);
      if (mods[q]) return q;
      const hit = Object.keys(mods).filter(id => id.toLowerCase().includes(String(q).toLowerCase()));
      if (hit.length !== 1) throw new Error(hit.length ? `ambiguous mod '${q}': ${hit.join(' | ')}` : `no mod matching '${q}'`);
      return hit[0];
    };
    const nexusOf = (m) => {
      const a = m.attributes || {};
      return { modId: a.modId, fileId: a.fileId, newestVersion: a.newestVersion, newestFileId: a.newestFileId,
               source: m.attributes && m.attributes.source };
    };
    const downloads = () => {
      const files = (state().persistent.downloads || {}).files || {};
      return Object.entries(files).map(([id, d]) => ({
        id, state: d.state, game: d.game, localPath: d.localPath, received: d.received, size: d.size,
        version: ((d.modInfo || {}).version) || ((((d.modInfo || {}).nexus || {}).fileInfo || {}).version),
        modId: (((d.modInfo || {}).nexus || {}).ids || {}).modId, fileId: (((d.modInfo || {}).nexus || {}).ids || {}).fileId,
        started: d.startTime,
      }));
    };
    const status = () => {
      const g = gameId();
      const prof = (state().persistent.profiles || {})[profileId()] || {};
      const enabled = prof.modState || {};
      const needDeploy = ((state().persistent.deployment || {}).needToDeploy || {})[g];
      return {
        gameId: g, profile: prof.name, needToDeploy: !!needDeploy,
        mods: Object.values(modsOf(g)).map(m => ({
          id: m.id, version: (m.attributes || {}).version, name: (m.attributes || {}).name,
          state: m.state, enabled: !!(enabled[m.id] || {}).enabled, installationPath: m.installationPath, nexus: nexusOf(m),
        })),
      };
    };
    const emit = (event, ...args) => new Promise((resolve, reject) => {
      api.events.emit(event, ...args, (err) => (err ? reject(err) : resolve()));
    });
    const handlers = {
      'GET /status': async () => status(),
      'POST /deploy': async () => { await emit('deploy-mods'); return status(); },
      'POST /purge': async () => {
        await new Promise((res, rej) => api.events.emit('purge-mods', false, (err) => (err ? rej(err) : res())));
        return status();
      },
      'POST /enable': async (b) => {
        if (!actions) throw new Error('vortex-api actions unavailable in this Vortex');
        const g = gameId(); const id = findMod(g, b.mod);
        api.store.dispatch(actions.setModEnabled(profileId(), id, b.enabled !== false));
        return { mod: id, enabled: b.enabled !== false, ...status() };
      },
      // Nexus, through Vortex's own Nexus login (no API key in the MCP): ask Nexus for each mod's newest file
      'POST /check-updates': async (b) => {
        const g = gameId();
        const mods = b.mod ? { [findMod(g, b.mod)]: modsOf(g)[findMod(g, b.mod)] } : modsOf(g);
        if (api.emitAndAwait) await api.emitAndAwait('check-mods-version', g, mods, true);
        else await new Promise((res) => { api.events.emit('check-mods-version', g, mods, true); setTimeout(res, 8000); });
        return status();
      },
      // download a mod's newest file: Premium downloads directly; otherwise Vortex opens the Nexus files page for one click
      'POST /update': async (b) => {
        const g = gameId(); const id = findMod(g, b.mod); const n = nexusOf(modsOf(g)[id]);
        if (!n.modId) throw new Error(`${id} has no Nexus mod id`);
        const fileId = b.fileId || n.newestFileId;
        if (!fileId) throw new Error(`no newer file known for ${id} - run check-updates first`);
        api.events.emit('mod-update', g, String(n.modId), String(fileId), 'nexus');
        return { mod: id, modId: n.modId, fileId, note: 'download started (or the Nexus page opened for a free account)' };
      },
      'GET /downloads': async () => ({ downloads: downloads() }),
      // install a finished download as a new staged mod (not enabled: the MCP switches versions explicitly)
      'POST /install': async (b) => {
        const modId = await new Promise((res, rej) => {
          const cb = (err, id) => (err ? rej(err) : res(id));
          api.events.emit('start-install-download', b.download, { allowAutoEnable: false }, cb);
        });
        return { installed: modId, ...status() };
      },
      'POST /remove': async (b) => {
        const g = gameId(); const id = findMod(g, b.mod);
        await new Promise((res, rej) => api.events.emit('remove-mod', g, id, (err) => (err ? rej(err) : res())));
        return { removed: id, ...status() };
      },
    };

    const server = http.createServer((req, res) => {
      const send = (code, obj) => { res.writeHead(code, { 'Content-Type': 'application/json' }); res.end(JSON.stringify(obj)); };
      if (req.headers['x-bridge-token'] !== cfg.token) return send(403, { error: 'bad token' });
      const h = handlers[`${req.method} ${req.url.split('?')[0]}`];
      if (!h) return send(404, { error: 'unknown endpoint', endpoints: Object.keys(handlers) });
      let body = '';
      req.on('data', (c) => { body += c; if (body.length > 65536) req.destroy(); });
      req.on('end', async () => {
        try { send(200, await h(body ? JSON.parse(body) : {})); }
        catch (e) { send(500, { error: String((e && e.message) || e) }); }
      });
    });
    server.on('error', (e) => api.showErrorNotification && api.showErrorNotification('bg3-data-mcp bridge', e, { allowReport: false }));
    server.listen(cfg.port, '127.0.0.1', () => {
      cfg.pid = process.pid; cfg.started = new Date().toISOString();
      fs.writeFileSync(cfgPath, JSON.stringify(cfg, null, 2), { mode: 0o600 });
    });
  });
  return true;
}

module.exports = { default: main };

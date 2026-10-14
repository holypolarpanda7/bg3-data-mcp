// bg3-data-mcp bridge for Vortex (optional; installed by the MCP tool bg3_vortex_bridge_install).
// A tiny HTTP server on 127.0.0.1 that runs Vortex's own actions (deploy, purge, enable/disable, remove, profile switch, and the Nexus
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
    const profilesOf = (g) => Object.values(state().persistent.profiles || {}).filter(p => p.gameId === g).map(p => ({
      id: p.id, name: p.name, lastActivated: p.lastActivated,
      enabled: Object.keys(p.modState || {}).filter(id => (p.modState[id] || {}).enabled).length,
    }));
    const nexusOf = (m) => {
      const a = m.attributes || {};
      return { modId: a.modId, fileId: a.fileId, newestVersion: a.newestVersion, newestFileId: a.newestFileId,
               source: m.attributes && m.attributes.source };
    };
    const downloads = () => {
      const files = (state().persistent.downloads || {}).files || {};
      const g = gameId();
      return Object.entries(files).filter(([, d]) => (d.game || []).includes(g)).map(([id, d]) => ({
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
    // Answer Vortex's own prompts during an install / update the MCP started (e.g. "Update current profile" vs "Update all
    // profiles" when a new version replaces one enabled in several profiles). Rules expire, so the user's own installs are untouched.
    let answerRules = [];
    const answered = [];
    const autoAnswer = (rules, ttlMs) => { const until = Date.now() + ttlMs; answerRules = rules.map(r => ({ ...r, until })); };
    const labelOf = (a) => (typeof a === 'string' ? a : (a && a.label) || '');
    api.store.subscribe(() => {
      if (!answerRules.length || !actions || !actions.closeDialog) return;
      answerRules = answerRules.filter(r => r.until > Date.now());
      const dialogs = ((state().session.notifications || {}).dialogs) || [];
      for (const d of dialogs) {
        if (answered.includes(d.id)) continue;
        for (const r of answerRules) {
          const label = (d.actions || []).map(labelOf).find(l => new RegExp(r.label, 'i').test(l));
          if (label && (!r.title || new RegExp(r.title, 'i').test(`${d.title} ${JSON.stringify(d.content || {})}`))) {
            answered.push(d.id);
            // Vortex 2.8.2 reads result.input.remember: answer with the dialog's checkboxes at their defaults
            const input = {}; for (const c of ((d.content || {}).checkboxes || [])) input[c.id] = !!c.value;
            setTimeout(() => api.store.dispatch(actions.closeDialog(d.id, label, input)), 200);
            break;
          }
        }
      }
    });
    const installRules = (b) => [{ label: (b && b.profiles) === 'all' ? '^update all profiles$|all profiles' : '^update current profile$|current profile' }];
    const emit = (event, ...args) => new Promise((resolve, reject) => {
      api.events.emit(event, ...args, (err) => (err ? reject(err) : resolve()));
    });
    const handlers = {
      'GET /status': async () => status(),
      'GET /answered': async () => ({ answered, rules: answerRules }),
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
        autoAnswer(installRules(b), 30 * 60000);   // the download and its install finish after this call returns
        api.events.emit('mod-update', g, String(n.modId), String(fileId), 'nexus');
        return { mod: id, modId: n.modId, fileId, note: 'download started (or the Nexus page opened for a free account)' };
      },
      // the file-based load order Vortex's BG3 extension keeps (what it writes modsettings.lsx from)
      'GET /loadorder': async () => ({ profile: profileId(), loadOrder: ((state().persistent.loadOrder || {})[profileId()]) || [] }),
      'POST /loadorder': async (b) => {
        if (!actions || !actions.setFBLoadOrder) throw new Error('this Vortex does not expose setFBLoadOrder');
        if (!Array.isArray(b.loadOrder)) throw new Error('loadOrder must be an array');
        api.store.dispatch(actions.setFBLoadOrder(profileId(), b.loadOrder));
        return { profile: profileId(), count: b.loadOrder.length };
      },
      // profiles of the active game; POST /profile switches to one (by id or name), creating it when asked
      'GET /profiles': async () => ({ active: profileId(), profiles: profilesOf(gameId()) }),
      'POST /profile': async (b) => {
        if (!actions) throw new Error('vortex-api actions unavailable in this Vortex');
        const g = gameId();
        let p = profilesOf(g).find(x => x.id === b.profile || (x.name || '').toLowerCase() === String(b.profile).toLowerCase());
        let created = false;
        if (!p) {
          if (!b.create) throw new Error(`no ${g} profile '${b.profile}'`);
          const id = crypto.randomBytes(6).toString('base64').replace(/[^A-Za-z0-9]/g, 'x');
          api.store.dispatch(actions.setProfile({ id, gameId: g, name: String(b.profile), modState: {}, lastActivated: 0 }));
          p = { id, name: String(b.profile) };
          created = true;
        }
        const from = profileId();
        if (from !== p.id) {
          api.store.dispatch(actions.setNextProfile(p.id));   // Vortex purges the old profile's deployment and activates this one
          const until = Date.now() + (b.timeoutMs || 180000);
          while (profileId() !== p.id || ((state().settings.profiles || {}).nextProfileId && state().settings.profiles.nextProfileId !== p.id)) {
            if (Date.now() > until) throw new Error(`timed out switching to profile '${p.name}'`);
            await new Promise(r => setTimeout(r, 500));
          }
        }
        return { switched: from !== p.id, from, created, ...status() };
      },
      // enable / disable many mods of the active profile at once (ids exactly as /status lists them)
      'POST /modstate': async (b) => {
        if (!actions) throw new Error('vortex-api actions unavailable in this Vortex');
        const g = gameId(); const mods = modsOf(g); const pid = profileId();
        const unknown = [...(b.enable || []), ...(b.disable || [])].filter(id => !mods[id]);
        if (unknown.length) throw new Error(`unknown mod id(s): ${unknown.join(' | ')}`);
        for (const id of b.disable || []) api.store.dispatch(actions.setModEnabled(pid, id, false));
        for (const id of b.enable || []) api.store.dispatch(actions.setModEnabled(pid, id, true));
        return status();
      },
      // a Nexus mod that is not installed yet, through Vortex's Nexus login: its info, its files, and downloading one
      'POST /nexus-info': async (b) => {
        const g = gameId(); const ext = api.ext || {};
        const info = ext.nexusGetModInfo ? await ext.nexusGetModInfo(g, Number(b.modId)) : null;
        const files = ext.nexusGetModFiles ? await ext.nexusGetModFiles(g, Number(b.modId)) : null;
        if (!info && !files) throw new Error('this Vortex does not expose nexusGetModInfo / nexusGetModFiles');
        return { info, files };
      },
      'POST /nexus-download': async (b) => {
        const g = gameId(); const ext = api.ext || {};
        let fileId = b.fileId;
        if (!fileId) {
          const files = (ext.nexusGetModFiles ? await ext.nexusGetModFiles(g, Number(b.modId)) : []) || [];
          const main = files.filter(f => f.category_id === 1).sort((x, y) => (y.uploaded_timestamp || 0) - (x.uploaded_timestamp || 0));
          if (!main.length) throw new Error(`no main file found for Nexus mod ${b.modId}`);
          fileId = main[0].file_id;
        }
        autoAnswer(installRules(b), 30 * 60000);
        api.events.emit('mod-update', g, String(b.modId), String(fileId), 'nexus');
        return { modId: b.modId, fileId, note: 'download started (or the Nexus page opened for a free account)' };
      },
      'GET /downloads': async () => ({ downloads: downloads() }),
      // install a finished download as a new staged mod (not enabled: the MCP switches versions explicitly)
      'POST /install': async (b) => {
        autoAnswer(installRules(b), 10 * 60000);
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
      const send = (code, obj) => { res.writeHead(code, { 'Content-Type': 'application/json; charset=utf-8' }); res.end(JSON.stringify(obj)); };
      if (req.headers['x-bridge-token'] !== cfg.token) return send(403, { error: 'bad token' });
      const h = handlers[`${req.method} ${req.url.split('?')[0]}`];
      if (!h) return send(404, { error: 'unknown endpoint', endpoints: Object.keys(handlers) });
      let body = '';
      req.setEncoding('utf8');
      req.on('data', (c) => { body += c; if (body.length > 16 * 1024 * 1024) req.destroy(); });
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

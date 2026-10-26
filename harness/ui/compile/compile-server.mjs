// Warm compile service: the stock gravty-ui rules screen, headless, kept loaded so a rule XML can be checked in ~2 s.
// POST /compile {xml, tenant?, canvas?: 'rules'|'segment', path?: 'upload'|'append'} -> what the UI made of it
// (with a tenant, `mocked` lists the GET paths the fixture answered because the tenant's dumps had no response):
// rules: canonical XML, DRL, validation messages, exceptions, dropped/changed fields, and the counters the upload
// dialog created (the offer starts with none); segment: the add-segment screen's saved XML, DRL, target_json, error.
// POST /introspect {types, tenant?} -> the dropdown options the screen offers per block field (tools/block_reference.py --live).
// usage: node compile-server.mjs [--port=8078] [--pool=2] [--busy=4] [--headful | --connect=http://127.0.0.1:9222]
//   (needs ng serve of gravty-ui on :4200). --connect: open the screens in an already running Chrome (the debug Chrome,
//   so someone can watch), each in its own browser context (a window); that Chrome is never closed, only disconnected.
import puppeteer from 'puppeteer-core';
import http from 'node:http';
import { openScreen, loadInScreen, loadSegmentInScreen, compareInPage, CHROME, CHROME_ARGS, tempProfile, dropProfile } from './screen.mjs';

const args = Object.fromEntries(process.argv.slice(2).map((a) => { const [k, v] = a.replace(/^--/, '').split('='); return [k, v ?? true]; }));
const PORT = Number(args.port || 8078);
const POOL = Number(args.pool || 2);

// --connect: one tab in the watched Chrome, one screen at a time; the next screen is prepared there after each compile
const SINGLE = !!args.connect;
const BUSY = SINGLE ? 1 : Number(args.busy || 4);  // compiles at once; more queue (a crowded Chrome crashes under load)

// The browser survives nothing it does not have to: when Chrome dies, the pools are dropped and the next request
// launches a fresh one; a request that was in flight gets a 'not loaded' answer, never a dead server.
let browser = null;
async function getBrowser() {
  if (!browser && args.connect) {
    browser = puppeteer.connect({ browserURL: args.connect, defaultViewport: null, protocolTimeout: 120000 });
    const b = await browser.catch((e) => { browser = null; throw e; });
    b.on('disconnected', () => { console.log('chrome disconnected: reconnecting on the next request'); browser = null; tab = null; single = null; });
  }
  if (!browser) {
    const profile = tempProfile('compile');
    browser = puppeteer.launch({
      executablePath: CHROME, headless: !args.headful, protocolTimeout: 120000, userDataDir: profile, args: CHROME_ARGS,
    });
    const b = await browser.catch((e) => { browser = null; dropProfile(profile); throw e; });
    b.on('disconnected', () => {
      console.log('chrome disconnected: relaunching on the next request');
      browser = null; spares.clear(); dropProfile(profile);
    });
  }
  return browser;
}

// ready screens per (screen, tenant) ('' = fixture): each is used once (an upload creates counters on its offer), then
// closed
const spares = new Map();
const failed = (e) => ({ error: `screen did not open: ${String(e && e.message || e).slice(0, 300)}`, console: [], context: { close: async () => {} } });
function warm(tenant, screen = 'rules') {
  const key = `${screen}:${tenant}`;
  const list = spares.get(key) || [];
  spares.set(key, list);
  while (list.length < POOL) {
    const ctx = { counters: [], drlGet: [], posted: [] };
    list.push(getBrowser().then((b) => openScreen(b, { tenant: tenant || null, ctx, screen })).catch(failed));
  }
}
// single-tab mode: the localhost:4200 tab already open in that Chrome (else one opened once), and the screen ready in it
let tab = null, single = null, seq = 0;
async function theTab(b) {
  if (!tab || tab.isClosed()) tab = (await b.pages()).find((p) => p.url().startsWith('http://localhost:4200')) || await b.newPage();
  return tab;
}
function prepare(tenant, screen) {
  const id = ++seq, ctx = { counters: [], drlGet: [], posted: [] };
  single = { key: `${screen}:${tenant}`, id, promise: getBrowser().then(async (b) => {
    const s = await openScreen(b, { tenant: tenant || null, ctx, screen, page: await theTab(b) });
    await s.page.evaluate((n) => { window.__spare = n; }, id).catch(() => {});
    return s;
  }).catch(failed) };
}
async function takeSingle(tenant, screen) {
  if (single && single.key !== `${screen}:${tenant}`) { await single.promise; single = null; }
  if (!single) prepare(tenant, screen);
  const { promise, id } = single;
  let s = await promise;
  if (!s.error && (await s.page.evaluate(() => window.__spare).catch(() => null)) !== id) {  // the tab was used since
    prepare(tenant, screen);
    s = await single.promise;
  }
  single = null;
  return s;
}

async function take(tenant, screen = 'rules') {
  if (SINGLE) return takeSingle(tenant, screen);
  warm(tenant, screen);
  const s = await spares.get(`${screen}:${tenant}`).shift();
  warm(tenant, screen);
  return s;
}

let running = 0;
const waiting = [];
async function limited(fn) {
  if (running >= BUSY) await new Promise((ok) => waiting.push(ok));
  running += 1;
  try { return await fn(); } finally { running -= 1; waiting.shift()?.(); }
}
process.on('unhandledRejection', (e) => console.log('unhandled rejection (kept running):', String(e && e.message || e).slice(0, 300)));

async function compile({ xml, tenant = '', canvas = 'rules', path: loadPath = 'upload' }) {
  const t0 = Date.now();
  const s = await take(tenant, canvas);
  try {
    if (s.error) return { loaded: false, error: s.error, console: s.console.slice(0, 20) };
    if (canvas === 'segment') {
      const out = await loadSegmentInScreen(s.page, xml);
      const cmp = out.canonical ? await s.page.evaluate(compareInPage, xml, out.canonical) : {};
      return { ...out, ms: Date.now() - t0, mocked: [...(s.mocked || [])].sort(), validation_errors: out.error ? [out.error] : [], block_warnings: out.warnings,
               ...cmp, console: s.console.filter((l) => !/favicon|DevTools/.test(l)).slice(0, 20) };
    }
    const out = await loadInScreen(s.page, xml, loadPath);
    const cmp = out.canonical ? await s.page.evaluate(compareInPage, xml, out.canonical) : {};
    return {
      loaded: true, ms: Date.now() - t0, drl: out.drl, canonical: out.canonical, validation_errors: out.validation_errors,
      exceptions: out.exceptions, block_warnings: out.warnings, disabled_blocks: out.disabled, top_blocks: out.topBlocks,
      counters_created: s.ctx.posted, mocked: [...(s.mocked || [])].sort(), ...cmp, console: s.console.filter((l) => !/favicon|DevTools/.test(l)).slice(0, 20),
    };
  } finally {
    s.context.close().catch(() => {});
    if (SINGLE) prepare(tenant, canvas);
  }
}

// Every dropdown field of every toolbox block, and for each option of a block's selector fields (OBJECT, action_type,
// ATTRIBUTE_TYPE, ...) the fields/options that choice reveals: one level deep, nothing block-specific.
async function introspect({ tenant = '', types = [] }) {
  const s = await take(tenant);
  try {
    return await s.page.evaluate((toolboxTypes) => {
      const comp = window.ng.getComponent(document.querySelector('app-offer-rules'));
      const ws = comp.workspace.workspace;
      const opts = (f) => { try { return f.getOptions(false).map((o) => (typeof o[0] === 'string' ? o[1] : o[1])).filter((v) => v !== '' && v !== '--'); } catch { return null; } };
      const shape = (b) => {
        const out = {};
        for (const inp of b.inputList) {
          for (const f of inp.fieldRow) {
            if (!f.name) continue;
            const o = f.getOptions ? opts(f) : null;
            out[f.name] = o ? o.slice(0, 40) : (f.getValue ? `<${typeof f.getValue()}>` : '');
          }
          if (inp.connection) out[`<${inp.type === 1 ? 'value' : 'statement'}:${inp.name}>`] = inp.connection.getCheck() || 'any';
        }
        return out;
      };
      const pick = (b, field, v) => {
        const f = b.getField(field);
        if (typeof f.onItemSelected_ === 'function' && Object.prototype.hasOwnProperty.call(f, 'onItemSelected_')) {
          f.onItemSelected_(null, { getValue: () => v });
        } else f.setValue(v);
      };
      const res = {};
      for (const t of toolboxTypes) {
        let b;
        try { b = ws.newBlock(t); b.initSvg?.(); b.render?.(); } catch (e) { res[t] = { error: String(e).slice(0, 200) }; continue; }
        const base = shape(b);
        const entry = { base };
        for (const [field, o] of Object.entries(base)) {
          if (field.startsWith('<') || !Array.isArray(o) || o.length > 12 || o.length < 2) continue;
          entry[field] = {};
          for (const v of o) {
            let nb;
            try { nb = ws.newBlock(t); nb.initSvg?.(); nb.render?.(); pick(nb, field, v); entry[field][v] = shape(nb); } catch (e) { entry[field][v] = { error: String(e).slice(0, 160) }; }
            try { nb?.dispose(false); } catch {}
          }
        }
        res[t] = entry;
        try { b.dispose(false); } catch {}
      }
      return res;
    }, types);
  } finally {
    s.context.close().catch(() => {});
    if (SINGLE) prepare(tenant, 'rules');
  }
}

const routes = { '/compile': (b) => limited(() => compile(b)), '/introspect': (b) => limited(() => introspect(b)) };
http.createServer((req, res) => {
  const send = (code, obj) => { res.writeHead(code, { 'Content-Type': 'application/json' }); res.end(JSON.stringify(obj)); };
  if (req.method === 'GET' && req.url === '/health') return send(200, { ok: true, running, waiting: waiting.length, pool: Object.fromEntries([...spares].map(([k, v]) => [k, v.length])) });
  const fn = routes[req.url];
  if (req.method !== 'POST' || !fn) return send(404, { error: 'POST /compile or /introspect' });
  let body = '';
  req.on('data', (c) => { body += c; });
  req.on('end', async () => {
    try { send(200, await fn(JSON.parse(body || '{}'))); } catch (e) { send(500, { error: String(e && e.stack || e).slice(0, 2000) }); }
  });
}).listen(PORT, '127.0.0.1', () => { warm(''); console.log(`compile-server on :${PORT} (pool ${POOL})`); });

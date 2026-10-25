// Warm compile service: the stock gravty-ui rules screen, headless, kept loaded so a rule XML can be checked in ~2 s.
// POST /compile {xml, tenant?, path?: 'upload'|'append'} -> what the UI made of it: canonical XML, DRL, validation
// messages, exceptions, dropped/changed fields, and the counters the upload dialog created (the offer starts with none).
// POST /introspect {types, tenant?} -> the dropdown options the screen offers per block field (tools/block_reference.py --live).
// usage: node compile-server.mjs [--port=8078] [--pool=2] [--headful]   (needs ng serve of gravty-ui on :4200)
import puppeteer from 'puppeteer-core';
import fs from 'node:fs';
import http from 'node:http';
import path from 'node:path';
import { openScreen, loadInScreen, compareInPage, CHROME, CHROME_ARGS } from './screen.mjs';

const HERE = path.dirname(new URL(import.meta.url).pathname);
const args = Object.fromEntries(process.argv.slice(2).map((a) => { const [k, v] = a.replace(/^--/, '').split('='); return [k, v ?? true]; }));
const PORT = Number(args.port || 8078);
const POOL = Number(args.pool || 2);

const browser = await puppeteer.launch({
  executablePath: CHROME, headless: !args.headful, protocolTimeout: 120000,
  userDataDir: fs.mkdtempSync(path.join(HERE, 'tmp-profile-compile-')), args: CHROME_ARGS,
});

// ready screens per tenant ('' = fixture): each is used once (the upload creates counters on its offer), then closed
const spares = new Map();
function warm(tenant) {
  const list = spares.get(tenant) || [];
  spares.set(tenant, list);
  while (list.length < POOL) {
    const ctx = { counters: [], drlGet: [], posted: [] };
    list.push(openScreen(browser, { tenant: tenant || null, ctx }));
  }
}
async function take(tenant) {
  warm(tenant);
  const s = await spares.get(tenant).shift();
  warm(tenant);
  return s;
}

async function compile({ xml, tenant = '', path: loadPath = 'upload' }) {
  const t0 = Date.now();
  const s = await take(tenant);
  try {
    if (s.error) return { loaded: false, error: s.error, console: s.console.slice(0, 20) };
    const out = await loadInScreen(s.page, xml, loadPath);
    const cmp = out.canonical ? await s.page.evaluate(compareInPage, xml, out.canonical) : {};
    return {
      loaded: true, ms: Date.now() - t0, drl: out.drl, canonical: out.canonical, validation_errors: out.validation_errors,
      exceptions: out.exceptions, block_warnings: out.warnings, disabled_blocks: out.disabled, top_blocks: out.topBlocks,
      counters_created: s.ctx.posted, ...cmp, console: s.console.filter((l) => !/favicon|DevTools/.test(l)).slice(0, 20),
    };
  } finally {
    s.context.close().catch(() => {});
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
  }
}

const routes = { '/compile': compile, '/introspect': introspect };
http.createServer((req, res) => {
  const send = (code, obj) => { res.writeHead(code, { 'Content-Type': 'application/json' }); res.end(JSON.stringify(obj)); };
  if (req.method === 'GET' && req.url === '/health') return send(200, { ok: true, pool: Object.fromEntries([...spares].map(([k, v]) => [k || 'fixture', v.length])) });
  const fn = routes[req.url];
  if (req.method !== 'POST' || !fn) return send(404, { error: 'POST /compile or /introspect' });
  let body = '';
  req.on('data', (c) => { body += c; });
  req.on('end', async () => {
    try { send(200, await fn(JSON.parse(body || '{}'))); } catch (e) { send(500, { error: String(e && e.stack || e).slice(0, 2000) }); }
  });
}).listen(PORT, '127.0.0.1', () => { warm(''); console.log(`compile-server on :${PORT} (pool ${POOL})`); });

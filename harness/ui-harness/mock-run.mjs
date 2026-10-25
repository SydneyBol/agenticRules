// Drive the REAL gravty-ui offer-rules Blockly screen against a fully mocked backend.
// usage: node mock-run.mjs [--path=append|upload|drl] [--only=S01,S14] [--discover] [--probe] [--headful]
import puppeteer from 'puppeteer-core';
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import * as M from './fixtures/mocks.mjs';

const HERE = path.dirname(new URL(import.meta.url).pathname);
const ROOT = path.resolve(HERE, '..');
const argOf = (k, d) => (process.argv.find((a) => a.startsWith(`--${k}=`)) || '').split('=')[1] || d;
// --in / --out: input dir of <scenario>.xml (+ .spec.json) and output dir, relative to results/
const COMPILED = path.join(ROOT, 'results', argOf('in', 'compiled'));
const OUT = path.join(ROOT, 'results', argOf('out', 'ui'));
fs.mkdirSync(OUT, { recursive: true });

const args = Object.fromEntries(process.argv.slice(2).map((a) => {
  const [k, v] = a.replace(/^--/, '').split('='); return [k, v ?? true];
}));
const LOAD_PATH = args.path || 'append';
const SUFFIX = args.tagdelay ? `${LOAD_PATH}-tagdelay${args.tagdelay}` : LOAD_PATH;
const only = args.only ? String(args.only).split(',') : null;
const CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const APP = 'http://localhost:4200';

const scenarios = fs.readdirSync(COMPILED).filter((f) => f.endsWith('.xml'))
  .map((f) => f.replace(/\.xml$/, ''))
  .filter((s) => !only || only.some((o) => s.startsWith(o)))
  .sort();

const unknown = new Map(); // "METHOD path" -> count
const endpointsHit = new Map();

// --tenant=devintam1: the catalog GETs come from that tenant's bolapi dumps (catalogs/<t>/bolapi),
// so XML written in its codes (stores, activity types, accounts, privileges) loads as it does on the hosted screen
const TENANT = args.tenant ? path.resolve(HERE, '../../catalogs', args.tenant, 'bolapi') : null;
const tdump = (f) => { const x = path.join(TENANT, f + '.json'); return fs.existsSync(x) ? JSON.parse(fs.readFileSync(x, 'utf8')).body : null; };
const LOOKUP_DUMPS = { TIER_CLASS: 'extra_tierclass', PRODUCT_CATEGORY: 'extra_prodcat' };  // as Catalog.from_bolapi_dumps maps them
function tenantGet(u) {
  const p = u.pathname, q = u.searchParams;
  const table = [
    [/\/attributelibrary\/activity\/?$/, () => tdump('activity_attributes')],
    [/\/attributelibrary\/member\/?$/, () => tdump('member_attributes')],
    [/\/attributelibrary\/custom\/?$/, () => (q.get('module_type') === 'PRODUCT' ? tdump('product_attributes') : null)],
    // typed (lookups/?type=X) or all lookups at once, which the screen filters by type itself
    [/\/lookups\/?$/, () => (q.get('type') ? tdump('lookup_' + q.get('type')) || tdump(LOOKUP_DUMPS[q.get('type')] || '-')
      : [...fs.readdirSync(TENANT).filter((f) => f.startsWith('lookup_')).map((f) => f.slice(0, -5)), ...Object.values(LOOKUP_DUMPS)]
          .flatMap((f) => tdump(f) || []))],
    [/\/bit-?types\/?$/, () => tdump('bit_types')],
    [/\/lovs\/?$/, () => tdump('lovs')],
    [/\/sponsors\/(\d+\/)?locations\/?$/, () => tdump('extra_locations' + (p.match(/sponsors\/(\d+)\//) || [])[1])
      || tdump((fs.readdirSync(TENANT).find((f) => f.startsWith('extra_locations')) || '-').slice(0, -5))],
    [/\/sponsors\/loyaltyaccounts\/?$/, () => tdump('sponsor_loyalty_accounts')],
    [/\/loyaltyaccounts\/(\d+)\/tags\/?$/, () => tdump('extra_tags' + p.match(/loyaltyaccounts\/(\d+)/)[1]) || []],
    [/\/loyaltyaccounts\/?$/, () => tdump('loyalty_accounts_all')],
    [/\/trim\/product\/?$|\/product\/?$/, () => tdump('privilege_products')],
    [/\/tier-classes\/?$/, () => tdump('extra_tierclass')],
    [/\/tier-list\/?$/, () => tdump('extra_tiers')],
    [/\/(trim\/)?sponsors\/?$/, () => tdump('sponsors')],
  ];
  const hit = table.find(([re]) => re.test(p));
  return hit ? hit[1]() : null;
}

function isLocal(u) { return u.hostname === 'localhost' && u.port === '4200'; }

async function runScenario(browser, scen, loadPath) {
  const xml = fs.readFileSync(path.join(COMPILED, scen + '.xml'), 'utf8');
  const spec = JSON.parse(fs.readFileSync(path.join(COMPILED, scen + '.spec.json'), 'utf8'));
  // --discover: the offer starts with no counters, so the stock upload path must create them (POSTs recorded)
  const ctx = { counters: args.discover ? [] : (spec.counters || []), drlGet: [], posted: [] };
  if (loadPath === 'drl') {
    ctx.drlGet = [{ id: 77, offer: M.OFFER_ID, xml: Buffer.from(xml, 'utf8').toString('base64'), drl: '' }];
  }
  const context = await browser.createBrowserContext();
  const page = await context.newPage();
  await page.setViewport({ width: 1600, height: 1000 });
  const consoleLines = [];
  page.on('console', (m) => { const t = m.text(); if (m.type() === 'error' || m.type() === 'warn') consoleLines.push(`[${m.type()}] ${t.slice(0, 400)}`); });
  page.on('pageerror', (e) => consoleLines.push(`[pageerror] ${String(e.message).slice(0, 400)}`));

  await page.setRequestInterception(true);
  page.on('request', async (req) => {
    let u;
    try { u = new URL(req.url()); } catch { return req.abort(); }
    if (u.protocol === 'data:' || u.protocol === 'blob:') return req.continue();
    if (isLocal(u)) return req.continue();
    const key = `${req.method()} ${u.hostname}${u.pathname}`;
    if (!/gravty\.info$/.test(u.hostname)) {
      endpointsHit.set('ABORT ' + key, (endpointsHit.get('ABORT ' + key) || 0) + 1);
      return req.abort('blockedbyclient');
    }
    const cors = {
      'Access-Control-Allow-Origin': APP, 'Access-Control-Allow-Credentials': 'true',
      'Access-Control-Allow-Headers': req.headers()['access-control-request-headers'] || '*',
      'Access-Control-Allow-Methods': 'GET,POST,PUT,DELETE,PATCH,OPTIONS',
      'Access-Control-Expose-Headers': 'Next,Prev,Count',
    };
    if (req.method() === 'OPTIONS') return req.respond({ status: 204, headers: cors, body: '' });
    if (args.tagdelay && /\/loyaltyaccounts\/\d+\/tags\/$/.test(u.pathname)) await new Promise((r) => setTimeout(r, Number(args.tagdelay)));
    let body = null;
    try { body = (TENANT && req.method() === 'GET' && tenantGet(u)) || M.route(req.method(), u, req.postData(), ctx); } catch (e) { body = null; }
    if (req.method() === 'POST' && /\/member-tracked-attributes\/$/.test(u.pathname) && body) {
      ctx.posted.push(JSON.parse(req.postData() || '{}'));
      ctx.counters.push({ name: body.name, level: 'MA_O', data_type: body.data_type, counter_type: body.counter_type,
                          target_type: body.target_type, vector_namespace: (body.entity_attribute_map?.Activity || [])[0] });
    }
    endpointsHit.set(key, (endpointsHit.get(key) || 0) + 1);
    if (body === null) {
      unknown.set(`${req.method()} ${u.pathname}${u.search}`, (unknown.get(`${req.method()} ${u.pathname}${u.search}`) || 0) + 1);
      body = req.method() === 'GET' ? [] : {};
    }
    return req.respond({ status: 200, headers: { ...cors, 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  });

  await page.evaluateOnNewDocument((globals) => {
    if (location.origin !== 'http://localhost:4200') return;
    localStorage.setItem('globals', JSON.stringify(globals));
    localStorage.setItem('offe_type', 'reward');
    localStorage.setItem('offer_type', 'reward');
    localStorage.setItem('programInfo', JSON.stringify([{ date_format: 'DD/MM/YYYY', timezone: 'UTC' }]));
    localStorage.setItem('enableMultilingual', 'false');
    // record every validation message the blockly service emits
    window.__harness = { errors: [] };
  }, M.globals);

  const res = { scenario: scen, load_path: loadPath, loaded: false };
  const t0 = Date.now();
  await page.setCookie({ name: 'XSRF-COOKIE', value: 'mock-xsrf-local', url: 'http://localhost:4200/' });
await page.goto(`${APP}/#/offer/${M.OFFER_ID}/rules`, { waitUntil: 'domcontentloaded', timeout: 120000 });

  // Wait for the offer-rules component + rendered workspace + metadata forkJoin to finish.
  try {
    await page.waitForFunction(() => {
      const el = document.querySelector('app-offer-rules');
      if (!el || !window.ng) return false;
      const c = window.ng.getComponent(el);
      const ws = c?.workspace?.workspace;
      return !!(ws && ws.rendered && c.blockDefintionService?.attributes?.activity?.length && ws.getAllBlocks(false).length > 0);
    }, { timeout: 90000, polling: 250 });
  } catch (e) {
    res.error = 'timeout waiting for workspace: ' + e.message;
    res.url = page.url();
    res.console = consoleLines.slice(0, 40);
    await page.screenshot({ path: path.join(OUT, scen + '.timeout.png') });
    await context.close();
    return res;
  }
  res.ready_ms = Date.now() - t0;
  // tags are fetched by a separate fire-and-forget subscribe (not in the forkJoin). Record whether present now.
  res.tags_at_ready = await page.evaluate(() => {
    const c = window.ng.getComponent(document.querySelector('app-offer-rules'));
    return JSON.stringify(c.blockDefintionService.tags || null);
  });
  await page.waitForFunction(() => {
    const c = window.ng.getComponent(document.querySelector('app-offer-rules'));
    return !!c.blockDefintionService.tags?.loyalty_account_1;
  }, { timeout: 15000 }).catch(() => { res.tags_timeout = true; });

  const out = await page.evaluate(async (xml, loadPath) => {
    const r = { window_Blockly: typeof window.Blockly };
    const comp = window.ng.getComponent(document.querySelector('app-offer-rules'));
    const ngx = comp.workspace; // NgxBlocklyComponent
    const ws = ngx.workspace;
    const bds = comp.blockDefintionService;
    const errSvc = comp.blockValidationErrorShow;
    // hook validation messages
    const seen = [];
    const origSet = errSvc.setValidationError.bind(errSvc);
    errSvc.setValidationError = (m) => { seen.push(String(m)); return origSet(m); };
    const exceptions = [];
    try {
      if (loadPath === 'append') {
        ws.clear();
        ngx.appendFromXml(xml);
      } else if (loadPath === 'upload') {
        comp.offertype = 'reward';
        comp.rulesUploadConfirmation();
        comp.rulesUploadPopup.close();
        comp.rulesUploadPopup.componentInstance.loadBlocklyFileUpload(xml, true);
      } // 'drl': already loaded by the screen itself from GET offers/{id}/drl/
    } catch (e) { exceptions.push('load: ' + (e && e.stack || e)); }
    await new Promise((res) => setTimeout(res, 1500));
    r.blocks_out = ws.getAllBlocks(false).length;
    let canonical = '';
    try { canonical = ngx.FromXmltoText(); } catch (e) { exceptions.push('toText: ' + e); }
    r.canonical = canonical;
    let drl = '';
    try {
      bds.blockly_validation_error = '';
      errSvc.clearErrors();
      drl = ngx.workspaceToCode(ws.id);
    } catch (e) { exceptions.push('gen: ' + (e && e.stack || e)); }
    r.drl = drl;
    let last = null;
    try { last = errSvc.blocklyValidationError?.value ?? null; } catch {}
    r.validation_errors = [...new Set([...seen, ...(last ? [String(last)] : []), ...(bds.blockly_validation_error ? [String(bds.blockly_validation_error)] : [])])];
    r.exceptions = exceptions;
    r.warnings = ws.getAllBlocks(false).map((b) => b.warning ? `${b.type}#${b.id}: ${b.warning.getText ? b.warning.getText() : ''}` : null).filter(Boolean);
    r.disabled = ws.getAllBlocks(false).filter((b) => !b.isEnabled()).map((b) => `${b.type}#${b.id}`);
    r.tags = JSON.stringify(bds.tags);
    r.topBlocks = ws.getTopBlocks(true).map((b) => b.type);
    return r;
  }, xml, loadPath);
  Object.assign(res, out);
  res.mto_posted = ctx.posted;
  if (out.canonical) res.cmp = await page.evaluate(compareInPage, xml, out.canonical);
  res.console = consoleLines.slice(0, 40);
  res.loaded = true;
  await page.screenshot({ path: path.join(OUT, `${scen}.${loadPath}.png`), fullPage: false });
  if (args.pause) {  // --headful --pause=<ms>: someone is watching; keep each loaded draft on screen
    await page.bringToFront();
    console.log(`  showing ${scen} (${res.cmp?.blocks_out ?? "?"} blocks)`);
    await new Promise((r) => setTimeout(r, Number(args.pause)));
  }
  await context.close();
  return res;
}

// ---------- XML compare (runs in the page, uses the browser DOMParser) ----------
function compareInPage(inXml, outXml) {
  const parse = (x) => {
    const d = new DOMParser().parseFromString(x, 'text/xml');
    return [...d.getElementsByTagName('block')].concat([...d.getElementsByTagName('shadow')]).map((b) => {
      const fields = {}; let mutation = null;
      for (const ch of b.children) {
        if (ch.tagName === 'field') fields[ch.getAttribute('name')] = ch.textContent;
        if (ch.tagName === 'mutation') { mutation = {}; for (const at of ch.attributes) mutation[at.name] = at.value; }
      }
      return { type: b.getAttribute('type'), id: b.getAttribute('id'), fields, mutation };
    });
  };
  const blocksA = parse(inXml), blocksB = parse(outXml);
  const dropped = [], changed = [], mutation_diffs = [];
  const byId = new Map(blocksB.map((x) => [x.id, x]));
  blocksA.forEach((ba, i) => {
    let bb = byId.get(ba.id);
    if (!bb || bb.type !== ba.type) bb = blocksB.filter((x) => x.type === ba.type)[blocksA.filter((x, j) => j < i && x.type === ba.type).length];
    if (!bb) { dropped.push({ block: `${ba.type}#${ba.id}`, field: '*', in: 'BLOCK MISSING' }); return; }
    for (const [k, v] of Object.entries(ba.fields)) {
      if (!(k in bb.fields)) dropped.push({ block: `${ba.type}#${ba.id}`, field: k, in: v });
      else if (bb.fields[k] !== v) changed.push({ block: `${ba.type}#${ba.id}`, field: k, in: v, out: bb.fields[k] });
    }
    for (const [k, v] of Object.entries(ba.mutation || {})) {
      if (k === 'xmlns') continue;
      const o = (bb.mutation || {})[k];
      if (o !== v) mutation_diffs.push({ block: `${ba.type}#${ba.id}`, attr: k, in: v, out: o ?? null });
    }
  });
  const extra_out = blocksB.filter((b) => !blocksA.some((a) => a.id === b.id)).map((b) => `${b.type}#${b.id}`);
  return { blocks_in: blocksA.length, blocks_out: blocksB.length, dropped_fields: dropped, changed_fields: changed, mutation_diffs, extra_out_blocks: extra_out };
}

const browser = await puppeteer.launch({
  executablePath: CHROME, headless: args.headful ? false : true,
  userDataDir: fs.mkdtempSync(path.join(HERE, 'tmp-profile-mock-')),
  args: ['--no-first-run', '--no-default-browser-check', '--disable-extensions', '--disable-background-networking',
    '--disable-component-update', '--disable-sync', '--metrics-recording-only', '--disable-domain-reliability'],
});

const report = fs.existsSync(path.join(OUT, 'report.json')) && !args.fresh ? JSON.parse(fs.readFileSync(path.join(OUT, 'report.json'), 'utf8')) : {};
for (const scen of scenarios) {
  const r = await runScenario(browser, scen, LOAD_PATH);
  const xml = fs.readFileSync(path.join(COMPILED, scen + '.xml'), 'utf8');
  let entry = { loaded: r.loaded, load_path: LOAD_PATH, error: r.error };
  if (r.loaded) {
    const cmp = r.cmp || {};
    fs.writeFileSync(path.join(OUT, `${scen}${SUFFIX === 'append' ? '' : '.' + SUFFIX}.canonical.xml`), r.canonical);
    fs.writeFileSync(path.join(OUT, `${scen}${SUFFIX === 'append' ? '' : '.' + SUFFIX}.drl`), r.drl);
    entry = {
      ...entry, ...cmp, drl_bytes: Buffer.byteLength(r.drl || ''), validation_errors: r.validation_errors,
      exceptions: r.exceptions, block_warnings: r.warnings, disabled_blocks: r.disabled,
      ready_ms: r.ready_ms, tags_at_ready: r.tags_at_ready, tags_timeout: !!r.tags_timeout,
      window_Blockly: r.window_Blockly, top_blocks: r.topBlocks, console: r.console, mto_posted: r.mto_posted,
    };
  } else {
    entry.console = r.console; entry.url = r.url;
  }
  report[`${scen}${SUFFIX === 'append' ? '' : '@' + SUFFIX}`] = entry;
  const e = entry;
  console.log(`${scen.padEnd(40)} [${LOAD_PATH}] loaded=${e.loaded} blocks ${e.blocks_in}->${e.blocks_out} dropped=${e.dropped_fields?.length} changed=${e.changed_fields?.length} mut=${e.mutation_diffs?.length} drl=${e.drl_bytes} verr=${JSON.stringify(e.validation_errors || [])} ${e.error || ''}`);
}
fs.writeFileSync(path.join(OUT, 'report.json'), JSON.stringify(report, null, 1));
fs.writeFileSync(path.join(OUT, 'endpoints.json'), JSON.stringify({ hit: Object.fromEntries(endpointsHit), unknown: Object.fromEntries(unknown) }, null, 1));
console.log('\nUnknown (defaulted) requests:'); for (const [k, v] of unknown) console.log('  ', v, k);
await browser.close();

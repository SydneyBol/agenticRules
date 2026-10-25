// The REAL gravty-ui Blockly screens against a fully mocked backend (shared by mock-run.mjs, segment-run.mjs and
// compile-server.mjs): the offer-rules screen and the add-segment screen. Open one, load XML through the stock path,
// read back what the UI made of it.
import fs from 'node:fs';
import path from 'node:path';
import * as M from './fixtures/mocks.mjs';

const HERE = path.dirname(new URL(import.meta.url).pathname);
export const APP = 'http://localhost:4200';
export const CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
export const CHROME_ARGS = ['--no-first-run', '--no-default-browser-check', '--disable-extensions', '--disable-background-networking',
  '--disable-component-update', '--disable-sync', '--metrics-recording-only', '--disable-domain-reliability'];

// tenant 'devintam1': the catalog GETs come from that tenant's bolapi dumps (catalogs/<t>/bolapi),
// so XML written in its codes (stores, activity types, accounts, privileges) loads as it does on the hosted screen
const LOOKUP_DUMPS = { TIER_CLASS: 'extra_tierclass', PRODUCT_CATEGORY: 'extra_prodcat' };  // as Catalog.from_bolapi_dumps maps them
export function tenantGetter(tenant) {
  if (!tenant) return () => null;
  const dir = path.resolve(HERE, '../../catalogs', tenant, 'bolapi');
  const tdump = (f) => { const x = path.join(dir, f + '.json'); return fs.existsSync(x) ? JSON.parse(fs.readFileSync(x, 'utf8')).body : null; };
  return (u) => {
    const p = u.pathname, q = u.searchParams;
    const table = [
      [/\/attributelibrary\/activity\/?$/, () => tdump('activity_attributes')],
      [/\/attributelibrary\/member\/?$/, () => tdump('member_attributes')],
      [/\/attributelibrary\/custom\/?$/, () => (q.get('module_type') === 'PRODUCT' ? tdump('product_attributes') : null)],
      // typed (lookups/?type=X) or all lookups at once, which the screen filters by type itself
      [/\/lookups\/?$/, () => (q.get('type') ? tdump('lookup_' + q.get('type')) || tdump(LOOKUP_DUMPS[q.get('type')] || '-')
        : [...fs.readdirSync(dir).filter((f) => f.startsWith('lookup_')).map((f) => f.slice(0, -5)), ...Object.values(LOOKUP_DUMPS)]
            .flatMap((f) => tdump(f) || []))],
      [/\/bit-?types\/?$/, () => tdump('bit_types')],
      [/\/lovs\/?$/, () => tdump('lovs')],
      [/\/sponsors\/(\d+\/)?locations\/?$/, () => tdump('extra_locations' + (p.match(/sponsors\/(\d+)\//) || [])[1])
        || tdump((fs.readdirSync(dir).find((f) => f.startsWith('extra_locations')) || '-').slice(0, -5))],
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
  };
}

// Open the rules screen in a fresh browser context. ctx: {counters, drlGet, posted}; POSTed counters are recorded in
// ctx.posted and become part of the offer. stats: {endpoints, unknown} Maps, shared across screens if given.
const page1 = (rows) => (Array.isArray(rows) ? { count: rows.length, next: null, previous: null, results: rows } : rows);
const SCREENS = {
  rules: { hash: `#/offer/${M.OFFER_ID}/rules`, paged: [] },
  // the segment screen reads tier lists as paginated responses
  segment: { hash: `#/offer/${M.OFFER_ID}/selectmembers/membersegment/new`, paged: [/\/tier-list\/?$/, /\/tier-classes\/?$/] },
};

export async function openScreen(browser, { tenant = null, ctx, stats = { endpoints: new Map(), unknown: new Map() }, tagdelay = 0, timeout = 90000, screen: kind = 'rules' }) {
  const spec = SCREENS[kind];
  const raw = tenantGetter(tenant);
  const tenantGet = (u) => { const b = raw(u); return b && spec.paged.some((re) => re.test(u.pathname)) ? page1(b) : b; };
  const context = await browser.createBrowserContext();
  const page = await context.newPage();
  await page.setViewport({ width: 1600, height: 1000 });
  const consoleLines = [];
  page.on('console', (m) => { const t = m.text(); if (m.type() === 'error' || m.type() === 'warn') consoleLines.push(`[${m.type()}] ${t.slice(0, 400)}`); });
  page.on('pageerror', (e) => consoleLines.push(`[pageerror] ${String(e.message).slice(0, 400)}`));
  const bump = (map, k) => map.set(k, (map.get(k) || 0) + 1);

  await page.setRequestInterception(true);
  page.on('request', async (req) => {
    let u;
    try { u = new URL(req.url()); } catch { return req.abort(); }
    if (u.protocol === 'data:' || u.protocol === 'blob:') return req.continue();
    if (u.hostname === 'localhost' && u.port === '4200') return req.continue();
    const key = `${req.method()} ${u.hostname}${u.pathname}`;
    if (!/gravty\.info$/.test(u.hostname)) {
      bump(stats.endpoints, 'ABORT ' + key);
      return req.abort('blockedbyclient');
    }
    const cors = {
      'Access-Control-Allow-Origin': APP, 'Access-Control-Allow-Credentials': 'true',
      'Access-Control-Allow-Headers': req.headers()['access-control-request-headers'] || '*',
      'Access-Control-Allow-Methods': 'GET,POST,PUT,DELETE,PATCH,OPTIONS',
      'Access-Control-Expose-Headers': 'Next,Prev,Count',
    };
    if (req.method() === 'OPTIONS') return req.respond({ status: 204, headers: cors, body: '' });
    if (tagdelay && /\/loyaltyaccounts\/\d+\/tags\/$/.test(u.pathname)) await new Promise((r) => setTimeout(r, Number(tagdelay)));
    let body = null;
    if (/\/pulsar\/offer\/metadata\/?$/.test(u.pathname)) {  // segment name/description asked by the add-segment screen
      body = { success: true, metadata: { member_segment_name: 'AI segment', segment_description: 'Generated segment' } };
    }
    try { body = body || (req.method() === 'GET' && tenantGet(u)) || M.route(req.method(), u, req.postData(), ctx); } catch (e) { body = null; }
    if (req.method() === 'POST' && /\/member-tracked-attributes\/$/.test(u.pathname) && body) {
      ctx.posted.push(JSON.parse(req.postData() || '{}'));
      ctx.counters.push({ name: body.name, level: 'MA_O', data_type: body.data_type, counter_type: body.counter_type,
                          target_type: body.target_type, vector_namespace: (body.entity_attribute_map?.Activity || [])[0] });
    }
    bump(stats.endpoints, key);
    if (body === null) {
      bump(stats.unknown, `${req.method()} ${u.pathname}${u.search}`);
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
    window.__harness = { errors: [] };
  }, M.globals);

  const t0 = Date.now();
  await page.setCookie({ name: 'XSRF-COOKIE', value: 'mock-xsrf-local', url: 'http://localhost:4200/' });
  await page.goto(`${APP}/${spec.hash}`, { waitUntil: 'domcontentloaded', timeout: 120000 });
  const screen = { page, context, console: consoleLines, ctx, kind };
  if (kind === 'segment') {
    try {
      await page.waitForFunction(() => {
        const el = document.querySelector('app-add-segment');
        return !!(el && window.ng?.getComponent(el)?.segmentData);
      }, { timeout, polling: 250 });
      screen.ready_ms = Date.now() - t0;
    } catch (e) { screen.error = 'timeout waiting for the segment screen: ' + e.message; screen.url = page.url(); }
    return screen;
  }
  // the offer-rules component + rendered workspace + metadata forkJoin finished
  try {
    await page.waitForFunction(() => {
      const el = document.querySelector('app-offer-rules');
      if (!el || !window.ng) return false;
      const c = window.ng.getComponent(el);
      const ws = c?.workspace?.workspace;
      return !!(ws && ws.rendered && c.blockDefintionService?.attributes?.activity?.length && ws.getAllBlocks(false).length > 0);
    }, { timeout, polling: 250 });
  } catch (e) {
    screen.error = 'timeout waiting for workspace: ' + e.message;
    screen.url = page.url();
    return screen;
  }
  screen.ready_ms = Date.now() - t0;
  // tags are fetched by a separate fire-and-forget subscribe (not in the forkJoin)
  screen.tags_at_ready = await page.evaluate(() => {
    const c = window.ng.getComponent(document.querySelector('app-offer-rules'));
    return JSON.stringify(c.blockDefintionService.tags || null);
  });
  await page.waitForFunction(() => {
    const c = window.ng.getComponent(document.querySelector('app-offer-rules'));
    return Object.keys(c.blockDefintionService.tags || {}).some((k) => k.startsWith('loyalty_account_'));
  }, { timeout: 15000 }).catch(() => { screen.tags_timeout = true; });
  return screen;
}

// Load xml through a stock path ('append' | 'upload' | 'drl' = already loaded by the screen) and read back the
// canonical XML, the DRL the UI generates, validation messages, exceptions, warnings and disabled blocks.
export async function loadInScreen(page, xml, loadPath, settleMs = 1500) {
  return page.evaluate(async (xml, loadPath, settleMs) => {
    const r = { window_Blockly: typeof window.Blockly };
    const comp = window.ng.getComponent(document.querySelector('app-offer-rules'));
    const ngx = comp.workspace; // NgxBlocklyComponent
    const ws = ngx.workspace;
    const bds = comp.blockDefintionService;
    const errSvc = comp.blockValidationErrorShow;
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
      }
    } catch (e) { exceptions.push('load: ' + (e && e.stack || e)); }
    await new Promise((res) => setTimeout(res, settleMs));
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
  }, xml, loadPath, settleMs);
}

// What the UI dropped or changed: input XML vs the canonical XML read back (runs in the page, browser DOMParser)
export function compareInPage(inXml, outXml) {
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

// Load segment XML the way the stock AI hand-off does (member-segments-listing handlePopupResponse -> add-segment
// manipulateSegmentData: base64 XML in member_target_xml with fromAi) and read back what the screen would save:
// the re-serialised XML, target_json and the DRL its own generator writes (the call add-segment's getXMLDRL makes).
export async function loadSegmentInScreen(page, xml, settleMs = 1500) {
  await page.evaluate((b64) => {
    const c = window.ng.getComponent(document.querySelector('app-add-segment'));
    c.memberSegmentService.selectedSegment = { segment: { fromAi: true, member_target_xml: b64, rule_id: 'harness', id: 'new',
                                                          dynamic_segmentation: 'DYNAMIC', staticOption: 'rules' } };
    c.manipulateSegmentData();
    window.ng.applyChanges?.(c);
  }, Buffer.from(xml, 'utf8').toString('base64'));
  try {
    await page.waitForFunction(() => {
      const c = window.ng.getComponent(document.querySelector('app-add-segment'));
      const ws = c?.segmentBlockly?.workspace?.workspace;
      return !!(ws && ws.rendered && ws.getAllBlocks(false).length > 0);
    }, { timeout: 60000, polling: 250 });
  } catch (e) { return { loaded: false, error: 'timeout waiting for the segment canvas: ' + e.message.slice(0, 120) }; }
  return page.evaluate(async (settleMs) => {
    const r = { loaded: true, exceptions: [] };
    const sb = window.ng.getComponent(document.querySelector('app-add-segment')).segmentBlockly;
    const ws = sb.workspace.workspace;
    await new Promise((ok) => setTimeout(ok, settleMs));
    r.blocks = ws.getAllBlocks(false).map((b) => b.type);
    r.warnings = ws.getAllBlocks(false).map((b) => (b.warning ? `${b.type}#${b.id}: ${b.warning.getText?.() || ''}` : null)).filter(Boolean);
    try {
      const out = sb.generateDrlCode();
      r.error = out?.errorObj?.error ? out.errorObj.errorMsg : null;
      r.target_json = out?.target_json;
      r.canonical = out?.encodedXml ? atob(out.encodedXml) : '';
      r.drl = sb.blocklySourceDetails?.drl ? atob(sb.blocklySourceDetails.drl) : '';
    } catch (e) { r.exceptions.push('gen: ' + (e.stack || e)); }
    return r;
  }, settleMs);
}

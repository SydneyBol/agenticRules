// Load generated member-segment XML into the REAL gravty-ui segment canvas (Select Members > new segment) against the
// mocked backend, and capture what the screen would save: the re-serialised XML, target_json and the DRL its own
// generator writes (SegmentationBlockly.generateDrlCode, the same call add-segment's getXMLDRL makes before saving).
// Member attributes, tiers and tier classes come from the tenant's dumps (default devintam1), so the dropdowns know the codes.
// usage: node segment-run.mjs --in=<dir of *.xml> --out=<dir> [--tenant=devintam1] [--headful --pause=<ms>]
import puppeteer from 'puppeteer-core';
import fs from 'node:fs';
import path from 'node:path';
import * as M from './fixtures/mocks.mjs';

const HERE = path.dirname(new URL(import.meta.url).pathname);
const args = Object.fromEntries(process.argv.slice(2).map((a) => { const [k, v] = a.replace(/^--/, '').split('='); return [k, v ?? true]; }));
const IN = path.resolve(args.in), OUT = path.resolve(args.out);
const DUMPS = path.resolve(HERE, '../../catalogs', args.tenant || 'devintam1', 'bolapi');
fs.mkdirSync(OUT, { recursive: true });
const APP = 'http://localhost:4200';
const dump = (f) => JSON.parse(fs.readFileSync(path.join(DUMPS, f + '.json'), 'utf8')).body;
const page1 = (rows) => ({ count: rows.length, next: null, previous: null, results: rows });
const OVERRIDES = [
  [/\/attributelibrary\/member\/?$/, () => dump('member_attributes')],
  [/\/tier-list\/?$/, () => page1(dump('extra_tiers'))],
  [/\/tier-classes\/?$/, () => page1(dump('extra_tierclass'))],
];

const browser = await puppeteer.launch({
  executablePath: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', headless: args.headful ? false : true,
  userDataDir: fs.mkdtempSync(path.join(HERE, 'tmp-profile-mock-')), args: ['--no-first-run', '--no-default-browser-check', '--disable-extensions'],
});
const report = {};
for (const f of fs.readdirSync(IN).filter((x) => x.endsWith('.xml')).sort()) {
  const name = f.replace(/\.xml$/, ''), xml = fs.readFileSync(path.join(IN, f), 'utf8');
  const context = await browser.createBrowserContext();
  const page = await context.newPage();
  await page.setViewport({ width: 1600, height: 1000 });
  const consoleLines = [];
  page.on('pageerror', (e) => consoleLines.push(`[pageerror] ${String(e.message).slice(0, 300)}`));
  await page.setRequestInterception(true);
  page.on('request', (req) => {
    let u; try { u = new URL(req.url()); } catch { return req.abort(); }
    if (u.protocol === 'data:' || u.protocol === 'blob:' || (u.hostname === 'localhost' && u.port === '4200')) return req.continue();
    if (!/gravty\.info$/.test(u.hostname)) return req.abort('blockedbyclient');
    const cors = { 'Access-Control-Allow-Origin': APP, 'Access-Control-Allow-Credentials': 'true',
      'Access-Control-Allow-Headers': req.headers()['access-control-request-headers'] || '*', 'Access-Control-Allow-Methods': 'GET,POST,PUT,DELETE,PATCH,OPTIONS' };
    if (req.method() === 'OPTIONS') return req.respond({ status: 204, headers: cors, body: '' });
    let body = null;
    if (/\/pulsar\/offer\/metadata\/?$/.test(u.pathname)) {  // our /offer/metadata (category segment): name + description
      return req.respond({ status: 200, headers: { ...cors, 'Content-Type': 'application/json' },
        body: JSON.stringify({ success: true, metadata: { member_segment_name: 'AI ' + name, segment_description: 'Generated segment ' + name } }) });
    }
    const o = req.method() === 'GET' && OVERRIDES.find(([re]) => re.test(u.pathname));
    try { body = o ? o[1]() : M.route(req.method(), u, req.postData(), { counters: [], drlGet: [], posted: [] }); } catch { body = null; }
    if (body === null) { (globalThis.__unknown ||= new Set()).add(`${req.method()} ${u.pathname}${u.search}`); body = req.method() === 'GET' ? [] : {}; }
    return req.respond({ status: 200, headers: { ...cors, 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  });
  await page.evaluateOnNewDocument((globals) => {
    if (location.origin !== 'http://localhost:4200') return;
    localStorage.setItem('globals', JSON.stringify(globals));
    localStorage.setItem('offer_type', 'reward');
    localStorage.setItem('programInfo', JSON.stringify([{ date_format: 'DD/MM/YYYY', timezone: 'UTC' }]));
    localStorage.setItem('enableMultilingual', 'false');
  }, M.globals);
  await page.setCookie({ name: 'XSRF-COOKIE', value: 'mock-xsrf-local', url: 'http://localhost:4200/' });
  const res = { segment: name, loaded: false };
  await page.goto(`${APP}/#/offer/${M.OFFER_ID}/selectmembers/membersegment/new`, { waitUntil: 'domcontentloaded', timeout: 120000 });
  try {
    await page.waitForFunction(() => {
      const el = document.querySelector('app-add-segment');
      return !!(el && window.ng?.getComponent(el)?.segmentData);
    }, { timeout: 90000, polling: 250 });
    // the stock AI hand-off (member-segments-listing handlePopupResponse -> add-segment manipulateSegmentData):
    // the popup's XML arrives base64 in member_target_xml with fromAi, and the screen switches to a dynamic rules segment
    await page.evaluate((b64) => {
      const c = window.ng.getComponent(document.querySelector('app-add-segment'));
      c.memberSegmentService.selectedSegment = { segment: { fromAi: true, member_target_xml: b64, rule_id: 'harness', id: 'new',
                                                            dynamic_segmentation: 'DYNAMIC', staticOption: 'rules' } };
      c.manipulateSegmentData();
      window.ng.applyChanges?.(c);
    }, Buffer.from(xml, 'utf8').toString('base64'));
    await page.waitForFunction(() => {
      const c = window.ng.getComponent(document.querySelector('app-add-segment'));
      const ws = c?.segmentBlockly?.workspace?.workspace;
      return !!(ws && ws.rendered && ws.getAllBlocks(false).length > 0);
    }, { timeout: 60000, polling: 250 });
  } catch (e) {
    res.error = 'timeout waiting for the segment canvas: ' + e.message.slice(0, 120);
    await page.screenshot({ path: path.join(OUT, name + '.timeout.png') });
    report[name] = res; await context.close(); continue;
  }
  Object.assign(res, await page.evaluate(async (xml) => {
    const r = { exceptions: [] };
    const sb = window.ng.getComponent(document.querySelector('app-add-segment')).segmentBlockly;
    const ws = sb.workspace.workspace;
    await new Promise((ok) => setTimeout(ok, 1500));  // loaded by the screen itself from member_target_xml
    r.blocks = ws.getAllBlocks(false).map((b) => b.type);
    r.warnings = ws.getAllBlocks(false).map((b) => (b.warning ? `${b.type}: ${b.warning.getText?.() || ''}` : null)).filter(Boolean);
    try {
      const out = sb.generateDrlCode();
      r.error = out?.errorObj?.error ? out.errorObj.errorMsg : null;
      r.target_json = out?.target_json;
      r.saved_xml = out?.encodedXml ? atob(out.encodedXml) : '';
      r.drl = sb.blocklySourceDetails?.drl ? atob(sb.blocklySourceDetails.drl) : '';
      r.segment_name = sb.blocklySourceDetails?.segment?.member_segment_name;
    } catch (e) { r.exceptions.push('gen: ' + (e.stack || e)); }
    return r;
  }, xml));
  res.loaded = true;
  res.console = consoleLines.slice(0, 20);
  fs.writeFileSync(path.join(OUT, name + '.drl'), res.drl || '');
  await page.screenshot({ path: path.join(OUT, name + '.png') });
  if (args.pause) { await page.bringToFront(); console.log(`  showing ${name}`); await new Promise((ok) => setTimeout(ok, Number(args.pause))); }
  console.log(`${name.padEnd(36)} blocks ${res.blocks?.length ?? '-'} error ${res.error || '-'} exceptions ${res.exceptions?.length ?? '-'} drl ${(res.drl || '').length}b`);
  report[name] = res;
  await context.close();
}
fs.writeFileSync(path.join(OUT, 'report.json'), JSON.stringify(report, null, 1));
if (args.unknown) console.log('defaulted requests:\n  ' + [...(globalThis.__unknown || [])].join('\n  ') + '\ntier/feature/lov requests:\n  ' + [...(globalThis.__seen || [])].join('\n  '));
await browser.close();

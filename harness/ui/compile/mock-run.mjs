// Drive the REAL gravty-ui offer-rules Blockly screen against a fully mocked backend.
// usage: node mock-run.mjs [--path=append|upload|drl] [--only=S01,S14] [--discover] [--probe] [--headful]
import puppeteer from 'puppeteer-core';
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import * as M from './mocks.mjs';
import { openScreen, loadInScreen, compareInPage, CHROME, CHROME_ARGS, tempProfile } from './screen.mjs';

const HERE = path.dirname(new URL(import.meta.url).pathname);
const ROOT = path.resolve(HERE, '../..');  // harness/
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

const scenarios = fs.readdirSync(COMPILED).filter((f) => f.endsWith('.xml'))
  .map((f) => f.replace(/\.xml$/, ''))
  .filter((s) => !only || only.some((o) => s.startsWith(o)))
  .sort();

const unknown = new Map(); // "METHOD path" -> count
const endpointsHit = new Map();
const TENANT = args.tenant || null;

async function runScenario(browser, scen, loadPath) {
  const xml = fs.readFileSync(path.join(COMPILED, scen + '.xml'), 'utf8');
  const spec = JSON.parse(fs.readFileSync(path.join(COMPILED, scen + '.spec.json'), 'utf8'));
  // --discover: the offer starts with no counters, so the stock upload path must create them (POSTs recorded)
  const ctx = { counters: args.discover ? [] : (spec.counters || []), drlGet: [], posted: [] };
  if (loadPath === 'drl') {
    ctx.drlGet = [{ id: 77, offer: M.OFFER_ID, xml: Buffer.from(xml, 'utf8').toString('base64'), drl: '' }];
  }
  const res = { scenario: scen, load_path: loadPath, loaded: false };
  const s = await openScreen(browser, { tenant: TENANT, ctx, stats: { endpoints: endpointsHit, unknown }, tagdelay: args.tagdelay });
  const page = s.page;
  if (s.error) {
    Object.assign(res, { error: s.error, url: s.url, console: s.console.slice(0, 40) });
    await page.screenshot({ path: path.join(OUT, scen + '.timeout.png') });
    await s.context.close();
    return res;
  }
  Object.assign(res, { ready_ms: s.ready_ms, tags_at_ready: s.tags_at_ready, tags_timeout: !!s.tags_timeout });
  const out = await loadInScreen(page, xml, loadPath);
  Object.assign(res, out);
  res.mto_posted = ctx.posted;
  if (out.canonical) res.cmp = await page.evaluate(compareInPage, xml, out.canonical);
  res.console = s.console.slice(0, 40);
  res.loaded = true;
  await page.screenshot({ path: path.join(OUT, `${scen}.${loadPath}.png`), fullPage: false });
  if (args.pause) {  // --headful --pause=<ms>: someone is watching; keep each loaded draft on screen
    await page.bringToFront();
    console.log(`  showing ${scen} (${res.cmp?.blocks_out ?? "?"} blocks)`);
    await new Promise((r) => setTimeout(r, Number(args.pause)));
  }
  await s.context.close();
  return res;
}

const browser = await puppeteer.launch({
  executablePath: CHROME, headless: args.headful ? false : true,
  userDataDir: tempProfile('mock'),
  args: CHROME_ARGS,
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

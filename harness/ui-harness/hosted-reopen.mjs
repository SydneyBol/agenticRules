// Read-only: reopen each ZZ-AI-SPIKE offer's rules screen (the app loads the saved XML from GET offers/{id}/drl/) and
// report whether the "Loading Please Wait..." overlay clears and the blocks render. No clicks, no writes.
// usage: node hosted-reopen.mjs [--tenant=devintam1] [--only=S06] [--nav=hash|reload]
import fs from 'fs';
import path from 'path';
import puppeteer from 'puppeteer-core';

const ROOT = path.resolve(path.dirname(new URL(import.meta.url).pathname), '..');
const arg = (n, d) => (process.argv.find((a) => a.startsWith(`--${n}=`)) || '').split('=')[1] || d;
const TENANT = arg('tenant', 'devintam1');
const ONLY = arg('only', '') ? arg('only', '').split(',') : null;
const NAV = arg('nav', 'reload');
const ORIGIN = `https://master.${TENANT}.gravty.info`;
const OUT = path.join(ROOT, 'results', `hosted_${TENANT}`);
const state = JSON.parse(fs.readFileSync(path.join(ROOT, 'results', TENANT, 'offers.json'), 'utf8'));
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const b = await puppeteer.connect({ browserURL: 'http://127.0.0.1:9222', defaultViewport: null });
const page = (await b.pages()).find((p) => p.url().includes(`${TENANT}.gravty.info`));
const loaderUp = () => page.evaluate(() => [...document.querySelectorAll('*')].some((e) => e.childElementCount === 0
  && /Loading Please Wait/i.test(e.textContent || '') && e.offsetParent !== null));
const rows = {};
for (const sid of Object.keys(state).filter((s) => !ONLY || ONLY.some((o) => s.startsWith(o)))) {
  const id = state[sid].offer_id;
  const errs = [];
  const onC = (m) => { if (m.type() === 'error') errs.push(m.text().slice(0, 200)); };
  const onE = (e) => errs.push('pageerror: ' + String(e.message || e).slice(0, 300));
  page.on('console', onC); page.on('pageerror', onE);
  const t0 = Date.now();
  await page.goto(`${ORIGIN}/#/offer/${id}/rules`, { waitUntil: 'domcontentloaded', timeout: 90000 });
  if (NAV === 'reload') await page.reload({ waitUntil: 'domcontentloaded', timeout: 90000 });
  let cleared = false;
  for (let i = 0; i < 80; i++) {
    await sleep(750);
    const blocks = await page.evaluate(() => document.querySelectorAll('.blocklyBlockCanvas g.blocklyDraggable').length);
    if (i > 6 && blocks > 0 && !(await loaderUp())) { cleared = true; break; }
  }
  const r = { offer_id: id, cleared, ms: Date.now() - t0,
    blocks: await page.evaluate(() => document.querySelectorAll('.blocklyBlockCanvas g.blocklyDraggable').length),
    header: await page.evaluate(() => document.querySelector('[class*="offer-name"], .sidebar h2, h1, h2')?.innerText?.slice(0, 60)),
    errors: [...new Set(errs)].filter((e) => !/compass|Failed to load resource/.test(e)).slice(0, 8) };
  page.off('console', onC); page.off('pageerror', onE);
  if (!cleared) await page.screenshot({ path: path.join(OUT, sid + `.reopen-${NAV}.png`) });
  rows[sid] = r;
  console.log(`${sid.padEnd(40)} ${cleared ? 'OK   ' : 'STUCK'} ${String(r.ms).padStart(6)}ms blocks=${r.blocks} ${JSON.stringify(r.errors).slice(0, 400)}`);
}
fs.writeFileSync(path.join(OUT, `reopen-${NAV}.json`), JSON.stringify(rows, null, 1));
await b.disconnect();

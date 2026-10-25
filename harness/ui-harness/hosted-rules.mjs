// For each ZZ-AI-SPIKE offer on the hosted tenant: open its rules screen in the logged-in debug Chrome tab, upload the
// compiled XML through the real "Upload Rules" dialog, then click the planning-status save button ("Select Offer
// Preview"). The app generates the DRL itself and POSTs/PUTs offers/{id}/drl/, where bolapi asks the engine to compile it.
// We record that request (decoded xml + drl) and the response. Never clicks anything labelled Launch.
// usage: node hosted-rules.mjs [--tenant=devintam1] [--only=S14] [--nosave]
import fs from 'fs';
import path from 'path';
import puppeteer from 'puppeteer-core';

const ROOT = path.resolve(path.dirname(new URL(import.meta.url).pathname), '..');
const arg = (n, d) => (process.argv.find((a) => a.startsWith(`--${n}=`)) || '').split('=')[1] || d;
const TENANT = arg('tenant', 'devintam1');
const ONLY = arg('only', '') ? arg('only', '').split(',') : null;
const NOSAVE = process.argv.includes('--nosave');
const ORIGIN = `https://master.${TENANT}.gravty.info`;
const IN = path.join(ROOT, 'results', `compiled_${TENANT}`);
const OUT = path.join(ROOT, 'results', `hosted_${TENANT}`);
const state = JSON.parse(fs.readFileSync(path.join(ROOT, 'results', TENANT, 'offers.json'), 'utf8'));
fs.mkdirSync(OUT, { recursive: true });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const b64 = (s) => Buffer.from(s || '', 'base64').toString('utf8');

const b = await puppeteer.connect({ browserURL: 'http://127.0.0.1:9222', defaultViewport: null });
const page = (await b.pages()).find((p) => p.url().includes(`${TENANT}.gravty.info`));
if (!page) throw new Error(`no ${TENANT} tab open`);
await page.bringToFront();

// visible text of the app's inline error banner / alert dialogs
const uiErrors = () => page.evaluate(() => [...document.querySelectorAll('.close-error, mat-dialog-container, .mat-mdc-dialog-container, .toast-message, .error-message')]
  .map((e) => (e.closest('div')?.parentElement || e).innerText.trim()).filter(Boolean).map((t) => t.slice(0, 400)));

async function clickByText(re, { forbid = /launch/i } = {}) {
  const handle = await page.evaluateHandle((src, flags, fsrc) => {
    const re = new RegExp(src, flags); const forbid = new RegExp(fsrc, 'i');
    return [...document.querySelectorAll('button, gravty-button')].find((e) => re.test(e.innerText.trim()) && !forbid.test(e.innerText)
      && e.offsetParent !== null && !e.disabled && !e.querySelector('button[disabled]')) || null;
  }, re.source, re.flags, forbid.source);
  const el = handle.asElement();
  if (!el) return false;
  const inner = (await el.$('button')) || el;
  await inner.click();
  return true;
}

const sids = Object.keys(state).filter((s) => !ONLY || ONLY.some((o) => s.startsWith(o)));
const summary = fs.existsSync(path.join(OUT, 'summary.json')) ? JSON.parse(fs.readFileSync(path.join(OUT, 'summary.json'), 'utf8')) : {};
for (const sid of sids) {
  const id = state[sid].offer_id;
  const xmlPath = path.join(IN, sid + '.xml');
  const res = { sid, offer_id: id };
  const drlCalls = [];
  const consoleErr = [];
  const onConsole = (m) => { if (m.type() === 'error') consoleErr.push(m.text().slice(0, 300)); };
  const onPageErr = (e) => consoleErr.push('pageerror: ' + String(e.message || e).slice(0, 300));
  page.on('console', onConsole); page.on('pageerror', onPageErr);
  const onReq = (req) => {
    if (/\/offers\/\d+\/drl\/(\d+\/)?$/.test(req.url()) && ['POST', 'PUT'].includes(req.method())) drlCalls.push({ method: req.method(), url: req.url(), body: req.postData() });
  };
  const onResp = async (resp) => {
    const c = drlCalls.find((d) => d.url === resp.url() && !d.status && resp.request().method() === d.method);
    if (c) { c.status = resp.status(); try { c.response = await resp.text(); } catch { c.response = ''; } }
  };
  page.on('request', onReq); page.on('response', onResp);
  try {
    // the offer wizard sets offe_type before /rules; a deep link leaves it null and uploads are rejected (spike bug list)
    await page.evaluate(() => localStorage.setItem('offe_type', 'reward'));
    await page.goto(`${ORIGIN}/#/offer/${id}/rules`, { waitUntil: 'domcontentloaded', timeout: 90000 });
    // hash navigation reuses the SPA (stale offer header, stuck loaders from the previous offer): reload for a clean screen
    await page.reload({ waitUntil: 'domcontentloaded', timeout: 90000 });
    await page.waitForSelector('img[title="Upload Rules"]', { visible: true, timeout: 90000 });
    await page.waitForSelector('.blocklySvg, .injectionDiv', { timeout: 90000 });
    await sleep(6000); // metadata forkJoin + the separate reward-tags fetch
    await page.click('img[title="Upload Rules"]');
    await sleep(1500);
    await clickByText(/^OK$/); // "upload new rules?" confirm, only shown when rules already exist
    const input = await page.waitForSelector('#inputFileUpload', { timeout: 20000 });
    await input.uploadFile(xmlPath);
    // counter discovery + appendFromXml; the app's "Loading Please Wait..." overlay must clear
    const loaderUp = () => page.evaluate(() => [...document.querySelectorAll('*')].some((e) => e.childElementCount === 0
      && /Loading Please Wait/i.test(e.textContent || '') && e.offsetParent !== null));
    for (let i = 0; i < 40; i++) { await sleep(750); if (i > 6 && !(await loaderUp())) break; }
    res.loader_stuck = await loaderUp();
    if (res.loader_stuck) throw new Error('loader stuck after upload');
    res.blocks_rendered = await page.evaluate(() => document.querySelectorAll('.blocklyBlockCanvas > g.blocklyDraggable').length);
    res.errors_after_upload = await uiErrors();
    await page.screenshot({ path: path.join(OUT, sid + '.upload.png') });
    if (!NOSAVE) {
      res.save_clicked = await clickByText(/^(Select Offer Preview|Save|Save Changes|Multilingual Support)$/i);
      for (let i = 0; i < 60 && !drlCalls.some((c) => c.status); i++) await sleep(500);
      await sleep(2500);
      res.errors_after_save = await uiErrors();
      res.url_after = page.url();
      await page.screenshot({ path: path.join(OUT, sid + '.save.png') });
    }
  } catch (e) {
    res.error = String(e.message || e);
    await page.screenshot({ path: path.join(OUT, sid + '.error.png') }).catch(() => {});
  } finally {
    page.off('request', onReq); page.off('response', onResp); page.off('console', onConsole); page.off('pageerror', onPageErr);
    res.console_errors = [...new Set(consoleErr)].slice(0, 15);
  }
  const call = drlCalls.find((c) => c.status) || drlCalls[0];
  if (call) {
    let body = {}; try { body = JSON.parse(call.body || '{}'); } catch {}
    const drl = b64(body.drl); const xml = b64(body.xml);
    fs.writeFileSync(path.join(OUT, sid + '.drl'), drl);
    fs.writeFileSync(path.join(OUT, sid + '.saved.xml'), xml);
    res.save = { method: call.method, status: call.status, response: (call.response || '').slice(0, 1500) };
    res.drl_bytes = drl.length;
  }
  summary[sid] = res;
  fs.writeFileSync(path.join(OUT, 'summary.json'), JSON.stringify(summary, null, 1));
  console.log(`${sid.padEnd(40)} offer=${id} blocks=${res.blocks_rendered} save=${res.save?.status ?? '-'} ${res.error || ''} ${JSON.stringify(res.errors_after_save || res.errors_after_upload || []).slice(0, 300)}`);
}
await b.disconnect();

// Stock hosted UI + our backend, no UI change. For each plan item on the hosted tenant:
//   1. POST offers/ -> a new ZZ-AI-SPIKE-<key> reward offer in planning status (never launched)
//   2. autoload: send the call Offer Main makes at creation (POST /ai/pulsar/offer/rules/?regenerate=true) to OUR backend
//      popup:    mark the offer as Pulsar-created (what the "create with Pulsar" flow stores) so the GenAI button shows
//   3. route the page's own /ai/pulsar/* requests to OUR backend (Authorization stripped); everything else goes to the tenant
//   4. open the rules screen; autoload lets the UI fetch prompt + xml and load it, popup types the prompt and clicks Generate
//   5. record the UI's own counter POSTs, click the planning save button, capture the DRL save (bolapi -> engine compile)
//   upload:   (tenants without the Pulsar toggle) get the XML from OUR backend for the item prompt and load it through the
//             stock "Upload Rules" dialog; the rest (counters, save, DRL capture) is the same
// usage: node hosted-pulsar.mjs --plan=plan.json [--tenant=devintam1] [--sponsor=1] [--backend=http://localhost:8077]
//   --tenant: the tenant signed in in the debug Chrome (https://master.<tenant>.gravty.info); the backend must run with that
//   tenant's catalog. Offer ids per key are kept per tenant (results/<tenant>/pulsar_offers.json).
import fs from 'fs';
import path from 'path';
import puppeteer from 'puppeteer-core';

const ROOT = path.resolve(path.dirname(new URL(import.meta.url).pathname), '..');
const arg = (n, d) => (process.argv.find((a) => a.startsWith(`--${n}=`)) || '').split('=').slice(1).join('=') || d;
const BACKEND = arg('backend', 'http://localhost:8077');
const PLAN = JSON.parse(fs.readFileSync(arg('plan'), 'utf8'));
const TENANT = arg('tenant', 'devintam1');
const SPONSOR = Number(arg('sponsor', '1'));
const ORIGIN = `https://master.${TENANT}.gravty.info`;
// devintam1 keeps its original result folders; other tenants get their own
const OUT = path.join(ROOT, 'results', ...(TENANT === 'devintam1' ? [] : [TENANT]), arg('out', 'hosted_pulsar'));
const STATE = path.join(ROOT, 'results', TENANT, 'pulsar_offers.json');
fs.mkdirSync(path.dirname(STATE), { recursive: true });
fs.mkdirSync(OUT, { recursive: true });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const b64 = (s) => Buffer.from(s || '', 'base64').toString('utf8');

// a stalled page call fails after 60s (default 180s) and the item is retried once from a fresh reload
const b = await puppeteer.connect({ browserURL: 'http://127.0.0.1:9222', defaultViewport: null, protocolTimeout: 60000 });
const page = (await b.pages()).find((p) => p.url().includes(`${TENANT}.gravty.info`));
if (!page) throw new Error(`no ${TENANT} tab open in the debug Chrome`);
await page.bringToFront();
// --watch=<s>: someone is watching. Un-minimise the window, type visibly and pause on each loaded draft before saving.
const WATCH = Number(arg('watch', '0')) * 1000;
const FRESH = process.argv.includes('--fresh');
const OFFER_KEY = arg('offer-key', '');
const OFFER_NAME = arg('offer-name', 'AI Rules Combination Test');
const say = (m) => WATCH && console.log(`[${new Date().toLocaleTimeString()}] ${m}`);
if (WATCH) {
  const cdp = await page.target().createCDPSession();
  const { windowId } = await cdp.send('Browser.getWindowForTarget');
  await cdp.send('Browser.setWindowBounds', { windowId, bounds: { windowState: 'normal' } });
  await cdp.send('Browser.setWindowBounds', { windowId, bounds: { windowState: 'maximized' } });
}

const api = (method, rel, body) => page.evaluate(async (method, rel, body) => {
  const cookie = (n) => decodeURIComponent((document.cookie.match(new RegExp('(?:^|; )' + n + '=([^;]+)')) || [])[1] || '');
  const r = await fetch(`/bolapi/v1/${rel}`, { method, body: body ? JSON.stringify(body) : undefined,
    headers: { Authorization: 'JWT ' + cookie('token_key'), 'X-CSRFToken': cookie('XSRF-COOKIE'), 'x-aws-waf-token': cookie('aws-waf-token'), 'Content-Type': 'application/json' } });
  const t = await r.text();
  try { return { status: r.status, body: JSON.parse(t) }; } catch { return { status: r.status, body: t.slice(0, 500) }; }
}, method, rel, body);

// the offer's status and counters as bolapi has them now (the backend's counter guard decides names against them;
// the backend gets no JWT from this harness, so the runner reads them through the signed-in page and sends them along)
async function existingCounters(id) {
  const offer = (await api('GET', `offers/${id}/`)).body;
  const mtos = (await api('GET', `offers/${id}/member-tracked-attributes/`)).body;
  return { offer_status: offer?.status?.code || offer?.status_code || offer?.status, counters: Array.isArray(mtos) ? mtos : mtos?.results || [] };
}

async function backend(method, rel, body) {
  const r = await fetch(BACKEND + rel, { method, headers: { 'Content-Type': 'application/json' }, body: body ? JSON.stringify(body) : undefined });
  return { status: r.status, body: await r.json() };
}

function offerPayload(key, name) {
  const now = new Date(); const end = new Date(now.getTime() + 90 * 864e5);
  return { offer_name: (name || 'ZZ-AI-SPIKE-' + key).slice(0, 50), offer_description: 'English-to-Blockly prototype test (stock UI). Do not launch.',
    term_and_condition: '', offer_type: 'reward', offer_billing_type: 'bit_sponsor', sponsors: [SPONSOR],
    start_date: now.toISOString().slice(0, 19), end_date: end.toISOString().slice(0, 10) + 'T23:59:00',
    offer_external_id: `zz-ai-spike-${key.toLowerCase()}-${Date.now().toString(36)}`, fb_share: false, twitter_share: false,
    offer_acceptance_required: false, non_cumulative: false, send_notification: false, product_targeting_file: null, repeat_weekday: '',
    repeat_day: '', repeat_month: '', is_base_offer: false, priority: 0, is_product_targeting: false, smart_family_contribution: true,
    sponsors_data: [], member_visible: false, products: [], sponsorship_details: [], notification_schedule_time: null,
    notification_schedule_type: null, member_visible_type: 'on_launch', valid_period: 'DAYS', extra_data: {}, tags: [], rich_extra_data: {},
    is_parent_offer: false, maximum_reward_points_info: { maximum_points_limit: null, loyalty_account: null, reward_limit_type: 'POINTS' },
    interval: '', interval_period: null, scheduled_on: null, scheduled_on_time: null, version: 1, max_usage_per_user: null,
    non_cumulative_level: null, locations: [], location_category: 'ALL_LOCATIONS', is_individualized: false };
}

const shown = (sel, timeout) => page.waitForFunction((sel) => { const e = document.querySelector(sel); return !!(e && e.offsetParent !== null); },
  { polling: 250, timeout }, sel);
const loaderUp = () => page.evaluate(() => [...document.querySelectorAll('*')].some((e) => e.childElementCount === 0
  && /Loading Please Wait|Generating/i.test(e.textContent || '') && e.offsetParent !== null));
const blocks = () => page.evaluate(() => document.querySelectorAll('.blocklyBlockCanvas g.blocklyDraggable').length);

async function clickSave() {
  const h = await page.evaluateHandle(() => [...document.querySelectorAll('gravty-button, button')].find((e) =>
    /^(Select Offer Preview|Save|Save Changes|Multilingual Support)$/i.test(e.innerText.trim()) && !/launch/i.test(e.innerText) && e.offsetParent !== null) || null);
  const el = h.asElement();
  if (!el) return false;
  const btn = (await el.$('button')) || el;
  if (await btn.evaluate((e) => e.disabled)) return 'disabled';
  if (process.argv.includes('--mouse-save')) { await page.bringToFront(); await btn.click(); }  // the original real-mouse click
  else await btn.evaluate((e) => e.click());
  return true;
}

if (!(await page.evaluate(() => /(?:^|; )token_key=/.test(document.cookie))) || /#\/login/.test(page.url())) {
  console.error(`logged out of ${TENANT}: sign in in the debug Chrome window, then re-run`);
  await b.disconnect();
  process.exit(2);
}
const state = fs.existsSync(STATE) ? JSON.parse(fs.readFileSync(STATE, 'utf8')) : {};
const summary = {};
await page.setRequestInterception(true);
const ai = [], aiSeen = [], mtoPosts = [], drlCalls = [];
const onReq = async (req) => {
  const u = req.url();
  let url; try { url = new URL(u); } catch { return req.continue(); }
  if (/\/ai\//.test(url.pathname)) aiSeen.push(`${req.method()} ${url.origin}${url.pathname}`);
  // only the endpoints our backend implements (offer rules + prompt store); the rest of Pulsar stays on the tenant.
  // The AI host may differ from the page origin, so answer CORS too.
  if (/^\/ai\/pulsar\/(offer\/rules\/?|\d+\/prompt\/[^/]+\/?)$/.test(url.pathname)) {
    const rel = url.pathname + url.search;
    const cors = url.origin === ORIGIN ? {} : { 'Access-Control-Allow-Origin': ORIGIN, 'Access-Control-Allow-Credentials': 'true',
      'Access-Control-Allow-Headers': req.headers()['access-control-request-headers'] || '*', 'Access-Control-Allow-Methods': 'GET,POST,PUT,OPTIONS' };
    if (req.method() === 'OPTIONS') return req.respond({ status: 204, headers: cors, body: '' });
    try {
      let body = ['POST', 'PUT'].includes(req.method()) ? req.postData() : undefined;
      if (body && /\/offer\/rules/.test(url.pathname)) {  // JWT is stripped, so send the offer's counters with the request
        const j = JSON.parse(body || '{}');
        if (j.id != null && !j.existing_counters) body = JSON.stringify({ ...j, existing_counters: await existingCounters(j.id) });
      }
      const r = await fetch(BACKEND + rel, { method: req.method(), headers: { 'Content-Type': 'application/json' }, body });
      const text = await r.text();
      ai.push({ method: req.method(), rel, status: r.status, result: text.slice(0, 120) });
      return req.respond({ status: r.status, headers: cors, contentType: 'application/json', body: text });
    } catch (e) {
      ai.push({ method: req.method(), rel, error: String(e) });
      return req.respond({ status: 502, headers: cors, contentType: 'application/json', body: JSON.stringify({ error: { message: 'backend down' } }) });
    }
  }
  if (/\/member-tracked-attributes\/$/.test(u) && req.method() === 'POST') mtoPosts.push({ url: u, body: JSON.parse(req.postData() || '{}') });
  if (/\/offers\/\d+\/drl\/(\d+\/)?$/.test(u) && ['POST', 'PUT'].includes(req.method())) drlCalls.push({ method: req.method(), url: u, body: req.postData() });
  return req.continue();
};
const onResp = async (resp) => {
  const u = resp.url();
  const m = mtoPosts.find((x) => x.url === u && x.status === undefined && resp.request().method() === 'POST');
  if (m) m.status = resp.status();
  const d = drlCalls.find((x) => x.url === u && x.status === undefined && resp.request().method() === x.method);
  if (d) { d.status = resp.status(); try { d.response = (await resp.text()).slice(0, 600); } catch { d.response = ''; } }
};
page.on('request', onReq); page.on('response', onResp);
try {
  const queue = PLAN.map((x) => ({ ...x, attempt: 1 }));
  for (const item of queue) {
    const res = { key: item.key, mode: item.mode, prompt: item.prompt, attempt: item.attempt };
    ai.length = 0; aiSeen.length = 0; mtoPosts.length = 0; drlCalls.length = 0;
    try {
      // --offer-key=K: every item runs on ONE offer (state[K], created once, named --offer-name); each popup
      // generation replaces its rules. --fresh: a new planning offer per item (earlier ids kept under `previous`)
      const skey = OFFER_KEY || item.key;
      let id = FRESH && !OFFER_KEY ? null : state[skey]?.offer_id;
      if (!id) {
        // one QA offer per tenant: a missing key in STATE must not silently create another offer
        if (OFFER_KEY && !process.argv.includes('--allow-create'))
          throw new Error(`no offer for key ${OFFER_KEY} in ${STATE}; pass --allow-create to create one`);
        const r = await api('POST', 'offers/', offerPayload(skey, OFFER_KEY ? OFFER_NAME : item.name));
        if (r.status !== 201) throw new Error('offer create ' + r.status + ' ' + JSON.stringify(r.body).slice(0, 300));
        const old = state[skey];
        const previous = old ? [...(old.previous || []), old.offer_id] : [];
        id = r.body.id; state[skey] = { offer_id: id, name: r.body.offer_name, status: r.body.status, previous };
        say(`${item.key}: created offer ${id} (${r.body.offer_name}, ${r.body.status})`);
        fs.writeFileSync(STATE, JSON.stringify(state, null, 1));
      }
      res.offer_id = id;
      // the one QA offer must still be editable: never touch an offer that has left planning
      const cur = await api('GET', `offers/${id}/`);
      const st = cur.body?.status?.code || cur.body?.status_code || cur.body?.status;
      if (cur.status !== 200 || st !== 'planning') throw new Error(`offer ${id} is ${cur.status} / ${JSON.stringify(st)}, not planning: stopping`);
      const req = { prompt: item.prompt, sponsor_id: SPONSOR, id, type: 'reward' };
      if (item.mode === 'autoload') {
        let r;
        for (let i = 0; i < 20; i++) { r = await backend('POST', '/ai/pulsar/offer/rules/?regenerate=true', req); if (r.body.result !== 'Generating') break; }
        res.offer_main_call = { status: r.status, result: r.body.result, backend_status: r.body.status };
        say(`${item.key}: AI generated the rules from "${item.prompt}" (${r.body.status})`);
      } else if (item.mode === 'upload') {
        // the same request the GenAI popup sends, made by the runner; the popup re-posts while the answer is Generating
        const mainPrompt = arg('offermain-prompt', '') || item.prompt;
        res.offer_main_prompt = (await backend('POST', `/ai/pulsar/${id}/prompt/offermain/`, { prompt: mainPrompt, offer_status: 'planning' })).status;
        const ask = { ...req, existing_counters: await existingCounters(id) };
        res.existing_counters = ask.existing_counters.counters.map((m) => `${m.name}:${m.data_type}:${m.counter_type}`);
        let r;
        for (let i = 0; i < 20; i++) { r = await backend('POST', '/ai/pulsar/offer/rules/?regenerate=true', ask); if (r.body.result !== 'Generating') break; }
        res.counter_report = r.body.counter_report;
        res.counter_notes = r.body.counter_notes;
        ai.push({ method: 'POST', rel: '/ai/pulsar/offer/rules/?regenerate=true', status: r.status, result: String(r.body.result || r.body.error?.message || '').slice(0, 120) });
        res.generation = { status: r.status, backend_status: r.body.status, blocking_questions: r.body.blocking_questions, unsupported: r.body.unsupported,
                           assumptions: r.body.assumptions, error: r.body.error };
        if (!(r.status < 300) || !r.body.xml) throw new Error(`generation failed (${r.status} ${JSON.stringify(r.body.error || r.body.status)}); not uploading`);
        res.upload_xml = path.join(OUT, item.key + '.generated.xml');
        fs.writeFileSync(res.upload_xml, b64(r.body.xml));
        say(`${item.key}: AI generated the rules from "${item.prompt}" (${r.body.status})`);
      } else {
        // Offer Main keeps the rules-screen GenAI button only for an offer with a stored 'offermain' prompt (it clears
        // offer_creation_method_<id> otherwise), so store one as the UI's setAiPrompt does when an offer is created by prompt
        // --offermain-prompt: a fixed Offer Main prompt (the offer's own description) so the popup prompt is a rules-only
        // prompt; otherwise the item prompt is also the whole-offer prompt and the backend splits it (segment / metadata)
        const mainPrompt = arg('offermain-prompt', '') || item.prompt;
        res.offer_main_prompt = (await backend('POST', `/ai/pulsar/${id}/prompt/offermain/`, { prompt: mainPrompt, offer_status: 'planning' })).status;
      }
      await page.evaluate(() => localStorage.setItem('offe_type', 'reward'));
      // load the app on the dashboard first (the menu loads the AI toggles the rules screen checks once), then move to the
      // rules screen inside the app, as a user does
      await page.goto(`${ORIGIN}/#/dashboard/mydashboard`, { waitUntil: 'domcontentloaded' });
      await page.reload({ waitUntil: 'domcontentloaded' });
      await page.evaluate(() => performance.setResourceTimingBufferSize(20000));  // the upload wait counts bolapi calls
      await sleep(8000);
      if (/#\/login/.test(page.url())) throw new Error(`logged out of ${TENANT}: sign in in the debug Chrome window`);
      say(`${item.key}: opening offer ${id} on Offer Main, then Offer Rules`);
      // Offer Main loads the offer into OfferSetupService (offerType), which the rules screen's GenAI check needs
      await page.evaluate((id) => { location.hash = `#/offer/${id}`; }, id);
      await sleep(7000);
      const rulesLink = await page.evaluateHandle(() => [...document.querySelectorAll('a, li, div, span')]
        .find((e) => e.childElementCount === 0 && e.textContent.trim() === 'Offer Rules' && e.offsetParent !== null) || null);
      if (!rulesLink.asElement()) throw new Error('Offer Rules menu item not found on Offer Main');
      await rulesLink.asElement().evaluate((e) => e.click());
      await sleep(5000);
      // the label click does not always reach the side nav's handler: fall back to an in-app route change (no reload, so
      // OfferSetupService keeps the offer Offer Main loaded)
      if (!/\/rules/.test(page.url())) await page.evaluate((id) => { location.hash = `#/offer/${id}/rules`; }, id);
      await page.waitForSelector('.blocklySvg, .injectionDiv', { timeout: 90000 });
      if (item.mode === 'upload') {
        await shown('img[title="Upload Rules"]', 90000);
        // wait for the screen's catalog calls (attributes, lookups, accounts, one tags call per account) to go quiet: XML
        // uploaded before they land loads with empty dropdowns (devintbf2: Filter attribute and account/tag left blank)
        const bolapiCalls = () => page.evaluate(() => performance.getEntriesByType('resource').filter((e) => /\/bolapi\//.test(e.name)).length);
        let last = -1, quietSince = Date.now();
        for (const t0 = Date.now(); Date.now() - t0 < 180000;) {
          await sleep(1000);
          const n = await bolapiCalls();
          if (n !== last) { last = n; quietSince = Date.now(); } else if (Date.now() - quietSince > 8000 && Date.now() - t0 > 10000) break;
        }
        res.screen_bolapi_calls = last;
        await page.evaluate(() => document.querySelectorAll('[class*="toast"] [class*="close"], .toast-close-button').forEach((x) => x.click()));
        await page.evaluate(() => document.querySelector('img[title="Upload Rules"]').click());
        await sleep(1500);
        // "upload new rules?" confirm, only shown when the offer already has rules
        await page.evaluate(() => { const ok = [...document.querySelectorAll('button')].find((b) => /^OK$/i.test(b.innerText.trim()) && b.offsetParent !== null); ok?.click(); });
        const input = await page.waitForSelector('#inputFileUpload', { timeout: 20000 });
        say(`${item.key}: uploading the generated XML through Upload Rules`);
        await input.uploadFile(res.upload_xml);
        // the previous case's rules are already on the canvas, so "blocks > 0" proves nothing: wait until the canvas
        // shows THIS XML's rule IDENTIFIERs, upload once more if it does not, and never save otherwise
        const tags = [...fs.readFileSync(res.upload_xml, 'utf8').matchAll(/<field name="IDENTIFIER">([^<]*)<\/field>/g)]
          .map((m) => m[1].replace(/&amp;/g, '&').replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&quot;/g, '"').replace(/&apos;/g, "'"))
          .filter(Boolean);
        const onCanvas = () => page.evaluate((tags) => {
          const ws = window.Blockly?.getMainWorkspace?.();
          const ids = ws ? ws.getAllBlocks(false).map((bl) => bl.getFieldValue?.('IDENTIFIER')).filter(Boolean) : null;
          const texts = ids || [...document.querySelectorAll('.blocklyText, .blocklyEditableText text')]
            .map((e) => (e.textContent || '').replace(/\u00a0/g, ' ').trim());
          // Blockly shortens long field text on the canvas with an ellipsis
          const has = (t) => texts.some((x) => x === t.trim() || (/(\u2026|\.\.\.)$/.test(x) && t.trim().startsWith(x.replace(/(\u2026|\.\.\.)$/, ''))));
          return tags.filter((t) => !has(t));
        }, tags);
        let missing = tags;
        for (let attempt = 1; attempt <= 2 && missing.length; attempt++) {
          for (let i = 0; i < 60 && (missing = await onCanvas()).length; i++) await sleep(1000);
          if (missing.length && attempt === 1) {
            say(`${item.key}: canvas still lacks ${missing.length} of ${tags.length} rule(s) after upload; uploading again`);
            await page.evaluate(() => document.querySelector('img[title="Upload Rules"]').click());
            await sleep(1500);
            await page.evaluate(() => { const ok = [...document.querySelectorAll('button')].find((b) => /^OK$/i.test(b.innerText.trim()) && b.offsetParent !== null); ok?.click(); });
            await (await page.waitForSelector('#inputFileUpload', { timeout: 20000 })).uploadFile(res.upload_xml);
          }
        }
        res.canvas_rules = { expected: tags.length, missing };
        if (missing.length) throw new Error(`uploaded rules never reached the canvas (missing ${JSON.stringify(missing)}); not saving`);
      }
      if (item.mode === 'popup') {
        // waits poll the DOM and clicks go through the DOM: puppeteer's visible-waits and element clicks need rendered
        // frames, which Chrome stops for a window hidden behind others (the stalls in earlier runs)
        await shown('[class*="gen-ai-main-logo"]', 60000);
        await sleep(4000);
        // AI-Trust notifications sit over the top-right corner where the GenAI button is: dismiss them, then click it
        await page.evaluate(() => document.querySelectorAll('[class*="toast"] [class*="close"], .toast-close-button').forEach((x) => x.click()));
        await sleep(800);
        await page.evaluate(() => document.querySelector('[class*="gen-ai-main-logo"]').click());
        await sleep(3000);
        await page.screenshot({ path: path.join(OUT, item.key + '.popup.png') });
        await shown('#promptField', 30000);
        await page.evaluate(() => { const f = document.querySelector('#promptField'); f.focus(); f.select?.(); });
        say(`${item.key}: typing the prompt into the GenAI popup`);
        await page.type('#promptField', item.prompt, { delay: WATCH ? 35 : 0 });
        if (WATCH) await sleep(1500);
        await page.evaluate(() => document.querySelector('.gen-generate').click());
      }
      for (let i = 0; i < 160; i++) { await sleep(750); if (i > 8 && (await blocks()) > 0 && !(await loaderUp())) break; }
      await sleep(2500);
      res.blocks = await blocks();
      res.note_on_canvas = await page.evaluate(() => [...document.querySelectorAll('textarea, .blocklyCommentText, .blocklyText')]
        .some((e) => /AI draft:/.test(e.value || e.textContent || '')));
      res.ai_seen = [...new Set(aiSeen)];
      res.ai_calls = ai.map((a) => `${a.method} ${a.rel} -> ${a.status ?? a.error} ${String(a.result || '').slice(0, 40)}`);
      res.ui_counter_posts = mtoPosts.map((m) => ({ name: m.body.name, data_type: m.body.data_type, counter_type: m.body.counter_type,
                                                    entity_attribute_map: m.body.entity_attribute_map, status: m.status }));
      await page.screenshot({ path: path.join(OUT, item.key + '.loaded.png') });
      say(`${item.key}: draft loaded (${res.blocks} blocks, note ${res.note_on_canvas ? 'shown' : 'missing'}, UI created ${res.ui_counter_posts.length} counter(s)); saving in ${WATCH / 1000}s`);
      // never save a canvas the AI did not just fill: a failed generation leaves the previous rules on it
      const gen = ai.filter((a) => a.method === 'POST' && /\/offer\/rules/.test(a.rel)).pop();
      if (item.mode !== 'autoload' && (!gen || !(gen.status < 300))) {
        throw new Error(`generation failed (${gen ? gen.status + ' ' + gen.result : 'no POST /offer/rules'}); not saving`);
      }
      if (WATCH) await sleep(WATCH);
      res.save_clicked = await clickSave();
      if (res.save_clicked === 'disabled') {
        say(`${item.key}: SAVE FAILED: Save is disabled (edit-offer permission off for this role); stopping`);
        summary[item.key] = res; fs.writeFileSync(path.join(OUT, 'summary.json'), JSON.stringify(summary, null, 1));
        break;
      }
      for (let i = 0; i < 60 && !drlCalls.some((c) => c.status); i++) await sleep(500);
      await sleep(2000);
      const call = drlCalls.find((c) => c.status);
      const savedOk = call && call.status < 300;
      say(savedOk ? `${item.key}: saved (HTTP ${call.status}), offer stays in planning`
                  : `${item.key}: SAVE FAILED (HTTP ${call ? call.status : 'none'})${call?.status === 403 ? ': edit-offer permission is off; stopping' : ''}`);
      if (WATCH) await sleep(3000);
      if (call) {
        let body = {}; try { body = JSON.parse(call.body || '{}'); } catch {}
        // a rejected save is not on the server: keep what the UI compiled, but not where the checker reads saved rules
        fs.writeFileSync(path.join(OUT, item.key + (savedOk ? '.drl' : '.unsaved.drl')), b64(body.drl));
        fs.writeFileSync(path.join(OUT, item.key + '.saved.xml'), b64(body.xml));
        res.save = { method: call.method, status: call.status, url: call.url, response: call.status >= 300 ? call.response : undefined,
                     body_keys: Object.keys(body), body_sizes: Object.fromEntries(Object.entries(body).map(([k, v]) => [k, String(v ?? '').length])),
                     body_small: Object.fromEntries(Object.entries(body).filter(([k, v]) => String(v ?? '').length < 200)) };
      }
      const stored = await api('GET', `offers/${id}/drl/`);
      const row = Array.isArray(stored.body) ? stored.body[0] : stored.body?.results?.[0];
      const ids = [...new Set([...b64(row?.drl).matchAll(/rule "activity-(\d+)-/g)].map((m) => m[1]))];
      res.stored_drl_offer_ids = ids;
      const mtos = await api('GET', `offers/${id}/member-tracked-attributes/`);
      res.stored_counters = (Array.isArray(mtos.body) ? mtos.body : []).map((m) => `${m.name}:${m.data_type}:${m.counter_type}`);
      res.offer_status = (await api('GET', `offers/${id}/`)).body?.status;
    } catch (e) {
      res.error = String(e.message || e);
      await page.screenshot({ path: path.join(OUT, item.key + '.error.png') }).catch(() => {});
      if (item.attempt < 2 && !/logged out/.test(res.error)) {
        say(`${item.key}: ${res.error.slice(0, 80)}; retrying once at the end`);
        queue.push({ ...item, attempt: item.attempt + 1 });
      }
    }
    summary[item.key] = res;
    fs.writeFileSync(path.join(OUT, 'summary.json'), JSON.stringify(summary, null, 1));
    console.log(JSON.stringify(res));
    if (res.save?.status === 403) break;
  }
} finally {
  page.off('request', onReq); page.off('response', onResp);
  await page.setRequestInterception(false);
  await b.disconnect();
}

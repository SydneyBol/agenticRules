// Read-only: GET offers/{id}/drl/ for each ZZ-AI-SPIKE offer and check the stored rules belong to that offer:
// DRL rule ids carry the offer id, and the stored XML equals what the hosted UI sent when we saved it.
import fs from 'fs';
import path from 'path';
import puppeteer from 'puppeteer-core';
const ROOT = path.resolve(path.dirname(new URL(import.meta.url).pathname), '..');
const TENANT = 'devintam1';
const OUT = path.join(ROOT, 'results', `hosted_${TENANT}`);
const state = JSON.parse(fs.readFileSync(path.join(ROOT, 'results', TENANT, 'offers.json'), 'utf8'));
const b = await puppeteer.connect({ browserURL: 'http://127.0.0.1:9222', defaultViewport: null });
const page = (await b.pages()).find((p) => p.url().includes(`${TENANT}.gravty.info`));
const get = (rel) => page.evaluate(async (rel) => {
  const cookie = (n) => decodeURIComponent((document.cookie.match(new RegExp('(?:^|; )' + n + '=([^;]+)')) || [])[1] || '');
  const r = await fetch(`/bolapi/v1/${rel}`, { headers: { Authorization: 'JWT ' + cookie('token_key'), 'X-CSRFToken': cookie('XSRF-COOKIE'), 'x-aws-waf-token': cookie('aws-waf-token') } });
  return { status: r.status, body: await r.json().catch(() => null) };
}, rel);
const b64 = (s) => Buffer.from(s || '', 'base64').toString('utf8');
for (const [sid, o] of Object.entries(state)) {
  const r = await get(`offers/${o.offer_id}/drl/`);
  const row = Array.isArray(r.body) ? r.body[0] : (r.body?.results?.[0] ?? r.body);
  const drl = b64(row?.drl), xml = b64(row?.xml);
  const ids = [...new Set([...drl.matchAll(/rule "activity-(\d+)-/g)].map((m) => m[1]))];
  const sent = fs.existsSync(path.join(OUT, sid + '.saved.xml')) ? fs.readFileSync(path.join(OUT, sid + '.saved.xml'), 'utf8') : '';
  const offer = await get(`offers/${o.offer_id}/`);
  console.log(`${sid.padEnd(40)} drl_ids=${ids.join(',')} own=${ids.length === 1 && ids[0] === String(o.offer_id)} xml_matches_sent=${xml === sent} blocks=${(xml.match(/<block /g) || []).length} status=${offer.body?.status}`);
}
await b.disconnect();

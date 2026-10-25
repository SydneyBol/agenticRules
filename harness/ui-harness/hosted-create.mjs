// Create the ZZ-AI-SPIKE-<sid> test offers (planning status) on the hosted tenant and pre-create their counters (MTOs).
// Runs inside the logged-in debug Chrome tab with the same headers the app's AuthInterceptor adds.
// Never launches anything: the only writes are POST offers/ and POST offers/{id}/member-tracked-attributes/.
// Idempotent: offer ids are kept in results/<tenant>/offers.json and counters are only created when missing by name.
// usage: node hosted-create.mjs [--tenant=devintam1] [--only=S14,S01]
import fs from 'fs';
import path from 'path';
import puppeteer from 'puppeteer-core';

const ROOT = path.resolve(path.dirname(new URL(import.meta.url).pathname), '..');
const arg = (n, d) => (process.argv.find((a) => a.startsWith(`--${n}=`)) || '').split('=')[1] || d;
const TENANT = arg('tenant', 'devintam1');
const ONLY = arg('only', '') ? arg('only', '').split(',') : null;
const SPONSOR = 1;
const IN = path.join(ROOT, 'results', `compiled_${TENANT}`);
const STATE = path.join(ROOT, 'results', TENANT, 'offers.json');
const PREFIX = 'ZZ-AI-SPIKE-';

const b = await puppeteer.connect({ browserURL: 'http://127.0.0.1:9222', defaultViewport: null });
const page = (await b.pages()).find((p) => p.url().includes(`${TENANT}.gravty.info`));
if (!page) throw new Error(`no ${TENANT} tab open`);

const api = (method, rel, body) => page.evaluate(async (method, rel, body) => {
  const cookie = (n) => decodeURIComponent((document.cookie.match(new RegExp('(?:^|; )' + n + '=([^;]+)')) || [])[1] || '');
  const g = JSON.parse(localStorage.getItem('globals') || '{}');
  const token = g?.currentUser?.token_key || cookie('token_key');
  if (!token) return { status: 0, body: 'not logged in' };
  const r = await fetch(`/bolapi/v1/${rel}`, {
    method,
    headers: { Authorization: 'JWT ' + token, 'X-CSRFToken': cookie('XSRF-COOKIE'), 'x-aws-waf-token': cookie('aws-waf-token'),
               'Content-Type': 'application/json' },
    body: body ? JSON.stringify(body) : undefined,
  });
  const text = await r.text();
  try { return { status: r.status, body: JSON.parse(text) }; } catch { return { status: r.status, body: text.slice(0, 800) }; }
}, method, rel, body);

// same shape newui's createOfferRequestPayload() sends for a reward offer (see the individualiseOfferAutomation skill)
function offerPayload(sid) {
  const now = new Date();
  const end = new Date(now.getTime() + 90 * 864e5);
  return {
    offer_name: (PREFIX + sid).slice(0, 50), offer_description: 'English-to-Blockly spike test offer. Do not launch.',
    term_and_condition: '', offer_type: 'reward', offer_billing_type: 'bit_sponsor', sponsors: [SPONSOR],
    start_date: now.toISOString().slice(0, 19), end_date: end.toISOString().slice(0, 10) + 'T23:59:00',
    offer_external_id: `zz-ai-spike-${sid.split('_')[0].toLowerCase()}-${Date.now().toString(36)}`,
    fb_share: false, twitter_share: false, offer_acceptance_required: false, non_cumulative: false, send_notification: false,
    product_targeting_file: null, repeat_weekday: '', repeat_day: '', repeat_month: '', is_base_offer: false, priority: 0,
    is_product_targeting: false, smart_family_contribution: true, sponsors_data: [], member_visible: false, products: [],
    sponsorship_details: [], notification_schedule_time: null, notification_schedule_type: null,
    member_visible_type: 'on_launch', valid_period: 'DAYS', extra_data: {}, tags: [], rich_extra_data: {},
    is_parent_offer: false,
    maximum_reward_points_info: { maximum_points_limit: null, loyalty_account: null, reward_limit_type: 'POINTS' },
    interval: '', interval_period: null, scheduled_on: null, scheduled_on_time: null, version: 1, max_usage_per_user: null,
    non_cumulative_level: null, locations: [], location_category: 'ALL_LOCATIONS', is_individualized: false,
  };
}

// counters from the compiler spec -> the MTO body the rules-upload dialog posts (rules-upload-model.component.ts)
function mtoPayload(offerId, c) {
  const m = { offer: offerId, name: c.name, data_type: c.data_type, level: c.level === 'MA_A' ? 'ACTIVITY' : 'OFFER',
              activity_id: 1, counter_type: c.counter_type || 'REGULAR' };
  if (c.data_type !== 'TEXT') m.target_type = c.target_type || 'NO TARGET';
  if (c.counter_type === 'VECTOR' && c.vector_namespace) m.entity_attribute_map = { [c.vector_level || 'Activity']: [c.vector_namespace] };
  return m;
}

const state = fs.existsSync(STATE) ? JSON.parse(fs.readFileSync(STATE, 'utf8')) : {};
const sids = fs.readdirSync(IN).filter((f) => f.endsWith('.xml')).map((f) => f.slice(0, -4))
  .filter((s) => !ONLY || ONLY.some((o) => s.startsWith(o)));
for (const sid of sids) {
  const spec = JSON.parse(fs.readFileSync(path.join(IN, sid + '.spec.json'), 'utf8'));
  let id = state[sid]?.offer_id;
  if (!id) {
    const r = await api('POST', 'offers/', offerPayload(sid));
    if (r.status !== 201 && r.status !== 200) { console.log(`${sid} CREATE ${r.status}`, JSON.stringify(r.body).slice(0, 600)); continue; }
    id = r.body.id;
    state[sid] = { offer_id: id, name: r.body.offer_name, status: r.body.status?.code ?? r.body.status, created: new Date().toISOString() };
    fs.writeFileSync(STATE, JSON.stringify(state, null, 1));
  }
  const have = await api('GET', `offers/${id}/member-tracked-attributes/`);
  const names = new Set((Array.isArray(have.body) ? have.body : []).map((m) => m.name));
  const made = [];
  for (const c of spec.counters) {
    if (names.has(c.name)) continue;
    const r = await api('POST', `offers/${id}/member-tracked-attributes/`, mtoPayload(id, c));
    made.push(`${c.name}:${r.status}${r.status >= 300 ? ' ' + JSON.stringify(r.body).slice(0, 300) : ''}`);
  }
  state[sid].counters = spec.counters.map((c) => c.name);
  fs.writeFileSync(STATE, JSON.stringify(state, null, 1));
  console.log(`${sid.padEnd(40)} offer=${id} status=${state[sid].status} counters=${spec.counters.length} ${made.join(' | ')}`);
}
await b.disconnect();

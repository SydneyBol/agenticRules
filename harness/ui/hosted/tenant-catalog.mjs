// Read-only: pull the tenant vocabulary the Blockly dropdowns use, from the logged-in debug Chrome tab.
// GET requests only, made from inside the page with the same headers the app's AuthInterceptor adds.
// Writes catalogs/<tenant>/bolapi/*.json ({status, body} per file), the layout
// Catalog.from_bolapi_dumps and mock-run.mjs --tenant read.
// usage: node tenant-catalog.mjs [--tenant=devintam1] [--sponsor=<id>] [--account=<id>] [--extra=name:path,...]
import fs from 'fs';
import path from 'path';
import puppeteer from 'puppeteer-core';

const HERE = path.dirname(new URL(import.meta.url).pathname);
const arg = (n, d) => (process.argv.find((a) => a.startsWith(`--${n}=`)) || '').split('=').slice(1).join('=') || d;
const TENANT = arg('tenant', 'devintam1');
const HOST = `${TENANT}.gravty.info`;
const OUT = path.resolve(HERE, '../../../catalogs', TENANT, 'bolapi');
fs.mkdirSync(OUT, { recursive: true });
// --extra=name:path,name:path  -> only those GETs, saved as extra_<name>.json
const extraArg = arg('extra', '');

const b = await puppeteer.connect({ browserURL: 'http://127.0.0.1:9222', defaultViewport: null });
const page = (await b.pages()).find((p) => p.url().includes(HOST));
if (!page) throw new Error(`no ${TENANT} tab open in the debug Chrome`);

const get = (rel) => page.evaluate(async (rel) => {
  const cookie = (n) => decodeURIComponent((document.cookie.match(new RegExp('(?:^|; )' + n + '=([^;]+)')) || [])[1] || '');
  const g = JSON.parse(localStorage.getItem('globals') || '{}');
  // develop reads globals.currentUser.token_key; the hosted build keeps it in the token_key cookie
  const token = g?.currentUser?.token_key || cookie('token_key');
  if (!token) return { __error: 'not logged in' };
  const r = await fetch(`/bolapi/v1/${rel}`, { headers: { Authorization: 'JWT ' + token, 'X-CSRFToken': cookie('XSRF-COOKIE'), 'x-aws-waf-token': cookie('aws-waf-token') } });
  const text = await r.text();
  try { return { status: r.status, body: JSON.parse(text) }; } catch { return { status: r.status, body: text.slice(0, 500) }; }
}, rel);

const list = (r) => (Array.isArray(r.body) ? r.body : (r.body?.results || []));
const save = (name, v) => fs.writeFileSync(path.join(OUT, name + '.json'), JSON.stringify(v, null, 1));
const fetchSave = async (name, rel) => {
  const r = await get(rel); save(name, r);
  console.log(`${name.padEnd(36)} ${r.status} ${Array.isArray(r.body) ? r.body.length : (r.body?.results?.length ?? typeof r.body)}`);
  return r;
};
const who = await page.evaluate(() => {
  const g = JSON.parse(localStorage.getItem('globals') || '{}');
  return { programId: g?.programId, role: g?.currentUser?.role?.code, sponsor_id: g?.currentUser?.sponsor_id,
           logged_in: !!(g?.currentUser?.token_key || /(?:^|; )token_key=/.test(document.cookie)) };
});
console.log(`${TENANT} session:`, who, '->', OUT);
if (!who.logged_in) { await b.disconnect(); process.exit(2); }

if (extraArg) {
  for (const pair of extraArg.split(',')) {
    const i = pair.indexOf(':');
    await fetchSave('extra_' + pair.slice(0, i), pair.slice(i + 1));
  }
  await b.disconnect(); process.exit(0);
}
const got = {};
for (const [k, rel] of Object.entries({
  activity_attributes: 'attributelibrary/activity/',
  member_attributes: 'attributelibrary/member/?all=True',
  product_attributes: 'attributelibrary/custom/?module_type=PRODUCT',
  sponsors: 'trim/sponsors/?page_size=200',
  loyalty_accounts_all: 'loyaltyaccounts/',
  bit_types: 'bit-types/',
  lovs: 'lovs/',
  offer_status: 'offer-status/',
  extra_tiers: 'tier-list/?page_size=500',
  extra_tierclass: 'lookups/?type=TIER_CLASS&active=True&page_size=1000',
  extra_prodcat: 'lookups/?type=PRODUCT_CATEGORY&active=True&page_size=1000',
})) got[k] = await fetchSave(k, rel);

const sponsors = list(got.sponsors);
const sponsor = arg('sponsor', '') || sponsors[0]?.id;
console.log('sponsors:', sponsors.slice(0, 12).map((s) => `${s.id}:${s.sponsor_name || s.name}`).join(' | '), '-> using', sponsor);
got.sla = await fetchSave('sponsor_loyalty_accounts', `sponsors/loyaltyaccounts/?sponsor_id=${sponsor}&offer_type=reward`);
await fetchSave('privilege_products', `product/?sponsor_in=${sponsor}&as_privilege=true&page_size=500&bundle_type=static`);
await fetchSave(`extra_locations${sponsor}`, `sponsors/${sponsor}/locations/?active=True&page_size=1000`);
const accounts = got.sla.body?.loyalty_accounts || [];
console.log('reward loyalty accounts:', accounts.map((a) => `${a.id}:${a.loyalty_currency}`).join(' | '));
for (const id of arg('account', '') ? [arg('account', '')] : accounts.map((a) => a.id)) {
  await fetchSave(`extra_tags${id}`, `loyaltyaccounts/${id}/tags/?active=true&page_size=1000`);
}
// lookups for the LOOKUP attributes the rules use
const want = ['h_bit_type', 'h_location', 'h_bit_amount', 'h_bit_date', 'l_quantity', 'l_amount', 'l_product_external_id'];
const lookupTypes = new Set(list(got.activity_attributes).filter((a) => want.includes(a.name) && a.lookup_type).map((a) => a.lookup_type));
lookupTypes.add('BIT_TYPE');
for (const lt of lookupTypes) await fetchSave('lookup_' + lt, `lookups/?type=${encodeURIComponent(lt)}&active=True&page_size=1000`);
await b.disconnect();

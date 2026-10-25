// Read-only: print the tenant's AI feature toggles (pulsar config) and the logged-in role.
import puppeteer from 'puppeteer-core';
const b = await puppeteer.connect({ browserURL: 'http://127.0.0.1:9222', defaultViewport: null });
const page = (await b.pages()).find((p) => p.url().includes('devintam1.gravty.info'));
console.log(JSON.stringify(await page.evaluate(async () => {
  const cookie = (n) => decodeURIComponent((document.cookie.match(new RegExp('(?:^|; )' + n + '=([^;]+)')) || [])[1] || '');
  const g = JSON.parse(localStorage.getItem('globals') || '{}');
  let gc = {}; try { gc = JSON.parse(cookie('globals') || '{}'); } catch {}
  const pid = g.currentUser?.programId ?? g.programId ?? gc.currentUser?.programId ?? gc.programId
    ?? JSON.parse(localStorage.getItem('programInfo') || '[{}]')[0]?.id;
  const r = await fetch(`/bolapi/v1/features/${pid}?owning_service=AI`, { headers: { Authorization: 'JWT ' + cookie('token_key'), 'X-CSRFToken': cookie('XSRF-COOKIE'), 'x-aws-waf-token': cookie('aws-waf-token') } });
  const body = await r.json().catch(() => null);
  const rows = Array.isArray(body) ? body : body?.results || [];
  return { programId: pid, status: r.status, role: g.currentUser?.role?.code,
           toggles: rows.map((x) => ({ name: x.toggle_name, value: x.toggle_name === 'pulsar' ? x.value : (x.value?.enabled ?? x.value?.active) })) };
}), null, 1));
await b.disconnect();

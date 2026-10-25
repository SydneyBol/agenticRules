// Read-only: key NAMES (no values) of globals in localStorage and cookie, and of programInfo.
import puppeteer from 'puppeteer-core';
const b = await puppeteer.connect({ browserURL: 'http://127.0.0.1:9222', defaultViewport: null });
const page = (await b.pages()).find((p) => p.url().includes('devintam1.gravty.info'));
console.log(JSON.stringify(await page.evaluate(() => {
  const cookie = (n) => decodeURIComponent((document.cookie.match(new RegExp('(?:^|; )' + n + '=([^;]+)')) || [])[1] || '');
  const keys = (o) => (o && typeof o === 'object' ? Object.keys(o) : typeof o);
  let gc = null; try { gc = JSON.parse(cookie('globals')); } catch { gc = 'unparsable'; }
  const g = JSON.parse(localStorage.getItem('globals') || 'null');
  const pi = JSON.parse(localStorage.getItem('programInfo') || 'null');
  return { ls_globals: keys(g), ls_user: keys(g?.currentUser), cookie_globals: keys(gc), cookie_user: keys(gc?.currentUser),
           programInfo: Array.isArray(pi) ? keys(pi[0]) : keys(pi), ls_keys: Object.keys(localStorage).filter((k) => /program|feature|ai/i.test(k)) };
})));
await b.disconnect();

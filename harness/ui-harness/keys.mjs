import puppeteer from 'puppeteer-core';
const b = await puppeteer.connect({ browserURL: 'http://127.0.0.1:9222', defaultViewport: null });
const page = (await b.pages()).find((p) => p.url().includes('devintam1.gravty.info'));
console.log('url', page.url());
console.log(await page.evaluate(() => {
  const g = JSON.parse(localStorage.getItem('globals') || '{}');
  const shape = (o, d = 0) => o && typeof o === 'object' && d < 2 ? Object.fromEntries(Object.keys(o).map((k) => [k, shape(o[k], d + 1)])) : typeof o;
  return JSON.stringify({ localStorage: Object.keys(localStorage), sessionStorage: Object.keys(sessionStorage),
    cookieNames: document.cookie.split(';').map((c) => c.split('=')[0].trim()), globalsShape: shape(g) }, null, 1);
}));
await b.disconnect();

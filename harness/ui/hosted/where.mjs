import puppeteer from 'puppeteer-core';
const b = await puppeteer.connect({ browserURL: 'http://127.0.0.1:9222', defaultViewport: null });
for (const p of await b.pages()) console.log(p.url(), '|', await p.title());
await b.disconnect();

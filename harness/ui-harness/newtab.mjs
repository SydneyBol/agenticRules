import puppeteer from 'puppeteer-core';
const b = await puppeteer.connect({ browserURL: 'http://127.0.0.1:9222', defaultViewport: null });
const p = await b.newPage();
await p.goto(process.argv[2], { waitUntil: 'domcontentloaded' });
await p.bringToFront();
console.log('opened', p.url());
await b.disconnect();

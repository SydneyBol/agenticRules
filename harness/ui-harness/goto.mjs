import puppeteer from 'puppeteer-core';
const url = process.argv[2];
const browser = await puppeteer.connect({ browserURL: 'http://127.0.0.1:9222', defaultViewport: null });
const pages = await browser.pages();
const page = pages.find(p => p.url().startsWith('http')) || pages[0];
await page.goto(url, { waitUntil: 'domcontentloaded' });
console.log('now at', page.url());
await browser.disconnect();

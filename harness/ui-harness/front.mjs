// Bring the debug Chrome's devintam1 tab to the front (login page if logged out) and un-minimise its window.
import puppeteer from 'puppeteer-core';
const b = await puppeteer.connect({ browserURL: 'http://127.0.0.1:9222', defaultViewport: null });
let page = (await b.pages()).find((p) => p.url().includes('devintam1.gravty.info'));
if (!page) { page = await b.newPage(); await page.goto('https://master.devintam1.gravty.info/#/login', { waitUntil: 'domcontentloaded' }); }
const s = await page.target().createCDPSession();
const { windowId } = await s.send('Browser.getWindowForTarget');
await s.send('Browser.setWindowBounds', { windowId, bounds: { windowState: 'normal' } });
await page.bringToFront();
console.log('front:', page.url());
await b.disconnect();

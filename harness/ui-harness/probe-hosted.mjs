// Read-only probe of the hosted devintam1 tab: which in-page hooks exist (no network writes).
import puppeteer from 'puppeteer-core';
const b = await puppeteer.connect({ browserURL: 'http://127.0.0.1:9222', defaultViewport: null });
const page = (await b.pages()).find((p) => p.url().includes('devintam1.gravty.info'));
console.log('url', page.url());
console.log(await page.evaluate(() => ({ ng: typeof window.ng, getComponent: typeof window.ng?.getComponent,
  Blockly: typeof window.Blockly, ngVersion: document.querySelector('[ng-version]')?.getAttribute('ng-version') })));
await b.disconnect();

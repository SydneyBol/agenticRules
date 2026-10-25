// Screenshot the hosted tab (read-only) to results/<tenant>/shot.png and print its url
import puppeteer from 'puppeteer-core';
const b = await puppeteer.connect({ browserURL: 'http://127.0.0.1:9222', defaultViewport: null });
const page = (await b.pages()).find((p) => p.url().includes('gravty.info'));
console.log(page.url());
await Promise.race([page.screenshot({ path: process.argv[2] || '/tmp/shot.png' }), new Promise((r) => setTimeout(r, 8000))]);
await b.disconnect();

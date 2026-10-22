/* Browser integration check. PLAYWRIGHT_MODULE may point to a bundled installation. */
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '..');
const base = process.env.APP_URL || 'http://127.0.0.1:8000';
const out = path.join(root, 'reports/screenshots');
fs.mkdirSync(out, { recursive: true });
const checks = [], errors = [];
function check(name, ok, detail) { checks.push({ name, passed: Boolean(ok), detail }); if (!ok) throw Error(name + ': ' + detail); }
(async () => {
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, locale: 'ru-RU' });
  const page = await context.newPage();
  page.on('pageerror', e => errors.push(e.message));
  page.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
  try {
    await page.goto(base);
    await page.waitForSelector('.news-row');
    check('feed loaded', await page.locator('.news-row').count() > 0, 'API-backed events');
    await page.screenshot({ path: path.join(out, 'desktop-feed.png'), fullPage: true });
    const companies = await page.locator('#company-select option').evaluateAll(nodes => nodes.map(n => n.value));
    check('three profiles', companies.length >= 3, companies);
    const reasonsBefore = await page.locator('.relevance').first().innerText();
    await page.selectOption('#company-select', companies[1]);
    await page.waitForTimeout(250);
    const reasonsAfter = await page.locator('.relevance').first().innerText();
    check('company changes relevance', reasonsBefore !== reasonsAfter, { reasonsBefore, reasonsAfter });
    await page.selectOption('#company-select', companies[0]);
    await page.selectOption('#role-select', 'cfo');
    await page.waitForTimeout(250);
    await page.locator('[data-analyze]').first().click();
    await page.waitForSelector('#question-form');
    check('analysis sections', await page.getByText('Для вашей компании', { exact: false }).count() > 0, 'facts/bank/interpretation displayed');
    await page.screenshot({ path: path.join(out, 'desktop-analysis.png'), fullPage: true });
    await page.locator('#question').fill('Какие риски для ликвидности и связи группы нужно проверить?');
    await page.locator('#question-form button[type=submit]').click();
    await page.getByRole('heading', { name: 'Ответ на ваш вопрос' }).waitFor();
    check('followup', true, 'Contextual response displayed');
    await page.locator('[data-rating]').first().click();
    await page.locator('#feedback-comment').fill('Автоматическая проверка интерфейса');
    await page.locator('#feedback-form button[type=submit]').click();
    await page.waitForFunction(() => document.querySelector('#feedback-status')?.textContent.includes('сохранена'));
    check('feedback stored', true, 'API accepted rating');
    await page.locator('[data-action=back]').click();
    await page.locator('#news-search').fill('несуществующее-событие-тест');
    check('empty search state', await page.getByRole('heading', { name: 'Подходящих событий нет' }).isVisible(), 'No fabricated rows');
    await page.locator('[data-action=reset-filters]').click();
    await page.locator('[data-action=import]').click();
    const title = 'Проверка нового события: расходы на логистику ' + Date.now();
    await page.locator('#import-form [name=title]').fill(title);
    await page.locator('#import-form [name=text]').fill('Синтетический тест: поставщик сообщил об изменении условий доставки. Стоимость транспортировки требует проверки договоров; суммы и валюта не известны.');
    await page.locator('#import-form [name=date]').fill('2026-10-22');
    await page.locator('#import-form [name=url]').fill('https://example.invalid/synthetic-ui-test');
    await page.locator('#import-form [name=synthetic]').check();
    await page.locator('#import-form button[type=submit]').click();
    await page.waitForFunction(() => !document.querySelector('#import-dialog').open);
    await page.locator('#news-search').fill(title);
    await page.locator('[data-analyze]').first().click();
    await page.waitForSelector('#question-form');
    check('unseen event full flow', await page.locator('h1').innerText() === title, 'No source code change');
    await page.goto(base + '/#profile');
    await page.getByRole('heading', { name: 'Профиль компании', exact: true }).waitFor();
    await page.screenshot({ path: path.join(out, 'desktop-profile.png'), fullPage: true });
    await page.goto(base + '/#home');
    await page.getByRole('heading', { name: 'Главная', exact: true }).waitFor();
    check('persistent entry', await page.getByRole('link', { name: 'Открыть персональную ленту →' }).isVisible(), 'Home entry and sidebar');
    await page.screenshot({ path: path.join(out, 'desktop-home.png'), fullPage: true });
    await page.goto(base + '/#settings');
    await page.waitForSelector('.metrics-grid');
    await page.screenshot({ path: path.join(out, 'desktop-settings.png'), fullPage: true });
    for (const width of [768, 390]) {
      await page.setViewportSize({ width, height: 900 });
      await page.goto(base + '/#pulse');
      await page.locator('#news-search').fill('');
      await page.waitForSelector('.news-row');
      check('no horizontal overflow ' + width, await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'Viewport width');
      await page.screenshot({ path: path.join(out, `feed-${width}.png`), fullPage: true });
      if (width === 390) {
        await page.locator('[data-analyze]').first().click();
        await page.waitForSelector('#question-form');
        check('analysis no overflow 390', await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'Mobile detail');
        await page.screenshot({ path: path.join(out, 'analysis-390.png'), fullPage: true });
      }
    }
    check('no JS/console errors', errors.length === 0, errors);
  } catch (e) {
    checks.push({ name: 'browser flow', passed: false, detail: e.message });
    await page.screenshot({ path: path.join(out, 'failure.png'), fullPage: true });
    process.exitCode = 1;
  } finally {
    const result = { checked_at: new Date().toISOString(), checks, errors };
    fs.writeFileSync(path.join(root, 'reports/browser-checks.json'), JSON.stringify(result, null, 2));
    console.log(JSON.stringify(result, null, 2));
    await browser.close();
  }
})();

import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const golden = join(dirname(fileURLToPath(import.meta.url)), '..', '..', 'fixtures', 'golden');
const decisions = JSON.parse(readFileSync(join(golden, 'decisions.json'), 'utf8')) as Record<string, { disposition: 'accepted' | 'rejected'; note?: string }>;
const expected = JSON.parse(readFileSync(join(golden, 'expected_reviewed.geojson'), 'utf8')) as { features: unknown[] };
const shots = process.env.SCREENSHOT_DIR;
const GOLDEN_FIELDS = ['condition', 'install_year', 'material', 'notes'];

async function axe(page: Page, label: string) {
  const r = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze();
  expect(r.violations.map((v) => `${label}: ${v.id} (${v.nodes.length}) ${v.help}`)).toEqual([]);
}

async function uploadAndCompare(page: Page) {
  await page.goto('/');
  await page.evaluate(() => localStorage.clear());
  await page.goto('/');
  await page.getByTestId('try-files').first().click();
  await expect(page.getByTestId('api-status')).toContainText('Live API ready', { timeout: 20_000 });
  await page.getByTestId('file-baseline').setInputFiles(join(golden, 'baseline.geojson'));
  await expect(page.getByTestId('summary-baseline')).toContainText('13 features');
  await page.getByTestId('file-proposed').setInputFiles(join(golden, 'proposed.geojson'));
  await expect(page.getByTestId('summary-proposed')).toContainText('13 features');
  await expect(page.getByTestId('run-compare')).toBeEnabled();
  // Match the golden parameters exactly (the UI also suggests last_inspected, which the golden run leaves unselected).
  const fields = page.getByRole('group', { name: 'Fields to compare' });
  for (const box of await fields.getByRole('checkbox').all()) {
    if (await box.isDisabled()) continue;
    const name = (await box.locator('xpath=..').innerText()).trim().split(/\s+/)[0]!;
    await box.setChecked(GOLDEN_FIELDS.includes(name));
  }
  await page.getByTestId('run-compare').click();
  await expect(page.locator('[role=listbox][aria-label="Changed assets"]')).toBeAttached();
  await expect(page.locator('.queue-count')).toContainText('9 decisions left');
}

async function openAsset(page: Page, id: string) {
  const search = page.locator('#queue-search');
  if (await search.isVisible()) {
    await search.fill(id);
    await page.locator(`#q-${id}`).click();
  }
  await expect(page.getByRole('heading', { level: 2, name: id })).toBeVisible();
}

test('upload, review BENCH-0142, finish every decision and download the final export', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', (e) => errors.push(e.message));
  page.on('console', (m) => m.type() === 'error' && errors.push(m.text()));
  await page.setViewportSize({ width: 1440, height: 900 });
  await uploadAndCompare(page);
  await expect(page.getByTestId('map')).toBeVisible();
  // Regression: MapLibre's CSS once collapsed the container to 0 px; the map must fill its pane and draw.
  const box = await page.locator('.map-wrap > .map').boundingBox();
  expect(box?.height ?? 0).toBeGreaterThan(400);
  await expect(page.locator('canvas.maplibregl-canvas')).toBeVisible();
  await axe(page, 'studio');

  // The story record: accept the condition update, reject the move.
  await openAsset(page, 'BENCH-0142');
  const move = page.locator('[data-component="BENCH-0142:move"]');
  await expect(move).toContainText('119.6 m');
  await expect(page.locator('.distance-label')).toContainText('119.6 m');
  await page.locator('[data-component="BENCH-0142:attr:condition"]').getByRole('button', { name: /Accept/ }).click();
  await expect(page.locator('[data-component="BENCH-0142:attr:condition"]').getByRole('button', { name: /Accept/ })).toHaveAttribute('aria-pressed', 'true');
  await move.getByRole('button', { name: /Reject/ }).click();
  await expect(page.locator('[data-component="BENCH-0142:move"]').getByRole('button', { name: /Reject/ })).toHaveAttribute('aria-pressed', 'true');
  if (shots) await page.screenshot({ path: join(shots, 'studio-1440.png') });

  // Final export stays locked until everything is decided.
  await page.getByTestId('export').click();
  await expect(page.getByTestId('export-final')).toBeDisabled();
  await page.keyboard.press('Escape');
  await expect(page.getByTestId('export-final')).toHaveCount(0);
  await expect(page.getByTestId('export')).toBeFocused();

  // Undo restores the previous decision.
  await page.getByRole('button', { name: /Undo last decision/ }).first().click();
  await expect(page.locator('[data-component="BENCH-0142:move"]').getByRole('button', { name: /Reject/ })).toHaveAttribute('aria-pressed', 'false');
  await page.locator('[data-component="BENCH-0142:move"]').getByRole('button', { name: /Reject/ }).click();

  for (const [cid, d] of Object.entries(decisions)) {
    if (cid.startsWith('BENCH-0142')) continue;
    const asset = cid.split(':')[0]!;
    await page.locator('#queue-search').fill(asset);
    await page.locator(`#q-${asset}`).click();
    const card = page.locator(`[data-component="${cid}"]`);
    const btn = card.getByRole('button', { name: d.disposition === 'accepted' ? /Accept/ : /Reject/ });
    await btn.click();
    await expect(page.locator(`[data-component="${cid}"]`).getByRole('button', { name: d.disposition === 'accepted' ? /Accept/ : /Reject/ })).toHaveAttribute('aria-pressed', 'true');
  }
  await page.locator('#queue-search').fill('');
  await expect(page.locator('.queue-count')).toContainText('0 decisions left');

  await page.getByTestId('export').click();
  await expect(page.getByTestId('export-final')).toBeEnabled();
  const files: import('@playwright/test').Download[] = [];
  page.on('download', (d) => files.push(d));
  await page.getByTestId('export-final').click();
  await expect.poll(() => files.length).toBe(2);
  const geo = files.find((f) => f.suggestedFilename().endsWith('.geojson') && !f.suggestedFilename().includes('report'));
  const rep = files.find((f) => f.suggestedFilename().endsWith('.report.json'));
  expect(geo, 'GeoJSON download').toBeTruthy();
  expect(rep, 'report download').toBeTruthy();
  const out = JSON.parse(readFileSync((await geo!.path())!, 'utf8')) as { features: unknown[] };
  expect(out.features).toEqual(expected.features);
  const report = JSON.parse(readFileSync((await rep!.path())!, 'utf8')) as { schema: string; mode: string; counts: { output_features: number } };
  expect(report.schema).toBe('geodiff.report/1');
  expect(report.mode).toBe('final');
  expect(report.counts.output_features).toBe(13);
  await expect(page.getByTestId('notice')).toContainText('Final export downloaded');

  // Live mode is backed by the real API: the server-side state, fetched directly with the
  // browser's own workspace token, holds the same decisions and the same final export.
  const { token, cid } = await page.evaluate(() => ({
    token: localStorage.getItem('geodiff.live.token'),
    cid: localStorage.getItem('geodiff.live.comparison'),
  }));
  expect(token && cid, 'live session stored').toBeTruthy();
  const api = process.env.E2E_API_URL ?? 'http://localhost:8000';
  const auth = { Authorization: `Bearer ${token}` };
  const srv = await page.request.get(`${api}/v1/comparisons/${cid}/export?mode=final&part=geojson`, { headers: auth });
  expect(srv.status()).toBe(200);
  expect(((await srv.json()) as { features: unknown[] }).features).toEqual(expected.features);
  const q = await page.request.get(`${api}/v1/comparisons/${cid}/queue?disposition=unresolved`, { headers: auth });
  expect(q.status()).toBe(200);
  expect(((await q.json()) as { items: unknown[] }).items).toEqual([]);
  expect(errors, 'browser console errors').toEqual([]);
});

test('keyboard-only review of BENCH-0142', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await uploadAndCompare(page);
  await page.getByRole('listbox', { name: 'Changed assets' }).focus();
  await page.keyboard.press('Home');
  await expect(page.getByRole('heading', { level: 2, name: 'BENCH-0142' })).toBeVisible();
  await expect(page.locator('[data-component="BENCH-0142:move"]').getByRole('button', { name: /Reject/ })).toBeEnabled();
  // Components are listed move first: r rejects the move, ] moves to the condition edit, a accepts it, u undoes.
  const move = page.locator('[data-component="BENCH-0142:move"]');
  const cond = page.locator('[data-component="BENCH-0142:attr:condition"]');
  await expect(move).toHaveClass(/active/);
  await page.keyboard.press('r');
  await expect(move.getByRole('button', { name: /Reject/ })).toHaveAttribute('aria-pressed', 'true');
  await page.keyboard.press(']');
  await expect(cond).toHaveClass(/active/);
  await page.keyboard.press('a');
  await expect(cond.getByRole('button', { name: /Accept/ })).toHaveAttribute('aria-pressed', 'true');
  await page.keyboard.press('u');
  await expect(cond.getByRole('button', { name: /Accept/ })).toHaveAttribute('aria-pressed', 'false');
  await expect(move.getByRole('button', { name: /Reject/ })).toHaveAttribute('aria-pressed', 'true');
});

for (const vp of [
  { name: 'tablet', width: 768, height: 1024 },
  { name: 'phone', width: 390, height: 844 },
]) {
  test(`responsive ${vp.width}px: no page overflow, decisions reachable`, async ({ page }) => {
    await page.setViewportSize({ width: vp.width, height: vp.height });
    await uploadAndCompare(page);
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(overflow).toBeLessThanOrEqual(0);
    if (vp.width < 768) {
      await page.getByRole('tab', { name: 'Queue' }).click();
      await page.locator('#queue-search').fill('BENCH-0142');
      await page.locator('#q-BENCH-0142').click();
      await expect(page.getByRole('tab', { name: 'Details' })).toHaveAttribute('aria-selected', 'true');
    } else {
      await page.locator('#queue-search').fill('BENCH-0142');
      await page.locator('#q-BENCH-0142').click();
    }
    await expect(page.getByRole('heading', { level: 2, name: 'BENCH-0142' })).toBeVisible();
    await page.locator('[data-component="BENCH-0142:attr:condition"]').getByRole('button', { name: /Accept/ }).click();
    await expect(page.locator('[data-component="BENCH-0142:attr:condition"]').getByRole('button', { name: /Accept/ })).toHaveAttribute('aria-pressed', 'true');
    await axe(page, `${vp.name}`);
    if (shots) await page.screenshot({ path: join(shots, `studio-${vp.width}.png`) });
    if (vp.width < 768) {
      await page.getByRole('tab', { name: 'Map' }).click();
      await expect(page.getByRole('region', { name: 'Quick decision' })).toBeVisible();
      if (shots) await page.screenshot({ path: join(shots, `studio-${vp.width}-map.png`) });
    }
  });
}

test('without WebGL the review falls back to an accessible table', async ({ page }) => {
  await page.addInitScript(() => {
    const orig = HTMLCanvasElement.prototype.getContext;
    HTMLCanvasElement.prototype.getContext = function (this: HTMLCanvasElement, type: string, ...rest: unknown[]) {
      if (/webgl/i.test(type)) return null;
      return (orig as (...a: unknown[]) => RenderingContext | null).call(this, type, ...rest);
    } as typeof orig;
  });
  await page.setViewportSize({ width: 1440, height: 900 });
  await uploadAndCompare(page);
  await expect(page.getByTestId('no-webgl')).toBeVisible();
  await expect(page.getByTestId('map')).toHaveCount(0);
  await openAsset(page, 'BENCH-0142');
  await expect(page.locator('[data-component="BENCH-0142:move"]')).toContainText('119.6 m');
});

test('API outage during review shows a recoverable message and keeps decisions', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await uploadAndCompare(page);
  await openAsset(page, 'BENCH-0142');
  await page.route('**/v1/**', (r) => r.abort('internetdisconnected'));
  await page.locator('[data-component="BENCH-0142:attr:condition"]').getByRole('button', { name: /Accept/ }).click();
  await expect(page.getByTestId('notice')).toContainText(/unavailable|connection|reach/i);
  await expect(page.locator('[data-component="BENCH-0142:attr:condition"]').getByRole('button', { name: /Accept/ })).toHaveAttribute('aria-pressed', 'false');
  await page.unroute('**/v1/**');
  await page.locator('[data-component="BENCH-0142:attr:condition"]').getByRole('button', { name: /Accept/ }).click();
  await expect(page.locator('[data-component="BENCH-0142:attr:condition"]').getByRole('button', { name: /Accept/ })).toHaveAttribute('aria-pressed', 'true');
  // A reload restores the same live session from this browser.
  await page.reload();
  await openAsset(page, 'BENCH-0142');
  await expect(page.locator('[data-component="BENCH-0142:attr:condition"]').getByRole('button', { name: /Accept/ })).toHaveAttribute('aria-pressed', 'true');
});

test('choosing both files at once uses one workspace', async ({ page }) => {
  await page.goto('/');
  await page.evaluate(() => localStorage.clear());
  await page.goto('/');
  await page.getByTestId('try-files').first().click();
  await expect(page.getByTestId('api-status')).toContainText('Live API ready', { timeout: 20_000 });
  const created: string[] = [];
  page.on('request', (r) => r.method() === 'POST' && r.url().endsWith('/v1/workspaces') && created.push(r.url()));
  await Promise.all([
    page.getByTestId('file-baseline').setInputFiles(join(golden, 'baseline.geojson')),
    page.getByTestId('file-proposed').setInputFiles(join(golden, 'proposed.geojson')),
  ]);
  await expect(page.getByTestId('run-compare')).toBeEnabled();
  expect(created).toHaveLength(1);
});

test('invalid upload explains the problem', async ({ page }) => {
  await page.goto('/');
  await page.evaluate(() => localStorage.clear());
  await page.goto('/');
  await page.getByTestId('try-files').first().click();
  await expect(page.getByTestId('api-status')).toContainText('Live API ready', { timeout: 20_000 });
  await page.getByTestId('file-baseline').setInputFiles(join(golden, '..', 'invalid', 'null_geometry.geojson'));
  await expect(page.getByTestId('summary-baseline')).toContainText(/problem\(s\) block comparison/);
  await page.getByTestId('file-proposed').setInputFiles(join(golden, '..', 'invalid', 'duplicate_id.geojson'));
  await expect(page.getByTestId('summary-proposed')).toContainText('No unique ID field found');
  await expect(page.getByTestId('run-compare')).toBeDisabled();
});

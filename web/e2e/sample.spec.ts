import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';
import { readFileSync } from 'node:fs';

const API = process.env.E2E_API_URL ?? 'http://localhost:8000';

/** The guided sample must work with the API completely unreachable. */
async function blockApi(page: Page): Promise<string[]> {
  const hits: string[] = [];
  await page.route(`${API}/**`, (r) => {
    hits.push(r.request().url());
    return r.abort('connectionrefused');
  });
  return hits;
}

const cond = (page: Page) => page.locator('[data-component="BENCH-0142:attr:condition"]');
const move = (page: Page) => page.locator('[data-component="BENCH-0142:move"]');

test('fresh browser, API down: the guided sample opens on BENCH-0142 and works end to end', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', (e) => errors.push(e.message));
  page.on('console', (m) => m.type() === 'error' && errors.push(m.text()));
  const apiHits = await blockApi(page);
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto('/');

  // Default entry: populated review, sample mode labeled, story asset selected and framed.
  await expect(page.getByTestId('mode')).toHaveText('Guided sample');
  await expect(page.getByRole('heading', { level: 2, name: 'BENCH-0142' })).toBeVisible();
  await expect(page.getByTestId('guide')).toContainText('11th St');
  await expect(page).toHaveURL(/#\/sample\/BENCH-0142\?t=2$/);
  await expect(page.locator('.distance-label')).toContainText('119.6 m');
  await expect(page.locator('.queue-count')).toContainText('50 assets · 54 decisions left');
  await expect(page.getByText('synthetic data, decisions saved in this browser')).toBeVisible();
  const r = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze();
  expect(r.violations.map((v) => `${v.id}: ${v.help}`)).toEqual([]);

  // Decide, then refresh: decisions persist locally.
  await cond(page).getByRole('button', { name: /Accept/ }).click();
  await expect(cond(page).getByRole('button', { name: /Accept/ })).toHaveAttribute('aria-pressed', 'true');
  await move(page).getByRole('button', { name: /Reject/ }).click();
  await expect(move(page).getByRole('button', { name: /Reject/ })).toHaveAttribute('aria-pressed', 'true');
  await page.reload();
  await expect(cond(page).getByRole('button', { name: /Accept/ })).toHaveAttribute('aria-pressed', 'true');
  await expect(move(page).getByRole('button', { name: /Reject/ })).toHaveAttribute('aria-pressed', 'true');
  await expect(page.locator('.queue-count')).toContainText('52 decisions left');

  // Draft export: approved location kept, new condition applied.
  const files: import('@playwright/test').Download[] = [];
  page.on('download', (d) => files.push(d));
  await page.getByTestId('export').click();
  await page.getByTestId('export-draft').click();
  await expect.poll(() => files.length).toBe(2);
  const geo = files.find((f) => !f.suggestedFilename().includes('report'))!;
  const rep = files.find((f) => f.suggestedFilename().includes('report'))!;
  expect(geo.suggestedFilename()).toMatch(/^geodiff-sample-draft-r\d+\.geojson$/);
  const out = JSON.parse(readFileSync((await geo.path())!, 'utf8')) as { features: { properties: Record<string, unknown>; geometry: { coordinates: number[] } }[] };
  const bench = out.features.find((f) => f.properties.asset_id === 'BENCH-0142')!;
  expect(bench.properties.condition).toBe('damaged');
  expect(bench.geometry.coordinates).toEqual([-96.7969, 32.78434]);
  expect(out.features).toHaveLength(400);
  const report = JSON.parse(readFileSync((await rep.path())!, 'utf8')) as { mode: string; complete: boolean; attribution: { note: string }; counts: { unresolved_components: number } };
  expect(report.mode).toBe('draft');
  expect(report.complete).toBe(false);
  expect(report.counts.unresolved_components).toBe(52);
  expect(report.attribution.note).toContain('SYNTHETIC');

  // Reset clears local decisions.
  page.once('dialog', (d) => void d.accept());
  await page.getByTestId('reset').click();
  await expect(cond(page).getByRole('button', { name: /Accept/ })).toHaveAttribute('aria-pressed', 'false');
  await expect(page.locator('.queue-count')).toContainText('54 decisions left');

  // Tolerance presets are exact precomputed runs.
  await page.getByTestId('preset').selectOption('1');
  await expect(page).toHaveURL(/\?t=1$/);
  await expect(page.locator('.queue-count')).toContainText('91 decisions left');
  await page.getByTestId('preset').selectOption('5');
  await expect(page.locator('.queue-count')).toContainText('51 decisions left');

  expect(apiHits, 'the sample never calls the live API').toEqual([]);
  expect(errors).toEqual([]);
});

test('a direct link opens a specific sample asset, survives refresh, and explains unknown IDs', async ({ page }) => {
  await blockApi(page);
  const bundle = JSON.parse(readFileSync(new URL('../../fixtures/demo/sample.json', import.meta.url), 'utf8')) as {
    presets: { tolerance_m: number; comparison: { assets: { asset_id: string; status: string }[] } }[];
  };
  const id = bundle.presets.find((p) => p.tolerance_m === 2)!.comparison.assets.find((a) => a.status === 'added')!.asset_id;
  await page.goto(`/#/sample/${encodeURIComponent(id)}?t=2`);
  await expect(page.getByRole('heading', { level: 2, name: id })).toBeVisible();
  await page.reload();
  await expect(page.getByRole('heading', { level: 2, name: id })).toBeVisible();
  await expect(page).toHaveURL(new RegExp(`#/sample/${id}\\?t=2$`));
  await page.goto('/#/sample/NOT-A-REAL-ID?t=2');
  await page.reload();
  await expect(page.getByTestId('notice')).toContainText('NOT-A-REAL-ID is not part of this review');
});

test('phone width: sample opens on the map with the quick decision bar', async ({ page }) => {
  await blockApi(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/');
  await expect(page.getByRole('tab', { name: 'Map' })).toHaveAttribute('aria-selected', 'true');
  const bar = page.getByRole('region', { name: 'Quick decision' });
  await expect(bar).toContainText('BENCH-0142');
  await expect(bar).toContainText('119.6 m');
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(0);
  await expect(page.locator('canvas.maplibregl-canvas')).toBeVisible();
  await expect(page.locator('.distance-label')).toContainText('119.6 m');
  if (process.env.SCREENSHOT_DIR) await page.screenshot({ path: `${process.env.SCREENSHOT_DIR}/sample-390.png` });
});

test('live upload with the API down says so and offers the sample, without rerouting', async ({ page }) => {
  await blockApi(page);
  await page.goto('/');
  await page.getByTestId('try-files').click();
  await expect(page.getByTestId('api-status')).toContainText('Live API unavailable', { timeout: 20_000 });
  await expect(page.getByText('Public layers are listed by the live API, which is unavailable right now.').first()).toBeAttached();
});

test('public layer snapshot through the live API (real network)', async ({ page }) => {
  test.skip(process.env.E2E_NETWORK !== '1', 'set E2E_NETWORK=1 to reach the real ArcGIS source');
  test.setTimeout(120_000);
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto('/');
  await page.getByTestId('import-layer').click();
  await expect(page.getByTestId('api-status')).toContainText('Live API ready', { timeout: 30_000 });
  for (const role of ['baseline', 'proposed'] as const) {
    await page.getByTestId(`import-${role}`).click();
    await expect(page.getByTestId(`provenance-${role}`)).toContainText('Yosemite National Park', { timeout: 60_000 });
  }
  await expect(page.getByTestId('summary-baseline')).toContainText('features');
  await expect(page.getByTestId('id-baseline')).toHaveValue('GlobalID');
  await page.getByTestId('run-compare').click();
  await expect(page.getByTestId('mode')).toHaveText('Live workspace', { timeout: 60_000 });
  await expect(page.locator('.queue-count')).toBeAttached();
  await expect(page.locator('.attrib')).toContainText('National Park Service');
  await page.waitForTimeout(1500); // let approved-position context draw for the screenshot
  if (process.env.SCREENSHOT_DIR) await page.screenshot({ path: `${process.env.SCREENSHOT_DIR}/live-public-layer.png` });
});

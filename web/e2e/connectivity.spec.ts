import { expect, test } from '@playwright/test';

test('browser reaches FastAPI, which reaches PostGIS', async ({ page }) => {
  await page.goto('/');
  const status = page.getByTestId('readiness');
  await expect(status.locator('[data-state="ready"]')).toBeVisible({ timeout: 15_000 });
  await expect(status).toContainText('PostGIS reachable');
  await expect(status).toContainText(/PostGIS 3\.\d+/);
  await expect(status).toContainText('0001_postgis');
});

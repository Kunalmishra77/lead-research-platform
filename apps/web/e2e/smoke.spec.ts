import { expect, test } from '@playwright/test';

test('login page renders the sign-in form', async ({ page }) => {
  await page.goto('/login');
  await expect(page.getByRole('heading', { name: 'Sign in' })).toBeVisible();
  await expect(page.getByLabel('Work email')).toBeVisible();
  await expect(page.getByLabel('Password')).toBeVisible();
  await expect(page.getByRole('link', { name: 'Create an account' })).toHaveAttribute(
    'href',
    '/signup',
  );
});

test('protected pages redirect to login and keep the destination', async ({ page }) => {
  await page.goto('/dashboard');
  await expect(page).toHaveURL(/\/login\?next=%2Fdashboard$/);
});

test('signup validates input on the server without calling Supabase', async ({ page }) => {
  await page.goto('/signup');
  await page.getByLabel('Work email').fill('not-an-email');
  await page.getByLabel('Password').fill('short');
  await page.getByRole('button', { name: 'Create account' }).click();
  await expect(page.getByText('Enter a valid email address')).toBeVisible();
  await expect(page.getByText('Use at least 10 characters')).toBeVisible();
});

test('the API proxy refuses cross-origin state-changing requests', async ({ request }) => {
  const res = await request.post('/api/app/dev/ping-job', {
    headers: { origin: 'https://evil.example', 'content-type': 'application/json' },
    data: {},
  });
  expect(res.status()).toBe(403);
});

test('security headers are set', async ({ request }) => {
  const res = await request.get('/login');
  expect(res.headers()['x-frame-options']).toBe('DENY');
  expect(res.headers()['x-content-type-options']).toBe('nosniff');
  expect(res.headers()['x-powered-by']).toBeUndefined();
});

test('the API proxy refuses paths that would escape /app/', async ({ request }) => {
  for (const path of [
    '/api/app/%2E%2E/health',
    '/api/app/a%2F..%2F..%2Fhealth',
    '/api/app/a/%2e%2e/%2e%2e/x',
  ]) {
    const res = await request.get(path);
    // 400 = rejected by the proxy; 404 = normalised by the client to a path outside the proxy.
    // Anything else (2xx, 5xx) means the request reached the upstream call.
    expect([400, 404], path).toContain(res.status());
  }
});

test('the admin area requires sign-in', async ({ page }) => {
  await page.goto('/admin/users');
  await expect(page).toHaveURL(/\/login\?next=%2Fadmin%2Fusers$/);
});

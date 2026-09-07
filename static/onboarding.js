const form = document.querySelector('#profile');
const message = document.querySelector('#message');
form.addEventListener('submit', async (event) => {
  event.preventDefault();
  const token = sessionStorage.getItem('leadlens_access_token');
  if (!token) { message.textContent = 'Sign in is required to configure your organization.'; return; }
  const payload = Object.fromEntries(new FormData(form));
  const response = await fetch('/api/onboarding/organization', { method: 'PUT', headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` }, body: JSON.stringify(payload) });
  message.textContent = response.ok ? 'Organization profile saved. Continue with privacy approval.' : `Could not save: ${(await response.json()).detail || 'request failed'}`;
});
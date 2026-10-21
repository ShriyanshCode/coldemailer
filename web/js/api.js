// Thin fetch wrapper around the coldemailer API.
export class ApiError extends Error {
  constructor(message, status) { super(message); this.status = status; }
}

function qs(params = {}) {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== '') q.set(k, v);
  const s = q.toString();
  return s ? `?${s}` : '';
}

async function handle(r) {
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new ApiError(data.detail || r.statusText, r.status);
  return data;
}

export const get = (path, params) => fetch(`/api/${path}${qs(params)}`).then(handle);

export const post = (path, body = {}) =>
  fetch(`/api/${path}`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) })
    .then(handle);

// Poll a background job until it finishes; onProgress(progress, total) on each tick.
export async function pollJob(id, onProgress) {
  for (;;) {
    const j = await get(`jobs/${id}`);
    onProgress?.(j.progress, j.total);
    if (j.status === 'done') return j;
    if (j.status === 'error') throw new ApiError(j.error || 'job failed', 500);
    await new Promise(r => setTimeout(r, 400));
  }
}

import { API_BASE } from './apiBase';
import { useAuthStore } from './store';

/**
 * POST /databases/:id/ask/stream and dispatch each server-sent event to
 * `onEvent(name, data)`. Resolves when the stream ends. Retries once after
 * refreshing the access token on a 401.
 */
export async function streamAsk(databaseId, body, onEvent, { signal, _retried = false } = {}) {
  const token = useAuthStore.getState().accessToken;
  const response = await fetch(`${API_BASE}/databases/${databaseId}/ask/stream`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify(body),
    signal,
  });

  if (response.status === 401 && !_retried) {
    const refreshed = await useAuthStore.getState().refreshAccessToken();
    if (refreshed) return streamAsk(databaseId, body, onEvent, { signal, _retried: true });
    await useAuthStore.getState().logout();
    window.location.href = '/login';
    return;
  }

  if (!response.ok) {
    let detail = `Request failed (${response.status})`;
    try {
      const json = await response.json();
      if (typeof json.detail === 'string') detail = json.detail;
    } catch {
      // keep fallback
    }
    throw new Error(detail);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  // eslint-disable-next-line no-constant-condition
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    let sep;
    while ((sep = buffer.indexOf('\n\n')) !== -1) {
      const frame = buffer.slice(0, sep);
      buffer = buffer.slice(sep + 2);

      let event = 'message';
      let data = '';
      for (const line of frame.split('\n')) {
        if (line.startsWith('event: ')) event = line.slice(7);
        else if (line.startsWith('data: ')) data += line.slice(6);
      }
      if (data) {
        try {
          onEvent(event, JSON.parse(data));
        } catch {
          onEvent(event, data);
        }
      }
    }
  }
}

import { streamAsk } from './streamAsk';
import { useAuthStore } from './store';

function streamOf(chunks) {
  const encoder = new TextEncoder();
  const queue = chunks.map((c) => encoder.encode(c));
  return {
    getReader: () => ({
      read: async () => (queue.length ? { value: queue.shift(), done: false } : { value: undefined, done: true }),
    }),
  };
}

beforeEach(() => {
  useAuthStore.setState({ accessToken: 'token-1' });
});

afterEach(() => {
  delete global.fetch;
});

test('parses events split across chunks and sends the token', async () => {
  global.fetch = jest.fn().mockResolvedValue({
    ok: true,
    status: 200,
    body: streamOf([
      'event: token\ndata: {"text": "Hel',
      'lo"}\n\nevent: tool_end\ndata: {"tool_name": "run_sql"}\n\n',
      'event: done\ndata: {"answer": "Hello", "conversation_id": "c1"}\n\n',
    ]),
  });
  const events = [];
  await streamAsk('db1', { query: 'hi' }, (name, data) => events.push([name, data]));

  expect(events).toEqual([
    ['token', { text: 'Hello' }],
    ['tool_end', { tool_name: 'run_sql' }],
    ['done', { answer: 'Hello', conversation_id: 'c1' }],
  ]);
  const [url, init] = global.fetch.mock.calls[0];
  expect(url).toMatch(/\/databases\/db1\/ask\/stream$/);
  expect(init.headers.Authorization).toBe('Bearer token-1');
  expect(JSON.parse(init.body)).toEqual({ query: 'hi' });
});

test('surfaces the server error detail', async () => {
  global.fetch = jest.fn().mockResolvedValue({
    ok: false,
    status: 429,
    json: async () => ({ detail: 'Daily limit reached' }),
  });
  await expect(streamAsk('db1', { query: 'hi' }, () => {})).rejects.toThrow('Daily limit reached');
});

test('refreshes the token once on 401 and retries', async () => {
  const refresh = jest.fn().mockImplementation(async () => {
    useAuthStore.setState({ accessToken: 'token-2' });
    return true;
  });
  useAuthStore.setState({ refreshAccessToken: refresh });
  global.fetch = jest
    .fn()
    .mockResolvedValueOnce({ ok: false, status: 401 })
    .mockResolvedValueOnce({ ok: true, status: 200, body: streamOf(['event: done\ndata: {}\n\n']) });

  const events = [];
  await streamAsk('db1', { query: 'hi' }, (name) => events.push(name));
  expect(refresh).toHaveBeenCalledTimes(1);
  expect(global.fetch.mock.calls[1][1].headers.Authorization).toBe('Bearer token-2');
  expect(events).toEqual(['done']);
});

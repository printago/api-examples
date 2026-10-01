/**
 * A minimal Printago REST client.
 *
 * Every example ships its own copy of this file so each folder stands alone.
 * It covers what the examples need and nothing more: API-key auth, query
 * strings, JSON bodies, readable errors, and backing off on HTTP 429.
 *
 * API reference: https://developers.printago.io
 */

// Load .env from the working directory if there is one (Node 20.12+).
try {
  process.loadEnvFile();
} catch {
  // No .env file: fall back to the real environment.
}

export interface PrintagoOptions {
  /** Your API key (Settings → API Keys). */
  apiKey: string;
  /** Defaults to https://api.printago.io. */
  baseUrl?: string;
  /** How many times to retry a request that was rate limited. Default 5. */
  maxRetries?: number;
}

type QueryValue = string | number | boolean | undefined | null;

export interface RequestOptions {
  query?: Record<string, QueryValue>;
  body?: unknown;
}

/** Thrown for any non-2xx response. `body` holds the parsed error payload. */
export class PrintagoError extends Error {
  constructor(
    readonly status: number,
    readonly method: string,
    readonly path: string,
    readonly body: unknown,
  ) {
    super(`${method} ${path} failed with ${status}: ${describe(body)}`);
    this.name = 'PrintagoError';
  }
}

export class Printago {
  readonly baseUrl: string;
  private readonly apiKey: string;
  private readonly maxRetries: number;

  constructor({ apiKey, baseUrl = 'https://api.printago.io', maxRetries = 5 }: PrintagoOptions) {
    if (!apiKey) throw new Error('A Printago API key is required');
    this.apiKey = apiKey;
    this.baseUrl = baseUrl.replace(/\/+$/, '');
    this.maxRetries = maxRetries;
  }

  get<T>(path: string, query?: RequestOptions['query']): Promise<T> {
    return this.request<T>('GET', path, { query });
  }

  post<T>(path: string, body?: unknown, query?: RequestOptions['query']): Promise<T> {
    return this.request<T>('POST', path, { body, query });
  }

  patch<T>(path: string, body?: unknown): Promise<T> {
    return this.request<T>('PATCH', path, { body });
  }

  async request<T>(method: string, path: string, { query, body }: RequestOptions = {}): Promise<T> {
    const url = new URL(this.baseUrl + path);
    for (const [key, value] of Object.entries(query ?? {})) {
      if (value !== undefined && value !== null) url.searchParams.set(key, String(value));
    }

    for (let attempt = 0; ; attempt++) {
      const response = await fetch(url, {
        method,
        headers: {
          authorization: `ApiKey ${this.apiKey}`,
          ...(body === undefined ? {} : { 'content-type': 'application/json' }),
        },
        body: body === undefined ? undefined : JSON.stringify(body),
      });

      // The API key limit is shared by every client on the store, and a 429
      // does not say how long to wait: back off exponentially (2s, 4s, 8s…).
      if (response.status === 429 && attempt < this.maxRetries) {
        await sleep(2000 * 2 ** attempt);
        continue;
      }

      const text = await response.text();
      const parsed = text ? safeJson(text) : null;
      if (!response.ok) throw new PrintagoError(response.status, method, path, parsed);
      return parsed as T;
    }
  }
}

/** Build a client from PRINTAGO_API_KEY / PRINTAGO_BASE_URL. */
export function printagoFromEnv(): Printago {
  return new Printago({
    apiKey: requireEnv('PRINTAGO_API_KEY'),
    baseUrl: process.env.PRINTAGO_BASE_URL || undefined,
  });
}

export function requireEnv(name: string): string {
  const value = process.env[name];
  if (!value) {
    console.error(`Missing ${name}. Copy .env.example to .env and fill it in.`);
    process.exit(1);
  }
  return value;
}

export const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

function safeJson(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

function describe(body: unknown): string {
  if (body && typeof body === 'object' && 'message' in body) {
    const message = (body as { message: unknown }).message;
    return Array.isArray(message) ? message.join('; ') : String(message);
  }
  return typeof body === 'string' ? body.slice(0, 300) : JSON.stringify(body)?.slice(0, 300) ?? '';
}

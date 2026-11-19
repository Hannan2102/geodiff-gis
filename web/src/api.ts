import { API_URL } from './adapters/live';

export { API_URL };

export interface Readiness {
  status: string;
  database: boolean;
  postgres?: string;
  postgis?: string | null;
  migrations?: { applied: string[]; pending: string[] };
  message?: string;
}

export type ReadinessResult =
  | { kind: 'ok'; data: Readiness }
  | { kind: 'degraded'; data: Readiness }
  | { kind: 'unreachable'; error: string };

export async function fetchReadiness(signal?: AbortSignal, timeoutMs = 8000): Promise<ReadinessResult> {
  const timeout = AbortSignal.timeout(timeoutMs);
  const combined = signal ? AbortSignal.any([signal, timeout]) : timeout;
  try {
    const res = await fetch(`${API_URL}/ready`, { signal: combined });
    const data = (await res.json()) as Readiness;
    return res.ok ? { kind: 'ok', data } : { kind: 'degraded', data };
  } catch (err) {
    const name = err instanceof DOMException ? err.name : 'NetworkError';
    return {
      kind: 'unreachable',
      error: name === 'TimeoutError' ? `The API did not answer within ${Math.round(timeoutMs / 1000)} seconds.` : 'The API could not be reached.',
    };
  }
}

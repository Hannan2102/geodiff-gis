import { useEffect, useState } from 'react';
import { API_URL, fetchReadiness, type ReadinessResult } from './api';

export default function App() {
  const [attempt, setAttempt] = useState(0);
  const [result, setResult] = useState<{ attempt: number; value: ReadinessResult } | null>(null);

  useEffect(() => {
    const ctl = new AbortController();
    void fetchReadiness(ctl.signal).then((value) => {
      if (!ctl.signal.aborted) setResult({ attempt, value });
    });
    return () => ctl.abort();
  }, [attempt]);

  const current = result && result.attempt === attempt ? result.value : null;

  return (
    <main className="boot">
      <h1>
        GeoDiff <span className="mono">foundation</span>
      </h1>
      <p>Point-inventory change review. This screen checks the live stack.</p>
      <section aria-live="polite" className="status" data-testid="readiness">
        {current === null && <p>Checking API at <code>{API_URL}</code>…</p>}
        {current?.kind === 'ok' && (
          <p data-state="ready">
            <strong>PostGIS reachable.</strong> {current.data.postgres} · PostGIS {current.data.postgis} · migrations{' '}
            {current.data.migrations?.applied.join(', ')}
          </p>
        )}
        {current?.kind === 'degraded' && (
          <p data-state="degraded">
            <strong>API up, database not ready:</strong> {current.data.message ?? current.data.status}
          </p>
        )}
        {current?.kind === 'unreachable' && (
          <p data-state="unreachable">
            <strong>API unavailable.</strong> {current.error}
          </p>
        )}
        <button type="button" onClick={() => setAttempt((n) => n + 1)}>
          Check again
        </button>
      </section>
    </main>
  );
}

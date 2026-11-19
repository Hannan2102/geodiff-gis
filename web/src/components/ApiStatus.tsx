import { useEffect, useState } from 'react';
import { fetchReadiness, type ReadinessResult } from '../api';

/** Shows whether the live API and PostGIS are reachable; free hosts may need time to wake. */
export default function ApiStatus() {
  const [attempt, setAttempt] = useState(0);
  const [state, setState] = useState<{ attempt: number; r: ReadinessResult } | null>(null);
  useEffect(() => {
    const ctl = new AbortController();
    void fetchReadiness(ctl.signal, 60_000).then((r) => !ctl.signal.aborted && setState({ attempt, r }));
    return () => ctl.abort();
  }, [attempt]);
  const r = state?.attempt === attempt ? state.r : null;
  return (
    <p className={`api-status ${r?.kind ?? 'checking'}`} role="status" data-testid="api-status">
      {r === null && <>Checking the live API… a free server can take up to a minute to wake up.</>}
      {r?.kind === 'ok' && <>Live API ready · PostGIS {r.data.postgis}</>}
      {r?.kind === 'degraded' && <>Live API is up but its database is not ready yet ({r.data.status}).</>}
      {r?.kind === 'unreachable' && <>Live API unavailable: {r.error}</>}
      {r && r.kind !== 'ok' && (
        <button type="button" className="btn ghost" onClick={() => setAttempt((n) => n + 1)}>
          Retry
        </button>
      )}
    </p>
  );
}

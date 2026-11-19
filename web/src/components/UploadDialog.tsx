import { useEffect, useRef, useState } from 'react';
import { ApiError, liveSession, workspaceApi } from '../adapters/live';
import type { ComparisonInfo, DatasetSummary, FieldSuggestion } from '../types';
import ApiStatus from './ApiStatus';
import Dialog from './Dialog';

type Role = 'baseline' | 'proposed';
const ROLE_LABEL: Record<Role, string> = { baseline: 'Approved inventory (baseline)', proposed: 'Field inventory (proposed)' };
const base = import.meta.env.BASE_URL;

interface Slot {
  file?: File;
  status: 'idle' | 'uploading' | 'done' | 'error';
  data?: DatasetSummary;
  error?: ApiError;
  idField?: string;
}

function DatasetCard({ role, slot, onFile, onIdField }: { role: Role; slot: Slot; onFile: (f: File) => void; onIdField: (f: string) => void }) {
  const d = slot.data;
  const v = d?.summary.validation;
  const [drag, setDrag] = useState(false);
  return (
    <section className="ds-card" aria-label={ROLE_LABEL[role]}>
      <h3>{ROLE_LABEL[role]}</h3>
      <label
        className={`drop ${drag ? 'drag' : ''}`}
        onDragOver={(e) => {
          e.preventDefault();
          setDrag(true);
        }}
        onDragLeave={() => setDrag(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDrag(false);
          const f = e.dataTransfer.files[0];
          if (f) onFile(f);
        }}
      >
        <input type="file" accept=".geojson,.json,application/geo+json,application/json" onChange={(e) => e.target.files?.[0] && onFile(e.target.files[0])} data-testid={`file-${role}`} />
        <span>{slot.file ? slot.file.name : 'Choose or drop a GeoJSON file'}</span>
      </label>
      {slot.status === 'uploading' && <p role="status">Uploading and validating {slot.file?.name}…</p>}
      {slot.status === 'error' && slot.error && (
        <p className="field-error" role="alert">
          {slot.error.message} {slot.error.guidance}
        </p>
      )}
      {d && (
        <div className="ds-summary" data-testid={`summary-${role}`}>
          <p>
            <strong className="mono">{d.name}</strong> · {d.feature_count.toLocaleString()} features · {(d.size_bytes / 1024).toFixed(0)} KB
            {d.duplicate ? ' · already uploaded (reused)' : ''}
          </p>
          <p className="mono hint">sha256 {d.sha256.slice(0, 16)}…</p>
          {d.valid ? (
            <>
              <label>
                ID field{' '}
                <select value={slot.idField ?? ''} onChange={(e) => onIdField(e.target.value)} data-testid={`id-${role}`}>
                  {d.summary.candidate_id_fields.length === 0 && <option value="">No unique ID field found</option>}
                  {d.summary.candidate_id_fields.map((f) => (
                    <option key={f} value={f}>{f}</option>
                  ))}
                </select>
              </label>
              <details>
                <summary>Field coverage ({d.summary.fields.length} fields)</summary>
                <ul className="coverage">
                  {d.summary.fields.map((f) => (
                    <li key={f.name}>
                      <span className="mono">{f.name}</span> {(f.coverage * 100).toFixed(0)}% · {f.types.join('/')}
                    </li>
                  ))}
                </ul>
              </details>
              {v && v.warning_count > 0 && <p className="hint">{v.warning_count} warning(s): {v.sample.filter((s) => s.severity === 'warning').map((s) => s.message).join(' ')}</p>}
            </>
          ) : (
            <div className="invalid" role="alert">
              <p>
                <strong>{v?.error_count} problem(s) block comparison.</strong>
              </p>
              <ul>
                {Object.entries(v?.counts ?? {}).map(([code, n]) => {
                  const s = v?.sample.find((x) => x.code === code);
                  return (
                    <li key={code}>
                      <span className="mono">{code}</span> ×{n}: {s?.message} <em>{s?.guidance}</em>
                    </li>
                  );
                })}
              </ul>
              {v?.sampled && <p className="hint">Only the first 20 issues of each kind are listed.</p>}
            </div>
          )}
        </div>
      )}
    </section>
  );
}

export default function UploadDialog({
  onClose,
  onCompared,
  current,
}: {
  onClose: () => void;
  onCompared: (token: string, info: ComparisonInfo) => void;
  current: ComparisonInfo | null;
}) {
  const [slots, setSlots] = useState<Record<Role, Slot>>({ baseline: { status: 'idle' }, proposed: { status: 'idle' } });
  const [fields, setFields] = useState<FieldSuggestion | null>(null);
  const [chosen, setChosen] = useState<string[]>([]);
  const [tolerance, setTolerance] = useState('2');
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);

  // Both uploads may start at once; share one workspace creation so they land in the same workspace.
  const creating = useRef<Promise<string> | null>(null);
  function token(): Promise<string> {
    const t = liveSession.token();
    if (t) return Promise.resolve(t);
    creating.current ??= workspaceApi
      .create()
      .then((nt) => {
        liveSession.save(nt);
        return nt;
      })
      .finally(() => {
        creating.current = null;
      });
    return creating.current;
  }

  async function onFile(role: Role, file: File) {
    setSlots((s) => ({ ...s, [role]: { file, status: 'uploading' } }));
    setFields(null);
    try {
      let t = await token();
      let data: DatasetSummary;
      try {
        data = await workspaceApi.upload(t, file, file.name);
      } catch (e) {
        if (!(e instanceof ApiError) || e.code !== 'workspace_expired') throw e;
        liveSession.save(null, null);
        t = await token();
        data = await workspaceApi.upload(t, file, file.name);
      }
      const ids = data.summary.candidate_id_fields;
      const idField = ids.includes('asset_id') ? 'asset_id' : ids[0];
      setSlots((s) => ({ ...s, [role]: { file, status: 'done', data, idField } }));
    } catch (e) {
      setSlots((s) => ({ ...s, [role]: { file, status: 'error', error: e instanceof ApiError ? e : new ApiError(0, 'error', String(e)) } }));
    }
  }

  const b = slots.baseline;
  const p = slots.proposed;
  const ready = b.data?.valid && p.data?.valid && b.idField && p.idField;

  useEffect(() => {
    if (!ready || !b.data || !p.data || !b.idField || !p.idField) return;
    let live = true;
    void (async () => {
      try {
        const s = await workspaceApi.suggestFields(await token(), b.data!.dataset_id, p.data!.dataset_id, b.idField!, p.idField!);
        if (!live) return;
        setFields(s);
        setChosen(s.suggested);
      } catch (e) {
        if (live) setError(e instanceof ApiError ? e : null);
      }
    })();
    return () => {
      live = false;
    };
  }, [ready, b.data, p.data, b.idField, p.idField]);

  const partial = current && current.progress.resolved_components > 0 && !current.progress.complete;
  const tol = Number(tolerance);
  const tolOk = tolerance.trim() !== '' && Number.isFinite(tol) && tol >= 0;

  async function compare() {
    if (!b.data || !p.data || !b.idField || !p.idField || !tolOk) return;
    if (partial && !window.confirm(`The current run has ${current.progress.resolved_components} of ${current.progress.components} decisions. A new run starts with fresh decisions; the current run is kept in this workspace. Continue?`)) return;
    setRunning(true);
    setError(null);
    try {
      const t = await token();
      const info = await workspaceApi.compare(t, {
        baseline_dataset_id: b.data.dataset_id,
        proposed_dataset_id: p.data.dataset_id,
        baseline_id_field: b.idField,
        proposed_id_field: p.idField,
        compared_fields: chosen,
        tolerance_m: tol,
        parent_comparison_id: current?.comparison_id ?? null,
      });
      liveSession.save(t, info.comparison_id);
      onCompared(t, info);
    } catch (e) {
      setError(e instanceof ApiError ? e : new ApiError(0, 'error', String(e)));
    } finally {
      setRunning(false);
    }
  }

  return (
    <Dialog title="Try your own files" onClose={onClose} wide>
      <ApiStatus />
      <p>
        Upload two GeoJSON point inventories in WGS84 (EPSG:4326). They are stored in a private, anonymous live workspace that expires 72 hours after
        last use. GeoDiff never changes your files. Limits: 5 MB and 10,000 features per file.
      </p>
      <p className="hint">
        Need files? Download the{' '}
        <a href={`${base}data/samples/baseline.geojson`} download>sample approved inventory</a> and{' '}
        <a href={`${base}data/samples/proposed.geojson`} download>sample field inventory</a>. Other projections: in QGIS, use Export → Save Features As…,
        Format GeoJSON, CRS EPSG:4326. Coordinate ranges alone cannot prove a file is WGS84.
      </p>
      <div className="ds-grid">
        {(['baseline', 'proposed'] as Role[]).map((r) => (
          <DatasetCard key={r} role={r} slot={slots[r]} onFile={(f) => void onFile(r, f)} onIdField={(f) => setSlots((s) => ({ ...s, [r]: { ...s[r], idField: f } }))} />
        ))}
      </div>
      {fields && (
        <fieldset className="fields">
          <legend>Fields to compare</legend>
          <ul>
            {fields.fields.map((f) => (
              <li key={f.name}>
                <label className={f.comparable ? '' : 'disabled'}>
                  <input
                    type="checkbox"
                    disabled={!f.comparable}
                    checked={chosen.includes(f.name)}
                    onChange={(e) => setChosen((c) => (e.target.checked ? [...c, f.name].sort() : c.filter((x) => x !== f.name)))}
                  />{' '}
                  <span className="mono">{f.name}</span> {f.note && <em className="hint">({f.note})</em>}
                </label>
              </li>
            ))}
          </ul>
          <p className="hint">Unselected and nested properties are kept from the approved inventory in the export.</p>
        </fieldset>
      )}
      <div className="run-row">
        <label>
          Move tolerance (meters){' '}
          <input type="number" min="0" step="0.1" value={tolerance} onChange={(e) => setTolerance(e.target.value)} aria-invalid={!tolOk} data-testid="tolerance" />
        </label>
        <span className="hint">A position change counts only when it is strictly greater than this geodesic distance.</span>
      </div>
      {partial && <p className="hint">Starting a new run keeps the current run ({current.progress.resolved_components}/{current.progress.components} decided) with its decisions.</p>}
      {error && (
        <p className="field-error" role="alert">
          {error.message} {error.guidance}
        </p>
      )}
      <div className="dialog-actions">
        <button type="button" className="btn" onClick={onClose}>Cancel</button>
        <button type="button" className="btn primary" disabled={!ready || !fields || !tolOk || running} onClick={() => void compare()} data-testid="run-compare">
          {running ? 'Comparing…' : 'Compare inventories'}
        </button>
      </div>
    </Dialog>
  );
}

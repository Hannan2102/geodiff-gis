import { useEffect, useRef, useState } from 'react';
import { ApiError, liveSession, workspaceApi } from '../adapters/live';
import type { ComparisonInfo, DatasetSummary, FieldSuggestion, PublicSource } from '../types';
import ApiStatus from './ApiStatus';
import Dialog from './Dialog';

type Role = 'baseline' | 'proposed';
const ROLE_LABEL: Record<Role, string> = { baseline: 'Approved inventory (baseline)', proposed: 'Field inventory (proposed)' };
const base = import.meta.env.BASE_URL;

interface Slot {
  file?: File;
  label?: string;
  status: 'idle' | 'uploading' | 'done' | 'error';
  data?: DatasetSummary;
  error?: ApiError;
  idField?: string;
}

function SourcePicker({ role, sources, onImport, busy }: { role: Role; sources: PublicSource[] | 'loading' | 'unavailable'; onImport: (s: PublicSource) => void; busy: boolean }) {
  const [pick, setPick] = useState('');
  if (sources === 'loading') return <p className="hint">Loading the curated public layers…</p>;
  if (sources === 'unavailable') return <p className="hint">Public layers are listed by the live API, which is unavailable right now.</p>;
  if (!sources.length) return <p className="hint">No public layers are configured on this server.</p>;
  const chosen = sources.find((s) => s.id === pick) ?? sources[0]!;
  return (
    <div className="source-pick">
      <label>
        Public layer{' '}
        <select value={chosen.id} onChange={(e) => setPick(e.target.value)} data-testid={`source-${role}`}>
          {sources.map((s) => (
            <option key={s.id} value={s.id}>
              {s.title}
            </option>
          ))}
        </select>
      </label>
      <p className="hint">
        {chosen.owner}. {chosen.reuse} Verified {chosen.verified_on}.{' '}
        <a href={chosen.item_url} target="_blank" rel="noreferrer">
          Source item ↗
        </a>
      </p>
      <button type="button" className="btn" disabled={busy} onClick={() => onImport(chosen)} data-testid={`import-${role}`}>
        Take a snapshot now
      </button>
    </div>
  );
}

function DatasetCard({
  role,
  slot,
  onFile,
  onIdField,
  sources,
  onImport,
  publicFirst,
}: {
  role: Role;
  slot: Slot;
  onFile: (f: File) => void;
  onIdField: (f: string) => void;
  sources: PublicSource[] | 'loading' | 'unavailable';
  onImport: (s: PublicSource) => void;
  publicFirst: boolean;
}) {
  const d = slot.data;
  const v = d?.summary.validation;
  const src = d?.source;
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
      <details className="public-layer" open={publicFirst}>
        <summary>Or snapshot a public ArcGIS layer</summary>
        <SourcePicker role={role} sources={sources} onImport={onImport} busy={slot.status === 'uploading'} />
      </details>
      {slot.status === 'uploading' && <p role="status">Reading and validating {slot.label}…</p>}
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
          {src && (
            <p className="hint" data-testid={`provenance-${role}`}>
              Snapshot of <a href={src.item_url} target="_blank" rel="noreferrer">{src.title}</a> taken {new Date(src.retrieved_at).toLocaleString()} ·
              {src.service_last_edit ? ` source last edited ${new Date(src.service_last_edit).toLocaleString()} ·` : ''} © {src.attribution}. {src.id_note}
            </p>
          )}
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
  startWithPublic = false,
}: {
  onClose: () => void;
  onCompared: (token: string, info: ComparisonInfo) => void;
  current: ComparisonInfo | null;
  startWithPublic?: boolean;
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

  async function load(role: Role, label: string, file: File | undefined, run: (token: string) => Promise<DatasetSummary>) {
    setSlots((s) => ({ ...s, [role]: { file, label, status: 'uploading' } }));
    setFields(null);
    try {
      let t = await token();
      let data: DatasetSummary;
      try {
        data = await run(t);
      } catch (e) {
        if (!(e instanceof ApiError) || e.code !== 'workspace_expired') throw e;
        liveSession.save(null, null);
        t = await token();
        data = await run(t);
      }
      const ids = data.summary.candidate_id_fields;
      const preferred = data.source?.id_field;
      const idField = preferred && ids.includes(preferred) ? preferred : ids.includes('asset_id') ? 'asset_id' : ids[0];
      setSlots((s) => ({ ...s, [role]: { file, label, status: 'done', data, idField } }));
    } catch (e) {
      setSlots((s) => ({ ...s, [role]: { file, label, status: 'error', error: e instanceof ApiError ? e : new ApiError(0, 'error', String(e)) } }));
    }
  }
  const onFile = (role: Role, file: File) => load(role, file.name, file, (t) => workspaceApi.upload(t, file, file.name));
  const onImport = (role: Role, src: PublicSource) => load(role, `a snapshot of ${src.title}`, undefined, (t) => workspaceApi.importArcgis(t, src.id));

  // Curated public layers come from the API (server-side allowlist).
  const [sources, setSources] = useState<PublicSource[] | 'loading' | 'unavailable'>('loading');
  useEffect(() => {
    let live = true;
    workspaceApi
      .sources()
      .then((s) => live && setSources(s))
      .catch(() => live && setSources('unavailable'));
    return () => {
      live = false;
    };
  }, []);

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
    <Dialog title={startWithPublic ? 'Import a public layer' : 'Try your own files'} onClose={onClose} wide>
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
      {startWithPublic && (
        <p className="hint">
          A snapshot reads every point from a curated, explicitly public ArcGIS layer at this moment; GeoDiff never writes to the source. Compare an older
          snapshot with a newer one (or with your own file) to see what changed between them. Differences reflect the published layer at those two
          times, not changes verified or reported by GeoDiff.
        </p>
      )}
      <div className="ds-grid">
        {(['baseline', 'proposed'] as Role[]).map((r) => (
          <DatasetCard
            key={r}
            role={r}
            slot={slots[r]}
            onFile={(f) => void onFile(r, f)}
            onImport={(src) => void onImport(r, src)}
            sources={sources}
            publicFirst={startWithPublic}
            onIdField={(f) => setSlots((s) => ({ ...s, [r]: { ...s[r], idField: f } }))}
          />
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

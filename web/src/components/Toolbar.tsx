import { useEffect, useRef, useState } from 'react';
import { useReview } from '../state/review';
import { REPO_URL } from '../config';

function download(name: string, text: string, type: string) {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const a = document.createElement('a');
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function ExportMenu() {
  const { adapter, info, setNotice } = useReview();
  const [open, setOpen] = useState(false);
  const [working, setWorking] = useState<string | null>(null);
  const wrap = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const complete = info?.progress.complete ?? false;
  const left = info?.progress.unresolved_components ?? 0;

  async function run(mode: 'draft' | 'final') {
    setWorking(mode);
    try {
      const r = await adapter.exportResult(mode);
      const stem = `geodiff-${adapter.mode}-${mode}-r${info?.revision ?? 0}`;
      download(`${stem}.geojson`, JSON.stringify(r.geojson, null, 1), 'application/geo+json');
      download(`${stem}.report.json`, JSON.stringify(r.report, null, 2), 'application/json');
      setNotice({
        tone: 'success',
        text:
          mode === 'final'
            ? `Final export downloaded: ${r.report.counts.output_features} features and a report.`
            : `Draft export downloaded (${r.report.counts.output_features} features). ${r.report.counts.unresolved_components} unresolved component(s) keep their approved values and are listed in the report.`,
      });
      setOpen(false);
    } catch (e) {
      const err = e as { message?: string; guidance?: string };
      setNotice({ tone: 'error', text: err.message ?? 'Export failed.', guidance: err.guidance });
    } finally {
      setWorking(null);
    }
  }

  // Disclosure: Escape or a click outside closes it and returns focus to the trigger.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return;
      e.stopPropagation();
      setOpen(false);
      trigger.current?.focus();
    };
    const onDown = (e: PointerEvent) => {
      if (!wrap.current?.contains(e.target as Node)) setOpen(false);
    };
    window.addEventListener('keydown', onKey, true);
    window.addEventListener('pointerdown', onDown);
    return () => {
      window.removeEventListener('keydown', onKey, true);
      window.removeEventListener('pointerdown', onDown);
    };
  }, [open]);

  return (
    <div className="menu" ref={wrap}>
      <button ref={trigger} type="button" className="btn primary" aria-expanded={open} aria-controls="export-options" onClick={() => setOpen((o) => !o)} data-testid="export">
        Export <span aria-hidden="true">▾</span>
      </button>
      {open && (
        <div className="menu-pop" id="export-options">
          <button type="button" onClick={() => void run('draft')} disabled={!!working} data-testid="export-draft">
            {working === 'draft' ? 'Preparing…' : 'Draft export'}
            <small>Any time. Unresolved changes keep approved values and are listed in the report.</small>
          </button>
          <button type="button" onClick={() => void run('final')} disabled={!complete || !!working} aria-describedby="final-why" data-testid="export-final">
            {working === 'final' ? 'Preparing…' : 'Final export'}
            <small id="final-why">{complete ? 'All decisions are made.' : `Available when all ${left} remaining decision(s) are accepted or rejected.`}</small>
          </button>
        </div>
      )}
    </div>
  );
}

export function Progress() {
  const { info } = useReview();
  if (!info) return null;
  const p = info.progress;
  const pct = p.components ? Math.round((p.resolved_components / p.components) * 100) : 100;
  return (
    <div className="progress" title={`${p.resolved_components} of ${p.components} components decided`}>
      <span className="progress-text">
        <strong>{p.resolved_components}</strong>/{p.components} decided
        {p.by_disposition.flagged ? ` · ${p.by_disposition.flagged} flagged` : ''}
      </span>
      <span role="progressbar" aria-label="Review progress" aria-valuemin={0} aria-valuemax={p.components} aria-valuenow={p.resolved_components} className="bar">
        <span style={{ width: `${pct}%` }} />
      </span>
    </div>
  );
}

export interface PresetControl {
  values: number[];
  current: number;
  onChange: (t: number) => void;
}

export default function Toolbar({
  onUpload,
  onImport,
  onTour,
  onSample,
  onReset,
  onLive,
  presets,
}: {
  onUpload: () => void;
  onImport?: () => void;
  onTour: () => void;
  onSample?: () => void;
  onReset?: () => void;
  onLive?: () => void;
  presets?: PresetControl;
}) {
  const { adapter, info } = useReview();
  return (
    <header className="toolbar">
      <div className="brand">
        <span className="logo" aria-hidden="true">⊕</span>
        <span className="name">GeoDiff</span>
        <span className={`mode mode-${adapter.mode}`} data-testid="mode">{adapter.mode === 'sample' ? 'Guided sample' : 'Live workspace'}</span>
      </div>
      {info && (
        <p className="datasets" title={`${info.inputs.baseline.name} → ${info.inputs.proposed.name}`}>
          <span className="mono">{info.inputs.baseline.name}</span> <span aria-hidden="true">→</span> <span className="sr-only">compared with</span>{' '}
          <span className="mono">{info.inputs.proposed.name}</span>
          {!presets && <span className="hint"> · ±{info.parameters.tolerance_m} m</span>}
          {adapter.mode === 'sample' && <span className="hint"> · synthetic data, decisions saved in this browser</span>}
        </p>
      )}
      {presets && (
        <label className="preset">
          <span>Move tolerance</span>
          <select value={presets.current} onChange={(e) => presets.onChange(Number(e.target.value))} data-testid="preset">
            {presets.values.map((v) => (
              <option key={v} value={v}>
                {v} m preset
              </option>
            ))}
          </select>
        </label>
      )}
      <Progress />
      <nav className="actions" aria-label="Main actions">
        <button type="button" className="btn" onClick={onUpload} data-testid="try-files">Try your own files</button>
        {onImport && <button type="button" className="btn" onClick={onImport} data-testid="import-layer">Import public layer</button>}
        {onLive && <button type="button" className="btn" onClick={onLive} data-testid="open-live">Live workspace</button>}
        {onSample && <button type="button" className="btn" onClick={onSample} data-testid="open-sample">Open sample</button>}
        {onReset && (
          <button type="button" className="btn ghost" onClick={onReset} data-testid="reset">
            {adapter.mode === 'sample' ? 'Reset sample' : 'Delete workspace'}
          </button>
        )}
        <ExportMenu />
        <button type="button" className="btn ghost" onClick={onTour}>Tour</button>
        <a className="btn ghost" href={REPO_URL} target="_blank" rel="noreferrer">Code ↗</a>
      </nav>
    </header>
  );
}

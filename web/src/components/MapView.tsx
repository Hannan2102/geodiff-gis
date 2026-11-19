import { LngLatBounds, Map as MLMap, Marker, NavigationControl, ScaleControl, setWorkerUrl, type GeoJSONSource, type MapMouseEvent } from 'maplibre-gl';
import 'maplibre-gl/dist/maplibre-gl.css';
import workerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?url';
import type { Feature, FeatureCollection } from 'geojson';
import { useEffect, useRef, useState } from 'react';
import { compass, displayDistance, primaryKind } from '../lib/format';
import { registerIcons } from '../map/icons';
import { proofStyle } from '../map/style';
import { prefersReducedMotion } from '../map/webgl';
import { useReview } from '../state/review';
import type { AssetDetail, Kind, LngLat } from '../types';
import { KindSymbol } from './Symbols';

setWorkerUrl(workerUrl);

// Cedar Hollow synthetic grid, used when a run has no extent yet.
const DEFAULT_BOUNDS: [number, number, number, number] = [-96.812, 32.774, -96.772, 32.8004];

function bboxOf(map: MLMap): [number, number, number, number] {
  const b = map.getBounds();
  const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));
  return [clamp(b.getWest(), -180, 180), clamp(b.getSouth(), -90, 90), clamp(b.getEast(), -180, 180), clamp(b.getNorth(), -90, 90)];
}

function selectionFeatures(d: AssetDetail) {
  const feats: Feature[] = [];
  const move = d.components.find((c) => c.kind === 'move');
  const from = d.baseline?.coordinates;
  const to = d.proposed?.coordinates;
  if (move && move.evidence.from && move.evidence.to) {
    const [a, b] = [move.evidence.from, move.evidence.to];
    feats.push({ type: 'Feature', properties: { role: 'line' }, geometry: { type: 'LineString', coordinates: [a, b] } });
    feats.push({ type: 'Feature', properties: { role: 'old' }, geometry: { type: 'Point', coordinates: a } });
    feats.push({ type: 'Feature', properties: { role: 'new' }, geometry: { type: 'Point', coordinates: b } });
    feats.push({ type: 'Feature', properties: { role: 'arrow', bearing: move.evidence.bearing_deg ?? 0 }, geometry: { type: 'Point', coordinates: b } });
  } else {
    const at = to ?? from;
    if (at) {
      if (from && d.status !== 'added') feats.push({ type: 'Feature', properties: { role: 'old' }, geometry: { type: 'Point', coordinates: from } });
      feats.push({ type: 'Feature', properties: { role: 'mark' }, geometry: { type: 'Point', coordinates: at } });
    }
  }
  const fc: FeatureCollection = { type: 'FeatureCollection', features: feats };
  return fc;
}

export default function MapView() {
  const { adapter, info, select, detail, mapVersion, selectedId } = useReview();
  const el = useRef<HTMLDivElement>(null);
  const mapRef = useRef<MLMap | null>(null);
  const labelRef = useRef<Marker | null>(null);
  const [ready, setReady] = useState(false);
  const [chooser, setChooser] = useState<{ x: number; y: number; ids: { id: string; kind: Kind }[] } | null>(null);
  const [mapError, setMapError] = useState<string | null>(null);
  const [truncated, setTruncated] = useState(false);
  const extent = info?.extent ?? null;
  // Credit any public source behind this run's inputs (deduplicated).
  const sources = [info?.inputs.baseline.source, info?.inputs.proposed.source]
    .filter((s): s is Record<string, unknown> => !!s && typeof s.item_url === 'string')
    .map((s) => ({ url: String(s.item_url), label: String(s.attribution) }))
    .filter((s, i, a) => a.findIndex((x) => x.url === s.url) === i);

  // Create the map once.
  useEffect(() => {
    if (!el.current) return;
    const map = new MLMap({
      container: el.current,
      style: proofStyle(),
      bounds: (extent ?? DEFAULT_BOUNDS) as [number, number, number, number],
      fitBoundsOptions: { padding: 40, maxZoom: 17 },
      attributionControl: false,
      maxZoom: 20,
      dragRotate: false,
      pitchWithRotate: false,
    });
    map.touchZoomRotate.disableRotation();
    map.addControl(new NavigationControl({ showCompass: false }), 'top-right');
    map.addControl(new ScaleControl({ unit: 'metric' }), 'bottom-left');
    map.on('styleimagemissing', () => registerIcons(map));
    map.on('load', () => {
      registerIcons(map);
      setReady(true);
    });
    map.on('error', (e) => {
      const msg = (e as unknown as { error?: { message?: string } }).error?.message ?? 'Map error';
      if (/webgl/i.test(msg)) setMapError(msg);
    });
    mapRef.current = map;
    return () => {
      labelRef.current?.remove();
      map.remove();
      mapRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- create once; extent applied below
  }, []);

  // Fit to the run's extent when a run loads.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !extent || selectedId) return;
    map.fitBounds(extent, { padding: 40, maxZoom: 17, duration: 0 });
  }, [extent, selectedId]);

  // Load changed assets (and baseline context) for the viewport.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready) return;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let seq = 0;
    const load = () => {
      const n = ++seq;
      const bbox = bboxOf(map);
      void adapter
        .mapAssets(bbox)
        .then(({ assets, truncated: t }) => {
          if (n !== seq || !mapRef.current) return;
          setTruncated(t);
          (map.getSource('changes') as GeoJSONSource | undefined)?.setData({
            type: 'FeatureCollection',
            features: assets.map((a) => ({
              type: 'Feature',
              id: a.asset_id,
              properties: {
                asset_id: a.asset_id,
                primary: primaryKind(a.kinds),
                resolved: a.dispositions.every((d) => d === 'accepted' || d === 'rejected'),
              },
              geometry: { type: 'Point', coordinates: (a.to ?? a.from) as LngLat },
            })),
          });
        })
        .catch(() => undefined);
      // Approved positions give context: small inventories from zoom 6, large ones from 14.
      const small = (info?.inputs.baseline.feature_count ?? Infinity) <= 2000;
      const baselineSrc = map.getSource('baseline') as GeoJSONSource | undefined;
      if (map.getZoom() < (small ? 6 : 14)) {
        void baselineSrc?.setData({ type: 'FeatureCollection', features: [] });
      } else {
        void adapter
          .baselinePoints(bbox)
          .then((pts) => {
            if (n !== seq) return;
            (map.getSource('baseline') as GeoJSONSource | undefined)?.setData({
              type: 'FeatureCollection',
              features: pts.map((c) => ({ type: 'Feature', properties: {}, geometry: { type: 'Point', coordinates: c } })),
            });
          })
          .catch(() => undefined);
      }
    };
    const onMove = () => {
      clearTimeout(timer);
      timer = setTimeout(load, 150);
    };
    load();
    map.on('moveend', onMove);
    return () => {
      clearTimeout(timer);
      map.off('moveend', onMove);
    };
  }, [adapter, ready, mapVersion, info?.inputs.baseline.feature_count]);

  // Click to select; overlapping symbols open a chooser so nothing is hidden.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready) return;
    const onClick = (e: MapMouseEvent) => {
      const p = e.point;
      const hits = map.queryRenderedFeatures(
        [
          [p.x - 10, p.y - 10],
          [p.x + 10, p.y + 10],
        ],
        { layers: ['changes'] },
      );
      const ids = [...new Map(hits.map((h) => [String(h.properties.asset_id), h.properties.primary as Kind])).entries()].map(([id, kind]) => ({ id, kind }));
      if (ids.length === 0) {
        setChooser(null);
        return;
      }
      if (ids.length === 1) {
        setChooser(null);
        select(ids[0]!.id, 'map');
        return;
      }
      setChooser({ x: p.x, y: p.y, ids });
    };
    const enter = () => (map.getCanvas().style.cursor = 'pointer');
    const leave = () => (map.getCanvas().style.cursor = '');
    map.on('click', onClick);
    map.on('mouseenter', 'changes', enter);
    map.on('mouseleave', 'changes', leave);
    return () => {
      map.off('click', onClick);
      map.off('mouseenter', 'changes', enter);
      map.off('mouseleave', 'changes', leave);
    };
  }, [ready, select]);

  // Draw and frame the selection.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready) return;
    const src = map.getSource('selection') as GeoJSONSource | undefined;
    labelRef.current?.remove();
    labelRef.current = null;
    if (!detail) {
      void src?.setData({ type: 'FeatureCollection', features: [] });
      return;
    }
    void src?.setData(selectionFeatures(detail));
    const duration = prefersReducedMotion() ? 0 : 600;
    const move = detail.components.find((c) => c.kind === 'move');
    if (move?.evidence.from && move.evidence.to) {
      const [a, b] = [move.evidence.from, move.evidence.to];
      const label = document.createElement('div');
      label.className = 'distance-label';
      label.textContent = `${displayDistance(move.evidence.distance_m ?? 0)} ${compass(move.evidence.bearing_deg ?? 0)}`;
      label.setAttribute('aria-hidden', 'true');
      labelRef.current = new Marker({ element: label }).setLngLat([(a[0] + b[0]) / 2, (a[1] + b[1]) / 2]).addTo(map);
      map.fitBounds(new LngLatBounds(a, a).extend(b), { padding: { top: 90, bottom: 90, left: 90, right: 90 }, maxZoom: 18.5, duration });
    } else {
      const at = detail.proposed?.coordinates ?? detail.baseline?.coordinates;
      if (at) map.easeTo({ center: at, zoom: Math.max(map.getZoom(), 17.5), duration });
    }
  }, [detail, ready]);

  if (mapError) {
    return <p className="map-error" role="alert">Map could not start: {mapError}. Use the queue and details panel instead.</p>;
  }

  return (
    <div className="map-wrap" data-testid="map">
      <div ref={el} className="map" role="region" aria-label="Map of changed assets" />
      <span className="reg reg-tl" aria-hidden="true" />
      <span className="reg reg-tr" aria-hidden="true" />
      <span className="reg reg-bl" aria-hidden="true" />
      <span className="reg reg-br" aria-hidden="true" />
      <ul className="legend" aria-label="Map legend">
        <li><KindSymbol kind="addition" title="Addition" /> Addition</li>
        <li><KindSymbol kind="deletion" title="Removal" /> Removal</li>
        <li><KindSymbol kind="move" title="Move" /> Move</li>
        <li><KindSymbol kind="attribute" title="Attribute edit" /> Edit</li>
        <li><span className="legend-ring" aria-hidden="true" /> Approved position</li>
      </ul>
      <p className="attrib">
        {sources.length > 0 && (
          <>
            Data:{' '}
            {sources.map((s, i) => (
              <span key={s.url}>
                {i > 0 && '; '}
                <a href={s.url} target="_blank" rel="noreferrer">
                  {s.label}
                </a>
              </span>
            ))}
            {' · '}
          </>
        )}
        Street grid: synthetic Cedar Hollow context (fictional, drawn near Dallas only)
      </p>
      {truncated && <p className="map-note">Zoom in to see every change in this area.</p>}
      {chooser && (
        <div className="chooser" style={{ left: chooser.x, top: chooser.y }} role="dialog" aria-label="Overlapping assets">
          <p>{chooser.ids.length} assets overlap here</p>
          <ul>
            {chooser.ids.map((c) => (
              <li key={c.id}>
                <button type="button" onClick={() => { setChooser(null); select(c.id, 'map'); }}>
                  <KindSymbol kind={c.kind} /> <span className="mono">{c.id}</span>
                </button>
              </li>
            ))}
          </ul>
          <button type="button" className="link" onClick={() => setChooser(null)}>Close</button>
        </div>
      )}
    </div>
  );
}

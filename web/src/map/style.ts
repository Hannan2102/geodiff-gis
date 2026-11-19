import type { FeatureCollection } from 'geojson';
import type { StyleSpecification } from 'maplibre-gl';

// A fully local style: no tiles, glyphs or sprites are fetched from third parties.
// Context layers are synthetic and bundled under /data/context.
const base = import.meta.env.BASE_URL;
const ctx = (name: string) => `${base}data/context/${name}.geojson`;

const empty = (): FeatureCollection => ({ type: 'FeatureCollection', features: [] });

export const PAPER = '#f5f2e9';

export function proofStyle(): StyleSpecification {
  return {
    version: 8,
    name: 'GeoDiff proof sheet',
    sources: {
      blocks: { type: 'geojson', data: ctx('blocks') },
      parks: { type: 'geojson', data: ctx('parks') },
      paths: { type: 'geojson', data: ctx('paths') },
      streets: { type: 'geojson', data: ctx('streets') },
      baseline: { type: 'geojson', data: empty() },
      changes: { type: 'geojson', data: empty(), promoteId: 'asset_id' },
      selection: { type: 'geojson', data: empty() },
    },
    layers: [
      { id: 'paper', type: 'background', paint: { 'background-color': PAPER } },
      { id: 'blocks', type: 'fill', source: 'blocks', paint: { 'fill-color': '#ebe6d9', 'fill-outline-color': '#d9d2c1' } },
      { id: 'parks', type: 'fill', source: 'parks', paint: { 'fill-color': '#dde3cc', 'fill-outline-color': '#b9c3a2' } },
      { id: 'paths', type: 'line', source: 'paths', paint: { 'line-color': '#a8a28f', 'line-width': 1, 'line-dasharray': [3, 2] } },
      {
        id: 'streets',
        type: 'line',
        source: 'streets',
        paint: {
          'line-color': '#fbf9f3',
          'line-width': ['interpolate', ['linear'], ['zoom'], 13, ['case', ['==', ['get', 'class'], 'avenue'], 2.5, 1.2], 18, ['case', ['==', ['get', 'class'], 'avenue'], 16, 9]],
        },
      },
      {
        id: 'baseline',
        type: 'circle',
        source: 'baseline',
        paint: { 'circle-radius': ['interpolate', ['linear'], ['zoom'], 6, 1.4, 16, 2.6], 'circle-color': '#8c9191', 'circle-opacity': 0.55 },
      },
      {
        id: 'changes',
        type: 'symbol',
        source: 'changes',
        layout: {
          'icon-image': ['concat', 'k-', ['get', 'primary']],
          'icon-size': ['interpolate', ['linear'], ['zoom'], 13, 0.55, 17, 0.9],
          'icon-allow-overlap': true,
          'icon-ignore-placement': true,
          'symbol-sort-key': ['case', ['get', 'resolved'], 0, 1],
        },
        paint: { 'icon-opacity': ['case', ['get', 'resolved'], 0.45, 1] },
      },
      // Selection overprint: old ring, connecting line with arrow, proposed filled mark.
      {
        id: 'sel-line-casing',
        type: 'line',
        source: 'selection',
        filter: ['==', ['get', 'role'], 'line'],
        paint: { 'line-color': PAPER, 'line-width': 6 },
      },
      {
        id: 'sel-line',
        type: 'line',
        source: 'selection',
        filter: ['==', ['get', 'role'], 'line'],
        paint: { 'line-color': '#2346a0', 'line-width': 2.5, 'line-dasharray': [2, 1] },
      },
      {
        id: 'sel-old',
        type: 'circle',
        source: 'selection',
        filter: ['==', ['get', 'role'], 'old'],
        paint: { 'circle-radius': 9, 'circle-color': 'rgba(0,0,0,0)', 'circle-stroke-color': '#202a2a', 'circle-stroke-width': 2.5 },
      },
      {
        id: 'sel-new',
        type: 'circle',
        source: 'selection',
        filter: ['==', ['get', 'role'], 'new'],
        paint: { 'circle-radius': 7, 'circle-color': '#2346a0', 'circle-stroke-color': PAPER, 'circle-stroke-width': 2 },
      },
      {
        id: 'sel-arrow',
        type: 'symbol',
        source: 'selection',
        filter: ['==', ['get', 'role'], 'arrow'],
        layout: {
          'icon-image': 'arrow',
          'icon-rotate': ['get', 'bearing'],
          'icon-rotation-alignment': 'map',
          'icon-allow-overlap': true,
          'icon-ignore-placement': true,
        },
      },
      {
        id: 'sel-mark',
        type: 'symbol',
        source: 'selection',
        filter: ['==', ['get', 'role'], 'mark'],
        layout: { 'icon-image': 'registration', 'icon-allow-overlap': true, 'icon-ignore-placement': true },
      },
    ],
  };
}

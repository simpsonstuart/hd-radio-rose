export const tabs = ['Listen', 'Spectrum', 'Station', 'Artwork', 'Traffic & weather', 'Song log', 'Recordings', 'Settings'];
export const media = (path) => path ? `/api/media?path=${encodeURIComponent(path)}` : '';
export const recordingUrl = (name) => `/api/recording?name=${encodeURIComponent(name)}`;
export const db = (value) => Number.isFinite(value) ? value.toFixed(1) : '—';
export const formatFrequency = value => Number(value).toFixed(1);
export function fmChannel(value) {
  const mhz = Number(value), tenths = Math.round(mhz * 10);
  if (!Number.isFinite(mhz) || mhz < 87.9 || mhz > 107.9 ||
      Math.abs(mhz * 10 - tenths) > .0001 || tenths % 2 !== 1)
    throw new Error('Enter an FM channel from 87.9 to 107.9 MHz in 0.2 MHz steps (for example, 99.1).');
  return tenths / 10;
}
export const digitalLocked = s => Boolean(s?.streaming && s.synced && !s.retuning && s.config?.analog_fallback_mode !== 'analog_only');
export const hdAvailable = (s, index) => digitalLocked(s) && Boolean(s.station?.programs?.[index]);
export function groupPlays(entries) {
  const groups = new Map();
  for (const entry of entries) {
    const key = JSON.stringify([entry.title, entry.artist]);
    const row = groups.get(key) || { ...entry, count: 0 };
    row.count++;
    row.ts_millis = Math.max(row.ts_millis, entry.ts_millis);
    groups.set(key, row);
  }
  return [...groups.values()].sort((a, b) => b.count - a.count || b.ts_millis - a.ts_millis);
}
export function layoutRead(storage) {
  try {
    const stored = JSON.parse(storage.getItem('rose-layout'));
    if (Array.isArray(stored) && stored.length === tabs.length && new Set(stored).size === tabs.length && stored.every(t => tabs.includes(t))) return stored;
  } catch { /* Local storage is optional. */ }
  return [...tabs];
}
export function waveformColor(value) {
  const x = Math.max(0, Math.min(1, (value + 110) / 90));
  return [Math.round(255 * Math.max(0, (x - .45) / .55)), Math.round(220 * x), Math.round(100 + 130 * Math.sin(x * Math.PI))];
}

export const receiverStatus = s => digitalLocked(s) ? s.status : (s.status || '').replace(/\s*\(HD[1-8]\)|\s+HD[1-8]/g, '');

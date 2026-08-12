// Ported 1:1 from legacy-vanilla-js/js/01-data.js — same path data, same look.
export const IC = {
  receipt: 'M5 3h14a1 1 0 0 1 1 1v17l-3-1.5-3 1.5-3-1.5-3 1.5-3-1.5V4a1 1 0 0 1 1-1ZM8 8h8M8 12h5',
  ticket: 'M3 8.5V6a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2v2.5a2.5 2.5 0 0 0 0 7V18a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-2.5a2.5 2.5 0 0 0 0-7ZM10 4v16',
  calendar: 'M8 2v4M16 2v4M3 10h18M5 4h14a2 2 0 0 1 2 2v13a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2Z',
  grid: 'M4 4h7v7H4zM13 4h7v7h-7zM4 13h7v7H4zM13 13h7v7h-7z',
  chart: 'M3 20h18M6 20V11M11 20V6M16 20v-6M21 20V9',
  users: 'M17 20v-1.5A3.5 3.5 0 0 0 13.5 15h-5A3.5 3.5 0 0 0 5 18.5V20M11 11.5A3.75 3.75 0 1 0 11 4a3.75 3.75 0 0 0 0 7.5M19 20v-1.5a3.5 3.5 0 0 0-2.5-3.35M16 4.2a3.75 3.75 0 0 1 0 7.1',
  shield: 'M12 3l7.5 3v5.5c0 4.3-3 8.2-7.5 9.5-4.5-1.3-7.5-5.2-7.5-9.5V6ZM9 12l2 2 4-4',
  team: 'M8 11a3 3 0 1 0 0-6 3 3 0 0 0 0 6ZM14 20v-1.5A3.5 3.5 0 0 0 10.5 15h-5A3.5 3.5 0 0 0 2 18.5V20M17 11a3 3 0 1 0 0-6M22 20v-1.5a3.5 3.5 0 0 0-2.6-3.38',
  webhook: 'M9.5 14.5 14.5 9.5M8 11 6 13a3.5 3.5 0 0 0 5 5l2-2M16 13l2-2a3.5 3.5 0 0 0-5-5l-2 2',
  gauge: 'M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18ZM12 12l4-4',
  sheet: 'M4 4h16v16H4zM4 9h16M4 14h16M9 4v16M15 4v16',
  building: 'M4 21V6a2 2 0 0 1 2-2h6a2 2 0 0 1 2 2v15M14 10h4a2 2 0 0 1 2 2v9M8 8h2M8 12h2M8 16h2M17 14h.01M17 18h.01M3 21h18',
  plus: 'M12 5v14M5 12h14', x: 'M18 6 6 18M6 6l12 12', check: 'M20 6.5 9.5 17 4 11.5',
  edit: 'M11 4H5a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2h13a2 2 0 0 0 2-2v-6M18.4 2.6a2 2 0 0 1 2.83 2.83L12 14.5l-3.5.9.9-3.5Z',
  trash: 'M4 7h16M9 7V5a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2M6 7l.8 12a2 2 0 0 0 2 1.9h6.4a2 2 0 0 0 2-1.9L18 7M10 11v6M14 11v6',
  filter: 'M4 5h16M7 12h10M10 19h4',
  download: 'M12 3v12M7.5 10.5 12 15l4.5-4.5M4 18v1a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-1',
  upload: 'M12 15V3M7.5 7.5 12 3l4.5 4.5M4 18v1a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-1',
  mail: 'M3 7a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2ZM3.5 7.5l7.4 5.3a2 2 0 0 0 2.2 0l7.4-5.3',
  key: 'M15.5 8.5a3 3 0 1 0-3-3M13.5 7.5 4 17v3h3l1-1v-2h2v-2h2l1.5-1.5',
  chevD: 'm6 9.5 6 6 6-6', chevR: 'm9.5 6 6 6-6 6', chevL: 'm14.5 6-6 6 6 6',
  // chevU was missing while chevD existed; Icon falls back to IC.info for an
  // unknown name, so an ascending-sort header rendered an (i) glyph.
  chevU: 'm6 14.5 6-6 6 6',
  arrowR: 'M4 12h15M13 6l6 6-6 6', arrowU: 'M12 19V5M6 11l6-6 6 6',
  refresh: 'M20 11a8 8 0 1 0-2.3 5.7M20 5v6h-6',
  star: 'M12 3.5l2.6 5.4 5.9.8-4.3 4.2 1 5.9-5.2-2.8-5.2 2.8 1-5.9L3.5 9.7l5.9-.8Z',
  list: 'M4 6h16M4 12h16M4 18h16', cols: 'M4 4h16v16H4zM10 4v16M16 4v16',
  clock: 'M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18ZM12 7.5V12l3 2',
  info: 'M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18ZM12 16v-4.5M12 8.2h.01',
  warn: 'M11 4.5 3.4 18a1.2 1.2 0 0 0 1 1.8h15.2a1.2 1.2 0 0 0 1-1.8L13 4.5a1.2 1.2 0 0 0-2 0ZM12 9.5v4M12 16.8h.01',
  globe: 'M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18ZM3.5 9h17M3.5 15h17M12 3a13 13 0 0 1 0 18M12 3a13 13 0 0 0 0 18',
  target: 'M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18ZM12 16.5a4.5 4.5 0 1 0 0-9 4.5 4.5 0 0 0 0 9ZM12 13.5a1.5 1.5 0 1 0 0-3 1.5 1.5 0 0 0 0 3',
  inbox: 'M4 13h4l1 3h6l1-3h4M4 13 6 5h12l2 8v5a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2Z',
  flag: 'M5 21V4M5 4h11l-1.5 4L16 12H5',
  sun: 'M12 17a5 5 0 1 0 0-10 5 5 0 0 0 0 10ZM12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4',
  moon: 'M20 14.5A8.5 8.5 0 1 1 9.5 4a7 7 0 0 0 10.5 10.5Z',
  lock: 'M6 11V8a6 6 0 0 1 12 0v3M5 11h14a1 1 0 0 1 1 1v8a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1v-8a1 1 0 0 1 1-1Z',
  send: 'M21 3 3 10.5l6 2.5 2.5 6L21 3ZM9 13l3-3',
  note: 'M5 3h9l6 6v12H5zM14 3v6h6M8 13h8M8 17h5',
  phone: 'M6 3h3l2 5-2.5 1.5a11 11 0 0 0 5 5L15 12l5 2v3a2 2 0 0 1-2 2A15 15 0 0 1 4 5a2 2 0 0 1 2-2Z',
  play: 'M7 4l12 8-12 8V4Z', pause: 'M9 5v14M15 5v14',
  copy: 'M9 9h9a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H9a2 2 0 0 1-2-2v-9a2 2 0 0 1 2-2ZM5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1',
  eye: 'M2 12s3.6-6.5 10-6.5S22 12 22 12s-3.6 6.5-10 6.5S2 12 2 12ZM12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6Z',
  link: 'M10 13a5 5 0 0 0 7.5.5l3-3a5 5 0 0 0-7-7L12 5M14 11a5 5 0 0 0-7.5-.5l-3 3a5 5 0 0 0 7 7L12 19',
  zap: 'M13 2 4 14h6l-1 8 9-12h-6l1-8Z',
};

export function Icon({ name, size = 15, style, className }) {
  const d = IC[name] || IC.info;
  return (
    <svg
      width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth={1.9} strokeLinecap="round" strokeLinejoin="round"
      aria-hidden="true" style={style} className={className}
    >
      <path d={d} />
    </svg>
  );
}

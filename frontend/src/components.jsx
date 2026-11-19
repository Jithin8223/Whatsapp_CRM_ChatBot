export function Icon({ name, className = 'h-5 w-5' }) {
  const paths = {
    dashboard: 'M3 3h7v7H3z M14 3h7v7h-7z M3 14h7v7H3z M14 14h7v7h-7z',
    inbox: 'M21 11.5a8.4 8.4 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.4 8.4 0 0 1-3.8-.9L3 21l1.9-5.7a8.4 8.4 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.4 8.4 0 0 1 3.8-.9h.5a8.5 8.5 0 0 1 8 8v.5z',
    connections: 'M8 2h8v20H8z M11 18h2 M11 5h2',
    contacts: 'M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2 M16 3a4 4 0 0 1 0 8 M22 21v-2a4 4 0 0 0-3-3.87 M13 7a4 4 0 1 1-8 0 4 4 0 0 1 8 0',
    settings: 'M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8 M12 2v3 M12 19v3 M2 12h3 M19 12h3 M5 5l2 2 M17 17l2 2 M5 19l2-2 M17 7l2-2',
    arrow: 'M5 12h14 M13 6l6 6-6 6',
    search: 'M21 21l-6-6 M17 10a7 7 0 1 1-14 0 7 7 0 0 1 14 0',
    calendar: 'M4 5h16v16H4z M4 10h16 M8 3v4 M16 3v4 M8 14h3v3H8z',
    followup: 'M4 5h16v16H4z M4 10h16 M8 3v4 M16 3v4 M9 15.5l2 2 4-4',
    leads: 'M3 4h18l-7 9v6l-4 2v-8z',
    collapse: 'M15 6l-6 6 6 6',
    expand: 'M9 6l6 6-6 6',
    whatsapp: 'M20.5 11.6a8.5 8.5 0 0 1-12.6 7.4L3.5 20.5l1.5-4.3a8.5 8.5 0 1 1 15.5-4.6 M9 8.5c0 3.5 2.6 6.5 6.5 6.5l1-1.6-2-1-1 .8a4.4 4.4 0 0 1-2.2-2.2l.8-1-1-2z',
    globe: 'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18 M3 12h18 M12 3c2.5 2.6 3.7 5.6 3.7 9s-1.2 6.4-3.7 9 M12 3C9.5 5.6 8.3 8.6 8.3 12s1.2 6.4 3.7 9',
    paperclip: 'M20 11.5l-8.4 8.4a5 5 0 0 1-7.1-7.1l8.8-8.8a3.3 3.3 0 0 1 4.7 4.7l-8.8 8.8a1.7 1.7 0 0 1-2.4-2.4l8-8',
    panel: 'M3 4h18v16H3z M15 4v16',
    check: 'M5 12.5l4.5 4.5L19 7.5',
    checks: 'M2.5 12.5l4.5 4.5L16.5 7.5 M11 16l1 1 9.5-9.5',
    clock: 'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18 M12 7v5l3 2',
    alert: 'M12 3l9.5 17h-19z M12 10v4 M12 17.5v.5',
    download: 'M12 4v11 M7 10l5 5 5-5 M5 20h14',
    file: 'M6 3h8l4 4v14H6z M14 3v4h4 M9 13h6 M9 17h6',
    close: 'M6 6l12 12 M18 6L6 18',
    bot: 'M7 8h10a3 3 0 0 1 3 3v5a3 3 0 0 1-3 3H7a3 3 0 0 1-3-3v-5a3 3 0 0 1 3-3z M12 4v4 M9 13h.01 M15 13h.01',
    user: 'M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8 M4.5 21a7.5 7.5 0 0 1 15 0',
    menu: 'M4 6h16 M4 12h16 M4 18h16',
    trash: 'M4 7h16 M9 7V4h6v3 M6 7l1 13h10l1-13 M10 11v6 M14 11v6',
  };
  return <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={paths[name] || paths.inbox} /></svg>;
}

// Outgoing-message receipt indicator. Shapes follow the familiar WhatsApp convention:
// clock (sending), single tick (sent), overlapping double tick (delivered; blue when read).
const TICK_PATHS = {
  pending: 'M9 2.2a5.3 5.3 0 1 0 0 10.6A5.3 5.3 0 0 0 9 2.2 M9 4.6v3.1l2 1.3',
  sent: 'M4 7.6l3 3L13.6 3.8',
  delivered: 'M1.2 7.6l3 3L10.8 3.8 M7.2 9.4l1.2 1.2L15 3.8',
  failed: 'M9 1.8a5.7 5.7 0 1 0 0 11.4A5.7 5.7 0 0 0 9 1.8 M9 4.4v3.9 M9 10.4v.2',
};
export function DeliveryTicks({ state, label }) {
  const shape = state === 'read' ? 'delivered' : state === 'unknown' ? 'failed' : state;
  if (!TICK_PATHS[shape]) return null;
  return <span className={`ticks ticks-${state}`} role="img" aria-label={label} title={label}>
    <svg viewBox="0 0 16 14" width="16" height="14" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={TICK_PATHS[shape]} /></svg>
  </span>;
}

export function ErrorNotice({ children }) { return children ? <div role="alert" className="notice error">{children}</div> : null; }
export function Empty({ title, children }) { return <div className="empty"><span className="empty-icon"><Icon name="inbox" /></span><h3>{title}</h3><p>{children}</p></div>; }
export function Status({ value }) { return <span className={`status ${['connected', 'open', 'submitted', 'live', 'read', 'delivered'].includes(value) ? 'good' : ['error', 'failed', 'unknown', 'disconnected'].includes(value) ? 'bad' : ''}`}><span />{value?.replaceAll('_', ' ') || 'pending'}</span>; }
export function PageHeading({ eyebrow, title, children, action }) { return <div className="page-heading"><div><div className="eyebrow">{eyebrow}</div><h1>{title}</h1><p>{children}</p></div>{action}</div>; }
export function Avatar({ name = '?', small = false }) { return <span className={`avatar ${small ? 'small' : ''}`}>{name.slice(0, 2).toUpperCase()}</span>; }
export function Pager({ page, setPage, data }) { return data?.count > 50 && <div className="pager"><button className="btn secondary" disabled={page === 1} onClick={() => setPage(page - 1)}>Previous</button><span>Page {page}</span><button className="btn secondary" disabled={!data.next} onClick={() => setPage(page + 1)}>Next</button></div>; }
export const contactName = contact => contact?.guest_label
  ? [contact.guest_label, contact.display_name].filter(Boolean).join(' · ')
  : contact?.display_name || contact?.phone || contact?.whatsapp_id || 'Customer';
export const time = value => value ? new Date(value).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : '';

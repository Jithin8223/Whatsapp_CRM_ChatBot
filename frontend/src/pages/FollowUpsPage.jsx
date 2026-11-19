import { useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { groupByDate, isOverdue, monthGrid, monthRange, parseISODate, today } from '../calendar.js';
import { ErrorNotice, PageHeading, Pager } from '../components.jsx';
import { FollowUpCard, NEXT_ACTIONS } from '../FollowUps.jsx';
import { useResource, useWorkspacePath } from '../hooks.js';

const WEEKDAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

function Filters({ filters, setFilters, team }) {
  const set = (key, value) => setFilters({ ...filters, [key]: value });
  return <div className="lead-filters" role="group" aria-label="Follow-up filters">
    <select aria-label="Filter by status" value={filters.status} onChange={event => set('status', event.target.value)}>
      <option value="">All</option><option value="pending">Pending</option><option value="done">Done</option><option value="overdue">Overdue</option>
    </select>
    <select aria-label="Filter by assignee" value={filters.assignee} onChange={event => set('assignee', event.target.value)}>
      <option value="">Anyone</option><option value="me">Assigned to me</option>{team?.map(m => <option key={m.id} value={m.user_id}>{m.username}</option>)}
    </select>
    <select aria-label="Filter by source" value={filters.source} onChange={event => set('source', event.target.value)}>
      <option value="">All sources</option><option value="whatsapp">WhatsApp</option><option value="website">Website</option><option value="lead">Lead</option>
    </select>
  </div>;
}

function Entry({ item, onOpen }) {
  const overdue = isOverdue(item);
  return <button type="button" className={`calendar-entry ${item.status} ${overdue ? 'overdue' : ''}`} onClick={() => onOpen(item)}
    title={`${item.contact_name} · ${item.assigned_agent_name || 'Unassigned'} · ${overdue ? 'Overdue' : item.status}`}>
    <strong>{item.due_time ? item.due_time.slice(0, 5) + ' ' : ''}{item.contact_name}</strong>
    <span>{item.assigned_agent_name || 'Unassigned'} · {item.status === 'done' ? 'Done' : overdue ? 'Overdue' : 'Pending'}</span>
  </button>;
}

export default function FollowUpsPage() {
  const base = useWorkspacePath();
  const [params, setParams] = useSearchParams();
  const focus = params.get('date') && /^\d{4}-\d{2}-\d{2}$/.test(params.get('date')) ? parseISODate(params.get('date')) : new Date();
  const [cursor, setCursor] = useState({ year: focus.getFullYear(), month: focus.getMonth() });
  const [view, setView] = useState('calendar');
  const [filters, setFilters] = useState({ status: '', assignee: '', source: '' });
  const [selected, setSelected] = useState(null);
  const [page, setPage] = useState(1);
  const team = useResource(`${base}team/`);
  const range = monthRange(cursor.year, cursor.month);
  const query = new URLSearchParams({ ...Object.fromEntries(Object.entries(filters).filter(([, v]) => v)), today: today() });
  const calendar = useResource(view === 'calendar' ? `${base}followups/?${query}&date_from=${range.from}&date_to=${range.to}` : null);
  const list = useResource(view === 'list' ? `${base}followups/?${query}&page=${page}` : null);
  const byDate = groupByDate(calendar.data);
  const move = delta => setCursor(({ year, month }) => {
    const next = new Date(year, month + delta, 1);
    return { year: next.getFullYear(), month: next.getMonth() };
  });
  const label = new Date(cursor.year, cursor.month, 1).toLocaleString(undefined, { month: 'long', year: 'numeric' });
  const current = selected && (calendar.data?.find(item => item.id === selected.id) || list.data?.results?.find(item => item.id === selected.id) || selected);
  return <>
    <PageHeading eyebrow="TEAM SCHEDULE" title="Follow-ups" action={<div className="tabs" role="tablist">
      <button role="tab" aria-selected={view === 'calendar'} className={view === 'calendar' ? 'active' : ''} onClick={() => setView('calendar')}>Calendar</button>
      <button role="tab" aria-selected={view === 'list'} className={view === 'list' ? 'active' : ''} onClick={() => setView('list')}>List</button>
    </div>}>Scheduled customer follow-ups from WhatsApp, website chat and leads. Add new ones from a conversation’s follow-up icon.</PageHeading>
    <Filters filters={filters} setFilters={value => { setFilters(value); setPage(1); }} team={team.data} />
    <ErrorNotice>{calendar.error || list.error}</ErrorNotice>
    <div className="followups-layout">
      {view === 'calendar' ? <section className="panel calendar" aria-label={`Follow-ups for ${label}`}>
        <div className="calendar-toolbar">
          <button className="btn ghost compact" onClick={() => move(-1)} aria-label="Previous month">←</button>
          <h2>{label}</h2>
          <button className="btn ghost compact" onClick={() => move(1)} aria-label="Next month">→</button>
          <button className="btn secondary compact" onClick={() => { const now = new Date(); setCursor({ year: now.getFullYear(), month: now.getMonth() }); setParams({}); }}>Today</button>
        </div>
        <div className="calendar-grid" role="grid">
          {WEEKDAYS.map(day => <div key={day} className="calendar-weekday" role="columnheader">{day}</div>)}
          {monthGrid(cursor.year, cursor.month).map(day => <div role="gridcell" key={day.iso}
            className={`calendar-day ${day.inMonth ? '' : 'outside'} ${day.iso === today() ? 'today' : ''} ${day.iso === params.get('date') ? 'focused' : ''}`}
            aria-label={`${day.iso}: ${(byDate[day.iso] || []).length} follow-ups`}>
            <span className="calendar-date">{day.day}</span>
            {(byDate[day.iso] || []).slice(0, 3).map(item => <Entry key={item.id} item={item} onOpen={setSelected} />)}
            {(byDate[day.iso] || []).length > 3 && <button type="button" className="calendar-more" onClick={() => { setView('list'); }}>+{byDate[day.iso].length - 3} more</button>}
          </div>)}
        </div>
        {calendar.loading && !calendar.data && <p className="muted p-3">Loading follow-ups…</p>}
      </section> : <section className="panel p-5 space-y-3" aria-label="Follow-up list">
        {list.data?.results?.map(item => <div key={item.id} className="followup-row">
          <Entry item={item} onOpen={setSelected} />
          <span className="text-sm">{item.due_date} · {NEXT_ACTIONS[item.next_action]} · {item.channel === 'website' ? 'Website' : 'WhatsApp'}{item.lead && ' · Lead'}</span>
        </div>)}
        {!list.loading && !list.data?.results?.length && <p className="muted">No follow-ups match these filters.</p>}
        <Pager page={page} setPage={setPage} data={list.data} />
      </section>}
      {current && <aside className="panel p-4 followup-detail" aria-label="Follow-up details">
        <div className="section-heading"><h2>{current.contact_name}</h2><button className="btn ghost compact" onClick={() => setSelected(null)} aria-label="Close details">✕</button></div>
        <p className="text-sm">{current.contact_phone || 'Phone not provided'} · {current.channel === 'website' ? 'Website' : 'WhatsApp'}</p>
        <FollowUpCard item={current} base={base} />
        <Link className="btn secondary compact mt-3" to={`/inbox/${current.conversation}`}>Open conversation</Link>
      </aside>}
    </div>
  </>;
}

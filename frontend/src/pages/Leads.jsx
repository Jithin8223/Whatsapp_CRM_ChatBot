import { useState } from 'react';
import { Link } from 'react-router-dom';
import { api, errorText } from '../api.js';
import { useAuth } from '../auth.js';
import { ErrorNotice, Icon, PageHeading, Pager } from '../components.jsx';
import { CRM_STATUSES } from '../FollowUps.jsx';
import { useAction, useResource, useWorkspacePath } from '../hooks.js';

export const LEAD_STATUSES = { new: 'New', follow_up: 'Needs follow-up', qualified: 'Qualified', converted: 'Converted', closed: 'Closed' };
export const LEAD_PRIORITIES = { low: 'Low', medium: 'Medium', high: 'High' };

const customer = lead => lead.contact_name || lead.guest_label || 'Website visitor';

function DeleteLeadDialog({ lead, base, close, onDeleted }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  async function remove() {
    setBusy(true); setError('');
    try {
      await api.delete(`${base}leads/${lead.id}/`);
      onDeleted(`Lead for ${customer(lead)} deleted.`);
    } catch (failure) {
      setError(errorText(failure));
      setBusy(false);
    }
  }
  return <div className="modal-backdrop" onKeyDown={event => { if (event.key === 'Escape' && !busy) close(); }}>
    <section className="modal confirm-dialog" role="alertdialog" aria-modal="true" aria-labelledby="delete-lead-title" aria-describedby="delete-lead-body">
      <h2 id="delete-lead-title">Delete lead for {customer(lead)}?</h2>
      <p id="delete-lead-body">This removes the lead record{lead.product_name ? ` (${lead.product_name})` : ''} from Leads. The customer’s conversation, messages and follow-ups are kept. Setting the conversation to Converted again creates the lead again.</p>
      <ErrorNotice>{error}</ErrorNotice>
      <div className="confirm-actions">
        <button type="button" className="btn secondary" onClick={close} disabled={busy} autoFocus>Cancel</button>
        <button type="button" className="btn danger-solid" onClick={remove} disabled={busy}>{busy ? 'Deleting…' : 'Delete lead'}</button>
      </div>
    </section>
  </div>;
}

export function LeadsTable({ base }) {
  const [filters, setFilters] = useState({ search: '', status: '', priority: '', source: '', assignee: '' });
  const [query, setQuery] = useState(filters);
  const [page, setPage] = useState(1);
  const params = new URLSearchParams({ ...Object.fromEntries(Object.entries(query).filter(([, value]) => value)), page: String(page) });
  const leads = useResource(`${base}leads/?${params}`);
  const team = useResource(`${base}team/`);
  const { user } = useAuth();
  const role = useAuth(s => s.workspaces.find(w => w.id === s.workspaceId)?.role);
  const { busy, error, run } = useAction();
  const [deleting, setDeleting] = useState(null);
  const [notice, setNotice] = useState('');
  const patch = (lead, values) => run(() => api.patch(`${base}leads/${lead.id}/`, values));
  const set = (key, value) => { const next = { ...filters, [key]: value }; setFilters(next); if (key !== 'search') { setQuery(next); setPage(1); } };
  return <section className="panel p-5 space-y-3">
    <ErrorNotice>{leads.error || error}</ErrorNotice>
    {notice && <p className="notice success" role="status">{notice} <button className="btn ghost compact" onClick={() => setNotice('')}>Dismiss</button></p>}
    <form className="lead-filters" role="search" onSubmit={event => { event.preventDefault(); setQuery(filters); setPage(1); }}>
      <input aria-label="Search leads" placeholder="Search name, WhatsApp ID, product or notes" value={filters.search} onChange={event => set('search', event.target.value)} />
      <select aria-label="Filter by lead status" value={filters.status} onChange={event => set('status', event.target.value)}><option value="">All statuses</option>{Object.entries(LEAD_STATUSES).map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select>
      <select aria-label="Filter by priority" value={filters.priority} onChange={event => set('priority', event.target.value)}><option value="">All priorities</option>{Object.entries(LEAD_PRIORITIES).map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select>
      <select aria-label="Filter by source" value={filters.source} onChange={event => set('source', event.target.value)}><option value="">All sources</option><option value="whatsapp">WhatsApp</option><option value="website">Website</option></select>
      <select aria-label="Filter by assignee" value={filters.assignee} onChange={event => set('assignee', event.target.value)}><option value="">Anyone</option><option value="me">Assigned to me</option><option value="none">Unassigned</option>{team.data?.map(m => <option key={m.id} value={m.user_id}>{m.username}</option>)}</select>
      <button className="btn secondary">Search</button>
    </form>
    <div className="table-scroll">
      <table className="leads-table">
        <thead><tr><th>Customer</th><th>Phone</th><th>Source</th><th>Interest</th><th>Assigned</th><th>Priority</th><th>Status</th><th>Created</th><th>Follow-up</th>{role === 'admin' && <th><span className="sr-only">Actions</span></th>}</tr></thead>
        <tbody>{leads.data?.results?.map(lead => {
          const editable = role === 'admin' || !lead.assigned_agent || lead.assigned_agent === user.id;
          return <tr key={lead.id}>
            <td><Link to={`/inbox/${lead.conversation_id}`}>{customer(lead)}</Link><small className="block muted">{CRM_STATUSES[lead.conversation_status]}</small></td>
            <td>{lead.contact_phone || lead.contact_email || <span className="muted">Not provided</span>}</td>
            <td><span className="status">{lead.source === 'website' ? 'Website' : 'WhatsApp'}</span></td>
            <td>{lead.product_name || <span className="muted">Not identified</span>}{lead.summary && <small className="block muted" title={lead.summary}>{lead.summary.slice(0, 80)}{lead.summary.length > 80 && '…'}</small>}</td>
            <td><select aria-label={`Assignee for ${customer(lead)}`} value={lead.assigned_agent || ''} disabled={busy || !editable} onChange={event => patch(lead, { assigned_agent: event.target.value ? Number(event.target.value) : null })}>
              <option value="">Unassigned</option>{team.data?.filter(m => role === 'admin' || m.user_id === user.id || m.user_id === lead.assigned_agent).map(m => <option key={m.id} value={m.user_id}>{m.username}</option>)}
            </select></td>
            <td><select aria-label={`Priority for ${customer(lead)}`} className={`priority ${lead.priority}`} value={lead.priority} disabled={busy || !editable} onChange={event => patch(lead, { priority: event.target.value })}>{Object.entries(LEAD_PRIORITIES).map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></td>
            <td><select aria-label={`Status for ${customer(lead)}`} value={lead.status} disabled={busy || !editable} onChange={event => patch(lead, { status: event.target.value })}>{Object.entries(LEAD_STATUSES).map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></td>
            <td><time dateTime={lead.created_at}>{new Date(lead.created_at).toLocaleDateString()}</time></td>
            <td>{lead.next_followup ? <Link to={`/followups?date=${lead.next_followup.due_date}`}>{lead.next_followup.due_date}</Link> : <Link className="muted" to={`/inbox/${lead.conversation_id}`}>Schedule in chat</Link>}</td>
            {role === 'admin' && <td className="row-actions"><button type="button" className="icon-danger" onClick={() => { setNotice(''); setDeleting(lead); }} aria-label={`Delete lead for ${customer(lead)}`} title="Delete lead"><Icon name="trash" className="h-4 w-4" /></button></td>}
          </tr>;
        })}</tbody>
      </table>
    </div>
    {!leads.loading && !leads.data?.results?.length && <p className="muted">No leads match. Conversations marked Converted and chatbot quotation requests appear here.</p>}
    <Pager page={page} setPage={setPage} data={leads.data} />
    {deleting && <DeleteLeadDialog lead={deleting} base={base} close={() => setDeleting(null)}
      onDeleted={message => { setDeleting(null); setNotice(message); useAuth.getState().refresh(); }} />}
  </section>;
}

export default function Leads() {
  const base = useWorkspacePath();
  return <><PageHeading eyebrow="SALES PIPELINE" title="Leads">Converted conversations and chatbot enquiries from WhatsApp and your website.</PageHeading>
    <LeadsTable base={base} /></>;
}

import { useState } from 'react';
import { api } from './api.js';
import { useAuth } from './auth.js';
import { today } from './calendar.js';
import { ErrorNotice } from './components.jsx';
import { useAction, useResource } from './hooks.js';

export const NEXT_ACTIONS = {
  call: 'Call the customer', send_quotation: 'Send quotation', share_information: 'Share product information',
  schedule_meeting: 'Schedule a meeting', decision: 'Follow up on a decision', other: 'Other',
};
export const PRIORITIES = { '': 'No priority', low: 'Low', medium: 'Medium', high: 'High' };
export const CRM_STATUSES = {
  unassigned: 'Unassigned', open: 'Open', unanswered: 'Unanswered', not_interested: 'Not Interested', converted: 'Converted',
};

export function canEditFollowUp(item, user, role) {
  return role === 'admin' || [item.assigned_agent, item.created_by].includes(user?.id);
}

export function FollowUpCard({ item, base, onChange, compact = false }) {
  const { user } = useAuth();
  const role = useAuth(s => s.workspaces.find(w => w.id === s.workspaceId)?.role);
  const { run, busy, error } = useAction();
  const [rescheduling, setRescheduling] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const overdue = item.status === 'pending' && item.due_date < today();
  const editable = canEditFollowUp(item, user, role);
  const deletable = role === 'admin' || (item.created_by === user?.id && item.status === 'pending');
  const act = request => run(async () => { await request(); onChange?.(); });
  return <article className={`followup-card ${item.status} ${overdue ? 'overdue' : ''}`}>
    <header>
      <strong>{item.due_date}{item.due_time && ` · ${item.due_time.slice(0, 5)}`}</strong>
      <span className={`status ${item.status === 'done' ? 'good' : overdue ? 'bad' : ''}`}>{item.status === 'done' ? 'Done' : overdue ? 'Overdue' : 'Pending'}</span>
      {item.priority && <span className="status">{PRIORITIES[item.priority]}</span>}
    </header>
    <p className="text-sm"><b>{NEXT_ACTIONS[item.next_action] || item.next_action}</b> · {item.assigned_agent_name || 'Unassigned'}</p>
    {!compact && <dl className="followup-notes">
      {item.customer_said && <><dt>Customer said</dt><dd>{item.customer_said}</dd></>}
      {item.team_said && <><dt>We told the customer</dt><dd>{item.team_said}</dd></>}
      {item.next_steps && <><dt>Next steps</dt><dd>{item.next_steps}</dd></>}
    </dl>}
    <p className="muted text-xs">Created by {item.created_by_name || 'system'} · {new Date(item.created_at).toLocaleString()}
      {item.completed_at && ` · completed by ${item.completed_by_name || 'someone'} ${new Date(item.completed_at).toLocaleString()}`}</p>
    <ErrorNotice>{error}</ErrorNotice>
    {editable && <div className="flex flex-wrap gap-2">
      {item.status === 'pending'
        ? <button className="btn secondary compact" disabled={busy} onClick={() => act(() => api.post(`${base}followups/${item.id}/complete/`))}>Mark done</button>
        : <button className="btn ghost compact" disabled={busy} onClick={() => act(() => api.post(`${base}followups/${item.id}/reopen/`))}>Reopen</button>}
      {item.status === 'pending' && <button className="btn ghost compact" disabled={busy} onClick={() => setRescheduling(!rescheduling)} aria-expanded={rescheduling}>Reschedule</button>}
    </div>}
    {deletable && (confirmDelete
      ? <div className="flex flex-wrap items-center gap-2 text-sm" role="alert"><span>Delete this follow-up permanently?</span>
          <button className="btn secondary compact danger" disabled={busy} onClick={() => act(() => api.delete(`${base}followups/${item.id}/`))}>Delete</button>
          <button className="btn ghost compact" onClick={() => setConfirmDelete(false)}>Keep</button></div>
      : <button className="btn ghost compact text-danger self-start" disabled={busy} onClick={() => setConfirmDelete(true)}>Delete</button>)}
    {rescheduling && <form className="flex flex-wrap items-end gap-2" onSubmit={event => {
      event.preventDefault();
      const data = new FormData(event.currentTarget);
      act(async () => {
        await api.patch(`${base}followups/${item.id}/`, { due_date: data.get('due_date'), due_time: data.get('due_time') || null });
        setRescheduling(false);
      });
    }}>
      <label>New date<input type="date" name="due_date" required defaultValue={item.due_date} /></label>
      <label>Time<input type="time" name="due_time" defaultValue={item.due_time?.slice(0, 5) || ''} /></label>
      <button className="btn primary compact" disabled={busy}>Save</button>
    </form>}
  </article>;
}

export function FollowUpModal({ conversation, base, team, close }) {
  const list = useResource(`${base}followups/?conversation=${conversation.id}&order=recent`);
  const { run, busy, error } = useAction();
  const [saved, setSaved] = useState('');
  const contact = conversation.contact || {};
  const name = contact.display_name || contact.guest_label || contact.phone || 'Customer';
  return <div className="modal-backdrop" onKeyDown={event => { if (event.key === 'Escape') close(); }}>
    <section className="modal followup-modal" role="dialog" aria-modal="true" aria-labelledby="followup-title">
      <div className="section-heading"><h2 id="followup-title">Follow-up · {name}</h2><button className="btn ghost" onClick={close} aria-label="Close follow-ups" autoFocus>✕</button></div>
      <dl className="followup-summary">
        <dt>Customer</dt><dd>{name}</dd>
        <dt>Phone</dt><dd>{contact.phone || 'Not provided'}</dd>
        <dt>Assigned</dt><dd>{conversation.assigned_agent_name || 'Unassigned'}</dd>
        <dt>Status</dt><dd>{CRM_STATUSES[conversation.crm_status] || conversation.crm_status}</dd>
      </dl>
      <ErrorNotice>{error || list.error}</ErrorNotice>
      {saved && <p role="status" className="notice">{saved}</p>}
      <form className="space-y-3" onSubmit={event => {
        event.preventDefault();
        const form = event.currentTarget;
        const data = new FormData(form);
        run(async () => {
          await api.post(`${base}followups/`, {
            conversation: conversation.id, customer_said: data.get('customer_said'), team_said: data.get('team_said'),
            next_action: data.get('next_action'), next_steps: data.get('next_steps'), due_date: data.get('due_date'),
            due_time: data.get('due_time') || null, priority: data.get('priority'),
            assigned_agent: data.get('assigned_agent') ? Number(data.get('assigned_agent')) : null,
          });
          form.reset();
          setSaved(`Follow-up scheduled for ${data.get('due_date')}. It now appears in the Follow-ups calendar.`);
        });
      }}>
        <label>What the customer said<textarea name="customer_said" rows={3} maxLength={4000} placeholder="Requirements, questions, product interests, concerns or objections" /></label>
        <label>What we told the customer<textarea name="team_said" rows={3} maxLength={4000} placeholder="Information shared, pricing discussed, promises or commitments" /></label>
        <label>What happens next<select name="next_action" defaultValue="call">{Object.entries(NEXT_ACTIONS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
        <label>Next-step details<textarea name="next_steps" rows={2} maxLength={4000} /></label>
        <div className="grid gap-3 sm:grid-cols-2">
          <label>Follow-up date<input type="date" name="due_date" required defaultValue={today()} /></label>
          <label>Time (optional)<input type="time" name="due_time" /></label>
          <label>Assigned to<select name="assigned_agent" defaultValue={conversation.assigned_agent || ''}>
            <option value="">{conversation.assigned_agent_name || 'Conversation owner / me'}</option>
            {team?.map(member => <option key={member.user_id} value={member.user_id}>{member.username}</option>)}
          </select></label>
          <label>Priority<select name="priority" defaultValue="">{Object.entries(PRIORITIES).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
        </div>
        <button className="btn primary" disabled={busy}>{busy ? 'Saving…' : 'Save follow-up'}</button>
      </form>
      <h3 className="font-semibold mt-5">Follow-up history</h3>
      {list.loading && !list.data ? <p className="muted">Loading follow-ups…</p>
        : !list.data?.results?.length ? <p className="muted">No follow-ups yet for this customer.</p>
          : <div className="space-y-3">{list.data.results.map(item => <FollowUpCard key={item.id} item={item} base={base} />)}</div>}
    </section>
  </div>;
}

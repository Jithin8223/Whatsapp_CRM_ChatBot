import { useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { api } from '../api.js';
import { useAuth } from '../auth.js';
import { showWhatsAppUi } from '../features.js';
import { useAction, useResource, useWorkspacePath } from '../hooks.js';
import { Avatar, Empty, ErrorNotice, PageHeading, Pager, Status, contactName } from '../components.jsx';

function ContactDetail({ id, base }) {
  const { data, error } = useResource(`${base}contacts/${id}/`);
  const { data: history } = useResource(`${base}conversations/?contact=${id}`);
  const whatsappEnabled = useAuth(s => s.whatsappEnabled) && showWhatsAppUi;
  const canStart = whatsappEnabled && Boolean(data?.whatsapp_id);
  const { data: connections } = useResource(canStart ? base + 'connections/' : null);
  const { run, busy, error: actionError } = useAction();
  const navigate = useNavigate();
  return <section className="panel contact-detail"><Link className="text-link" to="/contacts">← All contacts</Link><ErrorNotice>{error || actionError}</ErrorNotice>{data && <><div className="mt-6 flex gap-4 items-center"><Avatar name={contactName(data)} /><div><h2>{contactName(data)}</h2><p className="muted text-sm">{data.whatsapp_id}</p></div></div><form className="mt-6" onSubmit={event => { event.preventDefault(); const values = Object.fromEntries(new FormData(event.currentTarget)); run(() => api.patch(`${base}contacts/${id}/`, values)); }}><label>Display name<input name="display_name" defaultValue={data.display_name} maxLength={120} required /></label><button disabled={busy} className="btn secondary">Save name</button></form>{canStart && <form className="mt-7" onSubmit={event => { event.preventDefault(); const connection = new FormData(event.currentTarget).get('connection'); run(async () => { const { data: conversation } = await api.post(base + 'conversations/', { contact: id, connection }); navigate(`/inbox/${conversation.id}`); }); }}><label>Start or open conversation<select name="connection" required defaultValue=""><option value="" disabled>Choose a connection</option>{connections?.map(item => <option key={item.id} value={item.id}>{item.name} · {item.state}</option>)}</select></label><button disabled={busy || !connections?.length} className="btn primary">Open conversation →</button></form>}<h3 className="mt-8 font-semibold">Conversation history</h3>{history?.results?.length ? history.results.map(item => <Link key={item.id} className="connection-mini" to={`/inbox/${item.id}`}><span>{item.connection_name}</span><Status value={item.status} /></Link>) : <p className="muted text-sm mt-3">No conversations yet.</p>}</>}</section>;
}

export default function Contacts() {
  const base = useWorkspacePath();
  const { contactId } = useParams();
  const [search, setSearch] = useState('');
  const [page, setPage] = useState(1);
  const [showCreate, setShowCreate] = useState(false);
  const whatsappEnabled = useAuth(s => s.whatsappEnabled) && showWhatsAppUi;
  const { data, loading, error } = useResource(`${base}contacts/?search=${encodeURIComponent(search)}&page=${page}`);
  const { run, busy, error: actionError } = useAction();
  const navigate = useNavigate();
  return <><PageHeading eyebrow="THE PEOPLE BEHIND THE MESSAGES" title="Your customer directory." action={whatsappEnabled && <button className="btn primary" onClick={() => setShowCreate(!showCreate)}>＋ Add WhatsApp contact</button>}>Keep customer details and conversation history close at hand.</PageHeading><ErrorNotice>{error || actionError}</ErrorNotice>{whatsappEnabled && showCreate && <form className="panel inline-form" onSubmit={event => { event.preventDefault(); const values = Object.fromEntries(new FormData(event.currentTarget)); run(async () => { const { data: contact } = await api.post(base + 'contacts/', values); setShowCreate(false); navigate(`/contacts/${contact.id}`); }); }}><label>Display name<input name="display_name" maxLength={120} placeholder="Customer name" /></label><label>WhatsApp identifier<input name="whatsapp_id" placeholder="919876543210@c.us" required /></label><button className="btn primary" disabled={busy}>Save contact</button></form>}{contactId ? <ContactDetail key={contactId} id={contactId} base={base} /> : <section className="panel"><div className="section-heading"><h2>All contacts <span className="muted text-sm">({data?.count ?? '—'})</span></h2><input className="directory-search" aria-label="Search contacts" placeholder="Search name or number…" value={search} onChange={event => { setSearch(event.target.value); setPage(1); }} /></div>{loading && !data ? <p className="muted p-6">Loading contacts…</p> : !data?.results?.length ? <Empty title="Your customers belong here">Contacts appear when customers start a conversation.</Empty> : <div className="table-scroll"><table><thead><tr><th>Contact</th><th>Channel</th><th>Added</th><th /></tr></thead><tbody>{data.results.map(contact => <tr key={contact.id}><td><Link className="flex items-center gap-3 font-medium" to={`/contacts/${contact.id}`}><Avatar name={contactName(contact)} small />{contactName(contact)}</Link></td><td>{contact.whatsapp_id ? "WhatsApp" : "Website guest"}</td><td>{new Date(contact.created_at).toLocaleDateString()}</td><td><Link className="text-link" to={`/contacts/${contact.id}`}>View →</Link></td></tr>)}</tbody></table></div>}<Pager page={page} setPage={setPage} data={data} /></section>}</>;
}

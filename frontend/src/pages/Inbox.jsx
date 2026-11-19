import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { api, errorText } from '../api.js';
import { useAuth } from '../auth.js';
import { showWhatsAppUi, showWebsiteChatUi } from '../features.js';
import { useAction, useResource, useWorkspacePath } from '../hooks.js';
import { Avatar, DeliveryTicks, ErrorNotice, Icon, Pager } from '../components.jsx';
import { CRM_STATUSES, FollowUpCard, FollowUpModal } from '../FollowUps.jsx';
import { ACCEPTED_FILES, checkFile, customerIdentifier, customerName, deliveryState, formatBytes, groupByDay, modeBadge, relativeTime } from '../inbox.js';
import { readDetailsHidden, writeDetailsHidden } from '../preferences.js';

const FILTERS = [['', 'All'], ['unassigned', 'Unassigned'], ['open', 'Open'], ['unanswered', 'Unanswered'], ['not_interested', 'Not Interested'], ['converted', 'Converted']];
// Where an automatic reply came from. Only "generative" text is written by an external model.
const SOURCE_LABELS = { menu: 'Chatbot menu', knowledge: 'Knowledge base', generative: 'Generative AI', intake: 'Chatbot intake', fallback: 'Chatbot fallback', handoff: 'Handoff notice' };
const LEAD_STATUS = { new: 'New', follow_up: 'Needs follow-up', qualified: 'Qualified', converted: 'Converted', closed: 'Closed' };
const shortTime = value => new Date(value).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });

function ChannelIcon({ channel, simulated }) {
  const label = simulated ? 'WhatsApp simulation' : channel === 'website' ? 'Website chat' : 'WhatsApp';
  return <span className={`ix-channel ${channel === 'website' ? 'web' : 'wa'}`} title={label} aria-label={label}><Icon name={channel === 'website' ? 'globe' : 'whatsapp'} className="h-3.5 w-3.5" /></span>;
}

function StatusBadge({ value }) {
  return <span className={`ix-badge status-${value}`}>{CRM_STATUSES[value] || value}</span>;
}

function ModeBadge({ conversation }) {
  const mode = modeBadge(conversation);
  return mode && <span className={`ix-badge mode-${mode.tone}`} title={conversation.automation_blocker || mode.label}><Icon name={mode.icon} className="h-3.5 w-3.5" />{mode.label}</span>;
}

// ---- Left panel ---------------------------------------------------------------------------
function ConversationList({ base, selectedId }) {
  const [params, setParams] = useSearchParams();
  const [search, setSearch] = useState('');
  const [query, setQuery] = useState('');
  const [page, setPage] = useState(1);
  const crmStatus = params.get('status') || '';
  const mine = params.get('mine') === '1';
  const channel = showWhatsAppUi && showWebsiteChatUi ? params.get('channel') || '' : showWhatsAppUi ? 'whatsapp' : 'website';
  const update = values => { const next = new URLSearchParams(params); Object.entries(values).forEach(([k, v]) => v ? next.set(k, v) : next.delete(k)); setParams(next); setPage(1); };
  useEffect(() => { const timer = setTimeout(() => { setQuery(search.trim()); setPage(1); }, 300); return () => clearTimeout(timer); }, [search]);
  const url = `${base}conversations/?search=${encodeURIComponent(query)}&channel=${channel}&crm_status=${crmStatus}&assignee=${mine ? 'me' : ''}&page=${page}`;
  const { data, loading, error } = useResource(url);
  const suffix = params.toString() ? `?${params}` : '';
  return <aside className="ix-list" aria-label="Conversations">
    <div className="ix-list-head">
      <div className="flex items-center justify-between"><h1>Conversations <span>{data?.count ?? '—'}</span></h1><Link to="/contacts" className="ix-icon-btn" aria-label="Start a conversation from contacts" title="New conversation">＋</Link></div>
      <label className="ix-search"><Icon name="search" className="h-4 w-4" /><input placeholder="Search name, number or guest #" aria-label="Search conversations" value={search} onChange={event => setSearch(event.target.value)} /></label>
      <div className="ix-chips" role="tablist" aria-label="Filter by status">{FILTERS.map(([value, label]) => <button key={value || 'all'} role="tab" aria-selected={crmStatus === value} className={crmStatus === value ? 'active' : ''} onClick={() => update({ status: value })}>{label}</button>)}</div>
      <div className="ix-list-tools">
        {showWhatsAppUi && showWebsiteChatUi && <select aria-label="Channel" value={channel} onChange={event => update({ channel: event.target.value })}><option value="">All channels</option><option value="whatsapp">WhatsApp</option><option value="website">Website</option></select>}
        <label className="ix-toggle"><input type="checkbox" checked={mine} onChange={event => update({ mine: event.target.checked ? '1' : '' })} /> Mine</label>
      </div>
    </div>
    <ErrorNotice>{error}</ErrorNotice>
    <div className="ix-list-scroll">
      {loading && !data ? Array.from({ length: 6 }, (_, index) => <div key={index} className="ix-card skeleton" aria-hidden="true" />)
        : !data?.results?.length ? <div className="ix-empty"><Icon name="inbox" className="h-8 w-8" /><p>No conversations match.</p><small>New WhatsApp and website messages appear here automatically.</small></div>
          : data.results.map(item => <Link key={item.id} to={`/inbox/${item.id}${suffix}`} className={`ix-card ${item.id === selectedId ? 'active' : ''} ${item.unread_count ? 'is-unread' : ''}`} aria-current={item.id === selectedId ? 'page' : undefined}>
            <div className="ix-card-avatar"><Avatar name={customerName(item.contact)} small /><ChannelIcon channel={item.channel} simulated={item.simulated} /></div>
            <div className="ix-card-body">
              <div className="ix-card-row"><strong className="truncate">{customerName(item.contact)}</strong><time dateTime={item.last_activity_at}>{relativeTime(item.last_activity_at)}</time></div>
              <div className="ix-card-id truncate">{customerIdentifier(item.contact, item.channel)}</div>
              <p className="truncate ix-preview">{item.last_message && (() => { const tick = deliveryState(item.last_message); return tick && <DeliveryTicks state={tick.state} label={tick.label} />; })()}{item.last_message?.direction === 'outbound' && <span className="muted">{item.last_message.automated ? 'Bot: ' : 'You: '}</span>}{item.last_message?.content || 'No messages yet'}</p>
              <div className="ix-card-meta">
                <StatusBadge value={item.crm_status} />
                {item.pending_followups > 0 && <span className="ix-meta" title={`Next follow-up ${item.next_followup_date}`}><Icon name="calendar" className="h-3.5 w-3.5" />{item.next_followup_date}</span>}
                {item.last_viewed_by_name && <span className="ix-meta truncate" title="Last opened by"><Icon name="user" className="h-3.5 w-3.5" />{item.last_viewed_by_name}</span>}
                {item.unread_count > 0 && <b className="ix-unread" aria-label={`${item.unread_count} unread`}>{item.unread_count}</b>}
              </div>
            </div>
          </Link>)}
    </div>
    <Pager page={page} setPage={setPage} data={data} />
  </aside>;
}

// ---- Attachments ------------------------------------------------------------------------------
function useBlobUrl(path, enabled) {
  const [url, setUrl] = useState('');
  useEffect(() => {
    if (!enabled) return undefined;
    let objectUrl = '', cancelled = false;
    api.get(path, { responseType: 'blob', timeout: 60000 }).then(({ data }) => {
      if (cancelled) return;
      objectUrl = URL.createObjectURL(data);
      setUrl(objectUrl);
    }).catch(() => {});
    return () => { cancelled = true; if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [path, enabled]);
  return url;
}

async function downloadAttachment(base, conversationId, attachment) {
  const { data } = await api.get(`${base}conversations/${conversationId}/attachments/${attachment.id}/download/`, { responseType: 'blob', timeout: 60000 });
  const url = URL.createObjectURL(data);
  const link = document.createElement('a');
  link.href = url;
  link.download = attachment.filename;
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function AttachmentView({ base, conversationId, attachment }) {
  const [error, setError] = useState('');
  const preview = useBlobUrl(`${base}conversations/${conversationId}/attachments/${attachment.id}/download/?inline=1`, attachment.kind === 'image');
  const save = () => downloadAttachment(base, conversationId, attachment).catch(failure => setError(errorText(failure)));
  return <div className="ix-file">
    {attachment.kind === 'image' && (preview ? <button type="button" className="ix-file-preview" onClick={save} title="Download"><img src={preview} alt={attachment.filename} /></button> : <div className="ix-file-preview loading" aria-label="Loading preview" />)}
    <div className="ix-file-row"><Icon name="file" className="h-4 w-4" /><span className="truncate" title={attachment.filename}>{attachment.filename}</span><small>{formatBytes(attachment.size)}</small>
      <button type="button" className="ix-icon-btn small" onClick={save} aria-label={`Download ${attachment.filename}`} title="Download"><Icon name="download" className="h-4 w-4" /></button></div>
    {error && <small className="text-danger">{error}</small>}
  </div>;
}

function AttachmentsDrawer({ base, conversationId, close }) {
  const list = useResource(`${base}conversations/${conversationId}/attachments/`);
  return <div className="ix-drawer-backdrop" onClick={event => { if (event.target === event.currentTarget) close(); }} onKeyDown={event => { if (event.key === 'Escape') close(); }}>
    <aside className="ix-drawer" role="dialog" aria-modal="true" aria-labelledby="attachments-title">
      <div className="ix-drawer-head"><h2 id="attachments-title">Attachments</h2><button className="ix-icon-btn" onClick={close} aria-label="Close attachments" autoFocus><Icon name="close" className="h-4 w-4" /></button></div>
      <ErrorNotice>{list.error}</ErrorNotice>
      {list.loading && !list.data ? <p className="muted">Loading files…</p> : !list.data?.length ? <p className="muted">No files have been shared in this conversation. Use the paperclip in the composer to send one.</p>
        : <div className="space-y-3">{list.data.map(item => <div key={item.id}><AttachmentView base={base} conversationId={conversationId} attachment={item} /><small className="muted">Sent by {item.uploaded_by || 'team'} · {new Date(item.created_at).toLocaleString()}</small></div>)}</div>}
      <p className="muted text-xs mt-4">Files are stored privately and only workspace members (and, for website chats, the visitor) can open them.</p>
    </aside>
  </div>;
}

// ---- Center panel -------------------------------------------------------------------------
function Message({ message, base, conversationId, role, run, busy }) {
  const outbound = message.direction === 'outbound';
  const delivery = deliveryState(message);
  const who = message.sender_type === 'customer' ? 'Customer' : message.automated ? 'Chatbot' : message.sender_type === 'admin' ? 'Admin' : 'Agent';
  return <div className={`ix-msg ${outbound ? 'out' : 'in'} ${message.automated ? 'bot' : ''}`}>
    <div className="ix-bubble">
      {outbound && <div className="ix-msg-from">{message.automated ? <Icon name="bot" className="h-3.5 w-3.5" /> : <Icon name="user" className="h-3.5 w-3.5" />}{who}{message.reply_source && ` · ${SOURCE_LABELS[message.reply_source] || message.reply_source}`}</div>}
      {message.attachment && <AttachmentView base={base} conversationId={conversationId} attachment={message.attachment} />}
      {!(message.attachment && message.content.startsWith('📎')) && <p>{message.content}</p>}
      <div className="ix-msg-meta"><time dateTime={message.created_at} title={new Date(message.created_at).toLocaleString()}>{shortTime(message.provider_timestamp || message.created_at)}</time>
        {delivery && <DeliveryTicks state={delivery.state} label={delivery.label} />}</div>
      {message.last_error && <small className="ix-msg-error">{message.last_error}</small>}
      {message.send_state === 'unknown' && role === 'admin' && <div className="ix-resolve"><span>After checking WhatsApp:</span>{['confirmed_sent', 'confirmed_not_sent'].map(outcome => <button key={outcome} className="btn ghost compact" disabled={busy} onClick={() => { if (window.confirm(`Confirm this message was ${outcome === 'confirmed_sent' ? 'sent' : 'not sent'}? Check WhatsApp first. This will not resend it.`)) run(() => api.post(`${base}conversations/${conversationId}/resolve-send/`, { message_id: message.id, outcome })); }}>{outcome === 'confirmed_sent' ? 'It was sent' : 'It was not sent'}</button>)}</div>}
    </div>
  </div>;
}

function Composer({ base, conversation, blockedReason, onSent }) {
  const [text, setText] = useState('');
  const [file, setFile] = useState(null);
  const [progress, setProgress] = useState(null);
  const [error, setError] = useState('');
  const [sending, setSending] = useState(false);
  const requestId = useRef(null);
  const input = useRef(null);
  const disabled = Boolean(blockedReason);
  const choose = event => {
    const chosen = event.target.files?.[0];
    event.target.value = '';
    if (!chosen) return;
    const problem = checkFile(chosen);
    setError(problem);
    if (!problem) { setFile(chosen); requestId.current = null; }
  };
  async function send(event) {
    event.preventDefault();
    if (disabled || sending || (!text.trim() && !file)) return;
    requestId.current ||= crypto.randomUUID();
    setSending(true); setError('');
    try {
      if (file) {
        const form = new FormData();
        form.append('file', file);
        form.append('client_id', requestId.current);
        form.append('caption', text.trim());
        setProgress(0);
        await api.post(`${base}conversations/${conversation.id}/attachments/`, form, {
          timeout: 120000, onUploadProgress: progressEvent => setProgress(progressEvent.total ? Math.round(progressEvent.loaded / progressEvent.total * 100) : null),
        });
      } else {
        await api.post(`${base}conversations/${conversation.id}/messages/`, { content: text.trim(), client_id: requestId.current });
      }
      requestId.current = null; setText(''); setFile(null);
      onSent();
      useAuth.getState().refresh();
    } catch (failure) { setError(errorText(failure)); } finally { setSending(false); setProgress(null); }
  }
  return <form className="ix-composer" onSubmit={send}>
    <ErrorNotice>{error}</ErrorNotice>
    {file && <div className="ix-upload"><Icon name="file" className="h-4 w-4" /><span className="truncate">{file.name}</span><small>{formatBytes(file.size)}</small>
      {progress != null ? <progress max={100} value={progress} aria-label="Upload progress">{progress}%</progress>
        : <button type="button" className="ix-icon-btn small" onClick={() => setFile(null)} aria-label="Remove file"><Icon name="close" className="h-3.5 w-3.5" /></button>}</div>}
    <div className="ix-composer-row">
      <input ref={input} type="file" accept={ACCEPTED_FILES} hidden onChange={choose} />
      <button type="button" className="ix-icon-btn" disabled={disabled || sending} onClick={() => input.current?.click()} aria-label="Attach a file" title="Attach image or document"><Icon name="paperclip" className="h-5 w-5" /></button>
      <label className="sr-only" htmlFor="message-input">Message</label>
      <textarea id="message-input" rows={1} placeholder={blockedReason || (file ? 'Add a caption (optional)…' : 'Write a reply…')} maxLength={file ? 1000 : 4096} value={text} disabled={disabled || sending}
        onChange={event => { setText(event.target.value); requestId.current = null; }}
        onKeyDown={event => { if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); event.currentTarget.form.requestSubmit(); } }} />
      <button className="btn primary" disabled={disabled || sending || (!text.trim() && !file)}>{sending ? (progress != null ? `${progress ?? 0}%` : 'Sending…') : 'Send'} <Icon name="arrow" className="h-4 w-4" /></button>
    </div>
    <small className="ix-hint">Enter to send · Shift + Enter for a new line · Images, PDF, Word, Excel up to 10 MB</small>
  </form>;
}

function Chat({ id, base, detailsHidden, toggleDetails }) {
  const { data: conversation, error, loading } = useResource(`${base}conversations/${id}/`);
  const latest = useResource(`${base}conversations/${id}/messages/?page=1`);
  const { data: team } = useResource(base + 'team/');
  const { busy, error: actionError, run } = useAction();
  const [older, setOlder] = useState({ pages: [], next: 2, loading: false, done: false });
  const [followUpOpen, setFollowUpOpen] = useState(false);
  const [filesOpen, setFilesOpen] = useState(false);
  const [mobileDetails, setMobileDetails] = useState(false);
  const timeline = useRef(null);
  const nearBottom = useRef(true);
  const user = useAuth(s => s.user);
  const role = useAuth(s => s.workspaces.find(w => w.id === s.workspaceId)?.role);
  const navigate = useNavigate();
  // Record that this member opened the chat (server de-duplicates refreshes). Never claims it.
  useEffect(() => { api.post(`${base}conversations/${id}/view/`).catch(() => {}); }, [base, id]);
  // Team read policy: incoming messages count as seen only while this chat is on a visible tab.
  useEffect(() => {
    if (!conversation?.unread_count) return undefined;
    const markSeen = () => { if (document.visibilityState === 'visible') api.post(`${base}conversations/${id}/read/`).catch(() => {}); };
    markSeen();
    document.addEventListener('visibilitychange', markSeen);
    return () => document.removeEventListener('visibilitychange', markSeen);
  }, [base, id, conversation?.unread_count, conversation?.last_message?.id]);
  const messages = [...older.pages.flat(), ...[...(latest.data?.results || [])].reverse()]
    .filter((item, index, all) => all.findIndex(other => other.id === item.id) === index);
  const hasMore = !older.done && (older.pages.length ? true : Boolean(latest.data?.next));
  async function loadOlder() {
    const element = timeline.current;
    const before = element?.scrollHeight || 0;
    setOlder(state => ({ ...state, loading: true }));
    try {
      const { data } = await api.get(`${base}conversations/${id}/messages/?page=${older.next}`);
      setOlder(state => ({ pages: [[...data.results].reverse(), ...state.pages], next: state.next + 1, loading: false, done: !data.next }));
      requestAnimationFrame(() => { if (element) element.scrollTop = element.scrollHeight - before; });
    } catch { setOlder(state => ({ ...state, loading: false })); }
  }
  // Stay pinned to the newest message unless the reader scrolled up to read history.
  useLayoutEffect(() => {
    const element = timeline.current;
    if (element && nearBottom.current) element.scrollTop = element.scrollHeight;
  }, [latest.data, id]);
  const onScroll = () => { const element = timeline.current; nearBottom.current = element.scrollHeight - element.scrollTop - element.clientHeight < 120; };

  if (error) return <section className="ix-chat"><div className="ix-empty"><ErrorNotice>{error}</ErrorNotice><Link className="btn secondary" to="/inbox">Back to conversations</Link></div></section>;
  if (!conversation) return <section className="ix-chat" aria-busy="true"><div className="ix-chat-head skeleton" /><div className="ix-timeline">{loading && Array.from({ length: 5 }, (_, i) => <div key={i} className={`ix-msg ${i % 2 ? 'out' : 'in'}`}><div className="ix-bubble skeleton" /></div>)}</div></section>;

  const update = values => run(() => api.patch(`${base}conversations/${id}/`, values));
  const canManage = !conversation.assigned_agent || conversation.assigned_agent === user.id || role === 'admin';
  const whatsapp = conversation.channel === 'whatsapp';
  const disconnected = whatsapp && !conversation.simulated && !conversation.whatsapp_connected;
  const blockedReason = conversation.status !== 'open' ? 'Reopen this conversation to reply'
    : disconnected ? 'WhatsApp is not connected'
    : !canManage ? `Assigned to ${conversation.assigned_agent_name}`
    : conversation.reply_mode !== 'manual' || !conversation.assigned_agent ? 'Use Human takeover to reply yourself' : '';
  const name = customerName(conversation.contact);
  const detailsVisible = !detailsHidden || mobileDetails;
  return <>
    <section className="ix-chat" aria-label={`Conversation with ${name}`}>
      <header className="ix-chat-head">
        <Link to="/inbox" className="ix-back" aria-label="Back to conversations"><Icon name="collapse" className="h-5 w-5" /></Link>
        <div className="ix-identity"><Avatar name={name} /><div className="min-w-0"><h2 className="truncate">{name}</h2><span className="ix-sub"><ChannelIcon channel={conversation.channel} simulated={conversation.simulated} /><span className="truncate">{customerIdentifier(conversation.contact, conversation.channel)}</span></span></div></div>
        <div className="ix-head-end">
          <ModeBadge conversation={conversation} />
          <button type="button" className="ix-icon-btn ix-details-toggle" onClick={() => { if (window.matchMedia('(max-width: 1279px)').matches) setMobileDetails(!mobileDetails); else toggleDetails(); }} aria-pressed={detailsVisible} aria-label={detailsVisible ? 'Hide customer details' : 'Show customer details'} title="Customer details"><Icon name="panel" className="h-5 w-5" /></button>
        </div>
        <div className="ix-head-controls">
          <span className="ix-lastviewed" title={conversation.last_viewed_at ? new Date(conversation.last_viewed_at).toLocaleString() : ''}><span className="muted">Last opened by</span> <strong className="truncate">{conversation.last_viewed_by_name || '—'}</strong></span>
          <label className="ix-inline-field"><span className="sr-only">Conversation status</span><select aria-label="Conversation status" className={`ix-status-select status-${conversation.crm_status}`} value={conversation.crm_status} disabled={busy || !canManage} onChange={event => update({ crm_status: event.target.value })}>{Object.entries(CRM_STATUSES).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
          <button type="button" className="ix-icon-btn" onClick={() => setFollowUpOpen(true)} aria-label={`Follow-ups (${conversation.followups?.pending || 0} pending)`} title="Follow-ups"><Icon name="calendar" className="h-5 w-5" />{conversation.followups?.pending > 0 && <b className="ix-dot">{conversation.followups.pending}</b>}</button>
          <button type="button" className="ix-icon-btn" onClick={() => setFilesOpen(true)} aria-label="Attachments" title="Attachments"><Icon name="paperclip" className="h-5 w-5" /></button>
        </div>
      </header>
      <div className="ix-actions" role="toolbar" aria-label="Conversation actions">
        <span className="ix-assigned"><span className="muted">Assigned</span> {role === 'admin'
          ? <select aria-label="Assigned team member" value={conversation.assigned_agent || ''} disabled={busy} onChange={event => update({ assigned_agent: event.target.value ? Number(event.target.value) : null })}><option value="">Unassigned</option>{team?.map(m => <option key={m.id} value={m.user_id}>{m.username}</option>)}</select>
          : <strong>{conversation.assigned_agent_name || 'Unassigned'}</strong>}</span>
        {!conversation.assigned_agent && <button className="btn primary compact" disabled={busy} onClick={() => run(() => api.post(`${base}conversations/${id}/claim/`))}>Claim conversation</button>}
        <span className="ix-spacer" />
        {conversation.reply_mode !== 'manual' && <button className="btn secondary compact" disabled={busy || !canManage || conversation.status !== 'open'} onClick={() => run(() => api.post(`${base}conversations/${id}/takeover/`))} title="Stop automatic replies and reply yourself">Human takeover</button>}
        {conversation.reply_mode !== 'ai' && <button className="btn secondary compact" disabled={busy || !canManage || !conversation.automation_available || conversation.status !== 'open'} onClick={() => run(() => api.post(`${base}conversations/${id}/resume-ai/`))} title={conversation.automation_blocker || 'Let the chatbot reply automatically again'}>Resume AI</button>}
        {conversation.reply_mode !== 'assisted' && conversation.automation_available && <button className="btn ghost compact" disabled={busy || !canManage || conversation.status !== 'open'} onClick={() => update({ reply_mode: 'assisted' })} title="AI writes drafts that need approval">AI drafts</button>}
        <button className="btn ghost compact" disabled={busy} onClick={() => update({ status: conversation.status === 'open' ? 'closed' : 'open' })}>{conversation.status === 'open' ? 'Resolve' : 'Reopen'}</button>
      </div>
      <ErrorNotice>{actionError || latest.error}</ErrorNotice>
      {conversation.automation_blocker && conversation.reply_mode !== 'manual' && <div className="ix-banner"><Icon name="alert" className="h-4 w-4" />{conversation.automation_blocker}</div>}
      {conversation.reply_failure && <div className="ix-banner bad" role="alert"><Icon name="alert" className="h-4 w-4" /><span><strong>Automatic reply failed.</strong> The customer did not get an automatic answer to their last message. Reply manually or ask them to resend. <small>({conversation.reply_failure.error})</small></span></div>}
      {conversation.ai_draft && <div className="ix-banner draft"><div className="min-w-0"><strong>AI draft for review</strong><p className="whitespace-pre-wrap">{conversation.ai_draft.content}</p></div><button className="btn primary compact" disabled={busy || conversation.status !== 'open' || conversation.reply_mode !== 'assisted' || disconnected} onClick={() => run(() => api.post(`${base}conversations/${id}/approve-draft/`))}>Approve and send</button></div>}
      <div className="ix-timeline" ref={timeline} onScroll={onScroll} role="log" aria-live="polite" aria-label="Messages">
        {hasMore && <div className="ix-older"><button className="btn ghost compact" disabled={older.loading} onClick={loadOlder}>{older.loading ? 'Loading…' : 'Load earlier messages'}</button></div>}
        {latest.loading && !latest.data ? <p className="muted text-center">Loading messages…</p>
          : !messages.length ? <div className="ix-empty"><Icon name="inbox" className="h-8 w-8" /><p>No messages yet.</p></div>
            : groupByDay(messages).map(group => <div key={group.day}>
              <div className="ix-day" role="separator"><span>{group.label}</span></div>
              {group.messages.map(message => <Message key={message.id} message={message} base={base} conversationId={id} role={role} run={run} busy={busy} />)}
            </div>)}
        {conversation.ai_pending && <div className="ix-msg out bot"><div className="ix-bubble typing" role="status"><span /><span /><span /> Chatbot is replying…</div></div>}
      </div>
      <Composer base={base} conversation={conversation} blockedReason={blockedReason} onSent={() => { nearBottom.current = true; }} />
    </section>
    {detailsVisible && <CustomerDetails conversation={conversation} base={base} team={team} admin={role === 'admin'} busy={busy} update={update}
      close={() => { if (mobileDetails) setMobileDetails(false); else toggleDetails(); }} openFollowUps={() => setFollowUpOpen(true)}
      onDeleteGuest={() => { if (window.confirm('Permanently delete this guest conversation and revoke its session?')) run(async () => { await api.delete(`${base}conversations/${id}/guest-data/`); navigate('/inbox'); }); }}
      overlay={mobileDetails} />}
    {followUpOpen && <FollowUpModal conversation={conversation} base={base} team={team} close={() => setFollowUpOpen(false)} />}
    {filesOpen && <AttachmentsDrawer base={base} conversationId={id} close={() => setFilesOpen(false)} />}
  </>;
}

// ---- Right panel ----------------------------------------------------------------------------
function CustomerDetails({ conversation, base, admin, busy, update, close, openFollowUps, onDeleteGuest, overlay }) {
  const [note, setNote] = useState(null);
  const viewers = useResource(admin ? `${base}conversations/${conversation.id}/views/` : null);
  const followups = useResource(`${base}followups/?conversation=${conversation.id}&order=recent`);
  const contact = conversation.contact;
  const pending = followups.data?.results?.filter(item => item.status === 'pending') || [];
  const next = [...pending].sort((a, b) => (a.due_date + (a.due_time || '')).localeCompare(b.due_date + (b.due_time || '')))[0];
  return <aside className={`ix-details ${overlay ? 'overlay' : ''}`} aria-label="Customer details">
    <div className="ix-details-head"><h2>Customer details</h2><button className="ix-icon-btn small" onClick={close} aria-label="Hide customer details"><Icon name="close" className="h-4 w-4" /></button></div>
    <div className="ix-details-scroll">
      <section className="ix-section ix-profile"><Avatar name={customerName(contact)} /><div className="min-w-0"><strong className="truncate">{customerName(contact)}</strong><span className="truncate">{conversation.channel === 'website' ? 'Website visitor' : 'WhatsApp customer'}</span></div></section>
      <section className="ix-section"><dl className="ix-dl">
        {contact.guest_label && <><dt>Visitor ID</dt><dd>{contact.guest_label}</dd></>}
        <dt>{conversation.channel === 'website' ? 'Contact' : 'Phone'}</dt><dd>{contact.phone || contact.email || <span className="muted">Not provided</span>}</dd>
        {conversation.channel === 'whatsapp' && <><dt>WhatsApp ID</dt><dd className="break-all">{contact.whatsapp_id}</dd></>}
        {contact.email && contact.phone && <><dt>Email</dt><dd className="break-all">{contact.email}</dd></>}
        <dt>First contact</dt><dd>{new Date(conversation.created_at).toLocaleDateString()}</dd>
      </dl></section>
      <section className="ix-section"><h3>Ownership</h3><dl className="ix-dl">
        <dt>Assigned</dt><dd>{conversation.assigned_agent_name || 'Unassigned'}</dd>
        <dt>Last opened by</dt><dd>{conversation.last_viewed_by_name ? `${conversation.last_viewed_by_name} · ${relativeTime(conversation.last_viewed_at)}` : '—'}</dd>
        <dt>Status</dt><dd><StatusBadge value={conversation.crm_status} /></dd>
        <dt>Automation</dt><dd><ModeBadge conversation={conversation} /></dd>
      </dl>
        {admin && viewers.data?.length > 0 && <details className="ix-history"><summary>Viewing history ({viewers.data.length})</summary><ul>{viewers.data.map(row => <li key={row.user_id}>{row.username} · {new Date(row.last_viewed_at).toLocaleString()}{row.view_count > 1 && ` · ${row.view_count} visits`}</li>)}</ul></details>}
      </section>
      <section className="ix-section"><h3>Lead</h3>
        {conversation.lead ? <div className="ix-lead"><strong>{conversation.lead.product || 'No specific product'}</strong><span className="ix-badge">{LEAD_STATUS[conversation.lead.status] || conversation.lead.status}</span>{conversation.lead.summary && <p className="muted">{conversation.lead.summary}</p>}<Link to="/leads">Open in Leads</Link></div>
          : <p className="muted text-sm">No lead yet. Set the status to Converted to create one.</p>}
      </section>
      <section className="ix-section"><div className="flex items-center justify-between"><h3>Follow-ups</h3><button className="ix-link" onClick={openFollowUps}>Add / manage</button></div>
        {next ? <p className="text-sm"><Icon name="calendar" className="h-4 w-4 inline" /> Next: <strong>{next.due_date}{next.due_time && ` ${next.due_time.slice(0, 5)}`}</strong> · {next.assigned_agent_name || 'Unassigned'}</p> : <p className="muted text-sm">Nothing scheduled.</p>}
        <div className="space-y-2">{followups.data?.results?.slice(0, 4).map(item => <FollowUpCard key={item.id} item={item} base={base} compact />)}</div>
      </section>
      <section className="ix-section"><h3>Internal notes</h3>
        <form onSubmit={event => { event.preventDefault(); if (note !== null) update({ internal_note: note }).then(ok => { if (ok !== null) setNote(null); }); }}>
          <textarea aria-label="Internal notes" rows={4} maxLength={4000} value={note ?? conversation.internal_note ?? ''} onChange={event => setNote(event.target.value)} />
          <button className="btn secondary compact mt-2" disabled={busy || note === null}>Save note</button>
        </form>
      </section>
      {admin && conversation.channel === 'website' && <section className="ix-section"><h3>Guest data</h3><div className="flex flex-wrap gap-2"><a className="btn ghost compact" href={`/api${base}conversations/${conversation.id}/guest-data/`}>Export chat</a><button className="btn ghost compact text-danger" disabled={busy} onClick={onDeleteGuest}>Delete guest chat</button></div></section>}
    </div>
  </aside>;
}

export default function Inbox() {
  const base = useWorkspacePath();
  const { conversationId } = useParams();
  const [detailsHidden, setDetailsHidden] = useState(readDetailsHidden);
  const toggleDetails = () => setDetailsHidden(value => { writeDetailsHidden(!value); return !value; });
  return <div className={`ix ${conversationId ? 'has-chat' : ''} ${detailsHidden ? '' : 'with-details'}`}>
    <ConversationList base={base} selectedId={conversationId} />
    {conversationId ? <Chat key={`${base}${conversationId}`} id={conversationId} base={base} detailsHidden={detailsHidden} toggleDetails={toggleDetails} />
      : <section className="ix-chat ix-placeholder"><div className="ix-empty"><Icon name="inbox" className="h-10 w-10" /><h2>Select a conversation</h2><p>WhatsApp and website chats share this inbox. Pick one on the left to read the history and reply.</p></div></section>}
  </div>;
}

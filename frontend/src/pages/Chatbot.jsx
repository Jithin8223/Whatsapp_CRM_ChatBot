import { useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api.js';
import { useAction, useResource, useWorkspacePath } from '../hooks.js';
import { ErrorNotice, PageHeading, Pager } from '../components.jsx';
import { useAuth } from '../auth.js';
import { showWhatsAppUi } from '../features.js';
import { MENU_ACTIONS, formatMenu, lines, parseMenu } from '../chatbotConfig.js';

function WidgetForm({ widget, save, busy }) {
  const snippet = widget?.public_key
    ? `<script src="${location.origin}/widget.js" data-widget-key="${widget.public_key}" async></script>`
    : '';
  const [copied, setCopied] = useState(false);
  const [originError, setOriginError] = useState('');
  const [origins, setOrigins] = useState(widget?.origins?.join('\n') || '');
  const [preview, setPreview] = useState({
    title: widget?.title || 'Support', welcome: widget?.welcome || 'How can we help?',
    color: widget?.color || '#123b30', suggestions: widget?.suggestions || [],
  });
  const questions = value => String(value ?? '').split('\n').map(question => question.trim()).filter(Boolean);
  return <section className="panel p-5 space-y-4"><h2>Website widget</h2>
    <p className="muted text-sm">{widget?.enabled ? 'Published' : 'Unpublished'}</p>
    <form className="space-y-4" onChange={event => {
      const data = new FormData(event.currentTarget);
      setPreview({ title: data.get('title'), welcome: data.get('welcome'), color: data.get('color'), suggestions: questions(data.get('suggestions')) });
    }} onSubmit={event => {
      event.preventDefault();
      const data = new FormData(event.currentTarget);
      const action = event.nativeEvent.submitter?.value;
      const allowedOrigins = [...new Set(origins.split(/[\s,]+/).filter(Boolean))];
      const enabled = action === 'publish' || (action !== 'unpublish' && Boolean(widget?.enabled));
      if (enabled && !allowedOrigins.length) {
        setOriginError('Add an allowed website origin before publishing.');
        return;
      }
      setOriginError('');
      save({
        title: data.get('title'), welcome: data.get('welcome'), color: data.get('color'),
        origins: allowedOrigins,
        enabled,
        suggestions: questions(data.get('suggestions')),
        ...(showWhatsAppUi && {
          whatsapp_number: data.get('whatsapp_number'), whatsapp_message: data.get('whatsapp_message'),
        }),
      });
    }}>
      <h3 className="font-semibold">Appearance</h3>
      <label>Chat title<input name="title" maxLength={80} required defaultValue={widget?.title || 'Support'} /></label>
      <label>Accent<input name="color" type="color" defaultValue={widget?.color || '#123b30'} /></label>
      <h3 className="font-semibold">Welcome message</h3>
      <label>Greeting<textarea name="welcome" maxLength={240} required defaultValue={widget?.welcome || 'How can we help?'} /></label>
      <h3 className="font-semibold">Suggested questions</h3>
      <label>Up to six questions, one per line<textarea name="suggestions" rows={4} defaultValue={widget?.suggestions?.join('\n')} placeholder="What services do you offer?" /></label>
      <h3 className="font-semibold">Live preview</h3>
      <div className="overflow-hidden rounded-2xl border" aria-label="Live widget preview">
        <div className="px-4 py-3 font-semibold" style={{ background: preview.color, color: inkFor(preview.color) }}>{preview.title || 'Support'}</div>
        <div className="bg-emerald-50 p-4"><p className="rounded-xl bg-white p-3">{preview.welcome}</p>
          <div className="mt-2 flex flex-wrap gap-2">{preview.suggestions.slice(0, 6).map((question, index) => <span className="rounded-full border bg-white px-2 py-1 text-xs" key={index}>{question}</span>)}</div>
        </div>
        <div className="border-t px-4 py-3 muted text-sm">Write a message</div>
      </div>
      <h3 className="font-semibold">Website installation</h3>
      <label>Allowed website origins<textarea name="origins" aria-describedby="origin-help" placeholder="https://support.example" value={origins} onChange={event => { setOrigins(event.target.value); setOriginError(''); }} /></label>
      <div id="origin-help"><ErrorNotice>{originError}</ErrorNotice></div>
      <p className="muted text-sm">Enter exact origins without paths. For local testing, localhost and 127.0.0.1 are separate origins.</p>
      {showWhatsAppUi && <><h3 className="font-semibold">WhatsApp button</h3>
      <label>Optional business WhatsApp number<input name="whatsapp_number" type="tel" maxLength={40} defaultValue={widget?.whatsapp_number} /></label>
      <label>Optional WhatsApp message<input name="whatsapp_message" maxLength={240} defaultValue={widget?.whatsapp_message} /></label>
      <p className="muted text-sm">Use international digits. The link opens WhatsApp to the configured number.</p></>}
      <div className="flex flex-wrap gap-2">
        <button className="btn secondary" name="action" value="save" disabled={busy}>Save changes</button>
        {!widget?.enabled && <button className="btn primary" name="action" value="publish" disabled={busy}>Publish widget</button>}
        {widget?.enabled && <button className="btn ghost" name="action" value="unpublish" disabled={busy}>Unpublish</button>}
      </div>
    </form>
    {snippet && <div className="space-y-2"><label>Installation snippet<textarea readOnly value={snippet} onFocus={event => event.currentTarget.select()} /></label>
      <button className="btn secondary" onClick={async () => {
        try { await navigator.clipboard.writeText(snippet); setCopied(true); }
        catch { setCopied(false); }
      }}>{copied ? 'Copied' : 'Copy snippet'}</button></div>}
  </section>;
}

function inkFor(color) {
  if (!/^#[0-9a-f]{6}$/i.test(color || '')) return '#fff';
  const channels = color.slice(1).match(/../g).map(value => parseInt(value, 16) / 255);
  const [red, green, blue] = channels.map(value => value <= .04045 ? value / 12.92 : ((value + .055) / 1.055) ** 2.4);
  return .2126 * red + .7152 * green + .0722 * blue > .179 ? '#000' : '#fff';
}

function WhatsAppLinkForm({ widget, save, busy }) {
  const snippet = widget?.public_key
    ? `<script src="${location.origin}/whatsapp-link.js" data-widget-key="${widget.public_key}" async></script>`
    : '';
  return <section className="panel p-5 space-y-4">
    <h2>Website WhatsApp button</h2>
    <p className="muted">This opens WhatsApp to your business number. It works independently of CRM messaging.</p>
    {!widget?.whatsapp_number && <p role="status">Save a valid business WhatsApp number to activate the website button.</p>}
    <form className="space-y-3" onSubmit={event => {
      event.preventDefault();
      const data = new FormData(event.currentTarget);
      save({
        whatsapp_number: data.get('number'),
        whatsapp_message: data.get('message'),
        origins: [...new Set(String(data.get('origins') ?? '').split(/[\s,]+/).filter(Boolean))],
      });
    }}>
      <label>Business WhatsApp number<input name="number" type="tel" maxLength={40} defaultValue={widget?.whatsapp_number} /></label>
      <p className="muted text-sm">Include the country code. Spaces, parentheses, and a leading + are accepted. Leave it blank to hide the button.</p>
      <label>Prefilled greeting<input name="message" maxLength={240} defaultValue={widget?.whatsapp_message} /></label>
      <label>Allowed website origins<textarea name="origins" defaultValue={widget?.origins?.join('\n')} placeholder="https://support.example" /></label>
      <button className="btn primary" disabled={busy}>Save WhatsApp button</button>
    </form>
    {snippet && <label>Installation snippet<textarea readOnly value={snippet} onFocus={event => event.currentTarget.select()} /></label>}
  </section>;
}

const STAT_LABELS = { answered: 'Answered', handoff: 'Handed off', draft: 'Drafts awaiting approval', failed: 'Failed' };

function AIForm({ config, busy, save, whatsapp }) {
  const [menuError, setMenuError] = useState('');
  const handoffTerms = Array.isArray(config.whatsapp_handoff_terms) ? config.whatsapp_handoff_terms : [];
  return <section className="panel p-5 space-y-4"><h2>AI and workflows</h2>
    <form className="space-y-3" onSubmit={event => {
      event.preventDefault();
      const data = new FormData(event.currentTarget);
      const parsed = parseMenu(data.get('chatbot_menu'));
      if (parsed.error) { setMenuError(parsed.error); return; }
      setMenuError('');
      save({
        enabled: data.has('enabled'), provider: data.get('provider'), instructions: data.get('instructions') ?? '',
        issue_intake: data.has('issue_intake'), lead_intake: data.has('lead_intake'),
        retention_days: data.get('retention') ? Number(data.get('retention')) : null,
        chatbot_greeting: data.get('chatbot_greeting') ?? '',
        chatbot_menu: parsed.items,
        ...(whatsapp && { whatsapp_handoff_terms: lines(data.get('whatsapp_handoff_terms')) }),
      });
    }}>
      <label><input type="checkbox" name="enabled" defaultChecked={config.enabled} /> Enable automatic replies</label>
      <label>Response provider<select name="provider" defaultValue={config.provider}>
        <option value="knowledge">Knowledge base only</option>
        <option value="openai" disabled={!config.external_available}>OpenAI {!config.external_available && '(server setup required)'}</option>
      </select></label>
      <p className="muted text-sm">Knowledge base only returns approved answers. Generative replies require a configured external model.</p>
      <label>Instructions for the AI model<textarea name="instructions" rows={4} maxLength={3000} defaultValue={config.instructions} /></label>
      <label><input type="checkbox" name="issue_intake" defaultChecked={config.issue_intake} /> Ask for support issue details</label>
      <label><input type="checkbox" name="lead_intake" defaultChecked={config.lead_intake} /> Ask about interests and goals</label>
      <h3 className="font-semibold">Automatic menu and greeting</h3>
      <label>Welcome greeting<textarea name="chatbot_greeting" maxLength={480} defaultValue={config.chatbot_greeting} /></label>
      <p className="muted text-sm">Use {`{business}`} to insert this workspace’s name.</p>
      <label>Main menu options<textarea name="chatbot_menu" rows={7} defaultValue={formatMenu(config.chatbot_menu)} onChange={() => setMenuError('')} /></label>
      <ErrorNotice>{menuError}</ErrorNotice>
      <p className="muted text-sm">One option per line: label | action. Actions: {MENU_ACTIONS.join(', ')}. This menu is shared by website chat, WhatsApp, and simulation.</p>
      {whatsapp && <><h3 className="font-semibold">WhatsApp welcome and escalation</h3>
        <label>Extra handoff phrases<textarea name="whatsapp_handoff_terms" maxLength={730} defaultValue={handoffTerms.join('\n')} /></label>
        <p className="muted text-sm">One phrase per line. A customer message containing one of these phrases is handed to a human. Avoid common words such as “support”, which also appear in ordinary questions. Customers can always type “human”.</p>
        <p className="muted text-sm">WhatsApp also requires AI replies to be enabled on the connection itself (Connection status page).</p>
      </>}
      <div hidden={whatsapp}><h3 className="font-semibold">Privacy and retention</h3>
      <label>Guest retention policy in days<input name="retention" type="number" min={1} max={3650} defaultValue={config.retention_days ?? ''} /></label>
      <p className="muted text-sm">Leave retention blank to keep guest history. An administrator must explicitly run the retention procedure.</p></div>
      <button className="btn primary" disabled={busy}>Save AI settings</button>
    </form>
    <h3>Reply activity</h3>
    {config.stats?.length ? config.stats.map(row => <p key={row.state}>{STAT_LABELS[row.state] || row.state} · {row.count}{row.average_latency_ms != null && ` · ${Math.round(row.average_latency_ms)} ms average`}</p>) : <p className="muted">No automated replies yet.</p>}
    {whatsapp && <><h3>Recent WhatsApp errors</h3>
      {config.recent_errors?.length ? config.recent_errors.map((item, index) =>
        <p key={index}><time>{new Date(item.created_at).toLocaleString()}</time> · {item.last_error}</p>
      ) : <p className="muted">No recent send errors.</p>}</>}
  </section>;
}

export default function Chatbot({ channel = 'website' }) {
  const base = useWorkspacePath();
  const whatsapp = channel === 'whatsapp';
  const workspace = useAuth(state => state.workspaces.find(item => item.id === state.workspaceId));
  const widget = useResource(base + 'widget/');
  const config = useResource(base + 'ai/' + (whatsapp ? '?channel=whatsapp' : ''));
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState('');
  const [query, setQuery] = useState('');
  const knowledge = useResource(base + 'knowledge/?page=' + page + '&search=' + encodeURIComponent(query));
  const { run, busy, error } = useAction();
  const [editing, setEditing] = useState(null);
  return <><PageHeading title={whatsapp ? 'WhatsApp AI' : 'Website chatbot'} action={<Link className="btn secondary" to={whatsapp ? '/connections' : '/inbox?channel=website'}>{whatsapp ? 'Connection status' : 'Open website inbox'}</Link>}>
    {whatsapp ? 'Manage approved answers and the WhatsApp menu for ' : 'Chats from this widget appear in '}{workspace?.name || 'this workspace'}{workspace?.id && ` (${workspace.id.slice(0, 8)})`}.
  </PageHeading>
    <ErrorNotice>{error || widget.error || config.error || knowledge.error}</ErrorNotice>
    <div className="grid gap-5 lg:grid-cols-2">
      {(!widget.loading || widget.data) && !widget.error && (whatsapp
        ? <WhatsAppLinkForm widget={widget.data} busy={busy} save={values => run(() => api.patch(base + 'widget/', values))} />
        : <WidgetForm widget={widget.data} busy={busy} save={values => run(() => api.patch(base + 'widget/', values))} />)}
      {config.data && <AIForm config={config.data} busy={busy} whatsapp={whatsapp} save={values => run(() => api.patch(base + 'ai/', values))} />}
    </div>
    <section className="panel p-5 mt-5 space-y-4"><h2>Business knowledge</h2>
      <form className="flex flex-wrap gap-2" onSubmit={event => { event.preventDefault(); setPage(1); setQuery(search.trim()); }}>
        <input aria-label="Search FAQs" placeholder="Search FAQs" value={search} onChange={event => setSearch(event.target.value)} />
        <button className="btn secondary">Search</button>
        <button type="button" className="btn secondary" disabled={busy} onClick={() => run(async () => {
          await api.post(base + 'knowledge/starter/');
          setPage(1); setSearch(''); setQuery('');
        })}>Add starter drafts</button>
      </form>
      <p className="muted text-sm">Starter questions are inactive until you add approved answers and activate them.</p>
      <form key={editing?.id || 'new'} className="space-y-3" onSubmit={event => {
        event.preventDefault();
        const form = event.currentTarget;
        const values = new FormData(form);
        const data = { question: values.get('question'), answer: values.get('answer'), category: values.get('category'), active: values.has('active') };
        run(async () => {
          if (editing) await api.patch(base + 'knowledge/' + editing.id + '/', data);
          else await api.post(base + 'knowledge/', data);
          form.reset();
          setEditing(null);
        });
      }}>
        <label>Question or topic<input name="question" maxLength={240} required defaultValue={editing?.question} /></label>
        <label>Category<input name="category" maxLength={80} defaultValue={editing?.category} /></label>
        <label>Approved answer<textarea name="answer" maxLength={3000} defaultValue={editing?.answer} /></label>
        <label><input type="checkbox" name="active" defaultChecked={editing?.active ?? true} /> Active</label>
        <button className="btn primary" disabled={busy}>{editing ? 'Save answer' : 'Add answer'}</button>
        {editing && <button type="button" className="btn ghost" onClick={() => setEditing(null)}>Cancel</button>}
      </form>
      {knowledge.data?.count === 0 && <p className="muted">{query ? 'No FAQs match this search.' : 'Add an approved answer or start with draft questions.'}</p>}
      {knowledge.data?.results.map((item, index, results) => <div key={item.id}>
        {(index === 0 || results[index - 1].category !== item.category) && <h3 className="font-semibold mt-4">{item.category || 'Uncategorized'}</h3>}
        <article className="border-t pt-3">
        <h4>{item.question}</h4><small className="muted">{item.active ? 'Active' : 'Inactive'}</small>
        <p className="whitespace-pre-wrap">{item.answer || 'No approved answer yet.'}</p>
        <button className="btn ghost" disabled={busy} onClick={() => setEditing(item)}>Edit</button>
        <button className="btn ghost" disabled={busy} onClick={() => {
          if (window.confirm('Delete this knowledge entry?')) run(async () => {
            await api.delete(base + 'knowledge/' + item.id + '/');
            if (editing?.id === item.id) setEditing(null);
            if (page > 1 && knowledge.data.results.length === 1) setPage(page - 1);
          });
        }}>Delete</button>
      </article></div>)}
      <Pager page={page} setPage={setPage} data={knowledge.data} />
    </section></>;
}

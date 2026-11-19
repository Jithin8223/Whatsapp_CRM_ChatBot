import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api.js';
import { useAuth } from '../auth.js';
import { useAction, useResource, useWorkspacePath } from '../hooks.js';
import { Empty, ErrorNotice, Icon, PageHeading, Status } from '../components.jsx';

function elapsed(since, now) {
  if (!since) return '';
  const seconds = Math.max(0, Math.round((now - new Date(since).getTime()) / 1000));
  return seconds < 60 ? `${seconds}s` : `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
}

function Pairing({ item, close, base }) {
  const { data, error } = useResource(`${base}connections/${item.id}/qr/`, 3000);
  const [now, setNow] = useState(Date.now());
  const { run, busy, error: actionError } = useAction();
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);
  const valid = data?.qrcode && new Date(data.expires_at).getTime() > now;
  const waiting = data?.state === 'error' ? (data.last_error || 'Connection failed. Try reconnecting.')
    : data?.state === 'disconnected' ? (data.detail || 'Session disconnected. Request a fresh pairing code.')
    : data?.detail || 'Starting the WhatsApp session and waiting for a pairing code…';
  const since = elapsed(data?.state_changed_at, now);
  return <div className="modal-backdrop"><section className="modal" role="dialog" aria-modal="true" aria-labelledby="pair-title"><div className="section-heading"><h2 id="pair-title">Pair {item.name}</h2><button className="btn ghost" onClick={close} aria-label="Close pairing">✕</button></div><ErrorNotice>{error || actionError}</ErrorNotice>{data?.state === 'connected' ? <Empty title="WhatsApp connected">The provider verified a signed-in WhatsApp device.</Empty> : <><div className="qr-box">{valid ? <img src={data.qrcode} alt="Scan this QR code in WhatsApp Linked devices" /> : <div><Icon name="connections" className="h-10 w-10 mx-auto mb-4" /><p role="status">{waiting}</p></div>}</div>
    <p className="muted text-sm mt-2" role="status">{valid ? (data.detail || 'Scan the code now.') : ''}{data?.state && ` State: ${data.state}${since && ` for ${since}`}.`}{data?.last_error && data.state !== 'error' && ` ${data.last_error}`}</p><ol className="pair-instructions"><li>Open WhatsApp on your phone.</li><li>Go to Settings → Linked devices.</li><li>Choose Link a device and scan the QR code.</li></ol><p className="muted text-sm">Codes refresh automatically while this window is open. If initialization has stopped, reconnect below.</p><button className="btn primary w-full mt-5" disabled={busy} onClick={() => run(() => api.post(`${base}connections/${item.id}/reconnect/`))}>{busy ? 'Requesting…' : 'Reconnect / request fresh QR'}</button></>}<p className="text-xs muted mt-4">Treat this code as a login credential. Do not share screenshots.</p></section></div>;
}

// Connected and AI automation are separate facts; automatic replies need both
// plus the workspace-wide "Enable automatic replies" switch in WhatsApp AI.
function AutomationStatus({ item }) {
  const reason = !item.connected ? (item.status_detail || 'Pair or reconnect WhatsApp first.')
    : !item.operational ? 'The session was disconnected in the CRM. Click Pair / reconnect.'
    : !item.ai_enabled ? 'Enable AI replies for this connection below.'
    : !item.workspace_ai_enabled ? 'Turn on “Enable automatic replies” in WhatsApp AI settings for this workspace.'
    : '';
  return <div className="space-y-1 text-sm my-2" aria-label="Automation status">
    <dl className="automation-status">
      <dt>WhatsApp connected</dt><dd>{item.connected ? '● Signed-in device' : `○ ${item.state}`}</dd>
      <dt>Session ready</dt><dd>{item.operational ? '● Ready to send' : '○ Not ready'}</dd>
      <dt>AI automation (this connection)</dt><dd>{item.ai_enabled ? '● Enabled' : '○ Disabled'}</dd>
      <dt>Workspace automatic replies</dt><dd>{item.workspace_ai_enabled ? '● On' : <>○ Off · <Link to="/whatsapp-ai">Open WhatsApp AI</Link></>}</dd>
    </dl>
    <p role="status"><strong>{item.auto_reply_active ? 'Automatic replies are active for new WhatsApp messages.' : 'Automatic replies are not active.'}</strong>{reason && <span className="muted"> {reason}</span>}</p>
  </div>;
}

export default function Connections() {
  const base = useWorkspacePath();
  const deployment = useResource(base + 'connections/deployment-status/', 15000);
  const enabled = deployment.data?.enabled === true;
  const { data, loading, error } = useResource(enabled ? base + 'connections/' : null);
  const { busy, error: actionError, run } = useAction();
  const [showCreate, setShowCreate] = useState(false);
  const [pairing, setPairing] = useState(null);
  const role = useAuth(s => s.workspaces.find(w => w.id === s.workspaceId)?.role);
  if (deployment.error) return <><PageHeading title="Connection status unavailable" /><ErrorNotice>{deployment.error}</ErrorNotice></>;
  if (!deployment.data) return <PageHeading title="Checking WhatsApp integration…" />;
  if (!enabled) return <><PageHeading title='WhatsApp not connected'>WPPConnect is disabled for this deployment.</PageHeading><section className='panel'><p>An authorized operator can enable WPPConnect before a workspace administrator pairs a business account. No pairing has been started.</p></section></>;
  async function create(event) {
    event.preventDefault();
    const name = new FormData(event.currentTarget).get('name');
    await run(async () => {
      const { data: item } = await api.post(base + 'connections/', { name });
      try {
        await api.post(`${base}connections/${item.id}/start/`);
      } catch (error) {
        useAuth.getState().refresh();
        throw error;
      } finally {
        setPairing(item);
        setShowCreate(false);
      }
      return item;
    });
  }
  return <><PageHeading eyebrow="WHATSAPP CONNECTIONS" title="Your direct line to customers." action={role === 'admin' && <button className="btn primary" onClick={() => setShowCreate(!showCreate)}>＋ Connect WhatsApp</button>}>Only pair an authorized business WhatsApp account. Monitor its connection and AI reply settings here.</PageHeading><ErrorNotice>{error || actionError}</ErrorNotice>{showCreate && <form className="panel inline-form" onSubmit={create}><label>Connection name<input name="name" placeholder="e.g. Customer support" maxLength={100} required /></label><button className="btn primary" disabled={busy}>Create & pair</button><button className="btn ghost" type="button" onClick={() => setShowCreate(false)}>Cancel</button></form>}{loading && !data ? <p className="muted">Loading connections…</p> : !data?.length ? <section className="panel"><Empty title="Let’s get connected">Integration enabled, account disconnected. Connect an authorized business account to begin pairing.</Empty></section> : <div className="connection-grid">{data.map(item => <section className="panel connection-card" key={item.id}><div className="connection-card-top"><span className="service-icon"><Icon name="connections" /></span><Status value={item.operational ? "connected" : item.state === "qr" ? "awaiting_authorization" : item.state === "connected" ? "disconnected" : item.state} /></div><h2>{item.name}</h2><p className="muted text-sm">WhatsApp · WPPConnect</p><AutomationStatus item={item} /><dl><dt>Last checked</dt><dd>{item.checked_at ? new Date(item.checked_at).toLocaleString() : 'Not yet checked'}</dd></dl>{item.last_error && <ErrorNotice>{item.last_error}</ErrorNotice>}{role === 'admin' && <div className="connection-actions"><button className="btn primary" disabled={busy} onClick={async () => { if (['new', 'disconnected', 'error'].includes(item.state)) await run(() => api.post(`${base}connections/${item.id}/start/`)); setPairing(item); }}>{item.operational ? 'Connection details' : 'Pair / reconnect'}</button><button className="btn secondary" disabled={busy || (!item.operational && !item.ai_enabled)} onClick={() => { if (item.ai_enabled || window.confirm("Enable AI replies for this authorized business WhatsApp session?")) run(() => api.patch(`${base}connections/${item.id}/automation/`, { enabled: !item.ai_enabled })); }}>{item.ai_enabled ? "Disable AI replies" : "Enable AI replies"}</button><button className="btn secondary" disabled={busy} onClick={() => run(() => api.post(`${base}connections/${item.id}/disconnect/`))}>Disconnect</button><button className="text-danger" disabled={busy} onClick={() => { if (window.confirm(`Remove ${item.name} and log it out of WhatsApp? Conversation history will be kept.`)) run(() => api.post(`${base}connections/${item.id}/remove/`)); }}>Remove connection</button></div>}</section>)}</div>}<p className="muted text-xs mt-6">Unofficial WhatsApp connectivity can be interrupted by protocol changes or account restrictions. A healthy server process does not mean an account is paired.</p>{pairing && <Pairing item={data?.find(i => i.id === pairing.id) || pairing} close={() => setPairing(null)} base={base} />}</>;
}

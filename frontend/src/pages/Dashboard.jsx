import { Link } from 'react-router-dom';
import { useAuth } from '../auth.js';
import { showWhatsAppUi, showWebsiteChatUi } from '../features.js';
import { useResource, useWorkspacePath } from '../hooks.js';
import { Avatar, Empty, ErrorNotice, Icon, PageHeading, Status, contactName, time } from '../components.jsx';
import { CRM_STATUSES } from '../FollowUps.jsx';

export default function Dashboard() {
  const base = useWorkspacePath();
  const { data, error } = useResource(base + 'dashboard/');
  // All channels (WhatsApp + website), newest activity first — the inbox API's default order.
  // Previously this requested only website chats whenever the website UI was enabled, which
  // hid every WhatsApp conversation from "Recent conversations".
  const channel = showWhatsAppUi && showWebsiteChatUi ? '' : showWhatsAppUi ? 'whatsapp' : 'website';
  const recent = useResource(`${base}conversations/?channel=${channel}&page=1`);
  const user = useAuth(s => s.user);
  const workspaceName = useAuth(s => s.workspaces.find(workspace => workspace.id === s.workspaceId)?.name);
  const admin = useAuth(s => s.workspaces.find(workspace => workspace.id === s.workspaceId)?.role === 'admin');
  const whatsappEnabled = useAuth(s => s.whatsappEnabled);
  const whatsapp = showWhatsAppUi && whatsappEnabled && admin;
  const { data: connections } = useResource(whatsapp ? base + 'connections/' : null);
  const metrics = [
    ...(showWhatsAppUi ? [['inbox', 'WhatsApp conversations', data?.whatsapp_conversations, 'inbox?channel=whatsapp']] : []),
    ...(data?.simulation_conversations ? [['inbox', 'Simulations', data.simulation_conversations, 'inbox?channel=whatsapp']] : []),
    ...(showWebsiteChatUi ? [['inbox', 'Website conversations', data?.website_conversations, 'inbox?channel=website']] : []),
    ['contacts', 'Customer contacts', data?.contacts, 'contacts'],
    ['inbox', 'Open conversations', showWebsiteChatUi ? data?.open_conversations : data?.whatsapp_open_conversations, 'inbox'],
    ['inbox', 'Unread conversations', showWebsiteChatUi ? data?.unread_conversations : data?.whatsapp_unread_conversations, 'inbox'],
  ];
  return <>
    <PageHeading title={`Hello, ${user.username}.`} action={<Link className="btn primary" to="/inbox">Open inbox <Icon name="arrow" /></Link>}>
      Showing conversations for {workspaceName || 'the selected workspace'}.
    </PageHeading>
    <ErrorNotice>{error}</ErrorNotice>
    {admin && showWebsiteChatUi && <div className="welcome-card"><div><h2>Website chatbot</h2>
      <p>Set up anonymous support chat for your website.</p>
      <Link className="text-link" to="/chatbot">Configure chatbot <Icon name="arrow" /></Link>
    </div></div>}
    {showWhatsAppUi && data && <p className="dashboard-status"><span className={`status ${data.whatsapp_connected ? 'good' : ''}`}>{data.whatsapp_connected ? 'WhatsApp connected' : 'WhatsApp not connected'}</span></p>}
    <div className="metric-grid">{metrics.map(([icon, label, value, route]) =>
      <Link to={`/${route}`} className="metric" key={label}>
        <div className="metric-top"><span>{label}</span><Icon name={icon} /></div><strong>{value ?? '—'}</strong>
      </Link>
    )}</div>
    {whatsapp && !!data?.unknown_sends && <div className="notice error">{data.unknown_sends} send(s) need manual verification in WhatsApp.</div>}
    <div className={whatsapp ? 'dashboard-bottom' : 'mt-5'}>
      <section className="panel"><div className="section-heading"><h2>Recent conversations</h2><Link to="/inbox">View inbox →</Link></div>
        {recent.error ? <div className="recent-state" role="alert"><p>Recent conversations could not be loaded. {recent.error}</p>
            <button className="btn secondary compact" onClick={() => useAuth.getState().refresh()}>Retry</button></div>
          : recent.loading && !recent.data ? <div aria-busy="true" aria-label="Loading recent conversations">{[0, 1, 2].map(row => <div key={row} className="recent-row recent-skeleton" aria-hidden="true"><span /><div><i /><i /></div></div>)}</div>
            : recent.data?.results?.length ? recent.data.results.slice(0, 6).map(item =>
              <Link key={item.id} className="recent-row" to={`/inbox/${item.id}`}>
                <Avatar name={contactName(item.contact)} />
                <div><strong><span className="truncate">{contactName(item.contact)}</span>
                  <span className={`ix-channel ${item.channel === 'website' ? 'web' : 'wa'}`} title={item.channel === 'website' ? 'Website chat' : 'WhatsApp'} aria-label={item.channel === 'website' ? 'Website chat' : 'WhatsApp'}><Icon name={item.channel === 'website' ? 'globe' : 'whatsapp'} className="h-3 w-3" /></span></strong>
                  <p>{item.last_message?.content || 'No messages yet'}</p></div>
                <div className="recent-meta"><time dateTime={item.last_activity_at}>{time(item.last_activity_at)}</time>
                  <span className={`ix-badge status-${item.crm_status}`}>{CRM_STATUSES[item.crm_status] || item.crm_status}</span></div>
                {item.unread_count > 0 && <b className="unread" aria-label={`${item.unread_count} unread`}>{item.unread_count}</b>}
              </Link>)
              : <Empty title="No conversations yet">New customer messages will appear here.</Empty>}
      </section>
      {whatsapp && <section className="panel">
        <div className="section-heading"><h2>WhatsApp integration</h2><Icon name="connections" /></div>
        {connections?.map(item => <div className="connection-mini" key={item.id}><strong>{item.name}</strong><Status value={item.state} /></div>)}
        <Link className="btn secondary w-full" to="/connections">Manage connections</Link>
      </section>}
    </div>
  </>;
}

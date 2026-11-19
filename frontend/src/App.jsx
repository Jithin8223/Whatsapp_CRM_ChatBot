import { useEffect, useState } from 'react';
import { Navigate, NavLink, Route, Routes, useLocation } from 'react-router-dom';
import { useAuth } from './auth.js';
import { showWhatsAppUi, showWebsiteChatUi } from './features.js';
import { useRealtime, useAction } from './hooks.js';
import { errorText } from './api.js';
import { Avatar, ErrorNotice, Icon } from './components.jsx';
import AuthPage from './pages/AuthPage.jsx';
import Dashboard from './pages/Dashboard.jsx';
import Connections from './pages/Connections.jsx';
import Inbox from './pages/Inbox.jsx';
import Contacts from './pages/Contacts.jsx';
import Settings from './pages/Settings.jsx';
import { api } from './api.js';
import Infrastructure from './Infrastructure.jsx';
import Chatbot from './pages/Chatbot.jsx';
import Products from './pages/Products.jsx';
import Simulation from './pages/Simulation.jsx';
import Leads from './pages/Leads.jsx';
import FollowUpsPage from './pages/FollowUpsPage.jsx';
import { readSidebarCollapsed, writeSidebarCollapsed } from './preferences.js';
import { useResource } from './hooks.js';
import { today } from './calendar.js';

function Workspace() {
  const { user, workspaces, workspaceId, realtime, simulationEnabled, leadAlerts = 0 } = useAuth();
  const admin = workspaces.find(workspace => workspace.id === workspaceId)?.role === 'admin';
  const [menu, setMenu] = useState(false);
  const [collapsed, setCollapsed] = useState(readSidebarCollapsed);
  const { run, busy, error } = useAction();
  const location = useLocation();
  useRealtime();
  useEffect(() => setMenu(false), [location.pathname]);
  useEffect(() => {
    if (!menu) return;
    const close = event => { if (event.key === 'Escape') setMenu(false); };
    window.addEventListener('keydown', close);
    return () => window.removeEventListener('keydown', close);
  }, [menu]);
  const toggleSidebar = () => setCollapsed(value => { writeSidebarCollapsed(!value); return !value; });
  const counts = useResource(workspaceId ? `/workspaces/${workspaceId}/dashboard/?today=${today()}` : null).data;
  const badges = {
    '/inbox': counts?.unassigned_conversations,
    '/followups': (counts?.followups_overdue || 0) + (counts?.followups_due_today || 0),
  };
  const badgeLabels = { '/inbox': 'unassigned conversations', '/followups': 'follow-ups due or overdue' };
  const navigation = [['/', 'dashboard', 'Overview'], ['/inbox', 'inbox', 'Conversations'], ['/leads', 'leads', 'Leads'], ['/followups', 'calendar', 'Follow-ups'],...(admin && showWebsiteChatUi ? [['/chatbot', 'settings', 'Website chatbot'], ['/products', 'contacts', 'Products and leads']] : []), ...(admin && showWhatsAppUi ? [['/whatsapp-ai', 'settings', 'WhatsApp AI']] : []), ...(admin && showWhatsAppUi ? [['/connections', 'connections', 'WhatsApp integration']] : []), ...(admin && simulationEnabled ? [['/simulation', 'inbox', 'Test WhatsApp AI']] : []), ['/contacts', 'contacts', 'Contacts'], ['/settings', 'settings', 'Settings']];
  if (!workspaceId) return <main className="boot-state"><section className="panel"><h1 className="text-xl mb-4">Create your workspace</h1><ErrorNotice>{error}</ErrorNotice><form onSubmit={event => { event.preventDefault(); const name = new FormData(event.currentTarget).get('name'); run(async () => { const { data } = await api.post('/workspaces/', { name }); await useAuth.getState().loadMe(); useAuth.getState().selectWorkspace(data.id); }); }}><label>Business name<input name="name" maxLength={120} required /></label><button disabled={busy} className="btn primary">Create workspace</button></form><button className="btn ghost mt-4" disabled={busy} onClick={() => run(() => useAuth.getState().logout())}>Sign out</button></section></main>;
  return <div className={`app-shell ${collapsed ? 'sidebar-collapsed' : ''}`}>{menu && <div className="sidebar-backdrop" onClick={() => setMenu(false)} aria-hidden="true" />}<aside id="primary-sidebar" className={`sidebar ${menu ? 'mobile-open' : ''} ${collapsed ? 'collapsed' : ''}`} aria-label="Main navigation"><div className="sidebar-top"><a href="/" className="brand" aria-label="Relay CRM home"><span className="brand-mark">r.</span><span className="brand-text">relay<span className="brand-light">CRM</span></span></a><button type="button" className="sidebar-toggle" onClick={toggleSidebar} aria-controls="primary-sidebar" aria-expanded={!collapsed} aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'} title={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}><Icon name={collapsed ? 'expand' : 'collapse'} className="h-4 w-4" /></button></div><label className="workspace-selector"><span>YOUR WORKSPACE</span><select aria-label="Active workspace" value={workspaceId} onChange={event => useAuth.getState().selectWorkspace(event.target.value)}>{workspaces.map(item => <option key={item.id} value={item.id}>{item.name} · {item.id.slice(0, 8)}</option>)}</select></label><p className="nav-caption">WORKSPACE</p><nav>{navigation.map(([path, icon, label]) => <NavLink key={path} to={path} end={path === '/'} className={({ isActive }) => isActive ? 'active' : ''} data-label={label} aria-label={badges[path] ? `${label}, ${badges[path]} ${badgeLabels[path]}` : label} title={collapsed ? label : undefined}><Icon name={icon} /><span className="nav-label">{label}</span>{badges[path] > 0 && <b className="nav-badge" aria-hidden="true">{badges[path] > 99 ? '99+' : badges[path]}</b>}</NavLink>)}</nav><div className="sidebar-note"><span className="note-icon">✦</span><strong>A more personal touch.</strong><p>Good conversations build lasting customer relationships.</p></div><div className="sidebar-user" title={collapsed ? user.username : undefined}><Avatar name={user.username} small /><div className="sidebar-user-text"><strong>{user.username}</strong><span>{workspaces.find(w => w.id === workspaceId)?.role}</span></div><button title="Sign out" aria-label="Sign out" disabled={busy} onClick={() => run(() => useAuth.getState().logout())}>↪</button></div></aside><div className={`app-main ${location.pathname.startsWith('/inbox') ? 'app-main--fill' : ''}`}><header className="topbar"><button className="menu-toggle" onClick={() => setMenu(!menu)} aria-label={menu ? 'Close navigation' : 'Open navigation'} aria-expanded={menu} aria-controls="primary-sidebar"><Icon name="menu" className="h-6 w-6" /></button><span>{workspaces.find(w => w.id === workspaceId)?.name} <span className="breadcrumb-separator">/</span> <strong>{navigation.find(([path]) => path === '/' ? location.pathname === '/' : location.pathname.startsWith(path))?.[2] || 'Workspace'}</strong></span><div className="topbar-right"><span className={`live-indicator ${realtime === 'live' ? 'connected' : ''}`}><i />{realtime === 'live' ? 'Live updates' : realtime === 'offline' ? 'Updates delayed' : 'Connecting live updates'}</span><span className="topbar-divider" /><Avatar name={user.username} small /></div></header><ErrorNotice>{error}</ErrorNotice>{admin && leadAlerts > 0 && <div className="panel mx-4 my-2 p-3 flex flex-wrap items-center gap-3" role="status"><strong>{leadAlerts === 1 ? 'New customer enquiry received.' : `${leadAlerts} new customer enquiries received.`}</strong><NavLink to="/leads" onClick={() => useAuth.setState({ leadAlerts: 0 })}>Review leads</NavLink><button className="btn ghost compact" onClick={() => useAuth.setState({ leadAlerts: 0 })}>Dismiss</button></div>}<main className={`workspace-content ${location.pathname.startsWith('/inbox') ? 'inbox-content' : ''}`} key={workspaceId}><Routes><Route path="/" element={<Dashboard />} /><Route path="/inbox/:conversationId?" element={<Inbox />} /><Route path="/leads" element={<Leads />} /><Route path="/followups" element={<FollowUpsPage />} /><Route path="/chatbot" element={admin && showWebsiteChatUi ? <Chatbot /> : <Navigate to="/" replace />} /><Route path="/products" element={admin && showWebsiteChatUi ? <Products /> : <Navigate to="/" replace />} /><Route path="/whatsapp-ai" element={admin && showWhatsAppUi ? <Chatbot channel="whatsapp" /> : <Navigate to="/" replace />} /><Route path="/connections" element={admin && showWhatsAppUi ? <Connections /> : <Navigate to="/" replace />} /><Route path="/simulation" element={admin && simulationEnabled ? <Simulation /> : <Navigate to="/" replace />} /><Route path="/contacts/:contactId?" element={<Contacts />} /><Route path="/settings" element={<Settings />} /><Route path="*" element={<Navigate to="/" replace />} /></Routes></main></div></div>;
}

export default function App() {
  const ready = useAuth(s => s.ready);
  const user = useAuth(s => s.user);
  const [error, setError] = useState('');
  const location = useLocation();
  useEffect(() => { useAuth.getState().bootstrap().catch(error => setError(errorText(error))); }, []);
  if (location.pathname === '/infrastructure') return <Infrastructure />;
  if (error) return <main className="boot-state"><ErrorNotice>{error}</ErrorNotice><button className="btn primary" onClick={() => window.location.reload()}>Retry connection</button></main>;
  if (!ready) return <main className="boot-state"><span className="brand-mark">r.</span><p>Opening your workspace…</p></main>;
  return user ? <Workspace /> : <AuthPage />;
}

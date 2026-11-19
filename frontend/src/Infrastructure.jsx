import { useEffect } from 'react';
import { useInfrastructure } from './store.js';

const services = [
  ['postgres', 'PostgreSQL', 'Durable application storage', 'DB'],
  ['redis', 'Redis', 'Queue, cache and channel layer', 'RQ'],
  ['wppconnect', 'WPPConnect', 'WhatsApp connector process', 'WA'],
];

export default function App() {
  const { data, loading, error, updated, refresh } = useInfrastructure();
  useEffect(() => { refresh(); const timer = setInterval(refresh, 15000); return () => clearInterval(timer); }, [refresh]);
  return <div className="min-h-screen lg:grid lg:grid-cols-[240px_1fr]">
    <aside className="bg-[#102a25] text-white p-7 lg:min-h-screen">
      <a href="/" className="flex items-center gap-3 text-lg font-semibold"><span className="brand-mark">w.</span> Relay CRM</a>
      <p className="mt-12 text-xs uppercase tracking-[.2em] text-emerald-200/50">Workspace</p>
      <div className="mt-4 rounded-lg bg-white/10 p-3 text-sm">◈ &nbsp; Infrastructure</div>
      <p className="mt-7 text-sm leading-7 text-emerald-100/50">Your foundation for connected customer support.</p>
      <div className="mt-12 border-t border-white/10 pt-5 text-xs text-emerald-100/50">Local development · Phase 01</div>
    </aside>
    <main className="p-6 md:p-12 max-w-6xl w-full mx-auto">
      <header className="flex items-center justify-between gap-4 border-b border-slate-200 pb-6"><span className="text-sm text-slate-500">Workspace / Infrastructure</span><span className="rounded-full border border-slate-200 px-3 py-1 text-xs">Development</span></header>
      <section className="mt-10 flex flex-wrap items-end justify-between gap-5">
        <div><p className="eyebrow">BUILD THE FOUNDATION</p><h1 className="mt-3 text-3xl md:text-4xl font-semibold tracking-tight">A connected start.</h1><p className="mt-3 text-slate-500">Monitor the services behind your WhatsApp AI CRM.</p></div>
        <button onClick={refresh} disabled={loading} className="rounded-lg bg-[#163d32] px-5 py-3 text-sm font-medium text-white disabled:opacity-50">{loading ? 'Checking…' : '↻ Refresh status'}</button>
      </section>
      <section className="mt-9 rounded-2xl bg-[#e9f0e8] p-7 md:p-9 flex flex-wrap justify-between items-center gap-6">
        <div><span className="eyebrow">PHASE 01 / 07</span><h2 className="mt-3 text-2xl font-semibold">Infrastructure comes first.</h2><p className="mt-2 max-w-xl text-sm leading-6 text-slate-600">The development environment is taking shape. Verify every service before connecting WhatsApp and building your messaging pipeline.</p></div>
        <span className="rounded-full bg-white/70 px-4 py-2 text-sm">{error ? 'API unavailable' : !data ? 'Awaiting checks' : data.status === 'ok' ? '● Dependencies healthy' : '● Attention needed'}</span>
      </section>
      {error && <p role="alert" className="mt-5 rounded-lg bg-red-50 p-4 text-sm text-red-800">{error}</p>}
      <div className="mt-9 flex justify-between items-center"><h2 className="font-semibold text-lg">Service overview</h2><span aria-live="polite" className="text-xs text-slate-500">{updated ? `Checked ${updated.toLocaleTimeString()}` : 'Waiting for first check'}</span></div>
      <section className="mt-5 grid gap-4 md:grid-cols-3">{services.map(([key, name, description, icon]) => {
        const healthy = data?.checks?.[key];
        return <article key={key} className="rounded-xl border border-slate-200 bg-white p-6"><span className="inline-flex h-11 w-11 items-center justify-center rounded-xl bg-slate-100 text-xs font-bold text-slate-600">{icon}</span><h3 className="mt-5 font-semibold">{name}</h3><p className="mt-2 min-h-10 text-sm text-slate-500">{description}</p><div className={`mt-5 border-t border-slate-100 pt-4 text-sm ${healthy === true ? 'text-emerald-700' : healthy === false ? 'text-red-700' : 'text-slate-400'}`}>{healthy === true ? '● Healthy' : healthy === false ? '● Unavailable' : '○ Not checked'}</div></article>;
      })}</section>
      <section className="mt-8 rounded-xl border border-slate-200 bg-white p-6"><h2 className="font-semibold">Before moving to Phase 2</h2><p className="mt-2 text-sm leading-6 text-slate-500">Run the verification script to test Celery task execution and the Channels Redis round trip. Connector health confirms its HTTP process; WhatsApp pairing and message delivery are part of the next phase.</p><code className="mt-4 block rounded-lg bg-slate-50 p-4 text-sm">.\scripts\Verify.ps1</code></section>
      <footer className="mt-9 text-xs text-slate-400">No WhatsApp account connected. No customer messages processed.</footer>
    </main>
  </div>;
}

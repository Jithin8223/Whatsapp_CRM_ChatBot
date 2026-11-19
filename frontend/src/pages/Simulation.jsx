import { useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api.js';
import { ErrorNotice, PageHeading } from '../components.jsx';
import { useAction, useResource, useWorkspacePath } from '../hooks.js';

export default function Simulation() {
  const base = useWorkspacePath();
  const [simulation, setSimulation] = useState(null);
  const { run, busy, error } = useAction();
  const { data, error: historyError } = useResource(simulation && base + 'conversations/' + simulation.id + '/messages/');
  const { data: thread } = useResource(simulation && base + 'conversations/' + simulation.id + '/');
  return <>
    <PageHeading title="Test WhatsApp AI">Simulation only. All replies stay in the local CRM.</PageHeading>
    <ErrorNotice>{error || historyError}</ErrorNotice>
    <section className="panel space-y-4">
      <form className="space-y-3" onSubmit={event => {
        event.preventDefault();
        const number = new FormData(event.currentTarget).get('number');
        run(async () => setSimulation((await api.post(base + 'simulation/', { number })).data));
      }}>
        <label>Dummy test customer number<input name="number" type="tel" required placeholder="International format" maxLength={16} /></label>
        <button className="btn primary" disabled={busy}>Start simulation</button>
      </form>
      {simulation && <>
        {!simulation.ai_enabled && <p>Automatic replies are off for this workspace. Enable them in WhatsApp AI settings, then return the conversation to AI mode from the inbox.</p>}
        <Link className="btn secondary" to={`/inbox/${simulation.id}?channel=whatsapp`}>Open simulation in inbox</Link>
        <p role="status">{thread?.reply_mode === 'ai' ? 'AI Active' : thread?.reply_mode === 'manual' ? 'Human Active' : 'Waiting for Agent'}</p>
        <div role="log" aria-label="Simulated customer conversation" className="space-y-2 max-h-96 overflow-y-auto">
          {[...(data?.results || [])].reverse().map(item => <article key={item.id} className="rounded-lg border p-3">
            <strong>{item.direction === 'inbound' ? 'Test customer' : item.automated ? 'AI' : 'Agent'} · Simulation</strong>
            <p className="whitespace-pre-wrap">{item.content}</p>
          </article>)}
        </div>
        <form className="space-y-3" onSubmit={event => {
          event.preventDefault();
          const form = event.currentTarget;
          const content = new FormData(form).get('content');
          run(async () => {
            await api.post(base + 'simulation/' + simulation.id + '/', { id: crypto.randomUUID(), content });
            form.reset();
          });
        }}>
          <label>Test customer message<textarea name="content" required maxLength={2000} /></label>
          <button className="btn primary" disabled={busy}>Send test message</button>
        </form>
      </>}
    </section>
  </>;
}

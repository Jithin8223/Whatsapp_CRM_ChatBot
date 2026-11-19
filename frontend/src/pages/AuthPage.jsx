import { useState } from 'react';
import { Navigate, useNavigate } from 'react-router-dom';
import { useAuth } from '../auth.js';
import { useAction } from '../hooks.js';
import { ErrorNotice, Icon } from '../components.jsx';

export default function AuthPage() {
  const [registering, setRegistering] = useState(false);
  const { busy, error, run } = useAction();
  const user = useAuth(s => s.user);
  const navigate = useNavigate();
  if (user) return <Navigate to="/" replace />;
  async function submit(event) {
    event.preventDefault();
    const values = Object.fromEntries(new FormData(event.currentTarget));
    await run(async () => { await useAuth.getState().login(values, registering); navigate('/', { replace: true }); });
  }
  return <main className="auth-layout"><section className="auth-story"><a href="/" className="brand"><span className="brand-mark">r.</span> relay<span className="brand-light">CRM</span></a><div><span className="eyebrow">ONE INBOX. BETTER CONVERSATIONS.</span><h1>A little closer<br />to every customer.</h1><p>Manage website chat, customer conversations, and your support team.</p><div className="auth-preview"><span className="preview-bubble">Your next great customer conversation starts here.</span><span className="preview-bubble reply">Let’s make it a good one. <Icon name="arrow" /></span></div></div></section><section className="auth-form"><span className="eyebrow">YOUR CUSTOMER WORKSPACE</span><h2>{registering ? 'Make room for better support.' : 'Welcome back.'}</h2><p>{registering ? 'Create your account and business workspace.' : 'Sign in to pick up the conversation.'}</p><ErrorNotice>{error}</ErrorNotice><form onSubmit={submit}><label>Username<input name="username" autoComplete="username" maxLength={150} required /></label>{registering && <><label>Work email<input name="email" type="email" autoComplete="email" required /></label><label>Business name<input name="business_name" autoComplete="organization" maxLength={120} required /></label></>}<label>Password<input name="password" type="password" autoComplete={registering ? 'new-password' : 'current-password'} minLength={registering ? 10 : undefined} maxLength={128} required /></label><button className="btn primary w-full" disabled={busy}>{busy ? 'Please wait…' : registering ? 'Create workspace' : 'Sign in'}<Icon name="arrow" /></button></form><p className="auth-switch">{registering ? 'Already have an account?' : 'New to Relay?'} <button onClick={() => setRegistering(!registering)}>{registering ? 'Sign in' : 'Create a workspace'}</button></p><small className="muted">Your session is secured with an HTTP-only cookie.</small></section></main>;
}

(() => {
  const script = document.currentScript;
  const key = script?.dataset.widgetKey;
  if (!/^[0-9a-f-]{36}$/i.test(key || '')) return;
  const platform = new URL(script.src).origin;
  const url = platform + '/api/guest/' + key + '/';
  const storageKey = 'relay-chat:' + platform + ':' + key;
  let token = '', socket, fallback, sending = false, requestId, lastText, loading = false, dirty = false, retryAt = 0, view = 0;
  // The visitor's chat survives reloads and new tabs until the server-side session
  // expires; storage can be blocked by the host page, so every access is guarded.
  const stores = [];
  try { stores.push(localStorage); } catch { /* blocked */ }
  try { stores.push(sessionStorage); } catch { /* blocked */ }
  for (const store of stores) {
    try { token = store.getItem(storageKey) || ''; } catch { /* blocked */ }
    if (token) break;
  }

  const host = document.createElement('div');
  const root = host.attachShadow({ mode: 'closed' });
  root.innerHTML = `<style>
  :host{all:initial;position:fixed;right:16px;bottom:16px;z-index:2147483647;font:14px/1.45 system-ui,sans-serif;color:#182b26;--accent:#123b30;--ink:white}
  *,*::before,*::after{box-sizing:border-box}button,input{font:inherit}button{cursor:pointer}button:disabled{cursor:default;opacity:.55}
  button:focus-visible,a:focus-visible,input:focus-visible{outline:2px solid #22795f;outline-offset:3px}
  #launch,#send{border:0;background:var(--accent);color:var(--ink);display:grid;place-items:center}
  #launch{width:60px;height:60px;border-radius:50%;margin-left:auto;box-shadow:0 8px 22px #123b3040}
  #send{border-radius:11px;padding:10px 14px;font-weight:650}
  section{position:absolute;right:0;bottom:72px;display:flex;flex-direction:column;background:#fff;border:1px solid #d9e3de;border-radius:22px;box-shadow:0 22px 64px #182b262e;width:min(390px,calc(100vw - 32px));height:min(570px,calc(100dvh - 104px));max-height:calc(100vh - 104px);overflow:hidden;transform:translateY(8px) scale(.98);transform-origin:bottom right;opacity:0;visibility:hidden;pointer-events:none;transition:transform 170ms ease,opacity 170ms ease,visibility 170ms}
  section[data-open]{transform:none;opacity:1;visibility:visible;pointer-events:auto}
  header,form,footer{display:flex;align-items:center;gap:10px;padding:12px 14px}
  header{background:var(--accent);color:var(--ink);padding:15px;flex:none}header strong{font-size:16px;display:block}
  #availability{display:block;font-size:12px}#availability::before{display:inline-block;content:"";background:#8fe0bd;border:1px solid currentColor;width:8px;height:8px;margin-right:5px;border-radius:50%}
  #close{margin-left:auto;border:0;background:transparent;color:inherit;font-size:24px;line-height:1;padding:8px}
  #history{min-height:0;flex:1;overflow:auto;padding:16px;display:flex;flex-direction:column;gap:13px;background:#f6f9f7}
  p{margin:0;overflow-wrap:anywhere;white-space:pre-wrap}.row{display:flex;align-items:flex-start;gap:7px;max-width:92%;align-self:start}
  .bubble{border:1px solid #e1e9e4;border-radius:4px 13px 13px;background:#fff;padding:9px 11px}
  .inbound{align-self:end}.inbound .bubble{background:#def0e7;border-color:#d4e9df;border-radius:13px 4px 13px 13px}
  time{display:block;margin-top:3px;color:#526960;font-size:10px}
  .avatar{display:grid;place-items:center;background:#dceee5;color:#174c3c;border-radius:12px;width:33px;height:33px;flex:none}
  header .avatar{width:42px;height:42px;border-radius:14px}
  #suggestions{display:flex;gap:6px;flex-wrap:wrap;padding:0 14px 8px}
  #suggestions button,#retry{background:#eff6f1;border:1px solid #d3e6db;color:#164f3e;border-radius:12px;padding:5px 9px;font-size:12px;text-align:left}
  #notice{padding:5px 14px;font-size:12px;color:#36554a}#notice:empty{display:none}
  #retry{margin:4px 14px;align-self:start}
  .file{display:block;margin:0 0 4px;padding:6px 9px;border:1px solid #d3e6db;border-radius:9px;background:#f4faf6;color:#154f3e;font-size:12px;text-align:left;max-width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  #typing{padding:5px 14px;font-size:12px;color:#36554a}
  #typing::before{content:"•••";letter-spacing:2px;margin-right:6px;animation:pulse 1s ease infinite alternate}
  @keyframes pulse{to{opacity:.3}}
  form{border-top:1px solid #e1e9e4;padding-bottom:7px}
  input{min-width:0;flex:1;padding:10px;border:1px solid #cddbd4;border-radius:11px;font-size:16px}
  footer{font-size:11px;flex-wrap:wrap;color:#536c62;padding-top:4px}footer a{color:#154f3e;font-weight:650}
  #guest-name{width:100%}#guest-name summary{cursor:pointer;color:#154f3e}
  #guest-name form{padding:6px 0 0;border:0;display:grid;gap:6px}#guest-name input{font-size:16px;padding:4px 8px;width:100%}
  #guest-name label{display:grid;gap:3px}#guest-name button{justify-self:start}
  @media (max-width:440px){:host{right:10px;bottom:10px}section{width:calc(100vw - 20px);height:min(570px,calc(100dvh - 88px));max-height:calc(100vh - 88px)}}
  @media (prefers-reduced-motion:reduce){section,#typing::before{transition:none;animation:none}}
  [hidden]{display:none!important}
  </style><section inert id="support-window" aria-label="Support chat"><header><span class="avatar" aria-hidden="true">●</span><div><strong>Support chat</strong><span id="availability">Connecting</span></div><button id="close" aria-label="Close support chat">×</button></header>
  <div id="history" role="log" aria-live="polite" aria-label="Chat messages"></div><p id="notice" role="status"></p>
  <div id="typing" role="status" hidden>Preparing a reply</div><button id="retry" type="button" hidden>Try again</button>
  <div id="suggestions" aria-label="Suggested questions"></div>
  <form><input aria-label="Your message" placeholder="Write a message" maxlength="2000" required disabled><button id="send" disabled>Send</button></form>
  <footer><button id="handoff" type="button" disabled>Request a human</button><a hidden target="_blank" rel="noopener noreferrer">Continue in WhatsApp</a><span>Avoid sharing sensitive information.</span>
  <span id="guest-label" hidden></span>
  <details id="guest-name" hidden><summary>Share contact details (optional)</summary>
  <form><label>Display name<input aria-label="Display name" maxlength="120" autocomplete="nickname"></label>
  <label>Mobile number<input type="tel" aria-label="Mobile number" maxlength="40" autocomplete="tel" placeholder="+ country code and number"></label>
  <label>Email address<input type="email" aria-label="Email address" maxlength="254" autocomplete="email"></label>
  <small>Share an email or number if you’d like the support team to contact you.</small>
  <button type="submit">Save contact details</button><button id="clear-shared-contact" type="button" hidden>Remove shared contact details</button></form></details></footer>
  </section><button id="launch" aria-label="Chat with us" aria-controls="support-window" aria-expanded="false"></button>`;
  const panel = root.querySelector('section');
  const input = root.querySelector('input');
  const launch = root.querySelector('#launch');
  const send = root.querySelector('#send');
  const notice = root.querySelector('#notice');
  const history = root.querySelector('#history');
  const availability = root.querySelector('#availability');
  const typing = root.querySelector('#typing');
  const suggestions = root.querySelector('#suggestions');
  const retry = root.querySelector('#retry');
  const handoff = root.querySelector('#handoff');
  const profile = root.querySelector('#guest-name');
  const profileForm = profile.querySelector('form');
  const mobileInput = profileForm.querySelector('input[type=tel]');
  const emailInput = profileForm.querySelector('input[type=email]');
  const clearSharedContact = root.querySelector('#clear-shared-contact');
  const guestLabel = root.querySelector('#guest-label');
  let displayName = '';
  const icon = '<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true"><path d="M20 11.5a8 8 0 0 1-8 8H5l1.5-3A8 8 0 1 1 20 11.5Z"/><path d="M8.5 11.5h7"/></svg>';
  launch.innerHTML = icon;
  root.querySelector('.avatar').innerHTML = icon;
  let welcome = '', rendered = '';
  let savedMessages = [], pendingMessage = null;
  const isOpen = () => panel.hasAttribute('data-open');

  function problem(message, callback) {
    notice.textContent = message;
    retry.onclick = callback || null;
    retry.hidden = !callback;
  }

  function colorStyle(color) {
    if (!/^#[0-9a-f]{6}$/i.test(color)) return;
    const [r, g, b] = color.slice(1).match(/../g).map(value => parseInt(value, 16) / 255).map(value =>
      value <= .04045 ? value / 12.92 : ((value + .055) / 1.055) ** 2.4);
    host.style.setProperty('--accent', color);
    host.style.setProperty('--ink', .2126 * r + .7152 * g + .0722 * b > .179 ? '#000' : '#fff');
  }

  function setToken(value) {
    token = value;
    for (const store of stores) {
      try {
        if (value) { store.setItem(storageKey, value); break; }
        store.removeItem(storageKey);
      } catch { /* Optional continuity; try the next store. */ }
    }
  }
  async function request(path = '', method = 'GET', body) {
    if (Date.now() < retryAt) throw new Error('Please wait a moment and try again.');
    const headers = path && token ? { Authorization: 'Bearer ' + token } : {};
    if (body) headers['Content-Type'] = 'application/json';
    const response = await fetch(url + path, {
      method, headers, body: body && JSON.stringify(body), credentials: 'omit',
      referrerPolicy: 'origin', signal: AbortSignal.timeout(10000),
    });
    if (!response.ok) {
      if (response.status === 429) {
        const seconds = Number(response.headers.get('Retry-After')) || 15;
        retryAt = Date.now() + seconds * 1000;
      }
      const error = new Error(response.status === 429 ? 'Please wait a moment and try again.' :
        !path && response.status === 403 ? 'Chat is unavailable on this website. Ask the site owner to publish it and verify the allowed origin.' :
        'Chat is temporarily unavailable.');
      error.status = response.status;
      throw error;
    }
    return response;
  }
  // ---- Receipts for team/bot replies ------------------------------------------------------
  // "delivered": the reply reached this widget. "read": the bubble was actually on screen
  // (>=60% visible) in the open chat while the page was visible. Never from a socket alone.
  const confirmed = { delivered: new Set(), read: new Set() };
  const queued = { delivered: new Set(), read: new Set() };
  let receiptTimer = null;
  function queueReceipt(kind, id) {
    if (!id || confirmed[kind].has(id) || confirmed.read.has(id)) return;
    queued[kind].add(id);
    clearTimeout(receiptTimer);
    receiptTimer = setTimeout(flushReceipts, 400);
  }
  async function flushReceipts() {
    const body = { delivered: [...queued.delivered].slice(0, 100), read: [...queued.read].slice(0, 100) };
    if (!token || (!body.delivered.length && !body.read.length)) return;
    queued.delivered.clear(); queued.read.clear();
    try {
      await request('receipts/', 'POST', body);
      body.delivered.forEach(id => confirmed.delivered.add(id));
      body.read.forEach(id => { confirmed.read.add(id); confirmed.delivered.add(id); });
    } catch { /* not confirmed: retried on the next refresh */ }
  }
  const viewable = () => isOpen() && document.visibilityState === 'visible';
  const seen = typeof IntersectionObserver === 'function' ? new IntersectionObserver(entries => {
    for (const entry of entries) {
      if (entry.isIntersecting && entry.intersectionRatio >= 0.6 && viewable()) {
        queueReceipt('read', entry.target.dataset.receipt);
        seen.unobserve(entry.target);
      }
    }
  }, { root: history, threshold: [0.6] }) : null;
  document.addEventListener('visibilitychange', () => {
    if (!viewable()) return;
    // Rows already on screen while the page was hidden do not re-trigger the observer.
    const box = history.getBoundingClientRect();
    history.querySelectorAll('[data-receipt]').forEach(row => {
      const rect = row.getBoundingClientRect();
      const visible = Math.min(rect.bottom, box.bottom) - Math.max(rect.top, box.top);
      if (rect.height && visible / rect.height >= 0.6) queueReceipt('read', row.dataset.receipt);
    });
  });

  // Files are private: fetched with this visitor's session token, never via a public link.
  async function openAttachment(attachment) {
    try {
      const blob = await (await request('attachments/' + attachment.id + '/')).blob();
      const href = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = href;
      link.download = attachment.filename;
      root.append(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(href), 1000);
    } catch {
      problem('Could not download the file. Please try again.');
    }
  }
  function draw(messages) {
    savedMessages = messages;
    if (pendingMessage && messages.some(item => item.request_id === pendingMessage.request_id)) pendingMessage = null;
    const visible = pendingMessage ? [...messages, pendingMessage] : messages;
    const signature = JSON.stringify([welcome, visible]);
    if (signature === rendered) return;
    rendered = signature;
    seen?.disconnect();  // rows are rebuilt below and re-observed
    history.replaceChildren(...[{ content: welcome }, ...visible].filter(item => item.content).map(item => {
      const row = document.createElement('div');
      const incoming = item.direction === 'inbound';
      row.className = 'row' + (incoming ? ' inbound' : '');
      if (!incoming) {
        const avatar = document.createElement('span');
        avatar.className = 'avatar';
        avatar.setAttribute('aria-hidden', 'true');
        avatar.innerHTML = icon;
        row.append(avatar);
      }
      const bubble = document.createElement('div');
      bubble.className = 'bubble';
      if (item.attachment) {
        const file = document.createElement('button');
        file.type = 'button';
        file.className = 'file';
        file.textContent = '📎 ' + item.attachment.filename + ' · ' + Math.max(1, Math.round(item.attachment.size / 1024)) + ' KB';
        file.setAttribute('aria-label', 'Download ' + item.attachment.filename);
        file.onclick = () => openAttachment(item.attachment);
        bubble.append(file);
      }
      if (!(item.attachment && item.content.startsWith('📎'))) {
        const text = document.createElement('p');
        text.textContent = item.content;
        bubble.append(text);
      }
      if (item.created_at) {
        const time = document.createElement('time');
        time.dateTime = item.created_at;
        time.textContent = item.sending ? 'Sending…' : new Date(item.created_at).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
        bubble.append(time);
      }
      row.append(bubble);
      if (!incoming && item.id && item.status !== 'read' && !confirmed.read.has(item.id)) {
        row.dataset.receipt = item.id;
        seen?.observe(row);
      }
      return row;
    }));
    suggestions.hidden = visible.length > 0;
    history.scrollTop = history.scrollHeight;
  }
  async function refresh() {
    if (!token || !isOpen()) return;
    if (loading) { dirty = true; return; }
    loading = true;
    try {
      const data = await (await request('messages/')).json();
      draw(data.messages);
      for (const item of data.messages) {
        if (item.direction === 'outbound' && item.id && !['delivered', 'read'].includes(item.status)) queueReceipt('delivered', item.id);
      }
      displayName = data.display_name || '';
      profile.hidden = false;
      guestLabel.textContent = data.guest_label || '';
      guestLabel.hidden = !data.guest_label;
      clearSharedContact.hidden = !(data.mobile_shared || data.email_shared);
      availability.textContent = { manual: 'Human active', waiting: 'Waiting for agent', ai: 'AI active', assisted: 'Support team active' }[data.mode] || 'Support chat available';
      typing.hidden = !data.pending || data.mode !== 'ai';
      handoff.hidden = data.mode !== 'ai';
      problem(data.mode === 'waiting' ? 'Automated replies are paused while your conversation waits for a support agent. You can keep messaging here.' : '');
    } catch (error) {
      if (error.status === 403 || error.status === 401) {
        setToken('');
        socket?.close();
        profile.hidden = true;
        profile.open = false;
        guestLabel.hidden = true;
        mobileInput.value = '';
        emailInput.value = '';
        pendingMessage = null;
        displayName = '';
        draw([]);
        problem('Your chat session ended. Send a message to start a new conversation.');
      } else problem('Could not refresh the chat. Please try again.', refresh);
    } finally {
      loading = false;
      if (dirty) { dirty = false; refresh(); }
    }
  }
  function connect() {
    if (!token || !isOpen() || (socket && socket.readyState < WebSocket.CLOSING)) return;
    const connection = new WebSocket(platform.replace(/^http/, 'ws') + '/ws/guest/' + key + '/');
    socket = connection;
    connection.onopen = () => connection.send(token);
    connection.onmessage = refresh;
  }
  async function open() {
    const generation = ++view;
    if (!isOpen()) panel.setAttribute('data-open', '');
    panel.inert = false;
    launch.setAttribute('aria-expanded', 'true');
    problem('Opening chat…');
    input.disabled = send.disabled = handoff.disabled = true;
    root.querySelector('#close').focus();
    try {
      const config = await (await request()).json();
      if (generation !== view) return;
      root.querySelector('strong').textContent = config.title;
      availability.textContent = 'Support chat available';
      colorStyle(config.color);
      const menu = Array.isArray(config.menu) ? config.menu : [];
      welcome = menu.length
        ? `${config.welcome}\n\n${menu.map((label, index) => `${index + 1}. ${label}`).join('\n')}\n\nReply with a number from 1 to ${menu.length}.`
        : config.welcome;
      rendered = '';
      draw(savedMessages);
      const quick = (label, value) => {
        const button = document.createElement('button');
        button.type = 'button';
        button.textContent = label;
        button.onclick = () => { input.value = value; input.form.requestSubmit(); };
        return button;
      };
      suggestions.replaceChildren(
        ...menu.map((label, index) => quick(`${index + 1}. ${label}`, String(index + 1))),
        ...(config.suggestions || []).map(question => quick(question, question)),
      );
      const link = root.querySelector('a');
      link.hidden = !config.whatsapp;
      if (config.whatsapp) link.href = config.whatsapp;
      root.querySelector('footer span').textContent = config.external_ai
        ? 'Relevant chat text may be sent to an AI provider. Avoid sharing sensitive information.'
        : 'Avoid sharing sensitive information.';
      problem('');
      input.disabled = send.disabled = handoff.disabled = false;
      handoff.hidden = false;
      input.focus();
      connect();
      refresh();
      clearInterval(fallback);
      fallback = setInterval(() => {
        if (!socket || socket.readyState >= WebSocket.CLOSING) {
          connect();
          refresh();
        }
      }, 15000);
    } catch (error) {
      if (generation !== view) return;
      availability.textContent = 'Unavailable';
      problem(error.status ? error.message : 'Could not open chat. Check your connection or ask the site owner to verify the allowed origin.', open);
    }
  }
  launch.onclick = () => isOpen() ? close() : open();
  script.addEventListener('relay:open-chat', () => { if (!isOpen()) open(); });
  function close() {
    view++;
    panel.removeAttribute('data-open');
    panel.inert = true;
    launch.setAttribute('aria-expanded', 'false');
    clearInterval(fallback);
    socket?.close();
    launch.focus();
  }
  root.querySelector('#close').onclick = close;
  panel.addEventListener('keydown', event => { if (event.key === 'Escape') close(); });
  profile.addEventListener('toggle', () => {
    if (profile.open) profileForm.querySelector('input').value = displayName;
  });
  profileForm.onsubmit = async event => {
    event.preventDefault();
    if (!token) return;
    const button = profileForm.querySelector('button');
    button.disabled = true;
    try {
      const mobile = mobileInput.value.trim();
      const email = emailInput.value.trim();
      if (mobile && !/^\+[1-9][0-9]{7,14}$/.test(mobile.replace(/[ ()-]/g, ''))) {
        problem('Use an international mobile number starting with + and its country code.');
        mobileInput.focus();
        return;
      }
      const saved = await (await request('profile/', 'PATCH', {
        display_name: profileForm.querySelector('input').value,
        ...(mobile && { mobile }),
        ...(email && { email }),
      })).json();
      displayName = saved.display_name;
      clearSharedContact.hidden = !(saved.mobile_shared || saved.email_shared);
      mobileInput.value = '';
      emailInput.value = '';
      profile.open = false;
    } catch {
      problem('Could not save your contact details. Please try again.');
    } finally {
      button.disabled = false;
    }
  };
  clearSharedContact.onclick = async () => {
    clearSharedContact.disabled = true;
    try {
      await request('profile/', 'PATCH', { mobile: '', email: '' });
      clearSharedContact.hidden = true;
      mobileInput.value = '';
      emailInput.value = '';
    } catch {
      problem('Could not remove the shared contact details. Please try again.');
    } finally { clearSharedContact.disabled = false; }
  };

  handoff.onclick = async () => {
    if (sending) return;
    sending = handoff.disabled = send.disabled = true;
    try {
      if (!token) setToken((await (await request('', 'POST')).json()).token);
      await request('handoff/', 'POST');
      connect();
      await refresh();
    } catch (error) {
      if ([401, 403].includes(error.status) && token) {
        setToken('');
        socket?.close();
      }
      problem('Could not request human support. Please try again.', () => handoff.click());
    } finally {
      sending = handoff.disabled = send.disabled = false;
    }
  };
  root.querySelector('form').onsubmit = async event => {
    event.preventDefault();
    const content = input.value.trim();
    if (sending || !content) return;
    sending = true;
    send.disabled = handoff.disabled = true;
    problem('Sending…');
    if (lastText !== content || !requestId) requestId = crypto.randomUUID();
    lastText = content;
    pendingMessage = {
      direction: 'inbound', content, request_id: requestId, created_at: new Date().toISOString(), sending: true,
    };
    draw(savedMessages);
    try {
      if (!token) setToken((await (await request('', 'POST')).json()).token);
      await request('messages/', 'POST', { id: requestId, content });
      if (input.value.trim() === content) input.value = '';
      requestId = null;
      connect();
      await refresh();
    } catch (error) {
      pendingMessage = null;
      draw(savedMessages);
      if ([401, 403].includes(error.status) && token) {
        setToken('');
        socket?.close();
        draw([]);
        problem('Your chat session ended. Retry to send this message in a new chat.', () => input.form.requestSubmit());
      } else problem(error.status ? error.message : 'Could not send your message. Please try again.', () => input.form.requestSubmit());
    } finally {
      sending = false;
      send.disabled = handoff.disabled = false;
    }
  };
  if (document.body) document.body.append(host);
  else document.addEventListener('DOMContentLoaded', () => document.body.append(host), { once: true });
})();

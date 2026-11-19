(() => {
  const script = document.currentScript;
  const key = script?.dataset.widgetKey;
  if (!/^[0-9a-f-]{36}$/i.test(key || '')) return;
  const platform = new URL(script.src).origin;
  const endpoint = new URL(`/api/guest/${key}/whatsapp-link/`, platform);
  const links = () => document.querySelectorAll('a[data-whatsapp-link]');
  const status = message => document.querySelectorAll('[data-whatsapp-status]').forEach(element => {
    element.textContent = message;
    element.hidden = !message;
  });
  fetch(endpoint, { credentials: 'omit', referrerPolicy: 'origin', signal: AbortSignal.timeout(7000) })
    .then(response => response.ok ? response.json() : Promise.reject(new Error('Unavailable')))
    .then(({ url, detail }) => {
      if (!url || !/^https:\/\/wa\.me\/[1-9][0-9]{6,14}(?:\?text=.+)?$/.test(url)) {
        status(detail || 'WhatsApp contact is unavailable. Check the business number.');
        return;
      }
      const host = document.createElement('div');
      const shadow = host.attachShadow({ mode: 'open' });
      shadow.innerHTML = `<style>
        :host{all:initial;position:fixed;right:max(18px,env(safe-area-inset-right));bottom:max(18px,env(safe-area-inset-bottom));z-index:2147483647}
        a{display:grid;place-items:center;width:60px;height:60px;border-radius:50%;background:#087a52;color:white;box-shadow:0 6px 20px #123b3052;transition:background .15s,transform .15s}
        a:hover{background:#065d3f;transform:translateY(-2px)}a:focus-visible{outline:3px solid #075e48;outline-offset:4px}
        @media(prefers-reduced-motion:reduce){a{transition:none}}
      </style><a aria-label="Contact us on WhatsApp" target="_blank" rel="noopener noreferrer">
        <svg aria-hidden="true" width="32" height="32" viewBox="0 0 32 32" fill="none" stroke="currentColor" stroke-width="2">
          <path d="M27 15.6a11 11 0 0 1-16.5 9.6l-5.3 1.4 1.4-5.1A11 11 0 1 1 27 15.6Z"/>
          <path fill="currentColor" stroke="none" d="M12 10c-.4-.8-1.5-.5-2-.1-.9.8-1.1 2.2-.7 3.4 1 3.9 4.8 7.4 8.6 8 1.4.2 3.1-.5 3.7-1.6.3-.5.3-1.3-.2-1.6l-2.7-1.3c-.6-.3-1.1.2-1.7 1-.2.3-.5.3-.8.1-1.5-.6-3-1.9-3.7-3.2-.2-.4-.1-.6.2-.9.5-.5.8-1 .5-1.5L12 10Z"/>
        </svg></a>`;
      shadow.querySelector('a').href = url;
      links().forEach(link => {
        link.href = url;
        link.removeAttribute('aria-disabled');
      });
      document.body.append(host);
      status('');
    })
    .catch(() => status('WhatsApp contact could not load. Check the website origin and contact configuration.'));
})();

// Pure helpers for the inbox (unit-tested with `npm test`).

export const ACCEPTED_FILES = '.jpg,.jpeg,.png,.gif,.webp,.pdf,.doc,.docx,.xls,.xlsx,.pptx,.csv,.txt';
export const MAX_FILE_BYTES = 10 * 1024 * 1024;

export function formatBytes(size) {
  if (!Number.isFinite(size)) return '';
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${Math.round(size / 1024)} KB`;
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}

// Client-side pre-check only; the server re-validates type, content and size.
export function checkFile(file, maxBytes = MAX_FILE_BYTES) {
  if (!file) return 'Choose a file.';
  const ext = file.name.includes('.') ? '.' + file.name.split('.').pop().toLowerCase() : '';
  if (!ACCEPTED_FILES.split(',').includes(ext)) return 'This file type is not allowed. Use images, PDF, Word, Excel, PowerPoint, CSV or text.';
  if (file.size === 0) return 'The file is empty.';
  if (file.size > maxBytes) return `Files can be at most ${Math.round(maxBytes / (1024 * 1024))} MB.`;
  return '';
}

const dayKey = date => `${date.getFullYear()}-${date.getMonth()}-${date.getDate()}`;

export function dayLabel(value, now = new Date()) {
  const date = new Date(value);
  const yesterday = new Date(now.getFullYear(), now.getMonth(), now.getDate() - 1);
  if (dayKey(date) === dayKey(now)) return 'Today';
  if (dayKey(date) === dayKey(yesterday)) return 'Yesterday';
  return date.toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'short', year: date.getFullYear() === now.getFullYear() ? undefined : 'numeric' });
}

// Chronological messages -> [{ day, label, messages }]
export function groupByDay(messages, now = new Date()) {
  const groups = [];
  for (const message of messages || []) {
    const when = new Date(message.provider_timestamp || message.created_at);
    const key = dayKey(when);
    if (!groups.length || groups[groups.length - 1].day !== key) groups.push({ day: key, label: dayLabel(when, now), messages: [] });
    groups[groups.length - 1].messages.push(message);
  }
  return groups;
}

const at = value => value ? ' · ' + new Date(value).toLocaleString([], { day: 'numeric', month: 'short', hour: 'numeric', minute: '2-digit' }) : '';

// Outbound receipt state, strictly from confirmed data: "read" only after a real read receipt
// (WhatsApp ack 3/4 or the website widget confirming the message was on screen).
// Returns null for incoming messages, which never show outgoing ticks.
export function deliveryState(message) {
  if (message.direction !== 'outbound') return null;
  if (['failed', 'cancelled'].includes(message.send_state)) return { state: 'failed', label: message.send_state === 'failed' ? 'Failed to send' : 'Cancelled before sending' };
  if (message.send_state === 'unknown') return { state: 'unknown', label: 'Delivery unknown — check WhatsApp' };
  if (['queued', 'sending'].includes(message.send_state)) return { state: 'pending', label: message.send_state === 'queued' ? 'Sending (queued)' : 'Sending' };
  if (message.delivery_status === 'failed') return { state: 'failed', label: message.last_error || 'Not delivered' };
  if (message.delivery_status === 'read') return { state: 'read', label: 'Read' + at(message.read_at) };
  if (message.delivery_status === 'delivered') return { state: 'delivered', label: 'Delivered' + at(message.delivered_at) };
  if (message.delivery_status === 'simulated') return { state: 'sent', label: 'Sent (simulation — no receipts)' };
  if (message.delivery_status === 'pending') return { state: 'pending', label: 'Waiting for WhatsApp confirmation' };
  return { state: 'sent', label: 'Sent' };
}

export function customerIdentifier(contact, channel) {
  if (!contact) return '';
  if (channel === 'website') return contact.phone || contact.email || contact.guest_label || 'Website visitor';
  return contact.phone || (contact.whatsapp_id || '').replace(/@(c\.us|lid|simulation)$/, '') || 'WhatsApp customer';
}

export function customerName(contact) {
  return contact?.display_name || contact?.guest_label || contact?.phone || 'Customer';
}

export function modeBadge(conversation) {
  if (!conversation) return null;
  if (conversation.reply_mode === 'manual') return { label: 'Human', tone: 'human', icon: 'user' };
  if (conversation.reply_mode === 'waiting') return { label: 'Waiting for human', tone: 'waiting', icon: 'user' };
  if (!conversation.automation_available) return { label: 'AI paused', tone: 'paused', icon: 'bot' };
  return conversation.reply_mode === 'assisted' ? { label: 'AI drafts', tone: 'ai', icon: 'bot' } : { label: 'AI automatic', tone: 'ai', icon: 'bot' };
}

export function relativeTime(value, now = new Date()) {
  if (!value) return '';
  const date = new Date(value);
  const minutes = Math.round((now - date) / 60000);
  if (minutes < 1) return 'now';
  if (minutes < 60) return `${minutes}m`;
  if (dayKey(date) === dayKey(now)) return date.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
  if (minutes < 7 * 24 * 60) return date.toLocaleDateString(undefined, { weekday: 'short' });
  return date.toLocaleDateString(undefined, { day: 'numeric', month: 'short' });
}

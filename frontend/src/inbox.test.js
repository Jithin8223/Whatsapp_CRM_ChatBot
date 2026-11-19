import { test } from 'node:test';
import assert from 'node:assert/strict';
import { checkFile, customerIdentifier, deliveryState, formatBytes, groupByDay, modeBadge } from './inbox.js';
import { readDetailsHidden, writeDetailsHidden } from './preferences.js';

test('messages are grouped under Today / Yesterday separators in order', () => {
  const now = new Date(2026, 10, 19, 12);
  const groups = groupByDay([
    { id: 1, created_at: new Date(2026, 10, 18, 9).toISOString() },
    { id: 2, created_at: new Date(2026, 10, 18, 22).toISOString() },
    { id: 3, created_at: new Date(2026, 10, 19, 8).toISOString() },
  ], now);
  assert.deepEqual(groups.map(group => [group.label, group.messages.map(m => m.id)]), [['Yesterday', [1, 2]], ['Today', [3]]]);
});

test('delivery ticks come only from confirmed states; incoming messages have none', () => {
  const out = values => deliveryState({ direction: 'outbound', ...values });
  assert.equal(deliveryState({ direction: 'inbound', delivery_status: 'read' }), null);
  assert.equal(out({ send_state: 'queued' }).state, 'pending');
  assert.equal(out({ send_state: 'sending' }).state, 'pending');
  // Submitted to WhatsApp but no ack yet: still pending, never a tick or a blue tick.
  assert.equal(out({ send_state: 'submitted', delivery_status: 'pending' }).state, 'pending');
  assert.equal(out({ send_state: 'submitted', delivery_status: 'sent' }).state, 'sent');
  assert.equal(out({ send_state: 'submitted', delivery_status: 'delivered' }).state, 'delivered');
  assert.equal(out({ send_state: 'submitted', delivery_status: 'read', read_at: '2026-11-19T10:00:00Z' }).state, 'read');
  assert.match(out({ send_state: 'submitted', delivery_status: 'read', read_at: '2026-11-19T10:00:00Z' }).label, /^Read · /);
  assert.equal(out({ send_state: 'failed' }).state, 'failed');
  assert.equal(out({ send_state: 'submitted', delivery_status: 'failed', last_error: 'WhatsApp rejected the attachment as too large.' }).label,
    'WhatsApp rejected the attachment as too large.');
  assert.equal(out({ send_state: 'unknown' }).state, 'unknown');
  assert.equal(out({ send_state: 'submitted', delivery_status: 'simulated' }).state, 'sent');
});

test('file pre-checks match the server allow-list and size limit', () => {
  assert.equal(checkFile({ name: 'quote.PDF', size: 100 }), '');
  assert.match(checkFile({ name: 'run.exe', size: 100 }), /not allowed/);
  assert.match(checkFile({ name: 'logo.svg', size: 100 }), /not allowed/);
  assert.match(checkFile({ name: 'big.pdf', size: 11 * 1024 * 1024 }), /at most 10 MB/);
  assert.match(checkFile({ name: 'empty.png', size: 0 }), /empty/);
  assert.equal(formatBytes(1536), '2 KB');
  assert.equal(formatBytes(3.5 * 1024 * 1024), '3.5 MB');
});

test('customer identifiers never invent contact details', () => {
  assert.equal(customerIdentifier({ whatsapp_id: '919876543210@c.us', phone: '' }, 'whatsapp'), '919876543210');
  assert.equal(customerIdentifier({ phone: '+12025550149', whatsapp_id: 'x@lid' }, 'whatsapp'), '+12025550149');
  assert.equal(customerIdentifier({ guest_label: 'Guest #0007', phone: '' }, 'website'), 'Guest #0007');
});

test('mode badge distinguishes AI, human and paused automation', () => {
  assert.equal(modeBadge({ reply_mode: 'ai', automation_available: true }).label, 'AI automatic');
  assert.equal(modeBadge({ reply_mode: 'ai', automation_available: false }).label, 'AI paused');
  assert.equal(modeBadge({ reply_mode: 'manual' }).label, 'Human');
});

test('details panel preference persists', () => {
  const values = new Map();
  const storage = { getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, value) };
  assert.equal(readDetailsHidden(storage), false);
  writeDetailsHidden(true, storage);
  assert.equal(readDetailsHidden(storage), true);
});

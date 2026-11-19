import { test } from 'node:test';
import assert from 'node:assert/strict';
import { formatMenu, lines, parseMenu } from './chatbotConfig.js';

test('lines is null-safe (regression: "Cannot read properties of null (reading \'split\')")', () => {
  assert.deepEqual(lines(null), []);
  assert.deepEqual(lines(undefined), []);
  assert.deepEqual(lines(' human \n\n talk to person '), ['human', 'talk to person']);
});

test('parseMenu accepts valid label | action lines', () => {
  const { items, error } = parseMenu('View Products | products\nRequest Quotation | quotation\n');
  assert.equal(error, undefined);
  assert.deepEqual(items, [
    { label: 'View Products', action: 'products' },
    { label: 'Request Quotation', action: 'quotation' },
  ]);
});

test('parseMenu rejects unknown actions, missing labels, duplicates and size limits', () => {
  assert.match(parseMenu('Shop | shopping\nPrices | prices').error, /Invalid menu line “Shop \| shopping”/);
  assert.match(parseMenu(' | products\nPrices | prices').error, /Invalid menu line/);
  assert.match(parseMenu('A | products\nB | products').error, /used more than once/);
  assert.match(parseMenu('Only | products').error, /at least two/);
  assert.match(parseMenu(null).error, /at least two/);
  const ten = ['products', 'prices', 'features', 'quotation', 'business_info', 'general_question']
    .map((action, index) => `Option ${index} | ${action}`);
  assert.equal(parseMenu(ten.join('\n')).items.length, 6);
  assert.match(parseMenu(`${'x'.repeat(71)} | products\nB | prices`).error, /longer than 70/);
});

test('formatMenu tolerates null and malformed saved menus', () => {
  assert.equal(formatMenu(null), '');
  assert.equal(formatMenu([{ label: 'A', action: 'products' }, null, { label: '' }]), 'A | products');
});

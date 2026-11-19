import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readSidebarCollapsed, writeSidebarCollapsed } from './preferences.js';
import { groupByDate, isOverdue, monthGrid, monthRange } from './calendar.js';

function memoryStorage() {
  const values = new Map();
  return { getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, String(value)) };
}

test('sidebar preference persists and survives blocked storage', () => {
  const storage = memoryStorage();
  assert.equal(readSidebarCollapsed(storage), false);
  assert.equal(writeSidebarCollapsed(true, storage), true);
  assert.equal(readSidebarCollapsed(storage), true);
  writeSidebarCollapsed(false, storage);
  assert.equal(readSidebarCollapsed(storage), false);
  const blocked = { getItem() { throw new Error('denied'); }, setItem() { throw new Error('denied'); } };
  assert.equal(readSidebarCollapsed(blocked), false);
  assert.equal(writeSidebarCollapsed(true, blocked), false);
});

test('month grid is six Monday-first weeks containing the whole month', () => {
  const grid = monthGrid(2026, 11); // December 2026 starts on a Tuesday
  assert.equal(grid.length, 42);
  assert.equal(grid[0].iso, '2026-11-30');
  assert.equal(grid[1].iso, '2026-12-01');
  assert.equal(grid.filter(day => day.inMonth).length, 31);
  assert.deepEqual(monthRange(2026, 11), { from: '2026-11-30', to: '2027-01-10' });
  const february = monthGrid(2027, 1);
  assert.equal(february.filter(day => day.inMonth).length, 28);
});

test('follow-ups land on their scheduled date and only pending past items are overdue', () => {
  const items = [
    { id: 1, due_date: '2026-12-03', status: 'pending' },
    { id: 2, due_date: '2026-12-03', status: 'done' },
    { id: 3, due_date: '2026-12-09', status: 'pending' },
  ];
  assert.deepEqual(Object.keys(groupByDate(items)), ['2026-12-03', '2026-12-09']);
  assert.equal(groupByDate(items)['2026-12-03'].length, 2);
  assert.equal(isOverdue(items[0], '2026-12-05'), true);
  assert.equal(isOverdue(items[1], '2026-12-05'), false);
  assert.equal(isOverdue(items[2], '2026-12-05'), false);
});

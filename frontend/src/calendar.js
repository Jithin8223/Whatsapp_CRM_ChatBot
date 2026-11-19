// Calendar helpers. Dates are local calendar days (YYYY-MM-DD), matching how follow-ups are scheduled.

export const pad = value => String(value).padStart(2, '0');
export const isoDate = day => `${day.getFullYear()}-${pad(day.getMonth() + 1)}-${pad(day.getDate())}`;
export const today = () => isoDate(new Date());

export function parseISODate(value) {
  const [year, month, day] = String(value).split('-').map(Number);
  return new Date(year, month - 1, day);
}

// Six Monday-first weeks covering the month, so every month renders the same grid size.
export function monthGrid(year, month) {
  const first = new Date(year, month, 1);
  const offset = (first.getDay() + 6) % 7;
  const start = new Date(year, month, 1 - offset);
  return Array.from({ length: 42 }, (_, index) => {
    const day = new Date(start.getFullYear(), start.getMonth(), start.getDate() + index);
    return { iso: isoDate(day), day: day.getDate(), inMonth: day.getMonth() === month };
  });
}

export function monthRange(year, month) {
  const grid = monthGrid(year, month);
  return { from: grid[0].iso, to: grid[grid.length - 1].iso };
}

export const isOverdue = (item, now = today()) => item.status === 'pending' && item.due_date < now;

export function groupByDate(items) {
  const groups = {};
  for (const item of items || []) (groups[item.due_date] ||= []).push(item);
  return groups;
}

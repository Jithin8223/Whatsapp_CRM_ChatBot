// Pure, null-safe parsing for chatbot settings forms (unit-tested with `npm test`).

export const MENU_ACTIONS = ['products', 'prices', 'features', 'quotation', 'business_info', 'general_question'];

// FormData.get() returns null for fields that are not rendered; never call .split on null.
export const lines = value => String(value ?? '').split('\n').map(line => line.trim()).filter(Boolean);

// "label | action" lines -> menu items, or { error } describing the first invalid line.
export function parseMenu(value) {
  const items = [];
  for (const line of lines(value)) {
    const [label = '', action = ''] = line.split('|').map(part => part.trim());
    if (!label || !MENU_ACTIONS.includes(action)) {
      return { error: `Invalid menu line “${line}”. Use: label | one of ${MENU_ACTIONS.join(', ')}.` };
    }
    if (label.length > 70) return { error: `Menu label “${label.slice(0, 30)}…” is longer than 70 characters.` };
    if (items.some(item => item.action === action)) return { error: `The action “${action}” is used more than once.` };
    items.push({ label, action });
  }
  if (items.length < 2) return { error: 'The main menu needs at least two options.' };
  if (items.length > 9) return { error: 'The main menu can have at most nine options.' };
  return { items };
}

export const formatMenu = menu => (Array.isArray(menu) ? menu : [])
  .filter(item => item && item.label && item.action)
  .map(item => `${item.label} | ${item.action}`).join('\n');

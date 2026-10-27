'use strict';

// ---------------------------------------------------------------------------
// Icons (inline SVG, Lucide-style strokes)
// ---------------------------------------------------------------------------
const ICONS = {
  panel: '<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M9 3v18"/>',
  newchat: '<path d="M12 3H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.4 2.6a2.1 2.1 0 0 1 3 3L12 15l-4 1 1-4Z"/>',
  search: '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>',
  settings: '<path d="M20 7h-9"/><path d="M14 17H5"/><circle cx="17" cy="17" r="3"/><circle cx="7" cy="7" r="3"/>',
  paperclip: '<path d="m21.4 11-9.2 9.2a6 6 0 0 1-8.5-8.5l8.6-8.6a4 4 0 0 1 5.7 5.7l-8.6 8.5a2 2 0 0 1-2.8-2.8l8.5-8.5"/>',
  send: '<path d="M12 19V5"/><path d="m5 12 7-7 7 7"/>',
  stop: '<rect x="5" y="5" width="14" height="14" rx="2.5"/>',
  copy: '<rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>',
  check: '<path d="M20 6 9 17l-5-5"/>',
  retry: '<path d="M3 12a9 9 0 1 0 2.6-6.4L3 8"/><path d="M3 3v5h5"/>',
  edit: '<path d="M21.2 6.8a2.8 2.8 0 0 0-4-4L3.8 16.2a2 2 0 0 0-.5.8l-1.3 4.4 4.4-1.3a2 2 0 0 0 .8-.5Z"/><path d="m15 5 4 4"/>',
  more: '<circle cx="5" cy="12" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/>',
  trash: '<path d="M3 6h18"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>',
  close: '<path d="M18 6 6 18"/><path d="m6 6 12 12"/>',
  bulb: '<path d="M15 14c.2-1 .7-1.7 1.5-2.5A6 6 0 1 0 7.5 11.5c.8.8 1.3 1.5 1.5 2.5"/><path d="M9 18h6"/><path d="M10 22h4"/>',
  chevron: '<path d="m6 9 6 6 6-6"/>',
  file: '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/>',
  bolt: '<path d="M13 2 4 14h7l-1 8 9-12h-7z"/>',
};
function icon(name, cls = '') {
  return `<svg class="icon ${cls}" viewBox="0 0 24 24" aria-hidden="true">${ICONS[name] || ''}</svg>`;
}
function hydrateIcons(root = document) {
  for (const el of root.querySelectorAll('[data-icon]')) {
    if (!el.querySelector('svg')) el.insertAdjacentHTML('afterbegin', icon(el.dataset.icon));
  }
}
hydrateIcons();

const $ = (id) => document.getElementById(id);
const log = $('log');
const input = $('input');
const sendBtn = $('send');
const modelField = $('model');
const thinkBtn = $('thinkBtn');
const attachmentsEl = $('attachments');
const fileInput = $('fileInput');
const conversationListEl = $('conversationList');
const searchField = $('search');
const settingsEl = $('settings');
const narrow = window.matchMedia('(max-width: 780px)');

// localStorage can be unavailable (private windows, blocked site data) -
// everything still works without it, just without remembering.
function load(key, fallback) {
  try {
    const v = localStorage.getItem(key);
    return v === null ? fallback : JSON.parse(v);
  } catch {
    return fallback;
  }
}
function store(key, value) {
  try {
    if (value === undefined || value === null) localStorage.removeItem(key);
    else localStorage.setItem(key, JSON.stringify(value));
  } catch {
    // full or unavailable - just not remembered this time
  }
}

// ---------------------------------------------------------------------------
// Preferences: theme, speed tags, thinking
// ---------------------------------------------------------------------------
const prefs = {
  theme: load('theme', 'system'),
  showSpeed: load('showSpeed', true),
  thinking: load('thinking', true),
};

function applyTheme() {
  if (prefs.theme === 'system') delete document.documentElement.dataset.theme;
  else document.documentElement.dataset.theme = prefs.theme;
  for (const b of document.querySelectorAll('[data-theme-choice]')) {
    b.setAttribute('aria-checked', String(b.dataset.themeChoice === prefs.theme));
  }
}
for (const b of document.querySelectorAll('[data-theme-choice]')) {
  b.addEventListener('click', () => {
    prefs.theme = b.dataset.themeChoice;
    store('theme', prefs.theme);
    applyTheme();
  });
}
applyTheme();

const showSpeedField = $('showSpeed');
showSpeedField.checked = prefs.showSpeed;
document.body.classList.toggle('no-speed', !prefs.showSpeed);
showSpeedField.addEventListener('change', () => {
  prefs.showSpeed = showSpeedField.checked;
  store('showSpeed', prefs.showSpeed);
  document.body.classList.toggle('no-speed', !prefs.showSpeed);
});

function renderThinkButton() {
  thinkBtn.setAttribute('aria-pressed', String(prefs.thinking));
  thinkBtn.title = prefs.thinking
    ? 'Thinking is on: the model reasons before answering (slower, more careful). Click to turn off.'
    : 'Thinking is off: quicker answers. Click to let the model think first.';
}
thinkBtn.addEventListener('click', () => {
  prefs.thinking = !prefs.thinking;
  store('thinking', prefs.thinking);
  renderThinkButton();
});
renderThinkButton();

// ---------------------------------------------------------------------------
// Sidebar
// ---------------------------------------------------------------------------
function setSidebar(open) {
  document.body.classList.toggle('sidebar-closed', !open);
  if (!narrow.matches) store('sidebarOpen', open);
}
setSidebar(narrow.matches ? false : load('sidebarOpen', true));
narrow.addEventListener('change', () => setSidebar(narrow.matches ? false : load('sidebarOpen', true)));
$('sidebarClose').addEventListener('click', () => setSidebar(false));
$('sidebarOpen').addEventListener('click', () => setSidebar(true));
$('scrim').addEventListener('click', () => setSidebar(false));
function closeSidebarOnPhone() {
  if (narrow.matches) setSidebar(false);
}

// ---------------------------------------------------------------------------
// Conversations
// ---------------------------------------------------------------------------
// conversations: [{id, title, messages, updatedAt}], newest saved as they change.
// A message: {role, content} as sent to the model, plus what the page shows:
//   user: text (what was typed), files [{name, kind, path}]
//   assistant: tools [names], tps, tpsApprox, reasoning, thoughtMs
// Keys starting "_" (an image's data, for this session only) aren't saved.
let conversations = load('conversations', []);
if (!Array.isArray(conversations)) conversations = [];
let currentId = load('currentConversationId', null);
let history = [];
let controller = null;

function saveConversations() {
  try {
    localStorage.setItem('conversations', JSON.stringify(conversations, (k, v) => (k.startsWith('_') ? undefined : v)));
  } catch {
    // storage full - the newest change just isn't kept
  }
}

function currentConversation() {
  return conversations.find((c) => c.id === currentId);
}

// Save a chat's messages - into the chat `id`, or a new one if it's null.
// Returns the chat's id.
function saveChat(messages, id) {
  if (!messages.length) return id;
  const now = Date.now();
  let conv = conversations.find((c) => c.id === id);
  if (!conv) {
    id = window.crypto && crypto.randomUUID ? crypto.randomUUID() : 'c' + now + Math.random().toString(36).slice(2);
    const first = messages.find((m) => m.role === 'user');
    const title = ((first && (first.text || first.content)) || 'New chat').replace(/\s+/g, ' ').trim().slice(0, 60);
    conv = { id, title, messages, updatedAt: now };
    conversations.push(conv);
  }
  conv.messages = messages;
  conv.updatedAt = now;
  saveConversations();
  return id;
}

function saveCurrent() {
  const id = saveChat(history, currentId);
  if (id !== currentId) {
    currentId = id;
    store('currentConversationId', id);
  }
  renderConversationList();
  renderTitle();
}

function renderTitle() {
  const conv = currentConversation();
  $('chatTitle').textContent = conv ? conv.title : '';
  document.title = conv ? `${conv.title} · Agentic Harness` : 'Agentic Harness';
}

function dayGroup(ts) {
  const today = new Date().setHours(0, 0, 0, 0);
  const day = 864e5;
  if (ts >= today) return 'Today';
  if (ts >= today - day) return 'Yesterday';
  if (ts >= today - 7 * day) return 'Previous 7 days';
  if (ts >= today - 30 * day) return 'Previous 30 days';
  return 'Older';
}

function matches(conv, q) {
  if (!q) return true;
  q = q.toLowerCase();
  return conv.title.toLowerCase().includes(q)
    || (conv.messages || []).some((m) => String(m.text || m.content || '').toLowerCase().includes(q));
}

function renderConversationList() {
  conversationListEl.innerHTML = '';
  const q = searchField.value.trim();
  const list = conversations.filter((c) => matches(c, q)).sort((a, b) => b.updatedAt - a.updatedAt);
  if (!list.length) {
    conversationListEl.innerHTML = `<div class="empty-list">${q ? 'No chats match.' : 'Your chats will appear here.'}</div>`;
    return;
  }
  let group = null;
  for (const conv of list) {
    const g = dayGroup(conv.updatedAt);
    if (g !== group) {
      group = g;
      const label = document.createElement('div');
      label.className = 'group-label';
      label.textContent = g;
      conversationListEl.appendChild(label);
    }
    const row = document.createElement('div');
    row.className = 'chat-row' + (conv.id === currentId ? ' active' : '');
    const link = document.createElement('button');
    link.className = 'chat-link';
    link.textContent = conv.title;
    link.title = conv.title;
    link.addEventListener('click', () => openConversation(conv.id));
    const more = document.createElement('button');
    more.className = 'icon-btn row-menu-btn';
    more.title = 'More';
    more.setAttribute('aria-label', 'More options for ' + conv.title);
    more.innerHTML = icon('more');
    more.addEventListener('click', (e) => {
      e.stopPropagation();
      openRowMenu(more, row, conv);
    });
    row.append(link, more);
    conversationListEl.appendChild(row);
  }
}
searchField.addEventListener('input', renderConversationList);

// A small floating menu (Rename / Delete) next to a chat.
let openMenu = null;
function closeMenu() {
  if (!openMenu) return;
  openMenu.menu.remove();
  openMenu.row.classList.remove('menu-open');
  openMenu = null;
}
function openRowMenu(anchor, row, conv) {
  const wasOpen = openMenu && openMenu.row === row;
  closeMenu();
  if (wasOpen) return;
  const menu = document.createElement('div');
  menu.className = 'menu';
  menu.innerHTML = `<button data-act="rename">${icon('edit')}Rename</button><button data-act="delete" class="danger">${icon('trash')}Delete</button>`;
  menu.addEventListener('click', (e) => e.stopPropagation());
  menu.querySelector('[data-act="rename"]').addEventListener('click', () => {
    closeMenu();
    startRename(row, conv);
  });
  const del = menu.querySelector('[data-act="delete"]');
  del.addEventListener('click', () => {
    if (!del.dataset.armed) {
      del.dataset.armed = '1';
      del.innerHTML = `${icon('trash')}Click again to delete`;
      return;
    }
    closeMenu();
    deleteConversation(conv.id);
  });
  document.body.appendChild(menu);
  const r = anchor.getBoundingClientRect();
  menu.style.top = Math.min(r.bottom + 4, window.innerHeight - menu.offsetHeight - 8) + 'px';
  menu.style.left = Math.min(r.left, window.innerWidth - menu.offsetWidth - 8) + 'px';
  row.classList.add('menu-open');
  openMenu = { menu, row };
}
document.addEventListener('click', closeMenu);

function startRename(row, conv) {
  const field = document.createElement('input');
  field.className = 'rename';
  field.value = conv.title;
  row.replaceChildren(field);
  field.focus();
  field.select();
  let done = false;
  const finish = (save) => {
    if (done) return;
    done = true;
    const title = field.value.replace(/\s+/g, ' ').trim();
    if (save && title) {
      conv.title = title.slice(0, 80);
      saveConversations();
      renderTitle();
    }
    renderConversationList();
  };
  field.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') finish(true);
    if (e.key === 'Escape') finish(false);
  });
  field.addEventListener('blur', () => finish(true));
}

function deleteConversation(id) {
  conversations = conversations.filter((c) => c.id !== id);
  saveConversations();
  if (id === currentId) newChat();
  else renderConversationList();
}

function openConversation(id) {
  closeSidebarOnPhone();
  if (id === currentId) return;
  const conv = conversations.find((c) => c.id === id);
  if (!conv) return;
  if (controller) controller.abort();
  currentId = id;
  store('currentConversationId', id);
  history = conv.messages.map((m) => ({ ...m }));
  activeEnabled = new Set(pendingEnabled);
  clearPendingFiles();
  renderThread();
  renderConversationList();
  renderTitle();
}

function newChat() {
  closeSidebarOnPhone();
  if (controller) controller.abort();
  currentId = null;
  store('currentConversationId', null);
  history = [];
  activeEnabled = new Set(pendingEnabled);
  clearPendingFiles();
  renderThread();
  renderConversationList();
  renderTitle();
  input.focus();
}
$('newChat').addEventListener('click', newChat);
$('newChatTop').addEventListener('click', newChat);

// ---------------------------------------------------------------------------
// Rendering the thread
// ---------------------------------------------------------------------------
let thread = null;

function greeting() {
  const h = new Date().getHours();
  const part = h < 5 ? 'Up late?' : h < 12 ? 'Good morning' : h < 18 ? 'Good afternoon' : 'Good evening';
  $('greeting').innerHTML = `<span class="mark"></span>${part}`;
}

function setEmpty(empty) {
  document.body.classList.toggle('empty', empty);
  if (empty) greeting();
}

function renderThread() {
  log.innerHTML = '';
  thread = document.createElement('div');
  thread.className = 'thread';
  log.appendChild(thread);
  setEmpty(!history.length);
  history.forEach((m, i) => {
    if (m.role === 'user') thread.appendChild(userView(m, i));
    else if (m.role === 'assistant') thread.appendChild(assistantView(m, i).root);
  });
  refreshLastActions();
  scrollToEnd(true);
}

// An image (a chart a tool made) finishing loading shouldn't leave the view
// short of the end. "load" doesn't bubble, so listen in the capture phase.
let pinnedToEnd = true;
log.addEventListener('scroll', () => { pinnedToEnd = nearBottom(); });
log.addEventListener('load', (e) => {
  if (e.target.tagName === 'IMG' && pinnedToEnd) log.scrollTop = log.scrollHeight;
}, true);

function nearBottom() {
  return log.scrollHeight - log.scrollTop - log.clientHeight < 120;
}
function scrollToEnd(force = false) {
  if (force || nearBottom()) log.scrollTop = log.scrollHeight;
}

function actionButton(name, title, onClick) {
  const b = document.createElement('button');
  b.className = 'icon-btn';
  b.title = title;
  b.setAttribute('aria-label', title);
  b.innerHTML = icon(name);
  b.addEventListener('click', onClick);
  return b;
}

async function copyText(text, button) {
  try {
    await navigator.clipboard.writeText(text);
    button.innerHTML = icon('check');
  } catch {
    button.title = "Couldn't copy";
  }
  setTimeout(() => { button.innerHTML = icon(button.dataset.icon || 'copy'); }, 1400);
}

function fileChip(f, removable) {
  const chip = document.createElement('span');
  chip.className = 'file-chip';
  if (f._dataUrl) {
    const img = document.createElement('img');
    img.src = f._dataUrl;
    img.alt = '';
    chip.appendChild(img);
  } else {
    chip.insertAdjacentHTML('beforeend', icon('file'));
  }
  const name = document.createElement('span');
  name.className = 'name';
  name.textContent = f.name;
  chip.appendChild(name);
  if (removable) {
    const state = document.createElement('span');
    state.className = 'state';
    chip.appendChild(state);
  }
  return chip;
}

function userView(m, index) {
  const wrap = document.createElement('div');
  wrap.className = 'msg-user';
  wrap.dataset.index = index;
  if (m.files && m.files.length) {
    const files = document.createElement('div');
    files.className = 'msg-files';
    for (const f of m.files) {
      if (f._dataUrl) {
        const img = document.createElement('img');
        img.src = f._dataUrl;
        img.alt = f.name;
        files.appendChild(img);
      } else {
        files.appendChild(fileChip(f, false));
      }
    }
    wrap.appendChild(files);
  }
  const text = m.text !== undefined ? m.text : m.content;
  if (text) {
    const bubble = document.createElement('div');
    bubble.className = 'bubble';
    bubble.textContent = text;
    wrap.appendChild(bubble);
  }
  const actions = document.createElement('div');
  actions.className = 'msg-actions';
  const copy = actionButton('copy', 'Copy', () => copyText(text || '', copy));
  copy.dataset.icon = 'copy';
  actions.appendChild(copy);
  const edit = actionButton('edit', 'Edit and resend', () => startEdit(index));
  edit.classList.add('edit-btn');
  actions.appendChild(edit);
  wrap.appendChild(actions);
  return wrap;
}

function thinkingView(view) {
  const details = document.createElement('details');
  details.className = 'thinking';
  details.innerHTML = `<summary>${icon('bulb')}<span class="label">Thinking…</span>${icon('chevron', 'chev')}</summary><div class="thought"></div>`;
  view.root.insertBefore(details, view.answer);
  view.thinking = details;
  view.thought = details.querySelector('.thought');
  return details;
}

function setThought(view, text, ms, live) {
  if (!view.thinking) thinkingView(view);
  view.thought.textContent = text;
  view.thinking.classList.toggle('live', live);
  const label = view.thinking.querySelector('.label');
  label.textContent = live ? 'Thinking…' : ms ? `Thought for ${Math.max(1, Math.round(ms / 1000))}s` : 'Thought process';
}

function speedTag(tps, approx) {
  const tag = document.createElement('span');
  tag.className = 'speed';
  tag.title = 'Generation speed (not counting reading the prompt)' + (approx ? ' - estimated' : '');
  tag.innerHTML = `${icon('bolt')}${approx ? '~' : ''}${tps.toFixed(1)} tok/s`;
  return tag;
}

const TOOL_LABELS = {
  web_search: 'Searched the web',
  fetch_url: 'Read a web page',
  read_document: 'Read a document',
  create_file: 'Made a file',
  memory: 'Checked notes',
  reminder: 'Reminders',
  get_current_time: 'Checked the time',
  run_python: 'Ran some code',
  ask_claude: 'Asked Claude',
  ask_council: 'Asked the council',
  create_podcast: 'Made a podcast',
  list_files: 'Looked at files',
  read_file: 'Read a file',
  write_file: 'Wrote a file',
};
const toolLabel = (name) => TOOL_LABELS[name] || 'Used ' + name.replace(/_/g, ' ');

// ...and while it's running, for the status line under a reply in progress.
const TOOL_DOING = {
  web_search: 'Searching the web',
  fetch_url: 'Reading a web page',
  read_document: 'Reading a document',
  create_file: 'Making a file',
  memory: 'Checking notes',
  reminder: 'Sorting a reminder',
  get_current_time: 'Checking the time',
  run_python: 'Running some code',
  ask_claude: 'Asking Claude',
  ask_council: 'Asking the council',
  create_podcast: 'Making a podcast',
  list_files: 'Looking at files',
  read_file: 'Reading a file',
  write_file: 'Writing a file',
};
const toolDoing = (name) => TOOL_DOING[name] || 'Using ' + name.replace(/_/g, ' ');

// An assistant reply's view: tool chips, thinking, the answer, actions.
function assistantView(m, index) {
  const root = document.createElement('div');
  root.className = 'msg-assistant';
  if (index !== undefined) root.dataset.index = index;
  const tools = document.createElement('div');
  tools.className = 'tools';
  tools.hidden = true;
  const answer = document.createElement('div');
  answer.className = 'answer';
  const actions = document.createElement('div');
  actions.className = 'msg-actions';
  root.append(tools, answer, actions);
  const view = { root, tools, answer, actions, chips: new Map() };
  if (m) {
    for (const t of m.tools || []) addToolChip(view, t, false);
    if (m.reasoning) setThought(view, m.reasoning, m.thoughtMs, false);
    if (m.content) answer.innerHTML = renderMarkdown(m.content);
    else {
      answer.classList.add('placeholder');
      answer.textContent = m.stopped ? 'Stopped.' : 'No reply.';
    }
    fillActions(view, m);
  }
  return view;
}

function addToolChip(view, name, running) {
  if (view.chips.has(name)) return;
  const chip = document.createElement('span');
  chip.className = 'tool-chip' + (running ? ' running' : '');
  chip.textContent = toolLabel(name);
  view.tools.appendChild(chip);
  view.tools.hidden = false;
  view.chips.set(name, chip);
}

function fillActions(view, m) {
  view.actions.innerHTML = '';
  if (m.content) {
    const copy = actionButton('copy', 'Copy', () => copyText(m.content, copy));
    copy.dataset.icon = 'copy';
    view.actions.appendChild(copy);
  }
  const retry = actionButton('retry', 'Retry', retryLast);
  retry.classList.add('retry-btn');
  view.actions.appendChild(retry);
  if (m.tps) view.actions.appendChild(speedTag(m.tps, m.tpsApprox));
}

// Retry and edit only apply to the latest exchange.
function refreshLastActions() {
  const lastUser = history.map((m) => m.role).lastIndexOf('user');
  for (const el of thread.querySelectorAll('.msg-user')) {
    const own = Number(el.dataset.index) === lastUser && !controller;
    el.querySelector('.edit-btn').hidden = !own;
  }
  const items = thread.querySelectorAll('.msg-assistant');
  items.forEach((el, i) => {
    const btn = el.querySelector('.retry-btn');
    if (btn) btn.hidden = i !== items.length - 1 || !!controller;
    el.querySelector('.msg-actions').classList.toggle('always', i === items.length - 1);
  });
}

// ---------------------------------------------------------------------------
// Markdown (escapes HTML first, so nothing in a reply can inject real tags)
// ---------------------------------------------------------------------------
function escapeHtml(s) {
  return s.replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}
const OWN_FILES = location.origin + '/v1/files/';
function renderInline(text) {
  return text
    // Images only from the harness's own files (what the tools made) - never
    // fetched from anywhere a reply happens to name.
    .replace(/!\[([^\]]*)\]\(([^)\s]+)\)/g, (m, alt, url) => (
      url.startsWith(OWN_FILES) ? `<a href="${url}" target="_blank" rel="noopener"><img class="made-image" src="${url}" alt="${alt}"></a>` : alt
    ))
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/(^|[^*])\*([^*\s][^*]*)\*/g, '$1<em>$2</em>')
    .replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
}
function renderMarkdown(src) {
  const lines = escapeHtml(src).split('\n');
  const out = [];
  let code = null; // lines of an open fence
  let list = null; // {type, items}
  let table = null; // rows of cells

  const flushList = () => {
    if (list) out.push(`<${list.type}>${list.items.map((i) => `<li>${renderInline(i)}</li>`).join('')}</${list.type}>`);
    list = null;
  };
  const flushTable = () => {
    if (!table) return;
    const [head, ...body] = table.filter((r) => !r.every((c) => /^:?-{2,}:?$/.test(c)));
    if (!head) { table = null; return; } // only a divider row - nothing to show
    const row = (cells, tag) => `<tr>${cells.map((c) => `<${tag}>${renderInline(c)}</${tag}>`).join('')}</tr>`;
    out.push(`<table><thead>${row(head, 'th')}</thead><tbody>${body.map((r) => row(r, 'td')).join('')}</tbody></table>`);
    table = null;
  };
  const flush = () => { flushList(); flushTable(); };

  for (const line of lines) {
    if (/^\s*```/.test(line)) {
      if (code) {
        out.push(`<pre><button class="copy-code" type="button">Copy</button><code>${code.join('\n')}</code></pre>`);
        code = null;
      } else {
        flush();
        code = [];
      }
      continue;
    }
    if (code) { code.push(line); continue; }

    if (/^\s*\|.*\|\s*$/.test(line)) {
      flushList();
      (table = table || []).push(line.trim().slice(1, -1).split('|').map((c) => c.trim()));
      continue;
    }
    flushTable();

    const heading = line.match(/^(#{1,6})\s+(.*)/);
    if (heading) {
      flush();
      const level = Math.min(heading[1].length, 4);
      out.push(`<h${level}>${renderInline(heading[2])}</h${level}>`);
      continue;
    }
    const quote = line.match(/^&gt;\s?(.*)/);
    if (quote) { flush(); out.push(`<blockquote>${renderInline(quote[1])}</blockquote>`); continue; }
    const ordered = line.match(/^\s*\d+[.)]\s+(.*)/);
    const bullet = line.match(/^\s*[-*•]\s+(.*)/);
    if (ordered || bullet) {
      const type = ordered ? 'ol' : 'ul';
      if (list && list.type !== type) flushList();
      list = list || { type, items: [] };
      list.items.push((ordered || bullet)[1]);
      continue;
    }
    flushList();
    if (line.trim()) out.push(`<p>${renderInline(line)}</p>`);
  }
  flush();
  if (code) out.push(`<pre><code>${code.join('\n')}</code></pre>`); // a fence still open mid-stream
  return out.join('\n');
}
log.addEventListener('click', (e) => {
  const btn = e.target.closest('.copy-code');
  if (!btn) return;
  navigator.clipboard.writeText(btn.parentElement.querySelector('code').textContent).then(() => {
    btn.textContent = 'Copied';
    setTimeout(() => { btn.textContent = 'Copy'; }, 1400);
  }, () => {});
});

// ---------------------------------------------------------------------------
// Models, connection, tools
// ---------------------------------------------------------------------------
let modelTypes = {}; // id -> "llm" | "vlm" | ...

async function loadModels() {
  modelField.innerHTML = '<option value="">Default model</option>';
  modelTypes = {};
  try {
    const resp = await fetch('/v1/models');
    const body = await resp.json();
    for (const m of body.data || []) {
      if (m.type === 'embeddings') continue;
      modelTypes[m.id] = m.type;
      const opt = document.createElement('option');
      opt.value = m.id;
      opt.textContent = m.id.split('/').pop();
      opt.title = m.id;
      modelField.appendChild(opt);
    }
  } catch {
    // no list - the default model still works
  }
  const saved = load('model', '');
  if ([...modelField.options].some((o) => o.value === saved)) modelField.value = saved;
}
modelField.addEventListener('change', () => store('model', modelField.value || null));

function canSee() {
  const ids = Object.keys(modelTypes);
  const id = modelField.value || (ids.length === 1 ? ids[0] : null);
  return id ? modelTypes[id] === 'vlm' : false;
}

const connStatus = $('connStatus');
const endpointField = $('endpoint');
function setStatus(state, text) {
  connStatus.className = 'status' + (state === 'ok' ? ' ok' : state === 'err' ? ' err' : '');
  connStatus.textContent = text;
}
async function loadStatus() {
  try {
    const body = await (await fetch('/v1/status')).json();
    if (body.base_url) {
      endpointField.value = body.base_url;
      setStatus('ok', 'Connected to ' + body.base_url);
    } else {
      setStatus('mock', 'Using the built-in test model');
    }
  } catch {
    setStatus('err', "Can't reach the harness");
  }
}
async function connect() {
  const url = endpointField.value.trim();
  if (!url) return;
  const btn = $('connect');
  btn.disabled = true;
  setStatus('mock', 'Connecting…');
  try {
    const resp = await fetch('/v1/connect', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ base_url: url }),
    });
    const body = await resp.json();
    if (!resp.ok) throw new Error(body.detail || 'server returned ' + resp.status);
    setStatus('ok', 'Connected to ' + url);
    await loadModels();
  } catch (err) {
    setStatus('err', 'Failed: ' + err.message);
  } finally {
    btn.disabled = false;
  }
}
$('connect').addEventListener('click', connect);
endpointField.addEventListener('keydown', (e) => { if (e.key === 'Enter') connect(); });

// Tools: pendingEnabled is what Settings shows; activeEnabled is what this
// chat sends - it only picks up changes on a new chat, so a conversation's
// tool list stays the same (which keeps the server's prompt cache useful).
let allPlugins = [];
let pendingEnabled = new Set();
let activeEnabled = new Set();

function savePluginChoices() {
  store('enabledPlugins', [...pendingEnabled]);
  store('knownPlugins', allPlugins.map((p) => p.name));
}
async function loadPlugins() {
  try {
    allPlugins = ((await (await fetch('/v1/plugins')).json()).plugins || [])
      .sort((a, b) => a.name.localeCompare(b.name));
  } catch {
    return; // no settings - the server enables everything when none are sent
  }
  const names = allPlugins.map((p) => p.name);
  const saved = load('enabledPlugins', null);
  const known = load('knownPlugins', []);
  // A tool added to the server since starts switched on.
  pendingEnabled = new Set(Array.isArray(saved) ? names.filter((n) => saved.includes(n) || !known.includes(n)) : names);
  activeEnabled = new Set(pendingEnabled);
  savePluginChoices();
  const listEl = $('pluginList');
  listEl.innerHTML = '';
  for (const p of allPlugins) {
    const row = document.createElement('label');
    row.className = 'plugin-row';
    const text = document.createElement('span');
    const name = document.createElement('strong');
    name.textContent = p.name.replace(/_/g, ' ').replace(/^\w/, (c) => c.toUpperCase());
    const desc = document.createElement('small');
    desc.textContent = p.description || '';
    desc.title = p.description || '';
    text.append(name, desc);
    const sw = document.createElement('span');
    sw.className = 'switch';
    sw.innerHTML = '<input type="checkbox"><span class="track"></span>';
    const box = sw.querySelector('input');
    box.checked = pendingEnabled.has(p.name);
    box.addEventListener('change', () => {
      if (box.checked) pendingEnabled.add(p.name);
      else pendingEnabled.delete(p.name);
      savePluginChoices();
    });
    row.append(text, sw);
    listEl.appendChild(row);
  }
}

// Settings dialog
let focusBeforeSettings = null;
function openSettings() {
  focusBeforeSettings = document.activeElement;
  closeSidebarOnPhone();
  settingsEl.hidden = false;
  $('settingsClose').focus();
}
function closeSettings() {
  settingsEl.hidden = true;
  // Back to where you were (keyboard and screen-reader users).
  if (focusBeforeSettings && document.contains(focusBeforeSettings)) focusBeforeSettings.focus();
}
// Tab stays inside the dialog while it's open.
settingsEl.addEventListener('keydown', (e) => {
  if (e.key !== 'Tab') return;
  const focusable = [...settingsEl.querySelectorAll('button, input, select, [tabindex]:not([tabindex="-1"])')]
    .filter((el) => !el.disabled && el.offsetParent !== null);
  if (!focusable.length) return;
  const first = focusable[0];
  const last = focusable[focusable.length - 1];
  if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
  else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
});
$('settingsBtn').addEventListener('click', openSettings);
$('settingsClose').addEventListener('click', closeSettings);
settingsEl.addEventListener('click', (e) => { if (e.target === settingsEl) closeSettings(); });
document.addEventListener('keydown', (e) => {
  if (e.key !== 'Escape') return;
  if (openMenu) closeMenu();
  else if (!settingsEl.hidden) closeSettings();
  else if (controller && document.activeElement === input) controller.abort();
  else closeSidebarOnPhone();
});

// ---------------------------------------------------------------------------
// Attachments
// ---------------------------------------------------------------------------
let pendingFiles = []; // {id, name, kind, path, status, error, _dataUrl, chip}

function clearPendingFiles() {
  pendingFiles = [];
  attachmentsEl.innerHTML = '';
  updateSend();
}

function readDataUrl(file) {
  return new Promise((resolve) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = () => resolve(null);
    reader.readAsDataURL(file);
  });
}

function renderFileChip(f) {
  const chip = fileChip(f, true);
  const state = chip.querySelector('.state');
  if (f.status === 'uploading') state.textContent = 'Uploading…';
  else if (f.status === 'error') {
    chip.classList.add('error');
    state.textContent = f.error;
  }
  const remove = actionButton('close', 'Remove', () => {
    pendingFiles = pendingFiles.filter((x) => x !== f);
    chip.remove();
    updateSend();
  });
  chip.appendChild(remove);
  if (f.chip) f.chip.replaceWith(chip);
  else attachmentsEl.appendChild(chip);
  f.chip = chip;
}

async function addFiles(files) {
  for (const file of files) {
    const f = {
      id: Math.random().toString(36).slice(2), name: file.name || 'pasted-image.png',
      kind: (file.type || '').startsWith('image/') ? 'image' : 'file', status: 'uploading',
    };
    if (f.kind === 'image' && file.size < 8 * 1024 * 1024) f._dataUrl = await readDataUrl(file);
    pendingFiles.push(f);
    renderFileChip(f);
    updateSend();
    fetch('/v1/files?filename=' + encodeURIComponent(f.name), { method: 'POST', body: file })
      .then(async (resp) => {
        const body = await resp.json().catch(() => ({}));
        if (!resp.ok) throw new Error(resp.status === 413 ? 'Too large' : (body.detail || 'Upload failed'));
        f.path = body.path;
        f.status = 'done';
      })
      .catch((err) => {
        f.status = 'error';
        f.error = err.message || 'Upload failed';
      })
      .finally(() => {
        if (pendingFiles.includes(f)) renderFileChip(f);
        updateSend();
      });
  }
  input.focus();
}

$('attachBtn').addEventListener('click', () => fileInput.click());
fileInput.addEventListener('change', () => {
  addFiles([...fileInput.files]);
  fileInput.value = '';
});
input.addEventListener('paste', (e) => {
  const files = [...(e.clipboardData && e.clipboardData.files ? e.clipboardData.files : [])];
  if (files.length) {
    e.preventDefault();
    addFiles(files);
  }
});
let dragDepth = 0;
window.addEventListener('dragenter', (e) => {
  if (![...(e.dataTransfer.types || [])].includes('Files')) return;
  dragDepth++;
  document.body.classList.add('dragging');
});
window.addEventListener('dragleave', () => {
  dragDepth = Math.max(0, dragDepth - 1);
  if (!dragDepth) document.body.classList.remove('dragging');
});
window.addEventListener('dragover', (e) => e.preventDefault());
window.addEventListener('drop', (e) => {
  e.preventDefault();
  dragDepth = 0;
  document.body.classList.remove('dragging');
  if (e.dataTransfer.files.length) addFiles([...e.dataTransfer.files]);
});

// What the model is told about each attachment (it reads files with its tools).
function fileNote(f, seeing) {
  if (f.kind === 'image' && seeing) return `[Attached image: ${f.name}]`;
  const tip = /\.(csv|xlsx?|json|tsv)$/i.test(f.name)
    ? 'Use run_python or read_document to work with it.'
    : f.kind === 'image' ? "You can't see images, so say so if asked about it." : 'Use read_document to read it.';
  return `[Attached ${f.kind === 'image' ? 'image' : 'file'}: ${f.name}, saved as ${f.path}. ${tip}]`;
}

// ---------------------------------------------------------------------------
// Composer
// ---------------------------------------------------------------------------
function autoGrow() {
  input.style.height = 'auto';
  input.style.height = Math.min(input.scrollHeight, window.innerHeight * 0.4) + 'px';
}
function updateSend() {
  const uploading = pendingFiles.some((f) => f.status === 'uploading');
  const ready = input.value.trim() || pendingFiles.some((f) => f.status === 'done');
  sendBtn.disabled = !controller && (uploading || !ready);
  sendBtn.classList.toggle('stop', !!controller);
  sendBtn.innerHTML = icon(controller ? 'stop' : 'send');
  sendBtn.title = controller ? 'Stop' : uploading ? 'Waiting for uploads…' : 'Send';
  sendBtn.setAttribute('aria-label', sendBtn.title);
}
input.addEventListener('input', () => { autoGrow(); updateSend(); });
input.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
    e.preventDefault();
    if (!sendBtn.disabled && !controller) sendMessage();
  }
});
$('composer').addEventListener('submit', (e) => {
  e.preventDefault();
  if (controller) controller.abort();
  else sendMessage();
});

const SUGGESTIONS = [
  ['Summarise a document', 'Summarise the attached document in a few bullet points.'],
  ['Crunch some numbers', 'Work this out for me: '],
  ['Plan something', 'Help me plan '],
  ['Explain simply', 'Explain this to me simply: '],
];
for (const [label, text] of SUGGESTIONS) {
  const b = document.createElement('button');
  b.type = 'button';
  b.textContent = label;
  b.addEventListener('click', () => {
    input.value = text;
    autoGrow();
    updateSend();
    input.focus();
    input.setSelectionRange(text.length, text.length);
  });
  $('suggestions').appendChild(b);
}

function sendMessage() {
  const text = input.value.trim();
  const files = pendingFiles.filter((f) => f.status === 'done');
  if (!text && !files.length) return;
  const seeing = canSee();
  const notes = files.map((f) => fileNote(f, seeing));
  const content = [text, ...notes].filter(Boolean).join('\n');
  const msg = {
    role: 'user', content, text,
    files: files.map((f) => ({ name: f.name, kind: f.kind, path: f.path, _dataUrl: f._dataUrl })),
  };
  const images = seeing ? files.filter((f) => f.kind === 'image' && f._dataUrl) : [];
  if (images.length) {
    msg._parts = [{ type: 'text', text: content }, ...images.map((f) => ({ type: 'image_url', image_url: { url: f._dataUrl } }))];
  }
  history.push(msg);
  input.value = '';
  autoGrow();
  clearPendingFiles();
  if (!thread || document.body.classList.contains('empty')) renderThread();
  else thread.appendChild(userView(msg, history.length - 1));
  setEmpty(false);
  scrollToEnd(true);
  runTurn();
}

function startEdit(index) {
  if (controller) return;
  const m = history[index];
  const el = thread.querySelector(`.msg-user[data-index="${index}"]`);
  if (!m || !el) return;
  const box = document.createElement('div');
  box.className = 'msg-edit';
  box.innerHTML = '<textarea></textarea><div class="row"><button type="button" class="btn">Cancel</button><button type="button" class="btn primary">Send</button></div>';
  const area = box.querySelector('textarea');
  area.value = m.text !== undefined ? m.text : m.content;
  el.replaceChildren(box);
  area.focus();
  area.setSelectionRange(area.value.length, area.value.length);
  const [cancel, send] = box.querySelectorAll('button');
  cancel.addEventListener('click', renderThread);
  const submitEdit = () => {
    const text = area.value.trim();
    if (!text) return;
    const notes = (m.content || '').split('\n').filter((l) => /^\[Attached /.test(l));
    const edited = { ...m, text, content: [text, ...notes].join('\n') };
    if (edited._parts) edited._parts = [{ type: 'text', text: edited.content }, ...edited._parts.slice(1)];
    history = history.slice(0, index).concat(edited);
    renderThread();
    runTurn();
  };
  send.addEventListener('click', submitEdit);
  area.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); submitEdit(); }
    if (e.key === 'Escape') renderThread();
  });
}

function retryLast() {
  if (controller) return;
  while (history.length && history[history.length - 1].role === 'assistant') history.pop();
  if (!history.length) return;
  renderThread();
  runTurn();
}

// ---------------------------------------------------------------------------
// A turn: stream the reply
// ---------------------------------------------------------------------------
function apiMessages() {
  return history.map((m) => ({ role: m.role, content: m._parts || m.content }));
}

async function runTurn() {
  // The chat this turn belongs to - which may no longer be the one on screen
  // by the time it finishes (switching chats stops it, but its "Stopped."
  // reply still belongs here, not in the chat switched to).
  const turnHistory = history;
  const turnId = currentId;
  controller = new AbortController();
  // Screen readers: announce the reply when it's done, not every word of it
  // (or every tick of the status line) as it arrives.
  log.setAttribute('aria-busy', 'true');
  updateSend();
  refreshLastActions();
  const view = assistantView();
  thread.appendChild(view.root);
  scrollToEnd(true);

  // What she's doing right now, and for how long - so a slow step (a long
  // document to read, code running, Claude being asked) never looks frozen.
  const status = document.createElement('div');
  status.className = 'working';
  status.innerHTML = '<span class="typing"><span></span><span></span><span></span></span><span class="what"></span><span class="elapsed"></span>';
  view.root.insertBefore(status, view.actions);
  const turnStart = performance.now();
  let phase = null;
  const tick = () => {
    const secs = Math.floor((performance.now() - turnStart) / 1000);
    const elapsed = secs >= 2 ? `${secs}s` : '';
    status.querySelector('.elapsed').textContent = elapsed;
    if (phase === 'Thinking' && view.thinking) {
      view.thinking.querySelector('.label').textContent = 'Thinking…' + (elapsed ? ' ' + elapsed : '');
    }
  };
  const setPhase = (p) => {
    phase = p;
    // While thinking, the "Thinking…" line shows the time instead.
    status.hidden = !p || p === 'Thinking';
    if (p) status.querySelector('.what').textContent = p + '…';
    tick();
  };
  setPhase('Reading your message');
  const ticker = setInterval(tick, 1000);

  const reply = { role: 'assistant', content: '', tools: [], reasoning: '' };
  let started = false;
  let thinkStart = null;
  let thinkEnd = null;
  let serverTps = null;
  let firstDelta = null;
  let lastDelta = null;
  let deltas = 0;

  const showAnswer = () => {
    if (!started) {
      started = true;
      for (const chip of view.chips.values()) chip.classList.remove('running');
    }
    view.answer.innerHTML = renderMarkdown(reply.content);
    scrollToEnd();
  };

  const body = { messages: apiMessages(), stream: true, owner: true, thinking: prefs.thinking };
  if (modelField.value) body.model = modelField.value;
  if (allPlugins.length) body.enabled_plugins = [...activeEnabled];

  try {
    const resp = await fetch('/v1/chat/completions', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body), signal: controller.signal,
    });
    if (!resp.ok) {
      const detail = await resp.json().then((b) => b.detail, () => null);
      throw new Error(typeof detail === 'string' ? detail : 'the server returned ' + resp.status);
    }
    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let idx;
      while ((idx = buffer.indexOf('\n\n')) !== -1) {
        const line = buffer.slice(0, idx).trim();
        buffer = buffer.slice(idx + 2);
        if (!line.startsWith('data:')) continue;
        const data = line.slice(5).trim();
        if (data === '[DONE]') continue;
        let chunk;
        try { chunk = JSON.parse(data); } catch { continue; }

        if (chunk.error) {
          // The model server failed (it's down, the prompt doesn't fit...).
          throw new Error(chunk.error);
        }
        if (chunk.attachments) {
          // Files a tool made this turn - download links, saved with the reply.
          const links = chunk.attachments.map((a) => {
            // encodeURI leaves ( and ) alone - which would end the markdown
            // link early for a file like "chart (1).png".
            const url = location.origin + encodeURI(a.url).replace(/\(/g, '%28').replace(/\)/g, '%29');
            const image = /^image\/(png|jpe?g|gif|webp|svg\+xml)$/.test(a.content_type || '');
            return (image ? `![${a.filename}](${url})\n` : '') + `[Download ${a.filename}](${url})`;
          }).join('\n');
          reply.content += (reply.content ? '\n\n' : '') + links;
          showAnswer();
          continue;
        }
        if (chunk.replace_content) {
          // The reply had the model's working in it (e.g. <think> tags) - swap in the cleaned one.
          const { old, new: cleaned } = chunk.replace_content;
          if (reply.content.endsWith(old)) {
            reply.content = reply.content.slice(0, reply.content.length - old.length) + cleaned;
            showAnswer();
          }
          continue;
        }
        if (chunk.timings && chunk.timings.predicted_per_second) serverTps = chunk.timings.predicted_per_second;

        const delta = ((chunk.choices || [])[0] || {}).delta || {};
        const now = performance.now();
        if (delta.reasoning_content || delta.content) {
          firstDelta = firstDelta || now;
          lastDelta = now;
          deltas++;
        }
        if (delta.reasoning_content) {
          thinkStart = thinkStart || now;
          thinkEnd = now;
          reply.reasoning += delta.reasoning_content;
          setThought(view, reply.reasoning, 0, true);
          if (phase !== 'Thinking') setPhase('Thinking');
          else tick(); // setThought reset the label - put the time back
          scrollToEnd();
        }
        for (const tc of delta.tool_calls || []) {
          const name = tc.function && tc.function.name;
          if (name && !reply.tools.includes(name)) {
            reply.tools.push(name);
            addToolChip(view, name, true);
          }
          if (name) setPhase(toolDoing(name));
        }
        if (delta.content) {
          reply.content += delta.content;
          if (phase) setPhase(null); // the words themselves show she's working now
          showAnswer();
        }
      }
    }
  } catch (err) {
    if (err.name === 'AbortError') reply.stopped = true;
    else {
      const note = document.createElement('div');
      note.className = 'error-note';
      note.textContent = 'Something went wrong: ' + err.message;
      thread.appendChild(note);
    }
  } finally {
    clearInterval(ticker);
    log.setAttribute('aria-busy', 'false');
    status.remove();
    controller = null;
    for (const chip of view.chips.values()) chip.classList.remove('running');
    if (reply.reasoning) {
      reply.thoughtMs = thinkEnd && thinkStart ? thinkEnd - thinkStart : 0;
      setThought(view, reply.reasoning, reply.thoughtMs, false);
    } else {
      delete reply.reasoning;
    }
    if (serverTps) reply.tps = serverTps;
    else if (deltas > 8 && lastDelta > firstDelta) {
      reply.tps = deltas / ((lastDelta - firstDelta) / 1000);
      reply.tpsApprox = true;
    }
    if (reply.content) view.answer.innerHTML = renderMarkdown(reply.content);
    else {
      view.answer.classList.add('placeholder');
      view.answer.textContent = reply.stopped ? 'Stopped.' : 'No reply.';
    }
    if (reply.content || reply.stopped || reply.tools.length) {
      turnHistory.push(reply);
      if (history === turnHistory) {
        view.root.dataset.index = history.length - 1;
        saveCurrent();
      } else if (turnId === null || conversations.some((c) => c.id === turnId)) {
        // Moved to another chat meanwhile: saved to its own (unless deleted).
        saveChat(turnHistory, turnId);
        renderConversationList();
      }
    }
    fillActions(view, reply);
    refreshLastActions();
    updateSend();
    input.focus();
  }
}

// ---------------------------------------------------------------------------
// Start-up
// ---------------------------------------------------------------------------
const saved = currentConversation();
if (saved) history = saved.messages.map((m) => ({ ...m }));
else currentId = null;
renderThread();
renderConversationList();
renderTitle();
updateSend();
loadPlugins();
loadModels();
loadStatus();
input.focus();

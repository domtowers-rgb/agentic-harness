const log = document.getElementById('log');
const input = document.getElementById('input');
const sendBtn = document.getElementById('send');
const modelField = document.getElementById('model');
const newChatBtn = document.getElementById('newChat');
const endpointField = document.getElementById('endpoint');
const connectBtn = document.getElementById('connect');
const connStatus = document.getElementById('connStatus');
const connDot = document.getElementById('connDot');
const sidebar = document.getElementById('sidebar');
const sidebarToggle = document.getElementById('sidebarToggle');
const sidebarClose = document.getElementById('sidebarClose');
const scrim = document.getElementById('scrim');
const settingsBtn = document.getElementById('settingsBtn');
const settingsOverlay = document.getElementById('settingsOverlay');
const settingsClose = document.getElementById('settingsClose');
const pluginListEl = document.getElementById('pluginList');
const conversationListEl = document.getElementById('conversationList');

// localStorage can be unavailable (private windows, blocked site data) -
// everything here still works without it, just without remembering.
function storageGet(key, fallback = null) {
  try {
    const value = localStorage.getItem(key);
    return value === null ? fallback : value;
  } catch {
    return fallback;
  }
}
function storageSet(key, value) {
  try {
    if (value === null) localStorage.removeItem(key);
    else localStorage.setItem(key, value);
  } catch {
    // storage full/unavailable - it just won't be remembered this time
  }
}

// --- sidebar ---------------------------------------------------------------

const narrow = window.matchMedia('(max-width: 760px)');

function setSidebarOpen(open) {
  sidebar.classList.toggle('collapsed', !open);
  scrim.classList.toggle('hidden', !(open && narrow.matches));
}
sidebarToggle.addEventListener('click', () => setSidebarOpen(sidebar.classList.contains('collapsed')));
sidebarClose.addEventListener('click', () => setSidebarOpen(false));
scrim.addEventListener('click', () => setSidebarOpen(false));
// On a phone-width screen the sidebar slides over the chat, so start closed.
setSidebarOpen(!narrow.matches);
narrow.addEventListener('change', () => setSidebarOpen(!narrow.matches));

// --- conversations -----------------------------------------------------------

let history = [];
let controller = null;

// conversations: every saved conversation, as {id, title, messages, updatedAt}.
// currentConversationId: which one (if any) the current view corresponds to -
//   null means "not saved yet" (a brand new, still-empty chat).
let conversations = [];
let currentConversationId = null;

function loadConversationsFromStorage() {
  try {
    const saved = JSON.parse(storageGet('conversations', '[]'));
    if (Array.isArray(saved)) return saved;
  } catch {
    // malformed storage - start with no saved conversations
  }
  return [];
}

function saveConversationsToStorage() {
  storageSet('conversations', JSON.stringify(conversations));
}

function saveCurrentConversationId() {
  storageSet('currentConversationId', currentConversationId);
}

function dayGroup(timestamp) {
  const startOfToday = new Date().setHours(0, 0, 0, 0);
  const day = 24 * 60 * 60 * 1000;
  if (timestamp >= startOfToday) return 'Today';
  if (timestamp >= startOfToday - day) return 'Yesterday';
  if (timestamp >= startOfToday - 7 * day) return 'Previous 7 days';
  return 'Older';
}

function renderConversationList() {
  conversationListEl.innerHTML = '';
  if (!conversations.length) {
    const empty = document.createElement('div');
    empty.className = 'conversation-empty';
    empty.textContent = 'Your chats will appear here.';
    conversationListEl.appendChild(empty);
    return;
  }

  let group = null;
  for (const conv of [...conversations].sort((a, b) => b.updatedAt - a.updatedAt)) {
    const thisGroup = dayGroup(conv.updatedAt);
    if (thisGroup !== group) {
      group = thisGroup;
      const heading = document.createElement('div');
      heading.className = 'conversation-group';
      heading.textContent = group;
      conversationListEl.appendChild(heading);
    }

    const row = document.createElement('div');
    row.className = 'conversation-row' + (conv.id === currentConversationId ? ' active' : '');
    row.tabIndex = 0;

    const title = document.createElement('span');
    title.className = 'title';
    title.textContent = conv.title;
    title.title = conv.title;
    row.appendChild(title);

    const del = document.createElement('button');
    del.className = 'delete-btn';
    del.textContent = '✕';
    del.title = 'Delete chat';
    del.setAttribute('aria-label', 'Delete chat: ' + conv.title);
    del.addEventListener('click', (e) => {
      e.stopPropagation();
      if (confirm(`Delete "${conv.title}"? This can't be undone.`)) deleteConversation(conv.id);
    });
    row.appendChild(del);

    row.addEventListener('click', () => loadConversation(conv.id));
    row.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') loadConversation(conv.id);
    });
    conversationListEl.appendChild(row);
  }
}

// The chat itself sits in a centred column inside the scrolling log.
let column = null;

function clearLog() {
  log.innerHTML = '';
  column = document.createElement('div');
  column.className = 'column';
  log.appendChild(column);
}

function showEmptyState() {
  clearLog();
  const empty = document.createElement('div');
  empty.className = 'empty-state';
  empty.innerHTML = '<h2>How can I help?</h2><p>Ask anything - I can search the web, read documents and make files.</p>';
  column.appendChild(empty);
}

function scrollToEnd() {
  log.scrollTop = log.scrollHeight;
}

function renderHistoryIntoLog() {
  if (!history.length) {
    showEmptyState();
    return;
  }
  clearLog();
  for (const msg of history) {
    if (msg.role === 'user') {
      addBubble('user', msg.content);
    } else if (msg.role === 'assistant') {
      const turn = addAssistantTurn();
      turn.bubble.innerHTML = renderMarkdown(msg.content || '');
      finishTurn(turn, msg.content || '', '');
    }
  }
  scrollToEnd();
}

function closeSidebarIfNarrow() {
  if (narrow.matches) setSidebarOpen(false);
}

function deleteConversation(id) {
  conversations = conversations.filter((c) => c.id !== id);
  saveConversationsToStorage();
  if (id === currentConversationId) {
    startNewConversation();
  } else {
    renderConversationList();
  }
}

function loadConversation(id) {
  closeSidebarIfNarrow();
  const conv = conversations.find((c) => c.id === id);
  if (!conv || id === currentConversationId) return;
  if (controller) controller.abort();
  currentConversationId = id;
  saveCurrentConversationId();
  history = conv.messages.map((m) => ({ ...m }));
  activeEnabled = new Set(pendingEnabled);
  renderHistoryIntoLog();
  renderConversationList();
}

function startNewConversation() {
  closeSidebarIfNarrow();
  if (controller) controller.abort();
  currentConversationId = null;
  saveCurrentConversationId();
  history = [];
  showEmptyState();
  activeEnabled = new Set(pendingEnabled);
  renderConversationList();
  input.focus();
}

function saveCurrentConversation() {
  if (!history.length) return;
  const now = Date.now();
  if (!currentConversationId) {
    currentConversationId = window.crypto && window.crypto.randomUUID
      ? window.crypto.randomUUID()
      : 'c' + now + Math.random().toString(36).slice(2);
    saveCurrentConversationId();
    const firstUser = history.find((m) => m.role === 'user');
    const title = ((firstUser && firstUser.content) || 'New chat').replace(/\s+/g, ' ').slice(0, 48);
    conversations.push({ id: currentConversationId, title, messages: history, updatedAt: now });
  } else {
    const conv = conversations.find((c) => c.id === currentConversationId);
    if (conv) {
      conv.messages = history;
      conv.updatedAt = now;
    }
  }
  saveConversationsToStorage();
  renderConversationList();
}

newChatBtn.addEventListener('click', startNewConversation);

// --- tools (plugins) ---------------------------------------------------------

// allPlugins: every plugin the server has loaded.
// pendingEnabled: what the settings switches currently show (persisted).
// activeEnabled: what's actually sent with requests for the current conversation -
//   only adopts pendingEnabled's value when a new chat starts, so toggling a
//   plugin mid-conversation doesn't change the tool set the model has already
//   been seeing (which would defeat any prompt-prefix caching the backend does).
let allPlugins = [];
let pendingEnabled = new Set();
let activeEnabled = new Set();

// How each tool is described when the model uses it.
const TOOL_LABELS = {
  web_search: 'Searched the web',
  fetch_url: 'Read a web page',
  read_document: 'Read a document',
  create_file: 'Made a file',
  memory: 'Checked notes',
  reminder: 'Reminders',
  get_current_time: 'Checked the time',
  calculate: 'Calculated',
  list_files: 'Looked at files',
  read_file: 'Read a file',
  write_file: 'Wrote a file',
  ask_council: 'Asked the council',
  create_podcast: 'Made a podcast',
};
function toolLabel(name) {
  return TOOL_LABELS[name] || 'Used ' + name.replace(/_/g, ' ');
}

function loadEnabledFromStorage(names) {
  try {
    const saved = JSON.parse(storageGet('enabledPlugins', 'null'));
    if (Array.isArray(saved)) {
      // A tool the server has added since is on until switched off.
      const known = JSON.parse(storageGet('knownPlugins', '[]'));
      return new Set(names.filter((n) => saved.includes(n) || !known.includes(n)));
    }
  } catch {
    // malformed storage - fall through to "all enabled"
  }
  return new Set(names);
}

function savePluginChoices() {
  storageSet('enabledPlugins', JSON.stringify([...pendingEnabled]));
  storageSet('knownPlugins', JSON.stringify(allPlugins.map((p) => p.name)));
}

function renderPluginList() {
  pluginListEl.innerHTML = '';
  if (!allPlugins.length) {
    pluginListEl.innerHTML = '<div class="hint">No tools loaded.</div>';
    return;
  }
  for (const p of [...allPlugins].sort((a, b) => a.name.localeCompare(b.name))) {
    const row = document.createElement('label');
    row.className = 'plugin-row';

    const text = document.createElement('div');
    text.className = 'plugin-text';
    const name = document.createElement('div');
    name.className = 'plugin-name';
    name.textContent = p.name.replace(/_/g, ' ');
    const desc = document.createElement('div');
    desc.className = 'plugin-desc';
    desc.textContent = p.description || '';
    desc.title = p.description || '';
    text.append(name, desc);

    const sw = document.createElement('span');
    sw.className = 'switch';
    const checkbox = document.createElement('input');
    checkbox.type = 'checkbox';
    checkbox.checked = pendingEnabled.has(p.name);
    checkbox.addEventListener('change', () => {
      if (checkbox.checked) pendingEnabled.add(p.name);
      else pendingEnabled.delete(p.name);
      savePluginChoices();
    });
    const track = document.createElement('span');
    track.className = 'track';
    sw.append(checkbox, track);

    row.append(text, sw);
    pluginListEl.appendChild(row);
  }
}

async function loadPlugins() {
  try {
    const resp = await fetch('/v1/plugins');
    if (!resp.ok) return;
    const body = await resp.json();
    allPlugins = body.plugins || [];
    pendingEnabled = loadEnabledFromStorage(allPlugins.map((p) => p.name));
    activeEnabled = new Set(pendingEnabled);
    savePluginChoices();
    renderPluginList();
  } catch {
    // tool settings simply won't be available - the server still enables
    // everything, since enabled_plugins is then omitted
  }
}

// --- settings ----------------------------------------------------------------

function openSettings() {
  settingsOverlay.classList.remove('hidden');
  endpointField.focus();
}
function closeSettings() {
  settingsOverlay.classList.add('hidden');
}
settingsBtn.addEventListener('click', openSettings);
settingsClose.addEventListener('click', closeSettings);
settingsOverlay.addEventListener('click', (e) => {
  if (e.target === settingsOverlay) closeSettings();
});
document.addEventListener('keydown', (e) => {
  if (e.key !== 'Escape') return;
  if (!settingsOverlay.classList.contains('hidden')) closeSettings();
  else closeSidebarIfNarrow();
});

// --- models and connection -------------------------------------------------

async function loadModels() {
  modelField.innerHTML = '<option value="">Default model</option>';
  try {
    const resp = await fetch('/v1/models');
    if (!resp.ok) return;
    const body = await resp.json();
    for (const m of body.data || []) {
      if (m.type === 'embeddings') continue; // can't chat
      const opt = document.createElement('option');
      opt.value = m.id;
      opt.textContent = m.id + (m.loaded ? ' (loaded)' : '');
      modelField.appendChild(opt);
    }
  } catch {
    // no models available from the backend - keep just the default option
  }
  // The last model picked, if the server still has it.
  const saved = storageGet('model', '');
  if ([...modelField.options].some((o) => o.value === saved)) modelField.value = saved;
}
modelField.addEventListener('change', () => storageSet('model', modelField.value || null));

function setConnected(state, detail) {
  // state: 'ok' (a real model server), 'mock' (the built-in stand-in), 'err'
  const text = { ok: 'Connected to ' + detail, mock: 'Using the built-in mock model', err: 'Can\'t reach the server' }[state];
  connDot.className = 'conn-dot' + (state === 'ok' ? ' ok' : state === 'err' ? ' err' : '');
  connDot.title = text;
  connStatus.textContent = text;
  connStatus.className = 'status' + (state === 'ok' ? ' ok' : state === 'err' ? ' err' : '');
}

async function loadStatus() {
  try {
    const resp = await fetch('/v1/status');
    if (!resp.ok) throw new Error('server returned ' + resp.status);
    const body = await resp.json();
    if (body.base_url) endpointField.value = body.base_url;
    setConnected(body.base_url ? 'ok' : 'mock', body.base_url);
  } catch {
    setConnected('err');
  }
}

async function connect() {
  const url = endpointField.value.trim();
  if (!url) return;
  connectBtn.disabled = true;
  connStatus.textContent = 'Connecting…';
  connStatus.className = 'status';
  try {
    const resp = await fetch('/v1/connect', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ base_url: url }),
    });
    const body = await resp.json();
    if (!resp.ok) {
      throw new Error(body.detail || ('server returned ' + resp.status));
    }
    setConnected('ok', url);
    await loadModels();
  } catch (err) {
    connStatus.textContent = 'Failed: ' + err.message;
    connStatus.className = 'status err';
  } finally {
    connectBtn.disabled = false;
  }
}

connectBtn.addEventListener('click', connect);
endpointField.addEventListener('keydown', (e) => {
  if (e.key === 'Enter') {
    e.preventDefault();
    connect();
  }
});

// --- markdown ----------------------------------------------------------------

// Minimal, dependency-free markdown renderer for assistant messages. Escapes
// HTML first, so any markup in the source (model output, or text the model
// pulled in via fetch_url) can't inject real tags - only the elements we
// generate below are trusted.
function escapeHtml(s) {
  return s.replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]));
}

function renderInline(text) {
  return text
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/\*([^*]+)\*/g, '<em>$1</em>')
    .replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
}

function renderMarkdown(src) {
  const lines = escapeHtml(src).split('\n');
  const out = [];
  let inCode = false;
  let codeLines = [];
  let listType = null; // 'ul' | 'ol' | null
  let listItems = [];

  function flushList() {
    if (listType) {
      out.push(`<${listType}>` + listItems.map((li) => `<li>${renderInline(li)}</li>`).join('') + `</${listType}>`);
      listType = null;
      listItems = [];
    }
  }
  function flushCode() {
    if (inCode) {
      out.push(`<pre><code>${codeLines.join('\n')}</code></pre>`);
      inCode = false;
      codeLines = [];
    }
  }

  for (const rawLine of lines) {
    if (/^```/.test(rawLine)) {
      if (inCode) {
        flushCode();
      } else {
        flushList();
        inCode = true;
        codeLines = [];
      }
      continue;
    }
    if (inCode) {
      codeLines.push(rawLine);
      continue;
    }

    const heading = rawLine.match(/^(#{1,6})\s+(.*)/);
    if (heading) {
      flushList();
      const level = heading[1].length;
      out.push(`<h${level}>${renderInline(heading[2])}</h${level}>`);
      continue;
    }

    const ordered = rawLine.match(/^\d+\.\s+(.*)/);
    const unordered = rawLine.match(/^[-*]\s+(.*)/);
    if (ordered || unordered) {
      const type = ordered ? 'ol' : 'ul';
      if (listType && listType !== type) flushList();
      listType = type;
      listItems.push((ordered || unordered)[1]);
      continue;
    }
    flushList();

    out.push(rawLine.trim() === '' ? '' : `<p>${renderInline(rawLine)}</p>`);
  }
  flushList();
  flushCode(); // in case a fence was left unclosed mid-stream

  return out.join('\n');
}

// --- messages ----------------------------------------------------------------

function addBubble(role, text) {
  if (column.querySelector('.empty-state')) clearLog();
  const el = document.createElement('div');
  el.className = 'msg ' + role;
  el.textContent = text;
  column.appendChild(el);
  scrollToEnd();
  return el;
}

// An assistant reply: tools used (as chips), the text, then actions.
function addAssistantTurn() {
  if (column.querySelector('.empty-state')) clearLog();
  const wrapper = document.createElement('div');
  wrapper.className = 'turn';
  const tools = document.createElement('div');
  tools.className = 'tool-chips hidden';
  const bubble = document.createElement('div');
  bubble.className = 'msg assistant';
  wrapper.append(tools, bubble);
  column.appendChild(wrapper);
  scrollToEnd();
  return { wrapper, tools, bubble, chips: new Map() };
}

function showToolUse(turn, name) {
  if (turn.chips.has(name)) return;
  const chip = document.createElement('span');
  chip.className = 'tool-chip running';
  chip.textContent = toolLabel(name);
  turn.tools.appendChild(chip);
  turn.tools.classList.remove('hidden');
  turn.chips.set(name, chip);
  scrollToEnd();
}

function toolsDone(turn) {
  for (const chip of turn.chips.values()) chip.classList.remove('running');
}

function finishTurn(turn, text, reasoningText) {
  toolsDone(turn);
  const actions = document.createElement('div');
  actions.className = 'turn-actions';

  if (text) {
    const copy = document.createElement('button');
    copy.textContent = 'Copy';
    copy.addEventListener('click', async () => {
      try {
        await navigator.clipboard.writeText(text);
        copy.textContent = 'Copied';
      } catch {
        copy.textContent = 'Couldn\'t copy';
      }
      setTimeout(() => { copy.textContent = 'Copy'; }, 1500);
    });
    actions.appendChild(copy);
  }

  if (reasoningText) {
    const panel = document.createElement('div');
    panel.className = 'reasoning-panel hidden';
    panel.textContent = reasoningText;
    const toggle = document.createElement('button');
    toggle.textContent = 'Show reasoning';
    toggle.addEventListener('click', () => {
      const hidden = panel.classList.toggle('hidden');
      toggle.textContent = hidden ? 'Show reasoning' : 'Hide reasoning';
    });
    actions.appendChild(toggle);
    turn.wrapper.append(actions, panel);
  } else if (actions.childElementCount) {
    turn.wrapper.appendChild(actions);
  }
}

function setTyping(el, on) {
  if (on) {
    el.innerHTML = '<span class="dots"><span></span><span></span><span></span></span>';
  }
}

// --- composer ----------------------------------------------------------------

function autoGrow() {
  input.style.height = 'auto';
  input.style.height = input.scrollHeight + 'px';
}

function updateSendButton() {
  sendBtn.disabled = !controller && !input.value.trim();
}

function setBusy(busy) {
  sendBtn.textContent = busy ? '■' : '↑';
  sendBtn.title = busy ? 'Stop' : 'Send';
  sendBtn.setAttribute('aria-label', sendBtn.title);
  sendBtn.classList.toggle('stop', busy);
  updateSendButton();
}

input.addEventListener('input', () => {
  autoGrow();
  updateSendButton();
});

sendBtn.addEventListener('click', () => {
  if (controller) {
    controller.abort();
    return;
  }
  submit();
});

input.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
    e.preventDefault();
    submit();
  }
});

async function submit() {
  const text = input.value.trim();
  if (!text || controller) return;

  history.push({ role: 'user', content: text });
  addBubble('user', text);
  input.value = '';
  autoGrow();
  controller = new AbortController();
  setBusy(true);

  const turn = addAssistantTurn();
  const assistantEl = turn.bubble;
  setTyping(assistantEl, true);
  let assistantText = '';
  let gotContent = false;
  let reasoningText = '';

  const body = { messages: history, stream: true };
  const model = modelField.value.trim();
  if (model) body.model = model;
  // Only pin down enabled_plugins once we've actually loaded the plugin list -
  // otherwise an empty set here would look like "disable everything" to the
  // server, rather than the intended "no preference, use the default".
  if (allPlugins.length) {
    body.enabled_plugins = [...activeEnabled];
  }

  try {
    const resp = await fetch('/v1/chat/completions', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      signal: controller.signal,
    });
    if (!resp.ok) {
      throw new Error('server returned ' + resp.status);
    }

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      let idx;
      while ((idx = buffer.indexOf('\n\n')) !== -1) {
        const rawEvent = buffer.slice(0, idx);
        buffer = buffer.slice(idx + 2);
        const line = rawEvent.trim();
        if (!line.startsWith('data:')) continue;
        const data = line.slice(5).trim();
        if (data === '[DONE]') continue;

        let chunk;
        try {
          chunk = JSON.parse(data);
        } catch {
          continue;
        }

        if (chunk.attachments) {
          // Files a tool created this turn (e.g. a presentation) - added to
          // the reply as download links, so they're saved with it too.
          const links = chunk.attachments
            .map((a) => '[Download ' + a.filename + '](' + location.origin + encodeURI(a.url) + ')')
            .join('\n');
          assistantText += (assistantText ? '\n\n' : '') + links;
          gotContent = true;
          assistantEl.innerHTML = renderMarkdown(assistantText);
          continue;
        }

        if (chunk.replace_content) {
          // The reply just streamed had the model's working in it (e.g.
          // <think> tags) - swap it for the cleaned version.
          const { old, new: cleaned } = chunk.replace_content;
          if (assistantText.endsWith(old)) {
            assistantText = assistantText.slice(0, assistantText.length - old.length) + cleaned;
            assistantEl.innerHTML = renderMarkdown(assistantText);
          }
          continue;
        }

        const choice = (chunk.choices || [])[0] || {};
        const delta = choice.delta || {};

        if (delta.reasoning_content) {
          // kept hidden - offered via "Show reasoning" once the turn finishes
          reasoningText += delta.reasoning_content;
        }

        for (const tc of delta.tool_calls || []) {
          const name = tc.function && tc.function.name;
          if (name) showToolUse(turn, name);
        }

        if (delta.content) {
          if (!gotContent) {
            gotContent = true;
            toolsDone(turn);
          }
          assistantText += delta.content;
          assistantEl.innerHTML = renderMarkdown(assistantText);
          scrollToEnd();
        }
      }
    }

    history.push({ role: 'assistant', content: assistantText });
  } catch (err) {
    if (err.name !== 'AbortError') {
      addBubble('error', 'Something went wrong: ' + err.message);
    }
  } finally {
    if (!gotContent) {
      assistantEl.textContent = assistantText || (controller.signal.aborted ? '(stopped)' : '(no reply)');
      assistantEl.style.color = 'var(--muted)';
    }
    finishTurn(turn, assistantText, reasoningText);
    saveCurrentConversation();
    controller = null;
    setBusy(false);
    input.focus();
  }
}

// --- start-up ------------------------------------------------------------------

conversations = loadConversationsFromStorage();
const savedConversationId = storageGet('currentConversationId');
const savedConversation = savedConversationId && conversations.find((c) => c.id === savedConversationId);
if (savedConversation) {
  currentConversationId = savedConversation.id;
  history = savedConversation.messages.map((m) => ({ ...m }));
}
renderHistoryIntoLog();
renderConversationList();
setBusy(false);
loadPlugins();
loadModels();
loadStatus();
input.focus();

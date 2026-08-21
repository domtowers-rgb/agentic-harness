const log = document.getElementById('log');
const input = document.getElementById('input');
const sendBtn = document.getElementById('send');
const modelField = document.getElementById('model');
const newChatBtn = document.getElementById('newChat');
const endpointField = document.getElementById('endpoint');
const connectBtn = document.getElementById('connect');
const connStatus = document.getElementById('connStatus');
const sidebar = document.getElementById('sidebar');
const sidebarToggle = document.getElementById('sidebarToggle');
const settingsBtn = document.getElementById('settingsBtn');
const settingsOverlay = document.getElementById('settingsOverlay');
const settingsClose = document.getElementById('settingsClose');
const pluginListEl = document.getElementById('pluginList');
const conversationListEl = document.getElementById('conversationList');

sidebarToggle.addEventListener('click', () => {
  sidebar.classList.toggle('collapsed');
});

let history = [];
let controller = null;

// conversations: every saved conversation, as {id, title, messages, updatedAt}.
// currentConversationId: which one (if any) the current view corresponds to -
//   null means "not saved yet" (a brand new, still-empty chat).
let conversations = [];
let currentConversationId = null;

function loadConversationsFromStorage() {
  try {
    const saved = JSON.parse(localStorage.getItem('conversations') || '[]');
    if (Array.isArray(saved)) return saved;
  } catch {
    // ignore malformed storage - start with no saved conversations
  }
  return [];
}

function saveConversationsToStorage() {
  try {
    localStorage.setItem('conversations', JSON.stringify(conversations));
  } catch {
    // storage full/unavailable - persistence just won't stick this time
  }
}

function loadCurrentConversationId() {
  try {
    return localStorage.getItem('currentConversationId') || null;
  } catch {
    return null;
  }
}

function saveCurrentConversationId() {
  try {
    if (currentConversationId) {
      localStorage.setItem('currentConversationId', currentConversationId);
    } else {
      localStorage.removeItem('currentConversationId');
    }
  } catch {
    // ignore - just won't resume into this conversation on next page load
  }
}

function renderConversationList() {
  conversationListEl.innerHTML = '';
  if (!conversations.length) {
    const empty = document.createElement('div');
    empty.className = 'conversation-empty';
    empty.textContent = 'No saved conversations yet';
    conversationListEl.appendChild(empty);
    return;
  }

  const sorted = [...conversations].sort((a, b) => b.updatedAt - a.updatedAt);
  for (const conv of sorted) {
    const row = document.createElement('div');
    row.className = 'conversation-row' + (conv.id === currentConversationId ? ' active' : '');

    const title = document.createElement('span');
    title.className = 'title';
    title.textContent = conv.title;
    title.title = conv.title;
    row.appendChild(title);

    const del = document.createElement('button');
    del.className = 'delete-btn';
    del.textContent = '✕';
    del.title = 'Delete conversation';
    del.addEventListener('click', (e) => {
      e.stopPropagation();
      deleteConversation(conv.id);
    });
    row.appendChild(del);

    row.addEventListener('click', () => loadConversation(conv.id));
    conversationListEl.appendChild(row);
  }
}

function renderHistoryIntoLog() {
  log.innerHTML = '';
  for (const msg of history) {
    if (msg.role === 'user') {
      addBubble('user', msg.content);
    } else if (msg.role === 'assistant') {
      const { bubble } = addAssistantTurn();
      bubble.innerHTML = renderMarkdown(msg.content || '');
    }
  }
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
  if (controller) controller.abort();
  currentConversationId = null;
  saveCurrentConversationId();
  history = [];
  log.innerHTML = '';
  activeEnabled = new Set(pendingEnabled);
  renderConversationList();
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
    const title = ((firstUser && firstUser.content) || 'New conversation').slice(0, 48);
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

conversations = loadConversationsFromStorage();
const savedConversationId = loadCurrentConversationId();
const savedConversation = savedConversationId && conversations.find((c) => c.id === savedConversationId);
if (savedConversation) {
  currentConversationId = savedConversation.id;
  history = savedConversation.messages.map((m) => ({ ...m }));
  renderHistoryIntoLog();
}
renderConversationList();

// allPlugins: every plugin the server has loaded.
// pendingEnabled: what the settings checkboxes currently show (persisted to localStorage).
// activeEnabled: what's actually sent with requests for the current conversation -
//   only adopts pendingEnabled's value when a new chat starts, so toggling a
//   plugin mid-conversation doesn't change the tool set the model has already
//   been seeing (which would defrag any prompt-prefix caching the backend does).
let allPlugins = [];
let pendingEnabled = new Set();
let activeEnabled = new Set();

function loadEnabledFromStorage(names) {
  try {
    const saved = JSON.parse(localStorage.getItem('enabledPlugins') || 'null');
    if (Array.isArray(saved)) {
      return new Set(names.filter((n) => saved.includes(n)));
    }
  } catch {
    // ignore malformed storage - fall through to "all enabled"
  }
  return new Set(names);
}

function renderPluginList() {
  pluginListEl.innerHTML = '';
  for (const p of allPlugins) {
    const row = document.createElement('label');
    row.className = 'plugin-row';

    const checkbox = document.createElement('input');
    checkbox.type = 'checkbox';
    checkbox.checked = pendingEnabled.has(p.name);
    checkbox.addEventListener('change', () => {
      if (checkbox.checked) {
        pendingEnabled.add(p.name);
      } else {
        pendingEnabled.delete(p.name);
      }
      localStorage.setItem('enabledPlugins', JSON.stringify([...pendingEnabled]));
    });

    const label = document.createElement('span');
    label.textContent = p.name;
    label.title = p.description || '';

    row.appendChild(checkbox);
    row.appendChild(label);
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
    renderPluginList();
  } catch {
    // plugin settings simply won't be available - the server will still
    // default to enabling everything since enabled_plugins is omitted
  }
}
loadPlugins();

settingsBtn.addEventListener('click', () => {
  settingsOverlay.classList.remove('hidden');
});
settingsClose.addEventListener('click', () => {
  settingsOverlay.classList.add('hidden');
});
settingsOverlay.addEventListener('click', (e) => {
  if (e.target === settingsOverlay) settingsOverlay.classList.add('hidden');
});

async function loadModels() {
  try {
    const resp = await fetch('/v1/models');
    if (!resp.ok) return;
    const body = await resp.json();
    for (const m of body.data || []) {
      const opt = document.createElement('option');
      opt.value = m.id;
      opt.textContent = m.id;
      modelField.appendChild(opt);
    }
  } catch {
    // no models available from the backend - keep just the default option
  }
}

async function refreshModelOptions() {
  modelField.innerHTML = '<option value="">(server default)</option>';
  await loadModels();
}
loadModels();

async function loadStatus() {
  try {
    const resp = await fetch('/v1/status');
    if (!resp.ok) return;
    const body = await resp.json();
    if (body.base_url) {
      endpointField.value = body.base_url;
      connStatus.textContent = 'connected';
      connStatus.className = 'status ok';
    } else {
      connStatus.textContent = 'using built-in mock backend';
      connStatus.className = 'status';
    }
  } catch {
    // ignore - status is informational only
  }
}
loadStatus();

async function connect() {
  const url = endpointField.value.trim();
  if (!url) return;
  connectBtn.disabled = true;
  connStatus.textContent = 'connecting…';
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
    connStatus.textContent = 'connected';
    connStatus.className = 'status ok';
    await refreshModelOptions();
  } catch (err) {
    connStatus.textContent = 'failed: ' + err.message;
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

function addBubble(role, text) {
  const el = document.createElement('div');
  el.className = 'msg ' + role;
  el.textContent = text;
  log.appendChild(el);
  log.scrollTop = log.scrollHeight;
  return el;
}

function addAssistantTurn() {
  const wrapper = document.createElement('div');
  wrapper.className = 'turn';
  const bubble = document.createElement('div');
  bubble.className = 'msg assistant';
  wrapper.appendChild(bubble);
  log.appendChild(wrapper);
  log.scrollTop = log.scrollHeight;
  return { wrapper, bubble };
}

function addReasoningToggle(wrapper, reasoningText) {
  const toggle = document.createElement('button');
  toggle.className = 'reasoning-toggle';
  toggle.title = 'Show reasoning';
  toggle.textContent = '🧠';

  const panel = document.createElement('div');
  panel.className = 'reasoning-panel hidden';
  panel.textContent = reasoningText;

  toggle.addEventListener('click', () => {
    panel.classList.toggle('hidden');
  });

  wrapper.appendChild(toggle);
  wrapper.appendChild(panel);
}

function addStatsLine(wrapper, tokensPerSecond, isReal) {
  const stats = document.createElement('div');
  stats.className = 'turn-stats';
  stats.textContent = (isReal ? '' : '~') + tokensPerSecond.toFixed(1) + ' tok/s';
  if (!isReal) {
    stats.title = 'Estimated from response length - the backend did not report token usage';
  }
  wrapper.appendChild(stats);
}

function setTyping(el, on) {
  el.classList.toggle('pending', on);
  if (on) {
    el.innerHTML = '<span class="dots"><span></span><span></span><span></span></span>';
  }
}

function setBusy(busy) {
  input.disabled = busy;
  sendBtn.textContent = busy ? 'Stop' : 'Send';
  sendBtn.classList.toggle('stop', busy);
}

newChatBtn.addEventListener('click', startNewConversation);

sendBtn.addEventListener('click', () => {
  if (controller) {
    controller.abort();
    return;
  }
  submit();
});

input.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey) {
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
  setBusy(true);

  const { wrapper: assistantWrapper, bubble: assistantEl } = addAssistantTurn();
  setTyping(assistantEl, true);
  let assistantText = '';
  let gotContent = false;
  let toolEl = null;
  let reasoningText = '';
  let completionTokens = 0;
  let usageIsReal = false;
  const startTime = performance.now();

  const body = { messages: history, stream: true };
  const model = modelField.value.trim();
  if (model) body.model = model;
  // Only pin down enabled_plugins once we've actually loaded the plugin list -
  // otherwise an empty set here would look like "disable everything" to the
  // server, rather than the intended "no preference, use the default".
  if (allPlugins.length) {
    body.enabled_plugins = [...activeEnabled];
  }

  controller = new AbortController();
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

        if (chunk.error) {
          addBubble('error', 'Error: ' + chunk.error);
          continue;
        }

        // Some backends (confirmed: real OpenAI API, LM Studio) send a
        // final usage-only chunk when stream_options.include_usage is
        // requested (model.py always requests it). A tool-call round trip
        // means multiple such chunks can arrive in one response - sum them
        // for the total generation across every round.
        if (chunk.usage && typeof chunk.usage.completion_tokens === 'number') {
          completionTokens += chunk.usage.completion_tokens;
          usageIsReal = true;
        }

        const choice = (chunk.choices || [])[0] || {};
        const delta = choice.delta || {};

        if (delta.reasoning_content) {
          // kept hidden by default - surfaced via the reasoning-toggle icon once the turn finishes
          reasoningText += delta.reasoning_content;
        }

        if (delta.tool_calls) {
          for (const tc of delta.tool_calls) {
            const name = tc.function && tc.function.name;
            if (name && !toolEl) {
              toolEl = addBubble('tool', 'using tool: ' + name);
            }
          }
        }

        if (delta.content) {
          if (!gotContent) {
            gotContent = true;
            setTyping(assistantEl, false);
          }
          assistantText += delta.content;
          assistantEl.innerHTML = renderMarkdown(assistantText);
          log.scrollTop = log.scrollHeight;
          toolEl = null; // a new round of real content started
        }
      }
    }

    history.push({ role: 'assistant', content: assistantText });
  } catch (err) {
    if (err.name !== 'AbortError') {
      addBubble('error', 'Error: ' + err.message);
    }
  } finally {
    if (!gotContent) {
      setTyping(assistantEl, false);
      assistantEl.textContent = assistantText || '(no content in response)';
    }
    if (reasoningText) {
      addReasoningToggle(assistantWrapper, reasoningText);
    }
    if (gotContent) {
      const elapsedSeconds = (performance.now() - startTime) / 1000;
      // Fall back to a rough chars-per-token estimate when the backend
      // didn't report real usage, so the UI still shows something rather
      // than nothing.
      const tokens = usageIsReal ? completionTokens : Math.max(1, Math.round(assistantText.length / 4));
      if (elapsedSeconds > 0) {
        addStatsLine(assistantWrapper, tokens / elapsedSeconds, usageIsReal);
      }
    }
    saveCurrentConversation();
    controller = null;
    setBusy(false);
    input.focus();
  }
}

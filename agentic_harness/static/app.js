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

sidebarToggle.addEventListener('click', () => {
  sidebar.classList.toggle('collapsed');
});

let history = [];
let controller = null;

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

newChatBtn.addEventListener('click', () => {
  if (controller) {
    controller.abort();
  }
  history = [];
  log.innerHTML = '';
  activeEnabled = new Set(pendingEnabled);
});

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
          assistantEl.textContent = assistantText;
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
    controller = null;
    setBusy(false);
    input.focus();
  }
}

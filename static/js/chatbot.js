(() => {
  'use strict';
  const root = document.querySelector('[data-chatbot]');
  const configNode = document.getElementById('chatbot-config');
  if (!root || !configNode || !window.fetch || !window.HTMLDialogElement) return;
  const config = JSON.parse(configNode.textContent);
  const text = config.strings;
  const find = name => root.querySelector(`[data-chatbot-${name}]`);
  const panel = find('panel'), launcher = find('launcher'), input = find('input');
  const form = find('form'), send = find('send'), reset = find('reset');
  const messages = find('messages'), scroll = find('scroll'), status = find('status');
  const error = find('error'), errorText = find('error-text'), retry = find('retry');
  const suggestions = Array.from(root.querySelectorAll('[data-chatbot-suggestion]'));
  const mobile = window.matchMedia('(max-width: 768px)');
  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
  let snapshot = null, activeRequest = null, ready = false, busy = false, loading = false;
  let opened = false, modal = false, closing = false, pollTimer = null, retryAction = null;
  let renderedConversation = null, pageEpoch = 0;
  const controllers = new Set();
  class StalePage extends Error {}
  const rendered = new Map();
  const format = (value, values) => value.replace(/\{(\w+)\}/g, (_, key) => String(values[key]));
  const atCapacity = () => snapshot?.usage.limit !== null && snapshot?.usage.remaining === 0;
  const exhausted = () => snapshot?.usage.limit !== null && snapshot?.usage.consumed >= snapshot?.usage.limit;
  const remember = (key, value) => { try { window.sessionStorage.setItem(key, value); } catch (_) { /* Optional UI preference. */ } };
  const recalled = key => { try { return window.sessionStorage.getItem(key); } catch (_) { return null; } };
  const csrfToken = () => {
    const name = config.csrfCookieName || 'csrftoken';
    const cookie = document.cookie.split('; ').find(value => value.startsWith(`${name}=`));
    return cookie ? decodeURIComponent(cookie.slice(name.length + 1)) : form.querySelector('[name=csrfmiddlewaretoken]')?.value || '';
  };
  const requestId = () => {
    if (window.crypto.randomUUID) return window.crypto.randomUUID();
    const bytes = window.crypto.getRandomValues(new Uint8Array(16));
    bytes[6] = (bytes[6] & 15) | 64; bytes[8] = (bytes[8] & 63) | 128;
    const hex = Array.from(bytes, byte => byte.toString(16).padStart(2, '0')).join('');
    return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
  };

  async function api(url, body) {
    const epoch = pageEpoch;
    const controller = new AbortController();
    controllers.add(controller);
    const timer = window.setTimeout(() => controller.abort(), config.requestTimeoutMs || 20000);
    try {
      const headers = { Accept: 'application/json' };
      if (body !== undefined) Object.assign(headers, { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken() });
      const response = await window.fetch(url, { method: body === undefined ? 'GET' : 'POST',
        credentials: 'same-origin', mode: 'same-origin', cache: 'no-store', redirect: 'error',
        headers, signal: controller.signal, ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
      const json = response.headers.get('Content-Type')?.includes('application/json');
      if (!json && response.status !== 403) throw new Error('Unexpected response');
      const data = json ? await response.json() : {};
      if (epoch !== pageEpoch) throw new StalePage();
      return { code: response.status, data };
    } catch (error) {
      if (epoch !== pageEpoch) throw new StalePage();
      throw error;
    } finally { window.clearTimeout(timer); controllers.delete(controller); }
  }

  function controls() {
    const blocked = !ready || busy || Boolean(activeRequest) || atCapacity();
    input.disabled = !ready || exhausted();
    input.readOnly = busy || Boolean(activeRequest);
    send.disabled = blocked || !input.value.trim();
    reset.disabled = !ready || busy || Boolean(activeRequest);
    suggestions.forEach(button => { button.disabled = blocked; });
    retry.disabled = busy;
    root.classList.toggle('is-busy', busy);
    form.setAttribute('aria-busy', String(busy));
  }

  function setBusy(value, label = '') {
    busy = value;
    if (status.textContent !== label) status.textContent = label;
    controls();
  }
  function clearError() { error.hidden = true; errorText.textContent = ''; retryAction = null; }
  function showError(message, action = null, label = text.retry) {
    errorText.textContent = message; error.hidden = false; retryAction = action;
    retry.textContent = label; retry.hidden = !action; controls();
  }
  function resizeInput() { input.style.height = 'auto'; input.style.height = `${Math.min(input.scrollHeight, 88)}px`; }
  function toBottom() { scroll.scrollTop = scroll.scrollHeight; }

  function renderMessage(message, announce) {
    const key = `${message.role}:${message.request_id}`;
    let item = rendered.get(key);
    if (!item) {
      item = document.createElement('li');
      item.className = `chatbot__message chatbot__message--${message.role}`;
      const speaker = document.createElement('span'); speaker.className = 'chatbot__sr-only';
      speaker.textContent = message.role === 'user' ? text.you : text.assistant;
      item.append(speaker);
      if (message.role === 'assistant') item.append(root.querySelector('.chatbot__header .chatbot__avatar').cloneNode(true));
      const bubble = document.createElement('div'); bubble.className = 'chatbot__bubble';
      const content = document.createElement('p'), time = document.createElement('time');
      const note = document.createElement('span'); note.className = 'chatbot__message-note';
      const sources = document.createElement('div'); sources.className = 'chatbot__sources'; sources.hidden = true;
      note.hidden = true; bubble.append(content, sources, time, note); item.append(bubble);
      rendered.set(key, item);
      // Set content before insertion, so assistive technology announces a complete message.
      updateMessage(item, message);
      messages.append(item);
    } else updateMessage(item, message);
  }

  function updateMessage(item, message) {
    const content = item.querySelector('.chatbot__bubble p');
    if (content.textContent !== message.content) content.textContent = message.content;
    const time = item.querySelector('time'), date = new Date(message.created_at);
    if (Number.isFinite(date.getTime())) {
      if (time.dateTime !== message.created_at) time.dateTime = message.created_at;
      const label = new Intl.DateTimeFormat(document.documentElement.lang || 'es', { hour: '2-digit', minute: '2-digit' }).format(date);
      if (time.textContent !== label) time.textContent = label;
    }
    const note = item.querySelector('.chatbot__message-note');
    note.hidden = message.status !== 'failed';
    if (!note.hidden && note.textContent !== text.failed) note.textContent = text.failed;
    updateSources(item, message.role === 'assistant' ? message.sources : []);
  }

  function officialUrl(value) {
    if (typeof value !== 'string' || !value.startsWith('/') || value.startsWith('//') || /[\\\s]/.test(value)) return null;
    try {
      const url = new URL(value, window.location.origin), paths = config.sourcePaths || {};
      if (url.origin !== window.location.origin) return null;
      if (url.pathname === paths.faq && !url.search && /^#faq-question-\d+$/.test(url.hash)) return value;
      if (value === `${paths.home}#puntos-limpios`) return value;
      const entries = Array.from(url.searchParams.entries());
      if (url.pathname === paths.catalog && !url.hash && entries.length === 1 &&
          ['category', 'subcategory'].includes(entries[0][0]) && entries[0][1]) return value;
    } catch (_) { /* An invalid link is never rendered. */ }
    return null;
  }

  function updateSources(item, values) {
    const sources = Array.isArray(values) ? values.slice(0, 3) : [];
    const signature = JSON.stringify(sources);
    if (item.chatbotSources === signature) return;
    item.chatbotSources = signature;
    const container = item.querySelector('.chatbot__sources');
    container.replaceChildren(); container.hidden = sources.length === 0;
    if (!sources.length) return;
    const heading = document.createElement('p'); heading.textContent = text.sources;
    const list = document.createElement('ul'), seen = new Set();
    let unavailable = false;
    sources.forEach(source => {
      const url = officialUrl(source?.url);
      const valid = source?.available === true && typeof source.title === 'string' && source.title.trim() &&
        (url || (source.url === null && source.type === 'business_rule'));
      if (!valid && unavailable) return;
      const key = valid ? `${source.title}:${url}` : 'unavailable';
      if (seen.has(key)) return;
      seen.add(key);
      const row = document.createElement('li');
      if (valid && url) {
        const link = document.createElement('a'); link.href = url;
        link.textContent = source.title; link.setAttribute('aria-label', `${text.officialInformation}: ${source.title}`);
        link.addEventListener('click', () => { remember('trueca-chatbot-open', '0'); closePanel(); });
        row.append(link);
        if (source.type === 'center' && typeof source.directions_url === 'string') {
          try {
            const maps = new URL(source.directions_url), params = maps.searchParams;
            const destination = params.get('destination') || '';
            const coordinates = destination.split(',').map(Number);
            if (maps.origin === 'https://www.google.com' && maps.pathname === '/maps/dir/' && !maps.hash &&
                !maps.username && !maps.password && /^-?\d+(?:\.\d+)?,-?\d+(?:\.\d+)?$/.test(destination) &&
                params.get('api') === '1' && Array.from(params).length === 2 && coordinates.length === 2 &&
                Number.isFinite(coordinates[0]) && Number.isFinite(coordinates[1]) &&
                Math.abs(coordinates[0]) <= 90 && Math.abs(coordinates[1]) <= 180) {
              const directions = document.createElement('a'); directions.href = source.directions_url;
              directions.target = '_blank'; directions.rel = 'noopener noreferrer';
              directions.textContent = text.directions; directions.setAttribute('aria-label', `${text.directions}: ${source.title}`);
              row.append(directions);
            }
          } catch (_) { /* Only existing, validated map links are displayed. */ }
        }
      } else {
        const label = document.createElement('span');
        label.textContent = valid ? source.title : text.referenceUnavailable;
        row.append(label); unavailable ||= !valid;
      }
      list.append(row);
    });
    container.append(heading, list);
  }

  function renderSnapshot(data, announce = false) {
    if (!data.conversation || !Array.isArray(data.messages) || !data.usage) throw new Error('Invalid session');
    const nearBottom = scroll.scrollHeight - scroll.scrollTop - scroll.clientHeight < 80;
    messages.setAttribute('aria-live', announce && opened ? 'polite' : 'off');
    if (renderedConversation !== data.conversation.id) {
      if (renderedConversation !== null) { input.value = ''; resizeInput(); }
      messages.replaceChildren(); rendered.clear(); renderedConversation = data.conversation.id;
    }
    snapshot = data; ready = true;
    const keys = new Set(data.messages.map(message => `${message.role}:${message.request_id}`));
    rendered.forEach((item, key) => { if (!keys.has(key)) { item.remove(); rendered.delete(key); } });
    data.messages.forEach(message => renderMessage(message, announce));
    find('welcome').hidden = data.messages.length > 0;
    find('suggestions').hidden = data.messages.length > 0;
    const quota = find('quota'); quota.hidden = data.usage.limit === null;
    if (!quota.hidden) {
      const label = exhausted() ? format(text.exhausted, data.usage) : format(
        data.usage.remaining === 1 ? text.lastQuestion : text.remaining, data.usage);
      if (quota.textContent !== label) quota.textContent = label;
    }
    find('limit').hidden = !exhausted();
    if (nearBottom || !announce) toBottom();
    controls();
  }

  function schedulePoll() {
    window.clearTimeout(pollTimer);
    if (activeRequest && document.visibilityState !== 'hidden') pollTimer = window.setTimeout(poll, 1500);
  }

  function accept(data, announce = true) {
    const previous = activeRequest;
    renderSnapshot(data, announce);
    const pending = data.messages.find(message => message.role === 'user' && message.status === 'pending');
    if (pending) {
      activeRequest = { conversation_id: data.conversation.id, request_id: pending.request_id, content: pending.content };
      setBusy(true, text.thinking); schedulePoll();
    } else {
      activeRequest = null; window.clearTimeout(pollTimer); setBusy(false);
      const failed = previous && data.messages.find(message => message.request_id === previous.request_id && message.status === 'failed');
      if (failed) showError(text.failed, () => submit(previous.content));
    }
  }

  async function loadSession() {
    if (loading || busy || activeRequest) return;
    const epoch = pageEpoch;
    loading = true; clearError(); messages.hidden = true; setBusy(true, text.loading);
    try {
      const result = await api(config.sessionUrl);
      if (result.code !== 200) throw new Error('Session unavailable');
      accept(result.data, opened && ready);
    } catch (failure) {
      if (failure instanceof StalePage) return;
      clearPrivateState(); setBusy(false);
      showError(text.loadError, loadSession);
    } finally { if (epoch === pageEpoch) { loading = false; messages.hidden = false; } }
  }

  function reconnect(content = '') {
    clearPrivateState(); setBusy(false);
    showError(text.sessionChanged, loadSession, text.reconnect);
  }

  async function poll() {
    if (!activeRequest) return;
    const pending = activeRequest;
    try {
      const result = await api(`${config.sessionUrl}?conversation_id=${encodeURIComponent(pending.conversation_id)}`);
      if (activeRequest !== pending) return;
      if (result.code === 404 || result.code === 403) { reconnect(pending.content); return; }
      if (result.code !== 200) throw new Error('Polling failed');
      // An ambiguous network failure may have occurred before the server received the POST.
      if (!result.data.messages.some(message => message.request_id === pending.request_id)) {
        setBusy(false); showError(text.networkError, () => dispatch(pending), text.checkAnswer); return;
      }
      clearError(); accept(result.data);
    } catch (failure) {
      if (failure instanceof StalePage) return;
      setBusy(false); showError(text.networkError, poll, text.checkAnswer);
    }
  }

  async function dispatch(payload) {
    clearError(); activeRequest = payload; setBusy(true, text.thinking);
    try {
      const result = await api(config.messagesUrl, payload);
      if (result.data.conversation) { accept(result.data); return; }
      if (result.code === 403 || result.code === 404) { reconnect(payload.content); return; }
      if (result.code === 429 || result.code === 409) {
        activeRequest = null; setBusy(false); input.value = payload.content; resizeInput();
        await loadSession();
        if (result.code === 409) showError(text.sessionChanged, loadSession, text.reconnect);
        else if (result.data.error === 'rate_limited') showError(text.rateLimited);
        return;
      }
      if (result.code === 400 || result.code === 415) {
        activeRequest = null; setBusy(false); input.value = payload.content; resizeInput();
        showError(format(text.invalidInput, { max: config.maxInputChars })); return;
      }
      // No authoritative terminal state: keep the UUID for a safe, idempotent retry.
      setBusy(false); showError(result.code === 503 ? text.unavailable : text.networkError,
        () => dispatch(payload), text.checkAnswer);
    } catch (failure) {
      if (failure instanceof StalePage) return;
      setBusy(false); showError(text.networkError, () => dispatch(payload), text.checkAnswer);
    }
  }

  async function submit(value = input.value) {
    if (!ready || busy || activeRequest || atCapacity()) return;
    const content = value.trim();
    if (!content || content.length > config.maxInputChars) {
      showError(format(text.invalidInput, { max: config.maxInputChars })); return;
    }
    const payload = { conversation_id: snapshot.conversation.id, request_id: requestId(), content };
    activeRequest = payload; input.value = ''; resizeInput();
    find('welcome').hidden = true; find('suggestions').hidden = true;
    messages.setAttribute('aria-live', opened ? 'polite' : 'off');
    renderMessage({ role: 'user', request_id: payload.request_id, content, status: 'pending', created_at: new Date().toISOString() }, true);
    toBottom(); await dispatch(payload);
    if (opened && !mobile.matches && !input.disabled) input.focus({ preventScroll: true });
  }

  async function newConversation() {
    if (!ready || busy || activeRequest) return;
    clearError(); setBusy(true, text.loading);
    try {
      const result = await api(config.resetUrl, {});
      if (result.code === 429 && result.data.error === 'rate_limited') {
        setBusy(false); showError(text.resetRateLimited); return;
      }
      if (result.code !== 200) throw new Error('Reset failed');
      input.value = ''; resizeInput(); accept(result.data); status.textContent = text.newConversation;
    } catch (failure) {
      if (failure instanceof StalePage) return;
      ready = false; setBusy(false); showError(text.loadError, loadSession);
    }
  }

  function clearPrivateState() {
    snapshot = null; activeRequest = null; ready = false;
    rendered.clear(); renderedConversation = null; messages.replaceChildren();
    input.value = ''; resizeInput(); find('quota').textContent = ''; find('quota').hidden = true;
    find('welcome').hidden = true; find('suggestions').hidden = true; find('limit').hidden = true;
    window.clearTimeout(pollTimer);
  }

  function viewport() {
    const view = window.visualViewport;
    if (!view) return;
    panel.style.setProperty('--chatbot-vv-height', `${Math.round(view.height)}px`);
    panel.style.setProperty('--chatbot-vv-width', `${Math.round(view.width)}px`);
    panel.style.setProperty('--chatbot-vv-top', `${Math.round(view.offsetTop)}px`);
    panel.style.setProperty('--chatbot-vv-left', `${Math.round(view.offsetLeft)}px`);
  }

  function openPanel() {
    if (opened) return;
    opened = true; modal = mobile.matches; closing = false; viewport();
    root.classList.add('is-open'); launcher.setAttribute('aria-expanded', 'true');
    find('hint').hidden = true;
    remember('trueca-chatbot-open', '1'); remember('trueca-chatbot-hint-seen', '1');
    if (modal) panel.showModal(); else panel.show();
    document.documentElement.classList.toggle('chatbot-modal-open', modal);
    messages.setAttribute('aria-live', 'polite');
    (mobile.matches || input.disabled ? find('close') : input).focus({ preventScroll: true });
    toBottom();
    if (ready && !busy && !activeRequest) loadSession();
  }

  function finishClose() {
    if (!opened) return;
    opened = false; closing = false; root.classList.remove('is-open');
    document.documentElement.classList.remove('chatbot-modal-open');
    launcher.setAttribute('aria-expanded', 'false'); messages.setAttribute('aria-live', 'off');
    remember('trueca-chatbot-open', '0'); launcher.focus({ preventScroll: true });
  }

  async function closePanel() {
    if (!opened || closing) return;
    closing = true;
    if (!reducedMotion.matches && panel.animate) {
      try { await panel.animate([{ opacity: 1, transform: 'translateY(0)' }, { opacity: 0, transform: 'translateY(8px)' }], { duration: 130 }).finished; } catch (_) { /* Animation can be cancelled. */ }
    }
    panel.close(); finishClose();
  }

  launcher.addEventListener('click', openPanel);
  find('close').addEventListener('click', closePanel);
  panel.addEventListener('cancel', event => { event.preventDefault(); closePanel(); });
  panel.addEventListener('close', () => { if (!panel.open) finishClose(); });
  panel.addEventListener('keydown', event => {
    if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); closePanel(); }
  });
  form.addEventListener('submit', event => { event.preventDefault(); submit(); });
  input.addEventListener('input', () => { resizeInput(); controls(); });
  input.addEventListener('keydown', event => {
    if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) { event.preventDefault(); submit(); }
  });
  suggestions.forEach(button => button.addEventListener('click', () => submit(button.childNodes[0].textContent)));
  // Authentication links should reveal the destination form, especially on mobile.
  root.querySelectorAll('[data-chatbot-limit] a').forEach(link => link.addEventListener('click', () => remember('trueca-chatbot-open', '0')));
  reset.addEventListener('click', newConversation);
  retry.addEventListener('click', () => { if (!busy) retryAction?.(); });
  mobile.addEventListener('change', () => {
    if (!opened || modal === mobile.matches) return;
    const focused = document.activeElement;
    panel.close(); modal = mobile.matches;
    if (modal) panel.showModal(); else panel.show();
    document.documentElement.classList.toggle('chatbot-modal-open', modal);
    if (panel.contains(focused)) focused.focus({ preventScroll: true });
    viewport();
  });
  window.visualViewport?.addEventListener('resize', viewport);
  window.visualViewport?.addEventListener('scroll', viewport);
  window.addEventListener('resize', viewport);
  const refresh = () => {
    if (document.visibilityState === 'hidden') { window.clearTimeout(pollTimer); return; }
    if (activeRequest) { if (busy) schedulePoll(); }
    else if (!busy && !loading) loadSession();
  };
  document.addEventListener('visibilitychange', refresh);
  window.addEventListener('focus', refresh);
  window.addEventListener('pagehide', () => {
    pageEpoch += 1;
    controllers.forEach(controller => controller.abort());
    controllers.clear(); clearPrivateState(); clearError(); setBusy(false); loading = false;
  });
  window.addEventListener('pageshow', event => { if (event.persisted) refresh(); });
  find('hint').hidden = recalled('trueca-chatbot-hint-seen') === '1';
  root.hidden = false;
  loadSession();
  if (recalled('trueca-chatbot-open') === '1') openPanel();
})();

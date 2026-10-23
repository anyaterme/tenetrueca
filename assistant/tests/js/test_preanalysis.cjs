const assert = require('node:assert/strict');
const {test} = require('node:test');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../../../static/js/preanalysis.js'), 'utf8');

class Element {
  constructor() { this.handlers = {}; this.attributes = {}; this.hidden = false; }
  addEventListener(name, callback) { (this.handlers[name] ||= []).push(callback); }
  dispatchEvent(event) { for (const handler of this.handlers[event.type] || []) handler(event); }
  setAttribute(name, value) { this.attributes[name] = value; }
  getAttribute(name) { return this.attributes[name] || null; }
  removeAttribute(name) { delete this.attributes[name]; }
  focus() { this.focused = true; }
  fire(name, properties = {}) {
    const event = {type: name, defaultPrevented: false,
      preventDefault() { this.defaultPrevented = true; }, ...properties};
    const completion = Promise.all((this.handlers[name] || []).map(callback => callback(event)));
    return {event, completion};
  }
}

function setup({timeout = 60, dialogSupported = true, fetchResponse} = {}) {
  const form = new Element();
  // Real HTML forms expose named controls before the prototype's action property.
  form.action = {toString: () => '[object RadioNodeList]'};
  form.dataset = {aiTimeout: String(timeout)};
  const dialog = new Element();
  const parts = Object.fromEntries(['[data-ai-close]', '[data-ai-progress]', '[data-ai-errors]', 'h2']
    .map(key => [key, new Element()]));
  dialog.querySelector = selector => parts[selector];
  if (dialogSupported) dialog.showModal = () => { dialog.open = true; };
  dialog.close = () => { dialog.open = false; };
  const requests = [], navigations = [], timers = [];
  let resolveResponse;
  let rejectResponse;
  const response = new Promise((resolve, reject) => { resolveResponse = resolve; rejectResponse = reject; });
  const context = {
    document: {
      querySelector: selector => selector === '[data-ai-dialog]' ? dialog : null,
      querySelectorAll: selector => selector === '[data-ai-request]' ? [form] : [],
    },
    window: {location: {assign: url => navigations.push(url)}},
    location: {href: 'http://localhost/publicaciones/nueva/'},
    FormData: class { constructor() { this.values = {}; } set(k, v) { this.values[k] = v; } },
    AbortController, Event,
    setTimeout: (callback, duration) => { timers.push({callback, duration}); return timers.length; },
    clearTimeout: () => {},
    fetch: (url, options) => {
      requests.push({url, options});
      options.signal.addEventListener('abort', () => rejectResponse(Object.assign(new Error(), {name: 'AbortError'})));
      return fetchResponse ? Promise.resolve(fetchResponse) : response;
    },
  };
  vm.runInNewContext(source, context);
  const submitter = new Element();
  submitter.value = 'submit';
  return {form, dialog, parts, requests, navigations, timers, submitter, resolveResponse};
}

test('pending request opens the modal, traps Escape and deduplicates submissions', async () => {
  const ui = setup();
  const first = ui.form.fire('submit', {submitter: ui.submitter});
  assert.equal(first.event.defaultPrevented, true);
  assert.equal(ui.dialog.open, true);
  assert.equal(ui.parts['[data-ai-progress]'].hidden, false);
  assert.equal(ui.dialog.fire('cancel').event.defaultPrevented, true);
  ui.parts['[data-ai-close]'].fire('click');
  assert.equal(ui.dialog.open, true);
  await ui.form.fire('submit', {submitter: ui.submitter}).completion;
  assert.equal(ui.requests.length, 1);
  assert.equal(ui.requests[0].url, 'http://localhost/publicaciones/nueva/');
  assert.equal(ui.requests[0].options.body.values.action, 'submit');
  ui.resolveResponse({ok: true, headers: {get: () => 'application/json'}, json: async () => ({url: '/publicaciones/preparacion/token/'})});
  await first.completion;
  assert.deepEqual(ui.navigations, ['/publicaciones/preparacion/token/']);
});

test('validation failures preserve the form, display safe text and permit explicit retry', async () => {
  const ui = setup({fetchResponse: {ok: false, headers: {get: () => 'application/json'},
    json: async () => ({errors: {photos: [{message: '<script>no ejecutar</script>'}]}})}});
  let errorEvents = 0;
  ui.form.addEventListener('preanalysis:error', () => errorEvents++);
  await ui.form.fire('submit', {submitter: ui.submitter}).completion;
  assert.equal(ui.parts['[data-ai-errors]'].textContent, '<script>no ejecutar</script>');
  assert.equal(ui.parts['[data-ai-close]'].hidden, false);
  assert.equal(ui.parts['[data-ai-progress]'].hidden, true);
  assert.equal(errorEvents, 1);
  ui.parts['[data-ai-close]'].fire('click');
  assert.equal(ui.dialog.open, false);
  assert.equal(ui.submitter.focused, true);
  await ui.form.fire('submit', {submitter: ui.submitter}).completion;
  assert.equal(ui.requests.length, 2);
});

test('configured timeout aborts the HTTP wait and preserves the possibility of retry', async () => {
  const ui = setup({timeout: 12});
  const request = ui.form.fire('submit', {submitter: ui.submitter});
  assert.equal(ui.timers[0].duration, 27000); // 12s analysis + transport overhead.
  ui.timers[0].callback();
  await request.completion;
  assert.equal(ui.requests[0].options.signal.aborted, true);
  assert.match(ui.parts['[data-ai-errors]'].textContent, /tiempo de espera/);
  assert.equal(ui.navigations.length, 0);
});

test('saving a draft and invalid wizard submissions never launch analysis', async () => {
  const ui = setup();
  ui.submitter.value = 'save_draft';
  const draft = ui.form.fire('submit', {submitter: ui.submitter});
  await draft.completion;
  assert.equal(draft.event.defaultPrevented, false);
  ui.submitter.value = 'submit';
  await ui.form.fire('submit', {submitter: ui.submitter, defaultPrevented: true}).completion;
  assert.equal(ui.requests.length, 0);
  assert.equal(ui.dialog.open, undefined);
});

test('browsers without native dialog support keep the normal synchronous form POST', async () => {
  const ui = setup({dialogSupported: false});
  const request = ui.form.fire('submit', {submitter: ui.submitter});
  await request.completion;
  assert.equal(request.event.defaultPrevented, false);
  assert.equal(ui.requests.length, 0);
});

test('unexpected HTML response gives a recoverable error without losing data', async () => {
  const ui = setup({fetchResponse: {ok: false, headers: {get: () => 'text/html'}}});
  await ui.form.fire('submit', {submitter: ui.submitter}).completion;
  assert.match(ui.parts['[data-ai-errors]'].textContent, /Tus datos siguen en el formulario/);
  assert.equal(ui.parts['[data-ai-close]'].hidden, false);
});

test('photo-only analysis uses the same synchronous dialog and deduplicates its batch', async () => {
  const ui = setup();
  ui.submitter.value = 'analyze_photos';
  const first = ui.form.fire('submit', {submitter: ui.submitter});
  assert.equal(ui.dialog.open, true);
  await ui.form.fire('submit', {submitter: ui.submitter}).completion;
  assert.equal(ui.requests.length, 1);
  assert.equal(ui.requests[0].options.body.values.action, 'analyze_photos');
  ui.resolveResponse({ok: true, headers: {get: () => 'application/json'}, json: async () => ({url: '/photos-result/'})});
  await first.completion;
  assert.deepEqual(ui.navigations, ['/photos-result/']);
});

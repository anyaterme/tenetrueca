const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../../../static/js/publication_form.js'), 'utf8');

class Element {
  constructor() {
    this.handlers = {}; this.dataset = {}; this.value = ''; this.disabled = false;
    this.classList = {add() {}, toggle() {}};
  }
  addEventListener(name, fn) { (this.handlers[name] ||= []).push(fn); }
  fire(name, properties = {}) {
    const event = {defaultPrevented: false, preventDefault() { this.defaultPrevented = true; }, ...properties};
    for (const fn of this.handlers[name] || []) fn(event);
    return event;
  }
  setAttribute() {}
  focus() { this.focused = true; }
  scrollIntoView() {}
}

function setup({mobile = true, savedPhoto = false} = {}) {
  const form = new Element(), submit = new Element(), primary = new Element();
  const photoInput = new Element(), camera = new Element(), photoList = new Element();
  const message = new Element(), dropzone = new Element(), media = new Element();
  media.matches = mobile;
  const label = new Element(), makePrimary = new Element(), card = new Element();
  card.dataset.photoKey = 'existing:1';
  card.hasAttribute = name => name === 'data-existing-photo';
  card.querySelector = selector => ({'[data-primary-label]': label, '[data-make-primary]': makePrimary,
    '[data-remove-existing]': new Element()})[selector];
  const cards = savedPhoto ? [card] : [];
  photoList.querySelectorAll = () => cards;
  const controls = {'[data-primary-photo]': primary, '[data-photo-input]': photoInput};
  form.querySelector = selector => controls[selector] || null;
  const editor = new Element(); editor.dataset = {photoMax: '8', photoMaxBytes: '8388608'};
  editor.hasAttribute = name => name === 'data-photo-first';
  editor.querySelectorAll = () => [];
  const elements = {'[data-publication-form]': form, '[data-submit-publication]': submit,
    '[data-photo-camera]': camera, '[data-photo-list]': photoList, '[data-photo-count]': new Element(),
    '[data-photo-message]': message, '[data-add-photo]': new Element(),
    '[data-save-status] span:last-child': new Element(), '.publication-photo-picker__dropzone': dropzone,
    '.publication-photo-picker': new Element()};
  editor.querySelector = selector => elements[selector] || null;
  const window = new Element(); window.matchMedia = () => media;
  let requests = 0; form.requestSubmit = () => requests++;
  vm.runInNewContext(source, {
    window, document: {documentElement: {classList: {add() {}}},
      querySelector: () => editor, getElementById: () => null},
  });
  return {form, submit, media, card, message, dropzone, photoList, window, requests: () => requests};
}

test('mobile empty selection disables Continue and still validates a programmatic submission', () => {
  const ui = setup();
  assert.equal(ui.submit.disabled, true);
  assert.equal(ui.form.fire('submit').defaultPrevented, true);
  assert.match(ui.message.textContent, /al menos una fotografía/);
  assert.equal(ui.dropzone.focused, true);
  assert.equal(ui.requests(), 0);
});

test('desktop availability stays unchanged, including resizing across the mobile breakpoint', () => {
  const ui = setup({mobile: false});
  assert.equal(ui.submit.disabled, false);
  ui.media.matches = true; ui.media.fire('change');
  assert.equal(ui.submit.disabled, true);
  ui.media.matches = false; ui.media.fire('change');
  assert.equal(ui.submit.disabled, false);
  assert.equal(ui.requests(), 0);
});

test('removing the last saved photograph disables mobile Continue without starting analysis', () => {
  const ui = setup({savedPhoto: true});
  assert.equal(ui.submit.disabled, false);
  const remove = {closest: () => ui.card};
  ui.photoList.fire('click', {target: {closest: selector => selector === '[data-remove-photo]' ? remove : null}});
  assert.equal(ui.card.hidden, true);
  assert.equal(ui.submit.disabled, true);
  assert.equal(ui.requests(), 0);
});

test('busy state survives orientation changes and recovers after an analysis error or page restore', () => {
  const ui = setup({savedPhoto: true});
  assert.equal(ui.form.fire('submit').defaultPrevented, false);
  ui.form.fire('preanalysis:start');
  assert.equal(ui.submit.disabled, true);
  ui.media.matches = false; ui.media.fire('change');
  assert.equal(ui.submit.disabled, true);
  ui.form.fire('preanalysis:error');
  assert.equal(ui.submit.disabled, false);
  const empty = setup();
  empty.window.fire('pageshow', {persisted: true});
  assert.equal(empty.submit.disabled, true);
});

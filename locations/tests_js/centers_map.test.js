const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../../static/js/centers_map.js'), 'utf8');

class Element {
  constructor() { this.hidden = false; this.events = {}; this.children = []; this.attrs = {}; this.value = ''; this.textContent = ''; this.dataset = {}; this.classes = new Set(); this.classList = { toggle: (name, on) => on ? this.classes.add(name) : this.classes.delete(name) }; }
  addEventListener(name, fn) { this.events[name] = fn; }
  append(child) { this.children.push(child); }
  setAttribute(name, value) { this.attrs[name] = value; }
  removeAttribute(name) { delete this.attrs[name]; }
  focus() { this.focused = true; }
  scrollIntoView() {}
}

function setup({ editor = false, googleReady = true } = {}) {
  const canvas = new Element(); canvas.hidden = true;
  const intro = new Element();
  const status = new Element();
  const button = new Element();
  const select = new Element(); select.hidden = true; select.dataset.centerSelect = '1';
  const card = new Element(); card.dataset.centerCard = '1';
  const root = new Element(); root.dataset.mapMode = editor ? 'editor' : 'public';
  root.querySelector = (s) => ({ '[data-map-load]': button, '[data-map-canvas]': canvas, '[data-map-intro]': intro, '[data-map-status]': status })[s];
  root.querySelectorAll = (s) => ({ '[data-center-select]': [select], '[data-center-card]': [card] })[s] || [];
  const latitude = new Element(); latitude.value = '28.400000';
  const longitude = new Element(); longitude.value = '-16.500000';
  const data = [{ id: 1, name: '<script>unsafe</script>', lat: 28.4, lng: -16.5, address: 'Tenerife', status: 'Operativo', schedule: 'Horario', information: '<img onerror=alert(1)>', directionsUrl: 'https://www.google.com/maps/dir/?api=1&destination=28.4%2C-16.5' }];
  const elements = { 'google-maps-config': { textContent: JSON.stringify({ apiKey: 'test-key', mapId: 'test-id' }) }, 'centers-map-data': { textContent: JSON.stringify(data) }, 'id_latitude': latitude, 'id_longitude': longitude };
  const state = { markers: [], scripts: [], timers: [] };
  const google = { maps: {
    Map: class {
      constructor(el, options) { this.options = options; this.events = {}; state.map = this; }
      panTo(position) { this.position = position; }
      setCenter(position) { this.position = position; }
      setZoom(zoom) { this.zoom = zoom; }
      fitBounds(bounds) { this.bounds = bounds; }
      addListener(name, fn) { this.events[name] = fn; }
    },
    LatLngBounds: class { extend(p) { this.position = p; } getCenter() { return this.position; } },
    InfoWindow: class { constructor() { state.info = this; } setContent(content) { this.content = content; } open(options) { this.options = options; } },
    marker: { AdvancedMarkerElement: class {
      constructor(options) { Object.assign(this, options); this.events = {}; state.markers.push(this); }
      addEventListener(name, fn) { this.events[name] = fn; }
      addListener(name, fn) { this.events[name] = fn; }
    } },
  } };
  const window = { setTimeout: (fn) => { state.timers.push(fn); return 1; }, clearTimeout() {} };
  if (googleReady) window.google = google;
  const document = {
    querySelector: () => root,
    getElementById: (id) => elements[id],
    createElement: () => new Element(),
    head: { append: (script) => state.scripts.push(script) },
  };
  const context = { window, document, google, URLSearchParams, Map };
  vm.runInNewContext(source, context);
  return { ...state, state, window, root, canvas, intro, status, button, select, card, latitude, longitude };
}

test('does not contact Google before activation; selects centers using text safely', async () => {
  const x = setup();
  assert.equal(x.state.map, undefined);
  assert.equal(x.state.scripts.length, 0);
  await x.button.events.click();
  assert.equal(x.state.markers.length, 1);
  assert.equal(x.canvas.hidden, false);
  assert.equal(x.intro.hidden, true);
  assert.equal(x.select.hidden, false);
  x.select.events.click();
  assert.equal(x.state.map.zoom, 15);
  assert.equal(x.state.info.content.children[0].textContent, '<script>unsafe</script>');
  assert.ok(x.state.info.content.children.some((child) => child.textContent === '<img onerror=alert(1)>'));
  assert.equal(x.card.classes.has('is-selected'), true);
  assert.equal(x.state.info.options.shouldFocus, true);
  x.state.markers[0].events['gmp-click']();
  assert.equal(x.state.info.options.shouldFocus, false);
});

test('authorization failure retains the list and hides map selection controls', async () => {
  const x = setup({ googleReady: false });
  const loading = x.button.events.click();
  assert.equal(x.state.scripts.length, 1);
  assert.ok(x.state.scripts[0].src.startsWith('https://maps.googleapis.com/maps/api/js?'));
  x.window.gm_authFailure();
  await loading;
  assert.equal(x.canvas.hidden, true);
  assert.equal(x.intro.hidden, false);
  assert.equal(x.button.hidden, true);
  assert.equal(x.select.hidden, true);
  assert.match(x.status.textContent, /Cómo llegar/);
  assert.equal(x.root.attrs['aria-busy'], undefined);
});

test('network timeout gives a usable fallback', async () => {
  const x = setup({ googleReady: false });
  const loading = x.button.events.click();
  x.state.timers[0]();
  await loading;
  assert.equal(x.canvas.hidden, true);
  assert.match(x.status.textContent, /listado/);
});

test('editor synchronizes fields, map clicks and dragging without submitting', async () => {
  const x = setup({ editor: true });
  await x.button.events.click();
  assert.equal(x.state.map.zoom, 16);
  x.state.map.events.click({ latLng: { toJSON: () => ({ lat: 28.500707, lng: -16.206969 }) } });
  assert.equal(x.latitude.value, '28.500707');
  assert.equal(x.longitude.value, '-16.206969');
  x.state.markers[0].position = { lat: 28.454207, lng: -16.292419 };
  x.state.markers[0].events.dragend();
  assert.equal(x.latitude.value, '28.454207');
  x.latitude.value = '0'; x.longitude.value = '0';
  x.latitude.events.change();
  assert.equal(x.state.map.position.lat, 0);
  x.latitude.value = '91'; x.latitude.events.change();
  assert.equal(x.state.markers[0].position, null);
  assert.match(x.status.textContent, /Guarda el formulario/);
});

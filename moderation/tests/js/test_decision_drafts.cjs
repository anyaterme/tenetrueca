const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const {join} = require('node:path');
const {runInNewContext} = require('node:vm');

const script = readFileSync(join(__dirname, '../../../static/js/moderation_decision.js'), 'utf8');
const proposals = {request_changes: 'Cambios propuestos', approve: 'Aprobación propuesta', reject: 'Rechazo propuesto'};

// EventTarget models the public DOM events used by the form, without a network or SDK.
function page({selected = 'request_changes', text = proposals[selected], enabled = true} = {}) {
  const notes = new EventTarget();
  notes.value = text ?? '';
  notes.getAttribute = () => 'existing-help';
  notes.setAttribute = (key, value) => { notes[key] = value; };
  const options = Object.keys(proposals).map((value) => Object.assign(new EventTarget(), {
    value, checked: value === selected,
  }));
  const form = {
    dataset: {decisionDrafts: 'drafts'},
    querySelector: () => notes,
    querySelectorAll: () => options,
  };
  runInNewContext(script, {document: {
    querySelectorAll: () => enabled ? [form] : [],
    getElementById: () => ({textContent: JSON.stringify(proposals)}),
  }});
  return {
    notes,
    choose(value) {
      for (const option of options) option.checked = option.value === value;
      options.find((option) => option.checked).dispatchEvent(new Event('change'));
      return notes.value;
    },
    edit(value) {
      notes.value = value;
      notes.dispatchEvent(new Event('input'));
    },
  };
}

test('each choice displays its own proposal on first selection', () => {
  const form = page();
  for (const choice of ['approve', 'reject', 'request_changes']) {
    assert.equal(form.choose(choice), proposals[choice]);
  }
});

test('edits to all three choices survive repeated switching verbatim', () => {
  const form = page();
  for (const choice of ['request_changes', 'approve', 'reject']) {
    form.choose(choice);
    form.edit(`Motivo de ${choice}\n\nIndicaciones propias, con espacios  `);
  }
  for (let round = 0; round < 3; round++) {
    for (const choice of ['approve', 'request_changes', 'reject']) {
      assert.equal(form.choose(choice), `Motivo de ${choice}\n\nIndicaciones propias, con espacios  `);
    }
  }
});

test('cleared notes stay empty when returning to the choice', () => {
  const form = page();
  form.edit('');
  form.choose('approve');
  assert.equal(form.choose('request_changes'), '');
});

test('server-rendered human edits and validation errors take precedence', () => {
  for (const text of ['Corrección humana', '']) {
    const form = page({selected: 'reject', text});
    assert.equal(form.notes.value, text);
    form.choose('approve');
    assert.equal(form.choose('reject'), text);
  }
});

test('notes entered before a choice attach to the first chosen option', () => {
  const form = page({selected: null, text: ''});
  const unassigned = page({selected: null, text: 'Borrador devuelto sin decisión'});
  assert.equal(unassigned.choose('reject'), 'Borrador devuelto sin decisión');
  unassigned.choose('approve');
  assert.equal(unassigned.choose('reject'), 'Borrador devuelto sin decisión');
  const cleared = page({selected: null});
  cleared.edit('');
  assert.equal(cleared.choose('approve'), '');
  assert.equal(form.choose('approve'), proposals.approve);
});

test('without an evaluation the manual form is left untouched', () => {
  const form = page({enabled: false, text: 'Mi texto manual'});
  assert.equal(form.choose('reject'), 'Mi texto manual');
});

test('switching also preserves changes made without an input event', () => {
  const form = page();
  form.notes.value = 'Texto recuperado por el navegador';
  form.choose('reject');
  assert.equal(form.choose('request_changes'), 'Texto recuperado por el navegador');
  assert.equal(form.notes['aria-describedby'], 'existing-help decision-draft-help');
});

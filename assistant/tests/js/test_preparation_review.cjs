const test = require('node:test');
const assert = require('node:assert/strict');
const {stateFor, isApplicable, fileProblem} = require('../../../static/js/preparation_review.js');

test('bulk acceptance excludes identity changes and requests for facts or photos', () => {
  assert.equal(isApplicable({kind: 'replace', field: 'title'}), false);
  assert.equal(isApplicable({kind: 'replace', field: 'description'}), true);
  assert.equal(isApplicable({kind: 'replace', field: 'category'}), true);
  assert.equal(isApplicable({kind: 'information', field: 'description'}), false);
  assert.equal(isApplicable({kind: 'photo', field: 'photos'}), false);
});
test('editing a pending proposal does not imply accepting it', () => {
  assert.equal(stateFor({modified: true}), 'Editada, pendiente de aceptar');
  assert.equal(stateFor({modified: true, accepted: true}), 'Modificada y aceptada');
  assert.equal(stateFor({accepted: true}), 'Aceptada');
  assert.equal(stateFor({discarded: true, modified: true}), 'Descartada');
});
test('pending identity, information and photos have distinct actionable labels', () => {
  assert.equal(stateFor({requiresConfirmation: true}), 'Requiere confirmación');
  assert.equal(stateFor({kind: 'information'}), 'Completa la información');
  assert.equal(stateFor({kind: 'photo'}), 'Pendiente de fotografías');
});
test('photo chooser respects configured byte limit and actual supported formats', () => {
  for (const type of ['image/jpeg', 'image/png', 'image/webp']) {
    assert.equal(fileProblem({type, name: 'test', size: 100}, 100), '');
    assert.match(fileProblem({type, name: 'test', size: 101}, 100), /tamaño/);
  }
  assert.match(fileProblem({type: 'image/heic', name: 'a.heic', size: 1}, 100), /JPEG/);
  assert.match(fileProblem({type: '', name: 'a.html', size: 1}, 100), /JPEG/);
  assert.equal(fileProblem({type: '', name: 'a.JPEG', size: 1}, 100), '');
});

(function () {
  'use strict';
  function stateFor(value) {
    if (value.discarded) return 'Descartada';
    if (value.accepted) return value.modified ? 'Modificada y aceptada' : 'Aceptada';
    if (value.modified) return 'Editada, pendiente de aceptar';
    if (value.requiresConfirmation) return 'Requiere confirmación';
    if (value.kind === 'information') return 'Completa la información';
    if (value.kind === 'photo') return 'Pendiente de fotografías';
    return 'Pendiente';
  }
  function isApplicable(row) { return row.kind === 'replace' && row.field !== 'title'; }
  function fileProblem(file, maxBytes) {
    var validType = ['image/jpeg', 'image/png', 'image/webp'].indexOf(file.type) !== -1;
    if (!validType && !(file.type === '' && /\.(jpe?g|png|webp)$/i.test(file.name))) return 'Usa imágenes JPEG, PNG o WebP.';
    if (file.size > maxBytes) return 'La fotografía supera el tamaño máximo permitido.';
    return '';
  }
  if (typeof module !== 'undefined' && module.exports) module.exports = {stateFor: stateFor, isApplicable: isApplicable, fileProblem: fileProblem};
  if (typeof document === 'undefined') return;
  var root = document.querySelector('[data-ai-review]');
  if (!root) return;
  var form = root.querySelector('#ai-review-form');
  var rows = Array.from(root.querySelectorAll('[data-review-suggestion]'));
  var announcement = root.querySelector('[data-review-announcement]');
  var actionInput = root.querySelector('[data-review-action]');
  var cancelForm = root.querySelector('#ai-review-cancel');
  var cancelDialog = root.querySelector('[data-review-cancel-dialog]');
  var dirty = false, busy = false, navigating = false;
  var lockedButtons = [];
  function announce(text) { announcement.textContent = text; }
  function setBusy(value) {
    busy = value;
    form.setAttribute('aria-busy', String(value));
    if (value) {
      lockedButtons = Array.from(root.querySelectorAll('[data-review-mutation]')).filter(function (button) { return !button.disabled; });
      lockedButtons.forEach(function (button) { button.disabled = true; });
    } else {
      lockedButtons.forEach(function (button) { button.disabled = false; });
      lockedButtons = [];
    }
  }
  function rowInput(row) { return row.querySelector('[name="suggestion_' + row.dataset.reviewSuggestion + '"]'); }
  function inputLabel(input) {
    return input.tagName === 'SELECT' ? (input.selectedOptions[0] ? input.selectedOptions[0].textContent.trim() : '') : input.value;
  }
  function syncRow(row) {
    var accepted = row.querySelector('[data-suggestion-check]');
    var discarded = row.querySelector('[data-suggestion-discard]');
    var input = rowInput(row);
    var modified = !!input && input.value !== row.dataset.initialProposal;
    if (accepted.checked) discarded.checked = false;
    if (input && (!accepted.checked || input.value.trim())) input.setCustomValidity('');
    row.classList.toggle('is-accepted', accepted.checked);
    row.classList.toggle('is-discarded', discarded.checked);
    row.querySelector('[data-suggestion-status]').textContent = stateFor({
      accepted: accepted.checked, discarded: discarded.checked, modified: modified,
      requiresConfirmation: row.dataset.field === 'title', kind: row.dataset.kind,
    });
    row.querySelector('.ai-suggestion-accept > span').textContent = accepted.checked ? 'Aceptada' : 'Aceptar';
    var proposed = row.querySelector('[data-suggestion-proposed]');
    if (proposed && input) proposed.textContent = inputLabel(input);
  }
  rows.forEach(function (row) {
    var accepted = row.querySelector('[data-suggestion-check]');
    var discarded = row.querySelector('[data-suggestion-discard]');
    var input = rowInput(row);
    var editor = row.querySelector('[data-suggestion-editor]');
    var edit = row.querySelector('[data-edit-suggestion]');
    accepted.addEventListener('change', function () {
      if (accepted.checked) {
        discarded.checked = false;
        if (input && !input.value.trim()) { editor.open = true; input.focus(); announce('Completa la respuesta con datos que conozcas antes de enviarla.'); }
        if (row.dataset.kind === 'photo') announce('La sugerencia de fotografías requiere que añadas imágenes nuevas a la galería.');
      }
      dirty = true; syncRow(row);
    });
    discarded.addEventListener('change', function () {
      if (discarded.checked) accepted.checked = false;
      dirty = true; syncRow(row);
    });
    if (edit) edit.addEventListener('click', function () { editor.open = true; input.focus(); });
    if (input) input.addEventListener('input', function () { input.setCustomValidity(''); dirty = true; syncRow(row); });
    if (input && input.tagName === 'SELECT') input.addEventListener('change', function () { dirty = true; syncRow(row); });
    syncRow(row);
  });
  ['title', 'description', 'category'].forEach(function (field) {
    var input = form.querySelector('[name="' + field + '"]');
    function changed() {
      dirty = true;
      if (field === 'description') root.querySelector('[data-description-count]').textContent = input.value.length + ' caracteres';
      rows.filter(function (row) { return row.dataset.field === field; }).forEach(function (row) {
        var current = row.querySelector('[data-suggestion-current]');
        if (current) current.textContent = inputLabel(input);
        if (row.dataset.kind === 'replace' && row.querySelector('[data-suggestion-check]').checked) {
          row.querySelector('[data-suggestion-check]').checked = false;
          announce('Se conserva tu cambio manual. La propuesta de ' + fieldLabels[field] + ' ha quedado sin aceptar.');
          syncRow(row);
        }
      });
    }
    input.addEventListener(input.tagName === 'SELECT' ? 'change' : 'input', changed);
  });
  var fieldLabels = {title: 'nombre', description: 'descripción', category: 'clasificación'};
  var gallery = root.querySelector('[data-review-gallery]');
  var picker = gallery.querySelector('.ai-review-photo-picker');
  var photoInput = form.querySelector('#id_photos');
  var primary = form.querySelector('#id_primary_photo');
  var photoMessage = root.querySelector('[data-review-photo-message]');
  var maxCount = Number(form.dataset.photoMax), maxBytes = Number(form.dataset.photoMaxBytes);
  var files = [], objectUrls = [];
  var canTransfer = typeof DataTransfer !== 'undefined';
  function cards() { return Array.from(gallery.querySelectorAll('[data-review-photo]')); }
  function activeCards() { return cards().filter(function (card) { return card.dataset.removed !== 'true'; }); }
  function existingCount() { return activeCards().filter(function (card) { return !card.dataset.reviewPhoto.startsWith('new:'); }).length; }
  function syncGallery() {
    var active = activeCards();
    var value = primary.value;
    if (!value || !active.some(function (card) { return card.dataset.reviewPhoto === value; })) {
      var stored = active.find(function (card) { return card.dataset.storedPrimary === 'true'; });
      value = stored ? stored.dataset.reviewPhoto : active.length ? active[0].dataset.reviewPhoto : '';
    }
    primary.value = value;
    cards().forEach(function (card) {
      var isPrimary = card.dataset.reviewPhoto === value && card.dataset.removed !== 'true';
      card.classList.toggle('is-primary', isPrimary);
      card.classList.toggle('is-removed', card.dataset.removed === 'true');
      card.querySelector('[data-review-primary-badge]').hidden = !isPrimary;
      var button = card.querySelector('[data-review-primary]');
      button.textContent = isPrimary ? 'Principal' : 'Hacer principal';
      button.setAttribute('aria-pressed', String(isPrimary));
      button.disabled = card.dataset.removed === 'true';
      var label = card.querySelector('[data-review-remove-label]');
      if (label) label.textContent = card.dataset.removed === 'true' ? 'Restaurar' : 'Eliminar';
    });
    root.querySelector('[data-review-photo-count]').textContent = active.length + ' de ' + maxCount;
    photoInput.setCustomValidity('');
  }
  function bindCard(card) {
    card.querySelector('[data-review-primary]').addEventListener('click', function () {
      if (busy || card.dataset.removed === 'true') return;
      primary.value = card.dataset.reviewPhoto; dirty = true; syncGallery(); announce('Se ha actualizado la fotografía principal.');
    });
    var remove = card.querySelector('[data-review-remove]');
    if (remove) remove.addEventListener('change', function () {
      card.dataset.removed = String(remove.checked); dirty = true; syncGallery();
    });
  }
  cards().forEach(bindCard);
  function syncFiles() {
    if (!canTransfer) return;
    var transfer = new DataTransfer();
    files.forEach(function (file) { transfer.items.add(file); });
    photoInput.files = transfer.files;
  }
  function renderFiles() {
    var selected = primary.value;
    gallery.querySelectorAll('[data-new-review-photo]').forEach(function (card) { card.remove(); });
    primary.querySelectorAll('[data-js-new-photo]').forEach(function (option) { option.remove(); });
    objectUrls.forEach(function (url) { URL.revokeObjectURL(url); }); objectUrls = [];
    files.forEach(function (file, index) {
      var key = 'new:' + index, url = URL.createObjectURL(file); objectUrls.push(url);
      var card = document.createElement('figure'); card.className = 'ai-review-photo'; card.dataset.reviewPhoto = key; card.dataset.newReviewPhoto = ''; card.dataset.removed = 'false';
      var wrap = document.createElement('div'); wrap.className = 'ai-review-photo__image';
      var image = document.createElement('img'); image.src = url; image.alt = 'Nueva fotografía: ' + file.name; image.width = 240; image.height = 180; wrap.appendChild(image);
      var badge = document.createElement('span'); badge.className = 'ai-review-photo__badge'; badge.dataset.reviewPrimaryBadge = ''; badge.textContent = '★ Principal'; badge.hidden = true; wrap.appendChild(badge); card.appendChild(wrap);
      var caption = document.createElement('figcaption'), actions = document.createElement('div'); actions.className = 'ai-review-photo__actions';
      var makePrimary = document.createElement('button'); makePrimary.type = 'button'; makePrimary.className = 'ai-review-link'; makePrimary.dataset.reviewPrimary = ''; makePrimary.dataset.reviewMutation = ''; makePrimary.textContent = 'Hacer principal'; makePrimary.setAttribute('aria-label', 'Establecer nueva foto ' + (index + 1) + ' como principal'); actions.appendChild(makePrimary);
      if (canTransfer) {
        var remove = document.createElement('button'); remove.type = 'button'; remove.className = 'ai-review-link'; remove.style.color = '#ad291f'; remove.textContent = 'Eliminar'; remove.dataset.reviewMutation = ''; remove.setAttribute('aria-label', 'Eliminar nueva foto ' + (index + 1));
        remove.addEventListener('click', function () {
          if (busy) return;
          var old = primary.value;
          files.splice(index, 1);
          if (old === key) primary.value = '';
          else if (old.startsWith('new:') && Number(old.slice(4)) > index) primary.value = 'new:' + (Number(old.slice(4)) - 1);
          dirty = true; renderFiles();
        }); actions.appendChild(remove);
      }
      caption.appendChild(actions); card.appendChild(caption); gallery.insertBefore(card, picker);
      var option = document.createElement('option'); option.value = key; option.textContent = 'Nueva foto ' + (index + 1); option.dataset.jsNewPhoto = ''; primary.appendChild(option); bindCard(card);
    });
    if (Array.from(primary.options).some(function (option) { return option.value === selected; })) primary.value = selected;
    syncFiles(); syncGallery();
  }
  function addFiles(list) {
    photoMessage.textContent = '';
    if (!canTransfer) {
      var chosen = Array.from(list);
      var problem = chosen.map(function (file) { return fileProblem(file, maxBytes); }).find(Boolean);
      if (existingCount() + chosen.length > maxCount) problem = 'Puedes conservar hasta ' + maxCount + ' fotografías.';
      if (problem) { photoMessage.textContent = problem; photoInput.value = ''; files = []; renderFiles(); return; }
      files = chosen; dirty = true; renderFiles(); return;
    }
    Array.from(list).forEach(function (file) {
      var problem = fileProblem(file, maxBytes);
      if (existingCount() + files.length >= maxCount) problem = 'Puedes conservar hasta ' + maxCount + ' fotografías.';
      if (problem) { photoMessage.textContent = problem; return; }
      files.push(file); dirty = true;
    });
    renderFiles();
  }
  photoInput.addEventListener('change', function () { addFiles(photoInput.files); });
  var camera = root.querySelector('[data-review-camera]');
  root.querySelector('[data-review-camera-button]').addEventListener('click', function () { camera.click(); });
  camera.addEventListener('change', function () { addFiles(camera.files); camera.value = ''; });
  if (!canTransfer) {
    picker.classList.add('ai-review-photo-picker--native');
    root.querySelector('.ai-review-camera').hidden = true;
  }
  syncGallery();
  // Native validation must be able to focus fields inside collapsed sections.
  form.addEventListener('invalid', function (event) {
    var container = event.target.parentElement;
    while (container && container !== form) {
      if (container.tagName === 'DETAILS') container.open = true;
      container = container.parentElement;
    }
  }, true);
  function validateSelection() {
    if (!activeCards().length) { photoMessage.textContent = 'Conserva al menos una fotografía. Añade una nueva o restaura una de la galería.'; picker.scrollIntoView({block: 'center'}); photoInput.focus(); return false; }
    for (var row of rows) {
      if (!row.querySelector('[data-suggestion-check]').checked) continue;
      var input = rowInput(row);
      if (input && !input.value.trim()) {
        var editor = row.querySelector('[data-suggestion-editor]'); editor.open = true;
        var section = row.closest('.ai-review-section'); section.open = true;
        input.setCustomValidity('Completa este campo para aceptar la sugerencia.'); input.reportValidity(); input.focus(); return false;
      }
      if (row.dataset.kind === 'photo' && !files.length && !activeCards().some(function (card) { return card.dataset.addedPhoto === 'true'; })) {
        photoMessage.textContent = 'Añade una fotografía para aceptar esta sugerencia.'; root.querySelector('[data-review-photos]').closest('details').open = true; picker.scrollIntoView({block: 'center'}); photoInput.focus(); return false;
      }
    }
    return true;
  }
  form.addEventListener('submit', function (event) {
    var action = event.submitter ? event.submitter.value : 'confirm_selected';
    if (event.defaultPrevented) return;
    if (busy) { event.preventDefault(); return; }
    if (action === 'accept_applicable') {
      event.preventDefault();
      var count = 0;
      rows.forEach(function (row) {
        if (!isApplicable(row.dataset)) return;
        row.querySelector('[data-suggestion-check]').checked = true; row.querySelector('[data-suggestion-discard]').checked = false; syncRow(row); count++;
      });
      dirty = true; announce('Se han aceptado ' + count + ' sustituciones aplicables. Revisa individualmente el nombre, la información pendiente y las fotografías.'); return;
    }
    if (!validateSelection()) { event.preventDefault(); return; }
    actionInput.value = action;
    navigating = true; setBusy(true);
  });
  form.addEventListener('preanalysis:start', function () { navigating = true; setBusy(true); });
  form.addEventListener('preanalysis:error', function () { navigating = false; setBusy(false); });
  cancelForm.addEventListener('submit', function (event) {
    if (busy) { event.preventDefault(); return; }
    var confirmed = event.submitter && event.submitter.hasAttribute('data-confirm-cancel');
    if (dirty && !confirmed) {
      if (typeof cancelDialog.showModal === 'function') { event.preventDefault(); cancelDialog.showModal(); root.querySelector('[data-keep-editing]').focus(); return; }
      if (!window.confirm('¿Cancelar el envío y descartar los cambios sin guardar?')) { event.preventDefault(); return; }
    }
    navigating = true; setBusy(true);
  });
  root.querySelector('[data-keep-editing]').addEventListener('click', function () { cancelDialog.close(); root.querySelector('[data-review-cancel]').focus(); });
  window.addEventListener('beforeunload', function (event) { if (dirty && !navigating) { event.preventDefault(); event.returnValue = ''; } });
  window.addEventListener('pageshow', function (event) { if (event.persisted) { navigating = false; setBusy(false); } });
  form.addEventListener('input', function () { dirty = true; });
  form.addEventListener('change', function () { dirty = true; });
  var preview = root.querySelector('[data-review-preview]');
  if (preview) preview.focus({preventScroll: false});
  root.classList.add('is-enhanced');
})();

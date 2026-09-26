(function () {
  var editor = document.querySelector('[data-publication-editor]');
  if (!editor) return;

  document.documentElement.classList.add('js-publication-wizard');

  var form = editor.querySelector('[data-publication-form]');
  var panels = Array.prototype.slice.call(editor.querySelectorAll('[data-step-panel]'));
  var indicators = Array.prototype.slice.call(editor.querySelectorAll('[data-step-indicator]'));
  var nextButton = editor.querySelector('[data-step-next]');
  var backButton = editor.querySelector('[data-step-back]');
  var submitButton = editor.querySelector('[data-submit-publication]');
  var draftButton = editor.querySelector('.publication-wizard__draft');
  var titleInput = form.querySelector('#id_title');
  var descriptionInput = form.querySelector('#id_description');
  var parentSelect = form.querySelector('#id_parent_category');
  var childSelect = form.querySelector('#id_category');
  var primaryInput = form.querySelector('[data-primary-photo]');
  var photoInput = form.querySelector('[data-photo-input]');
  var cameraInput = editor.querySelector('[data-photo-camera]');
  var photoList = editor.querySelector('[data-photo-list]');
  var photoCount = editor.querySelector('[data-photo-count]');
  var photoMessage = editor.querySelector('[data-photo-message]');
  var addPhotoButton = editor.querySelector('[data-add-photo]');
  var saveStatus = editor.querySelector('[data-save-status] span:last-child');
  var maxPhotos = Number(editor.dataset.photoMax || 8);
  var maxBytes = Number(editor.dataset.photoMaxBytes || 8388608);
  var acceptedTypes = ['image/jpeg', 'image/png', 'image/webp'];
  var selectedFiles = [];
  var objectUrls = [];
  var currentStep = 1;
  var maxReachedStep = 1;
  var dirty = false;
  var submitting = false;

  var categoryDataElement = document.getElementById('publication-category-data');
  var categories = categoryDataElement ? JSON.parse(categoryDataElement.textContent) : [];
  var categorySheet = editor.querySelector('[data-category-sheet]');
  var categoryBackdrop = editor.querySelector('.publication-category-backdrop');
  var categoryOptions = editor.querySelector('[data-category-options]');
  var categorySearch = editor.querySelector('[data-category-search]');
  var categorySheetTitle = editor.querySelector('[data-category-sheet-title]');
  var activeCategoryKind = null;
  var activeCategoryTrigger = null;

  function visiblePhotoCards() {
    return Array.prototype.slice.call(photoList.querySelectorAll('[data-photo-card]')).filter(
      function (card) { return !card.hidden; }
    );
  }

  function existingPhotoCount() {
    return visiblePhotoCards().filter(function (card) {
      return card.hasAttribute('data-existing-photo');
    }).length;
  }

  function updatePhotoCount() {
    var count = existingPhotoCount() + selectedFiles.length;
    photoCount.textContent = count + ' de ' + maxPhotos;
    addPhotoButton.hidden = count >= maxPhotos;
    return count;
  }

  function syncPrimary() {
    var cards = visiblePhotoCards();
    var selectedKey = primaryInput.value;
    if (!selectedKey && cards.length) {
      selectedKey = cards[0].dataset.photoKey;
      primaryInput.value = selectedKey;
    }
    if (selectedKey && !cards.some(function (card) { return card.dataset.photoKey === selectedKey; })) {
      selectedKey = cards.length ? cards[0].dataset.photoKey : '';
      primaryInput.value = selectedKey;
    }
    cards.forEach(function (card) {
      var isPrimary = card.dataset.photoKey === selectedKey;
      card.classList.toggle('is-primary', isPrimary);
      var label = card.querySelector('[data-primary-label]');
      if (label) label.textContent = isPrimary ? 'Principal' : 'Hacer principal';
      card.querySelector('[data-make-primary]').setAttribute('aria-pressed', String(isPrimary));
    });
  }

  function syncFileInput() {
    if (typeof DataTransfer === 'undefined') return;
    var transfer = new DataTransfer();
    selectedFiles.forEach(function (file) { transfer.items.add(file); });
    photoInput.files = transfer.files;
  }

  function clearObjectUrls() {
    objectUrls.forEach(function (url) { URL.revokeObjectURL(url); });
    objectUrls = [];
  }

  function renderNewPhotos() {
    photoList.querySelectorAll('[data-new-photo]').forEach(function (card) { card.remove(); });
    clearObjectUrls();
    selectedFiles.forEach(function (file, index) {
      var url = URL.createObjectURL(file);
      objectUrls.push(url);
      var card = document.createElement('article');
      card.className = 'publication-photo-card';
      card.dataset.photoCard = '';
      card.dataset.newPhoto = String(index);
      card.dataset.photoKey = 'new:' + index;
      card.innerHTML =
        '<img src="' + url + '" alt="Vista previa de ' + escapeHtml(file.name) + '">' +
        '<button class="publication-photo-card__remove" type="button" aria-label="Eliminar ' + escapeHtml(file.name) + '" data-remove-photo><svg class="staff-icon" aria-hidden="true"><use href="/static/img/icons/backoffice.svg?v=20260926-center-filter#icon-x"></use></svg></button>' +
        '<button class="publication-photo-card__primary" type="button" data-make-primary><span data-primary-label>Hacer principal</span></button>' +
        '<span class="publication-photo-card__status" data-photo-status>Lista</span>';
      photoList.insertBefore(card, addPhotoButton);
    });
    syncFileInput();
    updatePhotoCount();
    syncPrimary();
  }

  function escapeHtml(value) {
    var element = document.createElement('span');
    element.textContent = value;
    return element.innerHTML;
  }

  function addFiles(fileList) {
    photoMessage.textContent = '';
    var room = maxPhotos - existingPhotoCount() - selectedFiles.length;
    Array.prototype.slice.call(fileList).forEach(function (file) {
      if (room <= 0) {
        photoMessage.textContent = 'Puedes añadir un máximo de ' + maxPhotos + ' fotografías.';
        return;
      }
      if (acceptedTypes.indexOf(file.type) === -1) {
        photoMessage.textContent = 'No pudimos añadir "' + file.name + '". Usa JPEG, PNG o WebP.';
        return;
      }
      if (file.size > maxBytes) {
        photoMessage.textContent = 'No pudimos añadir "' + file.name + '". Supera el máximo permitido.';
        return;
      }
      selectedFiles.push(file);
      room -= 1;
      dirty = true;
    });
    renderNewPhotos();
    cameraInput.value = '';
  }

  function removePhoto(card) {
    if (card.hasAttribute('data-existing-photo')) {
      card.querySelector('[data-remove-existing]').checked = true;
      card.hidden = true;
    } else {
      var removedIndex = Number(card.dataset.newPhoto);
      var selectedKey = primaryInput.value;
      selectedFiles.splice(removedIndex, 1);
      if (selectedKey === 'new:' + removedIndex) primaryInput.value = '';
      else if (selectedKey.indexOf('new:') === 0 && Number(selectedKey.slice(4)) > removedIndex) {
        primaryInput.value = 'new:' + (Number(selectedKey.slice(4)) - 1);
      }
      renderNewPhotos();
    }
    dirty = true;
    updatePhotoCount();
    syncPrimary();
  }

  function validatePhotos() {
    if (updatePhotoCount() > 0) {
      photoMessage.textContent = '';
      return true;
    }
    photoMessage.textContent = 'Añade al menos una fotografía para continuar.';
    editor.querySelector('.publication-photo-picker__dropzone').focus({preventScroll: true});
    editor.querySelector('.publication-photo-picker').scrollIntoView({behavior: 'smooth', block: 'center'});
    return false;
  }

  function validateInformation() {
    if (!titleInput.value.trim()) {
      titleInput.setCustomValidity('Introduce un nombre para el objeto.');
      titleInput.reportValidity();
      titleInput.setCustomValidity('');
      return false;
    }
    if (!descriptionInput.value.trim()) {
      descriptionInput.setCustomValidity('Introduce una descripción del objeto.');
      descriptionInput.reportValidity();
      descriptionInput.setCustomValidity('');
      return false;
    }
    return true;
  }

  function descendantsOf(rootId) {
    var ids = [Number(rootId)];
    var descendants = [];
    while (ids.length) {
      var children = categories.filter(function (category) {
        return ids.indexOf(category.parent_id) !== -1;
      });
      descendants = descendants.concat(children);
      ids = children.map(function (category) { return category.id; });
    }
    return descendants;
  }

  function validateCategory() {
    if (!parentSelect.value) {
      openCategorySheet('parent', editor.querySelector('[data-category-trigger="parent"]'));
      return false;
    }
    if (descendantsOf(parentSelect.value).length && !childSelect.value) {
      openCategorySheet('child', editor.querySelector('[data-category-trigger="child"]'));
      return false;
    }
    return true;
  }

  function validateStep(step) {
    if (step === 1) return validatePhotos();
    if (step === 2) return validateInformation();
    if (step === 3) return validateCategory();
    return true;
  }

  function updateReview() {
    editor.querySelector('[data-review-title]').textContent = titleInput.value.trim() || 'Sin nombre';
    editor.querySelector('[data-review-description]').textContent = descriptionInput.value.trim() || 'Sin descripción';
    editor.querySelector('[data-review-parent]').textContent = selectedOptionText(parentSelect) || 'Sin seleccionar';
    var childText = selectedOptionText(childSelect);
    editor.querySelector('[data-review-child]').textContent = childText ? ' · ' + childText : '';
    var reviewPhotos = editor.querySelector('[data-review-photos]');
    reviewPhotos.innerHTML = '';
    visiblePhotoCards().slice(0, 5).forEach(function (card) {
      var image = card.querySelector('img').cloneNode();
      image.removeAttribute('width');
      image.removeAttribute('height');
      if (card.classList.contains('is-primary')) image.classList.add('is-primary');
      reviewPhotos.appendChild(image);
    });
  }

  function selectedOptionText(select) {
    var option = select.options[select.selectedIndex];
    return option && option.value ? option.textContent.trim() : '';
  }

  function showStep(step, focusHeading) {
    currentStep = step;
    maxReachedStep = Math.max(maxReachedStep, step);
    panels.forEach(function (panel) {
      panel.hidden = Number(panel.dataset.stepPanel) !== step;
    });
    indicators.forEach(function (indicator) {
      var indicatorStep = Number(indicator.dataset.stepIndicator);
      var button = indicator.querySelector('button');
      indicator.classList.toggle('is-current', indicatorStep === step);
      indicator.classList.toggle('is-complete', indicatorStep < step);
      button.disabled = indicatorStep > maxReachedStep;
      if (indicatorStep === step) button.setAttribute('aria-current', 'step');
      else button.removeAttribute('aria-current');
    });
    backButton.hidden = step === 1;
    nextButton.hidden = step === 4;
    submitButton.hidden = step !== 4;
    if (step === 4) updateReview();
    if (focusHeading) {
      var heading = panels[step - 1].querySelector('h2');
      heading.setAttribute('tabindex', '-1');
      heading.focus({preventScroll: true});
      editor.scrollIntoView({behavior: 'smooth', block: 'start'});
    }
  }

  function rebuildChildSelect() {
    var selected = childSelect.value;
    var descendants = parentSelect.value ? descendantsOf(parentSelect.value) : [];
    childSelect.innerHTML = '<option value="">Selecciona una subcategoría</option>';
    descendants.forEach(function (category) {
      var option = document.createElement('option');
      option.value = category.id;
      option.textContent = category.name;
      childSelect.appendChild(option);
    });
    if (descendants.some(function (category) { return String(category.id) === selected; })) {
      childSelect.value = selected;
    }
    var trigger = editor.querySelector('[data-category-trigger="child"]');
    trigger.disabled = descendants.length === 0;
    editor.querySelector('[data-category-label="child"]').textContent = descendants.length
      ? (selectedOptionText(childSelect) || 'Selecciona una subcategoría')
      : 'No necesita subcategoría';
  }

  function syncCategoryLabels() {
    editor.querySelector('[data-category-label="parent"]').textContent =
      selectedOptionText(parentSelect) || 'Selecciona una categoría';
    rebuildChildSelect();
  }

  function categoryChoices(kind) {
    if (kind === 'parent') {
      return categories.filter(function (category) { return category.parent_id === null; });
    }
    return parentSelect.value ? descendantsOf(parentSelect.value) : [];
  }

  function renderCategoryOptions(kind, query) {
    var normalizedQuery = (query || '').trim().toLocaleLowerCase('es');
    categoryOptions.innerHTML = '';
    categoryChoices(kind).filter(function (category) {
      return !normalizedQuery || category.name.toLocaleLowerCase('es').indexOf(normalizedQuery) !== -1;
    }).forEach(function (category) {
      var button = document.createElement('button');
      button.type = 'button';
      button.dataset.categoryOption = category.id;
      button.innerHTML = '<span>' + escapeHtml(category.name) + '</span><span aria-hidden="true">›</span>';
      categoryOptions.appendChild(button);
    });
    if (!categoryOptions.children.length) {
      categoryOptions.innerHTML = '<p>No hay categorías que coincidan.</p>';
    }
  }

  function openCategorySheet(kind, trigger) {
    activeCategoryKind = kind;
    activeCategoryTrigger = trigger;
    categorySheetTitle.textContent = kind === 'parent' ? 'Selecciona una categoría' : 'Selecciona una subcategoría';
    categorySearch.value = '';
    renderCategoryOptions(kind, '');
    categoryBackdrop.hidden = false;
    categorySheet.hidden = false;
    categorySearch.focus();
  }

  function closeCategorySheet(restoreFocus) {
    if (categorySheet.hidden) return;
    categorySheet.hidden = true;
    categoryBackdrop.hidden = true;
    if (restoreFocus && activeCategoryTrigger) activeCategoryTrigger.focus();
  }

  photoInput.addEventListener('change', function () {
    addFiles(photoInput.files);
  });
  cameraInput.addEventListener('change', function () {
    addFiles(cameraInput.files);
  });
  addPhotoButton.addEventListener('click', function () { photoInput.click(); });
  photoList.addEventListener('click', function (event) {
    var remove = event.target.closest('[data-remove-photo]');
    if (remove) {
      removePhoto(remove.closest('[data-photo-card]'));
      return;
    }
    var primary = event.target.closest('[data-make-primary]');
    if (primary) {
      primaryInput.value = primary.closest('[data-photo-card]').dataset.photoKey;
      dirty = true;
      syncPrimary();
    }
  });

  nextButton.addEventListener('click', function () {
    if (validateStep(currentStep)) showStep(currentStep + 1, true);
  });
  backButton.addEventListener('click', function () { showStep(currentStep - 1, true); });
  editor.querySelectorAll('[data-go-step], [data-edit-step]').forEach(function (button) {
    button.addEventListener('click', function () {
      var target = Number(button.dataset.goStep || button.dataset.editStep);
      if (target <= maxReachedStep) showStep(target, true);
    });
  });

  titleInput.addEventListener('input', function () {
    editor.querySelector('[data-title-count]').textContent = titleInput.value.length;
    dirty = true;
  });
  descriptionInput.addEventListener('input', function () { dirty = true; });
  editor.querySelector('[data-title-count]').textContent = titleInput.value.length;

  editor.querySelectorAll('[data-category-trigger]').forEach(function (trigger) {
    trigger.addEventListener('click', function () {
      openCategorySheet(trigger.dataset.categoryTrigger, trigger);
    });
  });
  editor.querySelectorAll('[data-category-close]').forEach(function (button) {
    button.addEventListener('click', function () { closeCategorySheet(true); });
  });
  categorySearch.addEventListener('input', function () {
    renderCategoryOptions(activeCategoryKind, categorySearch.value);
  });
  categoryOptions.addEventListener('click', function (event) {
    var option = event.target.closest('[data-category-option]');
    if (!option) return;
    if (activeCategoryKind === 'parent') {
      parentSelect.value = option.dataset.categoryOption;
      childSelect.value = '';
    } else {
      childSelect.value = option.dataset.categoryOption;
    }
    dirty = true;
    syncCategoryLabels();
    closeCategorySheet(true);
  });
  document.addEventListener('keydown', function (event) {
    if (event.key === 'Escape' && !categorySheet.hidden) closeCategorySheet(true);
  });

  form.addEventListener('change', function () { dirty = true; });
  form.addEventListener('submit', function (event) {
    if (submitting) {
      event.preventDefault();
      return;
    }
    var isSubmit = event.submitter && event.submitter.value === 'submit';
    if (isSubmit) {
      if (!validatePhotos()) {
        event.preventDefault();
        showStep(1, true);
        return;
      }
      if (!validateInformation()) {
        event.preventDefault();
        showStep(2, true);
        return;
      }
      if (!validateCategory()) {
        event.preventDefault();
        showStep(3, true);
        return;
      }
    }
    dirty = false;
    saveStatus.textContent = isSubmit ? 'Subiendo fotografías...' : 'Guardando borrador...';
    visiblePhotoCards().forEach(function (card) {
      if (card.hasAttribute('data-new-photo')) card.querySelector('[data-photo-status]').textContent = 'Subiendo...';
    });
    submitting = true;
    submitButton.setAttribute('aria-disabled', 'true');
    draftButton.setAttribute('aria-disabled', 'true');
  });
  window.addEventListener('beforeunload', function (event) {
    if (!dirty) return;
    event.preventDefault();
    event.returnValue = '';
  });

  var firstErrorPanel = panels.find(function (panel) {
    return panel.querySelector('.form-field--error, .form-field__errors, .has-error');
  });
  syncCategoryLabels();
  updatePhotoCount();
  syncPrimary();
  if (firstErrorPanel) {
    var errorStep = Number(firstErrorPanel.dataset.stepPanel);
    maxReachedStep = errorStep;
    showStep(errorStep, false);
  } else {
    showStep(1, false);
  }
})();

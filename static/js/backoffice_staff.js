(function () {
  function submitWithConfirmation(form) {
    var title = form.dataset.confirmTitle;
    var text = form.dataset.confirmText || '';
    if (window.Swal) {
      window.Swal.fire({
        title: title,
        text: text,
        icon: 'warning',
        showCancelButton: true,
        confirmButtonText: 'Confirmar',
        cancelButtonText: 'Cancelar',
        confirmButtonColor: '#b42318'
      }).then(function (result) {
        if (result.isConfirmed) form.submit();
      });
      return;
    }
    if (window.confirm(title + (text ? '\n\n' + text : ''))) form.submit();
  }

  document.querySelectorAll('form[data-confirm-title]').forEach(function (form) {
    form.addEventListener('submit', function (event) {
      event.preventDefault();
      submitWithConfirmation(form);
    });
  });

  document.querySelectorAll('[data-copy-target]').forEach(function (button) {
    button.addEventListener('click', function () {
      var target = document.getElementById(button.dataset.copyTarget);
      var status = document.getElementById(button.dataset.copyTarget + '-status');
      if (!target) return;

      function showSuccess() {
        var label = button.querySelector('span');
        if (label) label.textContent = 'Copiada';
        if (status) status.textContent = 'Contraseña copiada al portapapeles.';
      }

      if (navigator.clipboard && window.isSecureContext) {
        navigator.clipboard.writeText(target.value).then(showSuccess);
        return;
      }
      target.focus();
      target.select();
      if (document.execCommand('copy')) showSuccess();
      target.setSelectionRange(0, 0);
    });
  });

  var roleField = document.getElementById('id_role');
  var managerCenter = document.querySelector('[data-manager-center]');
  if (roleField && managerCenter) {
    function syncManagerCenter() {
      managerCenter.hidden = roleField.value !== 'manager';
    }
    roleField.addEventListener('change', syncManagerCenter);
    syncManagerCenter();
  }

  document.querySelectorAll('[data-override-toggle]').forEach(function (toggle) {
    var target = document.getElementById(toggle.dataset.overrideToggle);
    if (!target) return;

    function syncOverride() {
      var controls = target.matches('input, select, textarea')
        ? [target]
        : target.querySelectorAll('input, select, textarea');
      controls.forEach(function (control) {
        control.disabled = !toggle.checked;
      });
    }

    toggle.addEventListener('change', syncOverride);
    syncOverride();
  });

  var passwordReplacement = document.querySelector('[data-password-replacement]');
  var passwordActions = document.querySelectorAll('input[name="password_action"]');
  if (passwordReplacement && passwordActions.length) {
    function syncPasswordAction() {
      var selected = document.querySelector('input[name="password_action"]:checked');
      passwordReplacement.disabled = !selected || selected.value !== 'replace';
      if (!passwordReplacement.disabled) passwordReplacement.focus();
    }
    passwordActions.forEach(function (option) {
      option.addEventListener('change', syncPasswordAction);
    });
    syncPasswordAction();
  }

  var emailFeedback = document.querySelector('[data-email-feedback]');
  if (emailFeedback && window.Swal) {
    emailFeedback.hidden = true;
    window.Swal.fire({
      title: emailFeedback.dataset.title,
      text: emailFeedback.dataset.text,
      icon: emailFeedback.dataset.icon || 'info',
      confirmButtonText: 'Aceptar',
      confirmButtonColor: '#006ba6'
    });
  }

  var centerFilter = document.querySelector('[data-center-filter]');
  if (centerFilter) {
    var toggle = centerFilter.querySelector('[data-center-filter-toggle]');
    var panel = centerFilter.querySelector('[data-center-filter-panel]');
    var form = centerFilter.querySelector('[data-center-filter-form]');
    var search = centerFilter.querySelector('[data-center-filter-search]');
    var allCenters = centerFilter.querySelector('[data-center-filter-all]');
    var centerInputs = Array.prototype.slice.call(
      centerFilter.querySelectorAll('[data-center-filter-center]')
    );
    var options = Array.prototype.slice.call(
      centerFilter.querySelectorAll('[data-center-filter-option]')
    );
    var count = centerFilter.querySelector('[data-center-filter-count]');
    var validation = centerFilter.querySelector('[data-center-filter-validation]');
    var apply = centerFilter.querySelector('[data-center-filter-apply]');
    var closeButtons = centerFilter.querySelectorAll('[data-center-filter-close]');

    function selectedCenters() {
      return centerInputs.filter(function (input) { return input.checked; });
    }

    function updateCenterFilterState() {
      var selected = selectedCenters().length;
      if (allCenters.checked) {
        count.textContent = 'Todos los centros';
        validation.textContent = '';
        apply.disabled = false;
        return;
      }
      count.textContent = selected + (selected === 1 ? ' centro seleccionado' : ' centros seleccionados');
      validation.textContent = selected ? '' : 'Selecciona al menos un punto limpio o todos los centros.';
      apply.disabled = selected === 0;
    }

    function resetCenterFilter() {
      form.reset();
      search.value = '';
      options.forEach(function (option) { option.hidden = false; });
      updateCenterFilterState();
    }

    function closeCenterFilter(restoreFocus) {
      if (panel.hidden) return;
      panel.hidden = true;
      centerFilter.querySelector('.staff-center-filter__backdrop').hidden = true;
      toggle.setAttribute('aria-expanded', 'false');
      resetCenterFilter();
      if (restoreFocus) toggle.focus();
    }

    function openCenterFilter() {
      panel.hidden = false;
      centerFilter.querySelector('.staff-center-filter__backdrop').hidden = false;
      toggle.setAttribute('aria-expanded', 'true');
      search.focus();
    }

    toggle.addEventListener('click', function () {
      if (panel.hidden) openCenterFilter();
      else closeCenterFilter(true);
    });
    closeButtons.forEach(function (button) {
      button.addEventListener('click', function () { closeCenterFilter(true); });
    });
    document.addEventListener('click', function (event) {
      if (!panel.hidden && !centerFilter.contains(event.target)) closeCenterFilter(false);
    });
    document.addEventListener('keydown', function (event) {
      if (event.key === 'Escape' && !panel.hidden) closeCenterFilter(true);
    });
    allCenters.addEventListener('change', function () {
      if (allCenters.checked) {
        centerInputs.forEach(function (input) { input.checked = false; });
      }
      updateCenterFilterState();
    });
    centerInputs.forEach(function (input) {
      input.addEventListener('change', function () {
        if (input.checked) allCenters.checked = false;
        updateCenterFilterState();
      });
    });
    search.addEventListener('input', function () {
      var query = search.value.trim().toLocaleLowerCase('es');
      options.forEach(function (option) {
        option.hidden = query && option.dataset.searchText.indexOf(query) === -1;
      });
    });
    form.addEventListener('submit', function (event) {
      if (!event.submitter || !event.submitter.matches('[data-center-filter-clear]')) {
        if (!allCenters.checked && selectedCenters().length === 0) {
          event.preventDefault();
          updateCenterFilterState();
        }
      }
    });
    updateCenterFilterState();
  }

  var prompt = document.getElementById('existing-account-prompt');
  var existingForm = document.getElementById('existing-account-form');
  if (!prompt || !existingForm) return;

  var title = 'La cuenta ya existe';
  var text = 'Ya existe una cuenta con ' + prompt.dataset.email + '. ¿Quieres concederle acceso staff y asignarle el rol seleccionado? Si ya tiene credenciales, se conservarán sin cambios.';
  if (window.Swal) {
    window.Swal.fire({
      title: title,
      text: text,
      icon: 'question',
      showCancelButton: true,
      confirmButtonText: 'Conceder acceso',
      cancelButtonText: 'Cancelar',
      confirmButtonColor: '#006ba6'
    }).then(function (result) {
      if (result.isConfirmed) existingForm.submit();
    });
  } else if (window.confirm(text)) {
    existingForm.submit();
  }
})();

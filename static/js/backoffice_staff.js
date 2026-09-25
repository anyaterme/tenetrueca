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

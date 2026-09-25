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
      confirmButtonColor: '#0c6a49'
    }).then(function (result) {
      if (result.isConfirmed) existingForm.submit();
    });
  } else if (window.confirm(text)) {
    existingForm.submit();
  }
})();

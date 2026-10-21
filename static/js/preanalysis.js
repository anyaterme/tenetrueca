(function () {
  'use strict';
  var dialog = document.querySelector('[data-ai-dialog]');
  var running = false;
  var previousFocus;
  if (dialog) {
    dialog.addEventListener('cancel', function (event) { if (running) event.preventDefault(); });
    dialog.querySelector('[data-ai-close]').addEventListener('click', function () {
      dialog.close();
      if (previousFocus) previousFocus.focus();
    });
  }
  document.querySelectorAll('[data-ai-request]').forEach(function (form) {
    form.addEventListener('submit', async function (event) {
      if (event.defaultPrevented) return;
      var action = event.submitter && event.submitter.value;
      if (action !== 'submit' && action !== 'retry') return;
      if (!dialog || typeof dialog.showModal !== 'function' || typeof fetch !== 'function') return;
      event.preventDefault();
      if (running) return;
      running = true;
      previousFocus = event.submitter;
      var progress = dialog.querySelector('[data-ai-progress]');
      var close = dialog.querySelector('[data-ai-close]');
      var errors = dialog.querySelector('[data-ai-errors]');
      progress.hidden = false;
      close.hidden = errors.hidden = true;
      dialog.showModal();
      dialog.querySelector('h2').focus();
      form.setAttribute('aria-busy', 'true');
      var data = new FormData(form);
      data.set('action', action);
      form.dispatchEvent(new Event('preanalysis:start'));
      var controller = new AbortController();
      // Allow upload/response overhead beyond the server's analysis deadline.
      var timer = setTimeout(function () { controller.abort(); }, (Number(form.dataset.aiTimeout) + 15) * 1000);
      try {
        var response = await fetch(form.getAttribute('action') || location.href, {
          method: 'POST', body: data, credentials: 'same-origin', signal: controller.signal,
          headers: {'X-Requested-With': 'XMLHttpRequest'}
        });
        if (response.redirected && response.ok) {
          window.location.assign(response.url);
          return;
        }
        if (!(response.headers.get('content-type') || '').includes('application/json')) {
          throw new Error('No se ha podido obtener el resultado. Tus datos siguen en el formulario.');
        }
        var result = await response.json();
        if (response.ok && result.url) {
          window.location.assign(result.url);
          return;
        }
        var messages = [];
        Object.keys(result.errors || {}).forEach(function (field) {
          result.errors[field].forEach(function (error) { messages.push(error.message); });
        });
        throw new Error(messages.join(' ') || 'No se ha podido completar el análisis.');
      } catch (error) {
        running = false;
        progress.hidden = true;
        close.hidden = errors.hidden = false;
        errors.textContent = error.name === 'AbortError'
          ? 'Se ha agotado el tiempo de espera. Tus datos se conservan; puedes volver al formulario y reintentar.'
          : error.message;
        close.focus();
        form.dispatchEvent(new Event('preanalysis:error'));
      } finally {
        clearTimeout(timer);
        form.removeAttribute('aria-busy');
      }
    });
  });
})();

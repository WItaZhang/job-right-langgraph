document.querySelectorAll('[data-example]').forEach(link => {
  link.addEventListener('click', event => {
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    const field = document.querySelector('#feedback');
    field.value = link.dataset.example;
    field.focus();
  });
});
document.querySelectorAll('form[data-busy]').forEach(form => {
  form.addEventListener('submit', event => {
    if (form.dataset.sending) {
      event.preventDefault();
      return;
    }
    form.dataset.sending = 'true';
    const button = form.querySelector('button');
    button.dataset.original = button.textContent;
    button.disabled = true;
    button.textContent = form.dataset.busy;
    form.setAttribute('aria-busy', 'true');
  });
});
window.addEventListener('pageshow', () => {
  document.querySelectorAll('form[data-sending]').forEach(form => {
    delete form.dataset.sending;
    form.removeAttribute('aria-busy');
    const button = form.querySelector('button');
    button.disabled = false;
    button.textContent = button.dataset.original;
  });
});

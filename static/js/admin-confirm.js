// Phase 6.6: external confirmation handlers compatible with a strict CSP.
document.addEventListener('submit', function (event) {
  const form = event.target;
  if (!(form instanceof HTMLFormElement)) return;
  const message = form.dataset.confirm;
  if (message && !window.confirm(message)) event.preventDefault();
});

(() => {
  // These values only identify the work currently displayed in a tool. Clear
  // them on every document entry (reload, direct navigation, or returning to a
  // tool) so a page always starts from its original state. Account sessions,
  // user preferences, and saved library records remain intact.
  [
    "image3d-session",
    "text-image-task",
    "visual-studio-task",
    "animal-cutout-task",
  ].forEach((key) => sessionStorage.removeItem(key));

  // Browsers may restore form controls (including text and selections) as part
  // of reload navigation. Reset them after the document has been parsed and
  // before deferred application scripts run.
  document.addEventListener("DOMContentLoaded", () => {
    document.querySelectorAll("form").forEach((form) => form.reset());
    document.querySelectorAll('input[type="file"]').forEach((input) => {
      input.value = "";
    });
  }, { once: true });

  // A page restored from the back/forward cache keeps its JavaScript memory
  // and DOM. Reload it once so application state is rebuilt from defaults.
  window.addEventListener("pageshow", (event) => {
    if (event.persisted) location.reload();
  });
})();

(() => {
  const navigation = performance.getEntriesByType?.("navigation")?.[0];
  const isReload = navigation?.type === "reload"
    || performance.navigation?.type === performance.navigation?.TYPE_RELOAD;

  if (!isReload) return;

  // These values only identify the work currently displayed in a tool. Keep
  // account/preferences and saved library records intact, but never revive a
  // transient task after the user refreshes the page.
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
})();

(() => {
  // Resume only the active STL post-processing task in this tab. The marker
  // never restores credentials by itself: an explicitly cleared/switched
  // session, another tool, or a mismatched marker keeps the normal reset.
  let resumeProcessing = false;
  try {
    const active = JSON.parse(sessionStorage.getItem("image3d-session") || "null");
    const marker = JSON.parse(sessionStorage.getItem("image3d-processing-resume") || "null");
    resumeProcessing = location.pathname === "/image-to-3d"
      && typeof active?.id === "string" && Boolean(active.id)
      && typeof active?.token === "string" && Boolean(active.token)
      && marker?.id === active.id && marker?.token === active.token
      && typeof marker.source_url === "string" && Boolean(marker.source_url)
      && ["pending", "running", "failed"].includes(marker.status);
  } catch (_) { /* Malformed saved values must not revive a previous task. */ }
  if (!resumeProcessing) sessionStorage.removeItem("image3d-processing-resume");

  // Other current-tool values still reset on every document entry. Account
  // sessions, user preferences, and saved library records remain intact.
  [
    "image3d-session",
    "text-image-task",
    "visual-studio-task",
    "animal-cutout-task",
  ].forEach((key) => {
    if (key !== "image3d-session" || !resumeProcessing) sessionStorage.removeItem(key);
  });

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

// Sends a file-upload form with the security token in a header (the gate reads the token from a header for uploads),
// then follows the redirect, or shows the page's own list of problems in place.
document.addEventListener("submit", async (event) => {
  const form = event.target;
  if (!(form instanceof HTMLFormElement) || !form.matches("form[data-upload]")) return;
  event.preventDefault();
  const button = form.querySelector("button[type=submit]");
  const result = document.getElementById("import-result");
  const tokenField = form.querySelector('input[name="csrf_token"]');
  const body = new FormData(form);
  body.delete("csrf_token");
  if (button) button.disabled = true;
  try {
    const response = await fetch(form.action, {
      method: "POST",
      body,
      headers: { "X-CSRF-Token": tokenField ? tokenField.value : "" },
      credentials: "same-origin",
    });
    if (response.redirected) {
      window.location.href = response.url;
      return;
    }
    const page = new DOMParser().parseFromString(await response.text(), "text/html");
    const fresh = page.getElementById("import-result");
    if (result) {
      if (fresh) result.replaceChildren(...fresh.childNodes);
      else result.textContent = "The file could not be sent (" + response.status + "). Reload the page and try again.";
    }
  } catch (problem) {
    if (result) result.textContent = "The file could not be sent. Check the connection and try again.";
  } finally {
    if (button) button.disabled = false;
  }
});

/* SPDX-License-Identifier: AGPL-3.0-only */
(() => {
  const initial = document.getElementById("list-items");
  if (!initial) return;
  async function poll() {
    const current = document.getElementById("list-items");
    // Preserve unsaved checkbox edits until submission.
    if (!document.hidden && !current.dataset.dirty) {
      try {
        const url = new URL(current.dataset.refreshUrl, location.href);
        url.searchParams.set("version", current.dataset.version);
        const response = await fetch(url, {cache: "no-store"});
        if (response.status === 200) {
          const html = await response.text();
          if (!current.dataset.dirty) {
            const open = current.querySelector("#staples").open;
            const parsed = new DOMParser().parseFromString(html, "text/html");
            const replacement = parsed.getElementById("list-items");
            if (replacement) {
              replacement.querySelector("#staples").open = open;
              current.replaceWith(replacement);
              document.querySelector(".list-add [name=list_id]").value = replacement.dataset.listId;
            }
          }
        }
      } catch (_) { /* Retry on the next poll; forms remain available. */ }
    }
    setTimeout(poll, Number(initial.dataset.pollMs));
  }
  document.addEventListener("change", event => {
    if (event.target.matches("#list-items input[type=checkbox]")) {
      document.getElementById("list-items").dataset.dirty = "1";
      event.target.form.requestSubmit(event.target.form.querySelector("button[value=check]"));
    }
  });
  setTimeout(poll, Number(initial.dataset.pollMs));
})();

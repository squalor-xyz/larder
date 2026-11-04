// SPDX-License-Identifier: AGPL-3.0-only
// An external file: the Content-Security-Policy forbids inline scripts.
if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js", { scope: "/" });
  });
}

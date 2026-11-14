/* Shared page behaviour: sidebar, alerts, confirmations, toasts, upload previews. */
(function () {
  "use strict";

  const KS = (window.KS = window.KS || {});
  const meta = document.querySelector('meta[name="csrf-token"]');
  KS.csrf = meta ? meta.content : "";

  // ---- Toasts --------------------------------------------------------------
  KS.toast = function (message, type, linkHref, linkText) {
    const stack = document.getElementById("toasts");
    if (!stack) { alert(message); return; }
    const el = document.createElement("div");
    el.className = "toast " + (type || "");
    el.setAttribute("role", type === "error" ? "alert" : "status");
    const text = document.createElement("span");
    text.textContent = message;
    el.appendChild(text);
    if (linkHref) {
      const a = document.createElement("a");
      a.href = linkHref;
      a.textContent = linkText || "View";
      el.appendChild(a);
    }
    stack.appendChild(el);
    setTimeout(() => { el.style.opacity = "0"; el.style.transition = "opacity .3s"; }, type === "error" ? 7000 : 4000);
    setTimeout(() => el.remove(), type === "error" ? 7400 : 4400);
  };

  // ---- Show / hide menus (remembered in this browser) ----------------------
  const remember = (key, value) => { try { localStorage.setItem(key, value); } catch (err) { /* optional */ } };
  const syncPressed = () => document.querySelectorAll("[data-toggle-cats]").forEach((b) =>
    b.setAttribute("aria-pressed", document.body.classList.contains("cats-hidden") ? "true" : "false"));
  syncPressed();

  document.addEventListener("click", function (e) {
    if (e.target.closest("[data-toggle-sidebar]")) {
      if (window.matchMedia("(max-width: 980px)").matches) {
        document.body.classList.toggle("sidebar-open");          // phones/tablets: slide-in menu
      } else {
        const collapsed = document.body.classList.toggle("sidebar-collapsed");  // desktop: icon-only menu
        remember("ks_sidebar", collapsed ? "collapsed" : "open");
      }
    }
    if (e.target.closest("[data-toggle-cats]")) {
      const hidden = document.body.classList.toggle("cats-hidden");
      remember("ks_cats", hidden ? "hidden" : "shown");
      syncPressed();
    }
    if (e.target.closest("[data-close-sidebar]")) document.body.classList.remove("sidebar-open");
    const dismiss = e.target.closest("[data-dismiss]");
    if (dismiss) closeAlert(dismiss.closest(".alert"));
  });

  function closeAlert(alertEl) {
    if (!alertEl) return;
    alertEl.classList.add("is-leaving");
    setTimeout(() => alertEl.remove(), 260);
  }
  document.querySelectorAll('.alert[data-autoclose="true"]').forEach((el) => setTimeout(() => closeAlert(el), 5000));

  // ---- Forms: confirm, busy state, auto-submit -----------------------------
  document.addEventListener("submit", function (e) {
    const form = e.target;
    const question = form.getAttribute("data-confirm");
    if (question && !window.confirm(question)) { e.preventDefault(); return; }
    if (form.hasAttribute("data-busy-on-submit") || form.hasAttribute("data-confirm")) {
      if (form.dataset.submitting === "1") { e.preventDefault(); return; }
      form.dataset.submitting = "1";
      const btn = form.querySelector('button[type="submit"]');
      if (btn) btn.classList.add("is-busy");
      // Re-enable if the browser comes back to this page from history.
      window.addEventListener("pageshow", () => { form.dataset.submitting = ""; if (btn) btn.classList.remove("is-busy"); }, { once: true });
    }
  });
  document.querySelectorAll("select[data-autosubmit]").forEach((sel) =>
    sel.addEventListener("change", () => { const f = sel.form; const p = f.querySelector('[name="page"]'); if (p) p.value = ""; f.submit(); })
  );

  // ---- Image preview -------------------------------------------------------
  document.querySelectorAll("input[data-image-input]").forEach((input) => {
    input.addEventListener("change", () => {
      const box = document.querySelector('[data-preview-for="' + input.id + '"]');
      const file = input.files && input.files[0];
      if (!box || !file) return;
      if (!file.type.startsWith("image/")) { KS.toast("Please choose an image file.", "error"); input.value = ""; return; }
      const img = document.createElement("img");
      img.alt = "New photo";
      img.src = URL.createObjectURL(file);
      box.replaceChildren(img);
    });
  });

  // ---- PDF drop zone -------------------------------------------------------
  document.querySelectorAll("[data-dropzone]").forEach((zone) => {
    const input = zone.querySelector('input[type="file"]');
    const label = zone.querySelector("[data-dropzone-label]");
    ["dragenter", "dragover"].forEach((ev) => zone.addEventListener(ev, () => zone.classList.add("drag")));
    ["dragleave", "drop"].forEach((ev) => zone.addEventListener(ev, () => zone.classList.remove("drag")));
    input.addEventListener("change", () => {
      const file = input.files && input.files[0];
      if (!file) return;
      if (!/\.pdf$/i.test(file.name)) { KS.toast("Only PDF files can be uploaded.", "error"); input.value = ""; return; }
      label.textContent = file.name + " (" + (file.size / 1024 / 1024).toFixed(2) + " MB)";
    });
  });

  // ---- Last-resort error reporting -----------------------------------------
  window.addEventListener("error", (e) => console.error("Page error:", e.message, e.error));
  window.addEventListener("unhandledrejection", (e) => console.error("Unhandled promise rejection:", e.reason));
})();

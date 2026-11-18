/* Part payment window: split one bill across Cash / Card / UPI ...
 *
 *   KS.PartPay.open({
 *     title: "Bill No: Op123",          // shown under the heading
 *     modes: [{id, name}],
 *     total: 50000,                      // bill total in paise
 *     payments: [{id, mode, amount}],    // amount in paise
 *     onAdd(modeId, paise)  -> Promise<payments | null>   (null = failed, keep window as is)
 *     onRemove(id)          -> Promise<payments | null>
 *     onFinish()            -> Promise<boolean>           (true closes the window)
 *     finishLabel: "Settle",
 *   })
 * The caller does the saving; this file only draws the window and keeps it in step. */
(function () {
  "use strict";
  const KS = (window.KS = window.KS || {});
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const money = (p) => "₹" + KS.rupees(p);

  let box = null;
  let opts = null;
  let modeId = null;
  let busy = false;
  let lastFocus = null;

  function paidOf(list) { return list.reduce((s, p) => s + p.amount, 0); }
  function due() { return opts.total - paidOf(opts.payments); }

  function build() {
    box = document.createElement("div");
    box.className = "pp-backdrop";
    box.hidden = true;
    box.innerHTML =
      '<div class="pp" role="dialog" aria-modal="true" aria-labelledby="pp-title">' +
      '  <header class="pp-head"><div><h2 id="pp-title">Part Payment</h2><span class="pp-sub" id="pp-sub"></span></div>' +
      '    <button type="button" class="icon-btn" data-pp="close" aria-label="Close">✕</button></header>' +
      '  <div class="pp-modes" id="pp-modes" role="radiogroup" aria-label="Paid by"></div>' +
      '  <div class="pp-payable"><span>Payable amount</span><strong id="pp-total"></strong></div>' +
      '  <form class="pp-entry" id="pp-form" novalidate>' +
      '    <label for="pp-amount">Enter amount <b id="pp-mode-name"></b></label>' +
      '    <div class="pp-entry-row"><span class="pp-rs">₹</span><input id="pp-amount" type="number" inputmode="decimal" min="0.01" step="0.01" autocomplete="off">' +
      '    <button type="submit" class="btn btn-primary" id="pp-save">Save</button></div>' +
      '  </form>' +
      '  <p class="error-text" id="pp-error" hidden></p>' +
      '  <section class="pp-summary" aria-live="polite"><h3>Payment summary</h3><ul id="pp-list"></ul></section>' +
      '  <footer class="pp-foot"><button type="button" class="btn btn-ghost" data-pp="close">Back to order</button>' +
      '    <button type="button" class="btn btn-success" id="pp-finish"></button></footer>' +
      "</div>";
    document.body.appendChild(box);

    box.addEventListener("click", async (e) => {
      if (e.target === box) return close();
      const btn = e.target.closest("[data-pp]");
      if (!btn || busy) return;
      const kind = btn.dataset.pp;
      if (kind === "close") close();
      else if (kind === "mode") { modeId = Number(btn.dataset.id); render(); box.querySelector("#pp-amount").focus(); }
      else if (kind === "remove") {
        const list = await run(() => opts.onRemove(Number(btn.dataset.id)));
        if (list) { opts.payments = list; render(true); }
      }
    });
    box.querySelector("#pp-form").addEventListener("submit", async (e) => {
      e.preventDefault();
      if (busy) return;
      const input = box.querySelector("#pp-amount");
      const paise = Math.round(Number(input.value) * 100);
      if (!modeId) return error("Choose how this amount was paid.");
      if (!Number.isFinite(paise) || paise <= 0) { input.focus(); return error("Enter the amount received."); }
      if (paise > due()) { input.focus(); return error("Only " + money(due()) + " is due. Enter " + money(due()) + " or less."); }
      const list = await run(() => opts.onAdd(modeId, paise));
      if (list) { opts.payments = list; render(true); }
    });
    box.querySelector("#pp-finish").addEventListener("click", async () => {
      if (busy || due() !== 0) return;
      const done = await run(() => opts.onFinish());
      if (done) close();
    });
    document.addEventListener("keydown", (e) => {
      if (box.hidden) return;
      if (e.key === "Escape") { e.preventDefault(); close(); }
      if (e.key === "Tab") trapFocus(e);
    });
  }

  function trapFocus(e) {
    const f = Array.from(box.querySelectorAll("button:not([disabled]), input")).filter((el) => el.offsetParent !== null);
    if (!f.length) return;
    const first = f[0], last = f[f.length - 1];
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  }

  async function run(fn) {
    busy = true;
    error("");
    box.classList.add("is-busy");
    try { return await fn(); }
    catch (err) { if (!err || !err.userMessage) console.error(err); error((err && err.userMessage) || "Something went wrong. Nothing was changed."); return null; }
    finally { busy = false; box.classList.remove("is-busy"); }
  }

  function error(msg) {
    const el = box.querySelector("#pp-error");
    el.textContent = msg || "";
    el.hidden = !msg;
  }

  function render(resetAmount) {
    const d = due();
    box.querySelector("#pp-sub").textContent = opts.title || "";
    box.querySelector("#pp-total").textContent = money(opts.total);
    box.querySelector("#pp-modes").innerHTML = opts.modes.map((m) =>
      `<button type="button" class="pp-mode${m.id === modeId ? " active" : ""}" data-pp="mode" data-id="${m.id}" role="radio" aria-checked="${m.id === modeId}">${esc(m.name)}</button>`
    ).join("");
    const cur = opts.modes.find((m) => m.id === modeId);
    box.querySelector("#pp-mode-name").textContent = cur ? "· " + cur.name : "";
    const input = box.querySelector("#pp-amount");
    if (resetAmount || !input.value) input.value = d > 0 ? (d / 100).toFixed(2) : "";
    input.disabled = d <= 0;
    box.querySelector("#pp-save").disabled = d <= 0;

    const rows = opts.payments.map((p) =>
      `<li><span class="pp-paid">Paid via ${esc(p.mode)}${p.time ? ` <small>${esc(p.time)}</small>` : ""}</span>` +
      `<strong>${money(p.amount)}</strong>` +
      `<button type="button" class="pp-x" data-pp="remove" data-id="${p.id}" aria-label="Remove ${esc(p.mode)} ${money(p.amount)}">✕</button></li>`
    );
    rows.push(d < 0
      ? `<li class="pp-due over"><span>Paid too much</span><strong>${money(-d)}</strong><span></span></li>`
      : `<li class="pp-due${d === 0 ? " done" : ""}"><span>${d === 0 ? "Fully paid" : "Not paid"}</span><strong>${money(d)}</strong><span></span></li>`);
    box.querySelector("#pp-list").innerHTML = rows.join("");

    const finish = box.querySelector("#pp-finish");
    finish.textContent = (opts.finishLabel || "Settle") + " " + money(opts.total);
    finish.disabled = d !== 0;
    finish.title = d === 0 ? "" : "Take the full amount first";
  }

  function open(o) {
    if (!box) build();
    opts = Object.assign({ payments: [] }, o);
    modeId = opts.modes.length ? opts.modes[0].id : null;
    lastFocus = document.activeElement;
    box.querySelector("#pp-amount").value = "";
    error("");
    render(true);
    box.hidden = false;
    document.body.classList.add("pp-open");
    setTimeout(() => { const i = box.querySelector("#pp-amount"); if (!i.disabled) { i.focus(); i.select(); } }, 30);
  }

  function close() {
    if (!box || box.hidden) return;
    box.hidden = true;
    document.body.classList.remove("pp-open");
    if (opts && opts.onClose) opts.onClose();
    if (lastFocus && lastFocus.focus) lastFocus.focus();
  }

  KS.PartPay = { open, close, isOpen: () => !!box && !box.hidden };
})();

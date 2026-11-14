/* Quick bill screen (Generate Bill / Delivery / Pick Up).
 * Menu grid, search and categories come from pos_menu.js.
 * Totals are calculated in paise (integers) using the same rules as
 * app/services/billing.py. The server recalculates everything from database
 * prices when saving, so these numbers are only for display. */
(function () {
  "use strict";

  const root = document.getElementById("pos");
  if (!root || root.dataset.mode !== "quick") return;
  const KS = window.KS;
  const $ = (id) => document.getElementById(id);
  const rupees = KS.rupees;
  const roundHalfUp = KS.roundHalfUp;
  const DRAFT_KEY = "ks_pos_draft_v1";

  // ---- State ---------------------------------------------------------------
  const cart = new Map(); // item id -> quantity, in the order added
  const state = { saving: false, containerMode: "percent", orderType: "dine_in" };
  const payButtons = Array.from(root.querySelectorAll("[data-pay]"));
  let payMode = payButtons.length ? Number(payButtons[0].dataset.pay) : null;

  const menu = KS.PosMenu({
    root,
    getQty: (id) => cart.get(id) || 0,
    onAdd: (item) => setQty(item.id, (cart.get(item.id) || 0) + 1),
  });
  const byId = menu.byId;

  function totals() {
    let subtotal = 0, gstSum = 0, count = 0;
    cart.forEach((qty, id) => {
      const item = byId.get(id);
      if (!item) return;
      const line = item.ratePaise * qty;
      subtotal += line;
      gstSum += roundHalfUp((line * item.gst) / 100);
      count += qty;
    });
    const half = roundHalfUp(gstSum / 2);
    const serviceSel = $("service-mode");
    const servicePct = serviceSel ? Number(serviceSel.selectedOptions[0].dataset.percent || 0) : 0;
    const service = roundHalfUp((subtotal * servicePct) / 100 / 100) * 100; // whole rupees
    let container = 0;
    if (state.orderType !== "dine_in" && $("container-on").checked && subtotal > 0) {
      const value = Math.max(0, Number($("container-value").value) || 0);
      container = state.containerMode === "percent" ? roundHalfUp((subtotal * value) / 100) : roundHalfUp(value * 100);
    }
    return { subtotal, cgst: half, sgst: half, gst: half * 2, service, container, count, grand: subtotal + half * 2 + service + container };
  }

  // ---- Rendering -----------------------------------------------------------
  function renderCart() {
    const lines = $("cart-lines");
    lines.querySelectorAll(".line").forEach((el) => el.remove());
    $("cart-empty").hidden = cart.size > 0;
    cart.forEach((qty, id) => {
      const item = byId.get(id);
      if (!item) return;
      const row = document.createElement("div");
      row.className = "line";
      row.dataset.id = id;
      const info = document.createElement("div");
      const name = document.createElement("div");
      name.className = "line-name";
      name.textContent = item.name;
      const metaLine = document.createElement("div");
      metaLine.className = "line-meta";
      metaLine.textContent = "₹" + rupees(item.ratePaise) + (item.gst ? " · GST " + item.gst + "%" : "");
      info.append(name, metaLine);

      const stepper = document.createElement("div");
      stepper.className = "stepper";
      stepper.innerHTML =
        '<button type="button" data-action="dec" aria-label="Decrease">−</button>' +
        '<input type="number" min="1" max="999" inputmode="numeric" aria-label="Quantity">' +
        '<button type="button" data-action="inc" aria-label="Increase">+</button>';
      stepper.querySelector("input").value = qty;

      const amt = document.createElement("div");
      amt.className = "line-amt with-x";
      amt.appendChild(document.createTextNode("₹" + rupees(item.ratePaise * qty)));
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "line-void";
      remove.dataset.action = "remove";
      remove.title = "Remove " + item.name;
      remove.setAttribute("aria-label", "Remove " + item.name);
      remove.textContent = "×";
      amt.appendChild(remove);
      row.append(info, stepper, amt);
      lines.appendChild(row);
    });
    renderTotals();
  }

  function renderTotals() {
    const t = totals();
    $("cart-count").textContent = cart.size;
    $("t-count").textContent = t.count + (t.count === 1 ? " item" : " items");
    $("t-subtotal").textContent = "₹" + rupees(t.subtotal);
    $("t-cgst").textContent = "₹" + rupees(t.cgst);
    $("t-sgst").textContent = "₹" + rupees(t.sgst);
    $("t-gst").textContent = "₹" + rupees(t.gst);
    $("row-service").hidden = t.service === 0;
    $("t-service").textContent = "₹" + rupees(t.service);
    $("t-container").textContent = "₹" + rupees(t.container);
    $("t-grand").textContent = "₹" + rupees(t.grand);
    $("save-bill").disabled = cart.size === 0 || !payMode || state.saving;
    saveDraft();
  }

  function refresh() { menu.render(); renderCart(); }

  // ---- Cart actions --------------------------------------------------------
  function setQty(id, qty) {
    if (!byId.has(id)) return;
    qty = Math.floor(Number(qty) || 0);
    if (qty <= 0) cart.delete(id);
    else cart.set(id, Math.min(999, qty));
    showError("");
    refresh();
  }

  $("cart-lines").addEventListener("click", (e) => {
    const action = e.target.closest("[data-action]");
    if (!action) return;
    const id = Number(action.closest(".line").dataset.id);
    const qty = cart.get(id) || 0;
    if (action.dataset.action === "inc") setQty(id, qty + 1);
    else if (action.dataset.action === "dec") setQty(id, qty - 1);
    else if (action.dataset.action === "remove") setQty(id, 0);
  });

  $("cart-lines").addEventListener("change", (e) => {
    if (!e.target.matches(".stepper input")) return;
    const id = Number(e.target.closest(".line").dataset.id);
    setQty(id, e.target.value);
  });

  // ---- Payment, service, container -----------------------------------------
  payButtons.forEach((btn) =>
    btn.addEventListener("click", () => {
      payMode = Number(btn.dataset.pay);
      payButtons.forEach((b) => { b.classList.toggle("active", b === btn); b.setAttribute("aria-checked", b === btn ? "true" : "false"); });
      renderTotals();
    })
  );
  if ($("service-mode")) $("service-mode").addEventListener("change", renderTotals);

  // Order type: dine-in never has container charges; pickup and delivery add them automatically.
  const orderButtons = Array.from(root.querySelectorAll("[data-order]"));
  function setOrderType(type) {
    if (!orderButtons.some((b) => b.dataset.order === type)) type = "dine_in";
    const changed = state.orderType !== type;
    state.orderType = type;
    orderButtons.forEach((b) => { const on = b.dataset.order === type; b.classList.toggle("active", on); b.setAttribute("aria-checked", on ? "true" : "false"); });
    const takeaway = type !== "dine_in";
    $("row-container").hidden = !takeaway;
    if (changed) $("container-on").checked = takeaway;
    syncContainer();
  }
  function syncContainer() {
    $("container-ctrl").style.opacity = $("container-on").checked ? "1" : ".4";
    $("container-ctrl").querySelectorAll("input, button").forEach((el) => (el.disabled = !$("container-on").checked));
    renderTotals();
  }
  orderButtons.forEach((btn) => btn.addEventListener("click", () => setOrderType(btn.dataset.order)));
  $("container-on").addEventListener("change", syncContainer);
  $("container-value").addEventListener("input", renderTotals);
  root.querySelectorAll("[data-cmode]").forEach((btn) =>
    btn.addEventListener("click", () => {
      if (state.containerMode === btn.dataset.cmode) return;
      state.containerMode = btn.dataset.cmode;
      root.querySelectorAll("[data-cmode]").forEach((b) => b.classList.toggle("active", b === btn));
      $("container-value").value = state.containerMode === "percent" ? 10 : 20;
      renderTotals();
    })
  );

  // ---- Validation & saving -------------------------------------------------
  function showError(msg) {
    const el = $("cart-error");
    el.textContent = msg;
    el.hidden = !msg;
  }

  function cleanPhone() {
    return $("customer-phone").value.replace(/[\s-]/g, "");
  }

  $("customer-phone").addEventListener("input", () => $("customer-phone").parentElement.classList.remove("invalid"));

  async function save() {
    if (state.saving) return;
    showError("");
    if (cart.size === 0) { showError("Add at least one item."); return; }
    if (!payMode) { showError("Choose a payment mode."); return; }
    const phone = cleanPhone();
    if (phone && !/^\+?\d{10,14}$/.test(phone)) {
      $("customer-phone").parentElement.classList.add("invalid");
      showError("Enter a valid mobile number (10 digits) or leave it empty.");
      $("customer-phone").focus();
      return;
    }
    const containerOn = state.orderType !== "dine_in" && $("container-on").checked;
    const payload = {
      order_type: state.orderType,
      items: Array.from(cart, ([item_id, quantity]) => ({ item_id, quantity })),
      payment_mode_id: payMode,
      service_mode_id: $("service-mode") ? $("service-mode").value || null : null,
      container: { enabled: containerOn, mode: state.containerMode, value: Number($("container-value").value) || 0 },
      customer_name: $("customer-name").value.trim(),
      customer_phone: phone,
    };

    const btn = $("save-bill");
    state.saving = true;
    btn.classList.add("is-busy");
    btn.disabled = true;
    let res, body;
    try {
      res = await fetch(root.dataset.saveUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json", "X-CSRF-Token": KS.csrf },
        body: JSON.stringify(payload),
        credentials: "same-origin",
      });
      body = await res.json().catch(() => ({}));
    } catch (err) {
      console.error(err);
      finishSaving();
      showError("Could not reach the server. Check the connection and try again. The bill was not saved.");
      return;
    }
    finishSaving();

    if (res.status === 401) {
      KS.toast("Your session has ended. Please sign in again.", "error");
      setTimeout(() => (window.location.href = "/login?next=" + encodeURIComponent(location.pathname)), 1200);
      return;
    }
    if (!res.ok || !body.ok) {
      showError((body && body.error) || "The bill could not be saved (error " + res.status + ").");
      if (body && body.error_id) console.error("Server error reference:", body.error_id);
      return;
    }

    const receipt = root.dataset.receiptUrl.replace(/\/0$/, "/" + body.bill_id);
    KS.toast("Bill " + body.bill_no + " saved · ₹" + rupees(Math.round(body.total * 100)), "success", receipt, "View bill");
    resetBill(body.next_bill_no);
    // After printing, go back to the Table View.
    KS.printUrl(receipt + "?embed=1", () => { location.href = root.dataset.doneUrl; });
  }

  function finishSaving() {
    state.saving = false;
    $("save-bill").classList.remove("is-busy");
    renderTotals();
  }

  function resetBill(nextNo) {
    cart.clear();
    $("customer-name").value = "";
    $("customer-phone").value = "";
    if (nextNo) $("bill-no").textContent = nextNo;
    setOrderType(root.dataset.initialOrder || "dine_in");
    clearDraft();
    refresh();
    menu.focusSearch();
  }

  $("save-bill").addEventListener("click", save);
  document.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key === "Enter") { e.preventDefault(); save(); }
  });

  // ---- Draft (survives an accidental reload in the same tab) --------------
  function saveDraft() {
    try {
      if (cart.size === 0) { sessionStorage.removeItem(DRAFT_KEY); return; }
      sessionStorage.setItem(DRAFT_KEY, JSON.stringify({ items: Array.from(cart), name: $("customer-name").value, phone: $("customer-phone").value, order: state.orderType }));
    } catch (err) { /* storage can be unavailable (private mode); drafts are optional */ }
  }
  function clearDraft() { try { sessionStorage.removeItem(DRAFT_KEY); } catch (err) { /* ignore */ } }
  function loadDraft() {
    try {
      const draft = JSON.parse(sessionStorage.getItem(DRAFT_KEY) || "null");
      if (!draft) return;
      draft.items.forEach(([id, qty]) => { if (byId.has(id)) cart.set(id, Math.min(999, Math.max(1, qty | 0))); });
      $("customer-name").value = draft.name || "";
      $("customer-phone").value = draft.phone || "";
      if (draft.order) setOrderType(draft.order);
      if (cart.size) KS.toast("Restored the unsaved bill from before the page reloaded.");
    } catch (err) { clearDraft(); }
  }
  ["customer-name", "customer-phone"].forEach((id) => $(id).addEventListener("input", saveDraft));
  window.addEventListener("beforeunload", (e) => {
    if (cart.size > 0 && !state.saving) { e.preventDefault(); e.returnValue = ""; }
  });

  // ---- Clock ----------------------------------------------------------------
  const clock = $("clock");
  const pad = (n) => String(n).padStart(2, "0");
  setInterval(() => {
    const d = new Date();
    clock.textContent = pad(d.getDate()) + "-" + pad(d.getMonth() + 1) + "-" + d.getFullYear() + " " + pad(d.getHours()) + ":" + pad(d.getMinutes());
  }, 1000);

  setOrderType(root.dataset.initialOrder || "dine_in");
  loadDraft();
  refresh();
})();

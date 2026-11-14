/* Table order screen: add items, Save / KOT & Print, Print Bill, Settle, Move, Cancel.
 * Already-sent items (KOTs) come from the server; new items live in `cart` until saved. */
(function () {
  "use strict";

  const root = document.getElementById("pos");
  if (!root || root.dataset.mode !== "table") return;
  const KS = window.KS;
  const $ = (id) => document.getElementById(id);
  const rupees = KS.rupees;
  const roundHalfUp = KS.roundHalfUp;
  const api = root.dataset.api;            // /api/tables/<id>
  const canBill = root.dataset.canBill === "1";
  const STATUS = { blank: "Free", running: "Running", kot: "KOT sent", ready: "Food ready", printed: "Bill printed" };

  let state = JSON.parse($("table-state").textContent || "{}");
  const cart = new Map(); // item id -> {qty, note}
  let busy = false;

  const payButtons = Array.from(root.querySelectorAll("[data-pay]"));
  let payMode = payButtons.length ? Number(payButtons[0].dataset.pay) : null;

  const menu = KS.PosMenu({
    root,
    getQty: (id) => (cart.get(id) || {}).qty || 0,
    onAdd: (item) => {
      const line = cart.get(item.id) || { qty: 0, note: "" };
      line.qty = Math.min(999, line.qty + 1);
      cart.set(item.id, line);
      showError("");
      refresh();
    },
  });

  // ---- Totals (all sent lines + new cart lines, merged like the server does) --
  function totals() {
    const merged = new Map();
    const add = (itemId, ratePaise, qty, gst) => {
      const key = itemId + ":" + ratePaise;
      const m = merged.get(key) || { ratePaise, qty: 0, gst };
      m.qty += qty;
      merged.set(key, m);
    };
    state.kots.forEach((k) => k.lines.forEach((l) => add(l.item_id, Math.round(l.rate * 100), l.quantity, l.gst)));
    cart.forEach((line, id) => { const item = menu.byId.get(id); if (item) add(id, item.ratePaise, line.qty, item.gst); });
    let subtotal = 0, gstSum = 0, count = 0;
    merged.forEach((m) => {
      const total = m.ratePaise * m.qty;
      subtotal += total;
      gstSum += roundHalfUp((total * m.gst) / 100);
      count += m.qty;
    });
    const half = roundHalfUp(gstSum / 2);
    const sel = $("service-mode");
    const pct = sel ? Number(sel.selectedOptions[0].dataset.percent || 0) : 0;
    const service = roundHalfUp((subtotal * pct) / 100 / 100) * 100;
    return { subtotal, half, gst: half * 2, service, count, grand: subtotal + half * 2 + service };
  }

  // ---- Rendering -----------------------------------------------------------
  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }

  function renderSent() {
    const box = $("sent-lines");
    box.replaceChildren();
    state.kots.forEach((k) => {
      const head = el("div", "kot-head");
      head.append(el("strong", "", "KOT " + k.no), el("span", "muted", k.time));
      head.append(el("span", "kot-tag " + (k.ready ? "ready" : k.printed ? "sent" : "saved"), k.ready ? "Ready" : k.printed ? "Sent" : "Saved"));
      box.appendChild(head);
      k.lines.forEach((l) => {
        const row = el("div", "line sent");
        const info = el("div");
        info.append(el("div", "line-name", l.name));
        const meta = el("div", "line-meta", "₹" + rupees(Math.round(l.rate * 100)) + (l.note ? " · " : ""));
        if (l.note) meta.appendChild(el("em", "line-note", l.note));
        info.appendChild(meta);
        const qty = el("div", "sent-qty", "× " + l.quantity);
        const right = el("div", "line-amt");
        right.append(document.createTextNode("₹" + rupees(Math.round(l.rate * 100) * l.quantity)));
        const del = el("button", "line-void", "×");
        del.type = "button";
        del.title = "Remove this item";
        del.dataset.void = l.id;
        del.dataset.name = l.name;
        right.appendChild(del);
        row.append(info, qty, right);
        box.appendChild(row);
      });
    });
  }

  function renderNew() {
    const box = $("new-lines");
    box.replaceChildren();
    if (!cart.size) return;
    box.appendChild(el("div", "kot-head new", "New items · not sent yet"));
    cart.forEach((line, id) => {
      const item = menu.byId.get(id);
      if (!item) return;
      const row = el("div", "line");
      row.dataset.id = id;
      const info = el("div");
      info.append(el("div", "line-name", item.name));
      const meta = el("div", "line-meta");
      const noteBtn = el("button", "line-remove", line.note ? "✎ " + line.note : "+ Note");
      noteBtn.type = "button";
      noteBtn.dataset.action = "note";
      meta.append(document.createTextNode("₹" + rupees(item.ratePaise) + " · "), noteBtn);
      info.appendChild(meta);
      const stepper = el("div", "stepper");
      stepper.innerHTML =
        '<button type="button" data-action="dec" aria-label="Decrease">−</button>' +
        '<input type="number" min="1" max="999" inputmode="numeric" aria-label="Quantity">' +
        '<button type="button" data-action="inc" aria-label="Increase">+</button>';
      stepper.querySelector("input").value = line.qty;
      const amt = el("div", "line-amt with-x", "₹" + rupees(item.ratePaise * line.qty));
      const rm = el("button", "line-void", "×");
      rm.type = "button";
      rm.dataset.action = "remove";
      rm.title = "Remove " + item.name;
      rm.setAttribute("aria-label", "Remove " + item.name);
      amt.appendChild(rm);
      row.append(info, stepper, amt);
      box.appendChild(row);
    });
  }

  function renderHeader() {
    const s = $("to-status");
    s.textContent = STATUS[state.status] || state.status;
    s.className = "to-status " + state.status;
    $("to-since").textContent = state.since_minutes != null ? state.since_minutes + " min" : "";
    $("to-billno").textContent = state.bill ? "Bill " + state.bill.bill_no : "";
    if (state.bill && !$("customer-name").value) $("customer-name").value = state.bill.customer_name || "";
    if (state.bill && !$("customer-phone").value) $("customer-phone").value = state.bill.customer_mb_no || "";
  }

  function renderTotals() {
    const t = totals();
    $("t-count").textContent = t.count + (t.count === 1 ? " item" : " items");
    $("t-subtotal").textContent = "₹" + rupees(t.subtotal);
    $("t-cgst").textContent = "₹" + rupees(t.half);
    $("t-sgst").textContent = "₹" + rupees(t.half);
    $("t-gst").textContent = "₹" + rupees(t.gst);
    if ($("t-service")) $("t-service").textContent = "₹" + rupees(t.service);
    $("t-grand").textContent = "₹" + rupees(t.grand);

    const hasSent = state.kots.length > 0;
    const unsentSaved = state.kots.some((k) => !k.printed);
    $("cart-empty").hidden = hasSent || cart.size > 0;
    $("act-save").disabled = busy || cart.size === 0;
    $("act-kot").disabled = busy || (cart.size === 0 && !unsentSaved);
    $("act-bill").disabled = busy || (!hasSent && cart.size === 0);
    $("act-bill").lastChild.textContent = state.status === "printed" || state.bill ? " Reprint Bill" : " Print Bill";
    $("act-bill").hidden = !canBill;
    $("act-settle").hidden = !canBill || !(state.status === "printed" && cart.size === 0);
    $("act-settle").disabled = busy || !payMode;
    $("to-move").disabled = busy || !hasSent;
    if ($("to-cancel")) $("to-cancel").disabled = busy || !hasSent;
  }

  function refresh() {
    menu.render();
    renderHeader();
    renderSent();
    renderNew();
    renderTotals();
  }

  // ---- Cart editing --------------------------------------------------------
  $("new-lines").addEventListener("click", (e) => {
    const action = e.target.closest("[data-action]");
    if (!action) return;
    const id = Number(action.closest(".line").dataset.id);
    const line = cart.get(id);
    if (!line) return;
    if (action.dataset.action === "inc") line.qty = Math.min(999, line.qty + 1);
    else if (action.dataset.action === "dec") line.qty -= 1;
    else if (action.dataset.action === "remove") line.qty = 0;
    else if (action.dataset.action === "note") {
      const note = window.prompt("Kitchen note for " + menu.byId.get(id).name + " (e.g. less spicy):", line.note || "");
      if (note === null) return;
      line.note = note.trim().slice(0, 120);
    }
    if (line.qty <= 0) cart.delete(id);
    refresh();
  });
  $("new-lines").addEventListener("change", (e) => {
    if (!e.target.matches(".stepper input")) return;
    const id = Number(e.target.closest(".line").dataset.id);
    const qty = Math.floor(Number(e.target.value) || 0);
    if (qty <= 0) cart.delete(id); else cart.get(id).qty = Math.min(999, qty);
    refresh();
  });

  payButtons.forEach((btn) =>
    btn.addEventListener("click", () => {
      payMode = Number(btn.dataset.pay);
      payButtons.forEach((b) => { b.classList.toggle("active", b === btn); b.setAttribute("aria-checked", b === btn ? "true" : "false"); });
      renderTotals();
    })
  );
  if ($("service-mode")) $("service-mode").addEventListener("change", renderTotals);

  // ---- Server calls --------------------------------------------------------
  function showError(msg) {
    $("cart-error").textContent = msg;
    $("cart-error").hidden = !msg;
  }

  async function call(path, payload, button) {
    if (busy) return null;
    busy = true;
    showError("");
    if (button) button.classList.add("is-busy");
    renderTotals();
    try {
      const res = await fetch(api + path, {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json", "X-CSRF-Token": KS.csrf },
        body: JSON.stringify(payload || {}),
        credentials: "same-origin",
      });
      const body = await res.json().catch(() => ({}));
      if (res.status === 401) {
        KS.toast("Your session has ended. Please sign in again.", "error");
        setTimeout(() => (location.href = "/login?next=" + encodeURIComponent(location.pathname)), 1200);
        return null;
      }
      if (!res.ok || !body.ok) {
        showError((body && body.error) || "Something went wrong (error " + res.status + "). Nothing was changed.");
        if (body && body.error_id) console.error("Server error reference:", body.error_id);
        return null;
      }
      if (body.state) state = body.state;
      return body;
    } catch (err) {
      console.error(err);
      showError("Could not reach the server. Check the connection and try again.");
      return null;
    } finally {
      busy = false;
      if (button) button.classList.remove("is-busy");
      refresh();
    }
  }

  const cartPayload = () => Array.from(cart, ([item_id, l]) => ({ item_id, quantity: l.qty, note: l.note }));

  const backToTables = () => { location.href = root.dataset.tablesUrl; };

  /** Saves new items (optionally printing a KOT). With goBack, returns to the Table View afterwards. */
  async function sendKot(print, button, goBack) {
    const body = await call("/kot", { items: cartPayload(), print }, button);
    if (!body) return false;
    cart.clear();
    refresh();
    KS.toast(print ? "KOT sent to the kitchen." : "Order saved.", "success");
    if (body.print_url) KS.printUrl(body.print_url, goBack ? backToTables : null);
    else if (goBack) setTimeout(backToTables, 600);
    return true;
  }

  function phoneOk() {
    const phone = $("customer-phone").value.replace(/[\s-]/g, "");
    if (phone && !/^\+?\d{10,14}$/.test(phone)) {
      showError("Enter a valid mobile number (10 digits) or leave it empty.");
      $("customer-phone").focus();
      return null;
    }
    return phone;
  }

  async function printBill() {
    const phone = phoneOk();
    if (phone === null) return;
    // New items go to the kitchen first, so the KOT and the bill match.
    if (cart.size && !(await sendKot(true, $("act-kot"), false))) return;
    const body = await call("/bill", {
      paid: false,
      service_mode_id: $("service-mode") ? $("service-mode").value || null : null,
      customer_name: $("customer-name").value.trim(),
      customer_phone: phone,
    }, $("act-bill"));
    if (!body) return;
    KS.toast("Bill " + body.bill.bill_no + " printed. Open the table and press Settle when the customer pays.", "success");
    KS.printUrl(body.print_url, backToTables);
  }

  async function settleBill() {
    if (!payMode) { showError("Choose how the customer paid."); return; }
    const body = await call("/settle", { payment_mode_id: payMode }, $("act-settle"));
    if (!body) return;
    KS.toast("Bill " + body.bill.bill_no + " settled · ₹" + rupees(Math.round(body.bill.total * 100)), "success");
    setTimeout(backToTables, 900);
  }

  $("act-save").addEventListener("click", () => sendKot(false, $("act-save"), true));
  $("act-kot").addEventListener("click", () => sendKot(true, $("act-kot"), true));
  $("act-bill").addEventListener("click", printBill);
  $("act-settle").addEventListener("click", settleBill);
  document.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key === "Enter" && !$("act-kot").disabled) { e.preventDefault(); sendKot(true, $("act-kot"), true); }
  });

  $("sent-lines").addEventListener("click", async (e) => {
    const btn = e.target.closest("[data-void]");
    if (!btn) return;
    if (!window.confirm("Remove " + btn.dataset.name + " from this table's order?" + (state.bill ? " The bill will need to be printed again." : ""))) return;
    if (await call("/void", { line_id: Number(btn.dataset.void) })) KS.toast("Item removed.", "success");
  });

  $("to-move").addEventListener("click", () => { $("move-box").hidden = !$("move-box").hidden; });
  $("move-go").addEventListener("click", async () => {
    const to = Number($("move-to").value);
    if (!to) { showError("Choose the table to move to."); return; }
    if (cart.size && !window.confirm("New items that are not saved yet will stay here. Move the saved order anyway?")) return;
    const body = await call("/move", { to_table_id: to }, $("move-go"));
    if (body) { KS.toast("Order moved to table " + body.moved_to_name + ".", "success"); cart.clear(); location.href = body.url; }
  });
  if ($("to-cancel")) {
    $("to-cancel").addEventListener("click", async () => {
      if (!window.confirm("Cancel the whole order on this table? Items sent to the kitchen and any unpaid bill will be removed.")) return;
      if (await call("/cancel", {}, $("to-cancel"))) { cart.clear(); KS.toast("Order cancelled.", "success"); setTimeout(() => (location.href = root.dataset.tablesUrl), 800); }
    });
  }

  window.addEventListener("beforeunload", (e) => {
    if (cart.size > 0) { e.preventDefault(); e.returnValue = ""; }
  });

  // Pick up changes made on another device while this screen is open.
  setInterval(async () => {
    if (busy || document.hidden) return;
    try {
      const res = await fetch(api, { headers: { Accept: "application/json" }, credentials: "same-origin" });
      const body = await res.json();
      if (body.ok) { state = body.state; refresh(); }
    } catch (err) { /* offline; the next action will report it */ }
  }, 20000);

  const clock = $("clock");
  const pad = (n) => String(n).padStart(2, "0");
  setInterval(() => {
    const d = new Date();
    clock.textContent = pad(d.getDate()) + "-" + pad(d.getMonth() + 1) + "-" + d.getFullYear() + " " + pad(d.getHours()) + ":" + pad(d.getMinutes());
  }, 1000);

  refresh();
})();

/* Shared menu panel for the billing screens (quick bill and table orders):
 * menu data, item grid, categories, search, and money helpers.
 *
 *   const menu = KS.PosMenu({ root, onAdd: (item) => ..., getQty: (id) => n });
 *   menu.render();          // redraw the grid (e.g. after quantities change)
 *   menu.byId.get(id)       // item {id, name, ratePaise, gst, veg, image, ...}
 */
(function () {
  "use strict";
  const KS = (window.KS = window.KS || {});

  KS.roundHalfUp = (n) => Math.sign(n) * Math.round(Math.abs(n) + 1e-9);
  KS.rupees = (paise) => {
    const v = (paise / 100).toFixed(2);
    const [whole, frac] = v.split(".");
    const last3 = whole.slice(-3);
    const rest = whole.slice(0, -3);
    return (rest ? rest.replace(/\B(?=(\d{2})+(?!\d))/g, ",") + "," : "") + last3 + "." + frac;
  };

  /** Prints a page (receipt or KOT) through a hidden iframe.
   *  onDone runs once the print dialog has closed (or after 60 s at the latest),
   *  so callers can navigate away without cancelling the print. */
  KS.printUrl = function (url, onDone) {
    const frame = document.createElement("iframe");
    frame.setAttribute("aria-hidden", "true");
    frame.style.cssText = "position:fixed;right:0;bottom:0;width:80mm;height:10px;border:0;opacity:0;pointer-events:none;";
    frame.src = url;
    let finished = false;
    const finish = () => {
      if (finished) return;
      finished = true;
      setTimeout(() => frame.remove(), 1000);
      if (onDone) setTimeout(onDone, 150);
    };
    frame.onload = () => {
      try {
        frame.contentWindow.addEventListener("afterprint", finish);
        frame.contentWindow.focus();
        frame.contentWindow.print();
        // print() returns once the dialog is closed (Chrome/Edge) or the job is handed to the
        // printer (silent/kiosk printing). The page content is already captured by then,
        // so it is safe to move on straight away.
        setTimeout(finish, 400);
      } catch (err) {
        console.error(err);
        window.open(url.replace("embed=1", "print=1"), "_blank");
        finish();
      }
    };
    frame.onerror = finish;
    // Safety net: if the print page never loads, do not leave the user stuck.
    setTimeout(finish, 15000);
    document.body.appendChild(frame);
  };

  KS.PosMenu = function (opts) {
    const root = opts.root;
    const $ = (id) => document.getElementById(id);
    const getQty = opts.getQty || (() => 0);

    let categories = [];
    try {
      categories = JSON.parse($("menu-data").textContent || "[]");
    } catch (err) {
      console.error(err);
      KS.toast("The menu could not be loaded. Please reload the page.", "error");
    }
    const allItems = [];
    const byId = new Map();
    categories.forEach((c) =>
      c.items.forEach((item) => {
        item.categoryId = c.id;
        item.ratePaise = Math.round(item.rate * 100);
        item.search = item.name.toLowerCase();
        allItems.push(item);
        byId.set(item.id, item);
      })
    );
    const state = { cat: "all", query: "" };

    function visibleItems() {
      const q = state.query.trim().toLowerCase();
      return allItems.filter((i) => (q ? i.search.includes(q) : state.cat === "all" || i.categoryId === state.cat));
    }

    function render() {
      const grid = $("item-grid");
      const list = visibleItems();
      const frag = document.createDocumentFragment();
      list.forEach((item) => {
        const qty = getQty(item.id);
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "item" + (qty ? " in-cart" : "") + (item.image ? "" : " no-img");
        btn.dataset.id = item.id;
        btn.title = item.description || item.name;
        const food = document.createElement("i");
        food.className = "food " + (item.veg ? "veg" : "nonveg");
        btn.appendChild(food);
        if (item.image) {
          const img = document.createElement("img");
          img.className = "item-img";
          img.src = item.image;
          img.alt = "";
          img.loading = "lazy";
          img.onerror = () => { img.remove(); btn.classList.add("no-img"); item.image = null; };
          btn.appendChild(img);
        }
        const body = document.createElement("div");
        body.className = "item-body";
        const name = document.createElement("span");
        name.className = "item-name";
        name.textContent = item.name;
        const price = document.createElement("span");
        price.className = "item-price";
        price.textContent = "₹" + KS.rupees(item.ratePaise);
        body.append(name, price);
        btn.appendChild(body);
        if (qty) {
          const badge = document.createElement("span");
          badge.className = "item-qty";
          badge.textContent = qty;
          btn.appendChild(badge);
        }
        frag.appendChild(btn);
      });
      grid.replaceChildren(frag);
      $("items-empty").hidden = list.length > 0 || allItems.length === 0;
      $("items-count").textContent = list.length + " item" + (list.length === 1 ? "" : "s");
      let title = "All Items";
      if (state.query.trim()) title = 'Results for "' + state.query.trim() + '"';
      else if (state.cat !== "all") title = (categories.find((c) => c.id === state.cat) || {}).name || title;
      $("items-title").textContent = title;
    }

    $("item-grid").addEventListener("click", (e) => {
      const btn = e.target.closest(".item");
      if (btn) opts.onAdd(byId.get(Number(btn.dataset.id)));
    });

    $("pos-cats").addEventListener("click", (e) => {
      const btn = e.target.closest(".cat");
      if (!btn) return;
      root.querySelectorAll(".cat").forEach((c) => c.classList.toggle("active", c === btn));
      state.cat = btn.dataset.cat === "all" ? "all" : Number(btn.dataset.cat);
      state.query = "";
      $("pos-search").value = "";
      render();
      $("item-grid").parentElement.scrollTop = 0;
    });

    const search = $("pos-search");
    search.addEventListener("input", () => { state.query = search.value; render(); });
    search.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        const first = visibleItems()[0];
        if (first) { opts.onAdd(first); search.select(); }
      } else if (e.key === "Escape") {
        search.value = ""; state.query = ""; render(); search.blur();
      }
    });
    document.addEventListener("keydown", (e) => {
      const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName);
      if (e.key === "/" && !typing) { e.preventDefault(); search.focus(); }
    });

    return { byId, render, focusSearch: () => search.focus() };
  };
})();

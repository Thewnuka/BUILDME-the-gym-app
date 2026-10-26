/* Gym Management System — shared UI behaviour */
(function () {
  "use strict";

  document.addEventListener("DOMContentLoaded", function () {
    initSidebar();
    initToasts();
    initConfirmations();
    initLoadingButtons();
    initAutoSubmit();
    initPasswordToggles();
    initToggleAll();
  });

  // Responsive sidebar (off-canvas below the lg breakpoint).
  function initSidebar() {
    var body = document.body;
    document.querySelectorAll("[data-sidebar-toggle]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var open = body.classList.toggle("sidebar-open");
        btn.setAttribute("aria-expanded", open ? "true" : "false");
      });
    });
    var backdrop = document.querySelector(".sidebar-backdrop");
    if (backdrop) {
      backdrop.addEventListener("click", function () { body.classList.remove("sidebar-open"); });
    }
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape") body.classList.remove("sidebar-open");
    });
  }

  // Django messages rendered as Bootstrap toasts.
  function initToasts() {
    if (!window.bootstrap) return;
    document.querySelectorAll(".toast").forEach(function (el) {
      var isError = el.classList.contains("toast-error");
      new bootstrap.Toast(el, { autohide: !isError, delay: 5000 }).show();
    });
  }

  // <button data-confirm="Are you sure?"> inside a form opens a confirmation modal.
  function initConfirmations() {
    var modalEl = document.getElementById("confirmModal");
    if (!modalEl || !window.bootstrap) return;
    var modal = new bootstrap.Modal(modalEl);
    var pendingForm = null;
    document.addEventListener("click", function (e) {
      var trigger = e.target.closest("[data-confirm]");
      if (!trigger) return;
      e.preventDefault();
      pendingForm = trigger.closest("form");
      modalEl.querySelector("[data-confirm-title]").textContent = trigger.dataset.confirmTitle || "Please confirm";
      modalEl.querySelector("[data-confirm-message]").textContent = trigger.dataset.confirm;
      var okBtn = modalEl.querySelector("[data-confirm-ok]");
      okBtn.textContent = trigger.dataset.confirmLabel || "Confirm";
      okBtn.className = "btn " + (trigger.dataset.confirmClass || "btn-primary");
      modal.show();
    });
    modalEl.querySelector("[data-confirm-ok]").addEventListener("click", function () {
      if (pendingForm) {
        pendingForm.dataset.confirmed = "1";
        modal.hide();
        if (pendingForm.requestSubmit) pendingForm.requestSubmit(); else pendingForm.submit();
      }
    });
  }

  // Disable the submit button and show a spinner while a form is submitting.
  function initLoadingButtons() {
    document.addEventListener("submit", function (e) {
      var form = e.target;
      if (form.dataset.noLoading !== undefined) return;
      var btn = e.submitter || form.querySelector("[type=submit]");
      if (!btn || btn.dataset.loadingApplied) return;
      btn.dataset.loadingApplied = "1";
      btn.dataset.originalHtml = btn.innerHTML;
      var width = btn.offsetWidth;
      btn.style.minWidth = width + "px";
      btn.classList.add("btn-loading");
      var label = btn.dataset.loadingText || "Please wait…";
      // Defer disabling so the button's name/value is still submitted.
      setTimeout(function () {
        btn.disabled = true;
        btn.innerHTML = '<span class="spinner-border me-2" role="status" aria-hidden="true"></span>' + label;
      }, 0);
    });
    // Restore buttons if the page is shown from the back/forward cache.
    window.addEventListener("pageshow", function (e) {
      if (!e.persisted) return;
      document.querySelectorAll(".btn-loading").forEach(function (btn) {
        btn.disabled = false;
        btn.innerHTML = btn.dataset.originalHtml;
        btn.classList.remove("btn-loading");
        delete btn.dataset.loadingApplied;
      });
    });
  }

  // <select data-auto-submit> submits its form on change (filters).
  // form.submit() fires no "submit" event, so note the scroll position here.
  function initAutoSubmit() {
    document.querySelectorAll("[data-auto-submit]").forEach(function (el) {
      el.addEventListener("change", function () {
        if (!el.form) return;
        keepScrollForForm(el.form);
        el.form.submit();
      });
    });
  }

  function storage() {
    try { return window.sessionStorage; } catch (e) { return null; }
  }

  // The sidebar scrolls on its own. Every page load used to rebuild it scrolled
  // to the top, hiding the item just clicked (e.g. Inventory, Gym Settings).
  // Keep its position across pages and make sure the current item is visible.
  function initSidebarScroll() {
    var nav = document.querySelector(".sidebar-nav");
    if (!nav) return;
    var store = storage();
    var KEY = "gym:sidebar-scroll";
    var saved = store ? parseInt(store.getItem(KEY) || "", 10) : NaN;
    if (!isNaN(saved)) nav.scrollTop = saved;
    var active = nav.querySelector(".sidebar-link.active");
    if (active) {
      var a = active.getBoundingClientRect(), n = nav.getBoundingClientRect();
      if (a.top < n.top || a.bottom > n.bottom) nav.scrollTop += a.top - n.top - (n.height - a.height) / 2;
    }
    window.addEventListener("pagehide", function () {
      try { if (store) store.setItem(KEY, String(nav.scrollTop)); } catch (e) { /* storage full or blocked */ }
    });
  }

  // Keep the reader's place when a control reloads the *same* page.
  // Filters, category chips, sort options and "show" toggles are ordinary GET
  // requests to the current URL; a fresh page load starts at the top, so the
  // page jumped. Just before such a request we note where the reader is and
  // restore it once on arrival. Links to other pages, back/forward and reloads
  // are left entirely to the browser.
  var SCROLL_KEY = "gym:keep-scroll";

  function samePage(url) {
    return url.origin === location.origin && url.pathname === location.pathname && !url.hash;
  }

  function saveScroll(state) {
    var store = storage();
    state.path = location.pathname;
    state.t = Date.now();
    try { if (store) store.setItem(SCROLL_KEY, JSON.stringify(state)); } catch (e) { /* ignore */ }
  }

  // Child-index path from <main> to ``el``, to find the same block on the next page.
  function domPath(el) {
    var main = document.getElementById("main-content"), path = [];
    while (el && el !== main && el.parentElement) {
      path.unshift(Array.prototype.indexOf.call(el.parentElement.children, el));
      el = el.parentElement;
    }
    return el === main ? path : null;
  }

  function resolvePath(path) {
    var el = document.getElementById("main-content");
    for (var i = 0; el && i < path.length; i++) el = el.children[path[i]];
    return el || null;
  }

  function keepScrollForForm(form) {
    var method = (form.getAttribute("method") || "get").toLowerCase();
    var action = new URL(form.getAttribute("action") || location.href, location.href);
    if ((method === "get" && samePage(action)) || form.hasAttribute("data-keep-scroll")) {
      saveScroll({ y: window.scrollY });
    }
  }

  function initScrollKeeper() {
    document.addEventListener("click", function (e) {
      if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
      var link = e.target.closest("a[href]");
      if (!link || link.target === "_blank" || link.hasAttribute("download")) return;
      if (link.closest(".app-sidebar, .app-topbar")) return;  // menu items are page navigation
      var url = new URL(link.href, location.href);
      if (!samePage(url)) return;
      var pager = link.closest(".pagination");
      if (pager) {
        // New page of results: show the top of the list, not the old position.
        var block = link.closest(".card");
        if (block && !block.querySelector("table, .list-group") && block.previousElementSibling) {
          block = block.previousElementSibling;  // pager in its own card below a grid of cards
        }
        saveScroll({ anchor: block ? domPath(block) : null });
      } else {
        saveScroll({ y: window.scrollY });
      }
    });
    document.addEventListener("submit", function (e) {
      if (!e.defaultPrevented) keepScrollForForm(e.target);
    });

    var store = storage();
    var raw = store ? store.getItem(SCROLL_KEY) : null;
    if (!raw) return;
    try { store.removeItem(SCROLL_KEY); } catch (e) { /* ignore */ }
    var state;
    try { state = JSON.parse(raw); } catch (e) { return; }
    var nav = window.performance && performance.getEntriesByType ? performance.getEntriesByType("navigation")[0] : null;
    if (!state || state.path !== location.pathname || Date.now() - state.t > 15000) return;
    if (nav && nav.type !== "navigate") return;  // back/forward and reload: the browser restores

    var userMoved = false;
    ["wheel", "touchmove", "keydown", "mousedown"].forEach(function (type) {
      window.addEventListener(type, function () { userMoved = true; }, { once: true, passive: true });
    });
    var restore = function () {
      var top = state.y;
      if (state.anchor) {
        var el = resolvePath(state.anchor);
        if (!el) return;
        var bar = document.querySelector(".app-topbar");
        top = el.getBoundingClientRect().top + window.scrollY - (bar ? bar.offsetHeight : 0) - 12;
      }
      if (typeof top !== "number" || top <= 0) return;
      try { window.scrollTo({ top: top, behavior: "instant" }); } catch (e) { window.scrollTo(0, top); }
    };
    restore();
    // Charts and images can change the page height while loading; settle once more.
    window.addEventListener("load", function () { if (!userMoved) restore(); }, { once: true });
  }

  // [data-reveal] blocks (membership plans) rise into place the first time they
  // come into view. Each plays once; reduced-motion users see them immediately.
  function initReveal() {
    var root = document.documentElement;
    root.classList.add("reveal-ready");
    var items = document.querySelectorAll("[data-reveal]");
    if (!items.length) return;
    var reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reduce || !("IntersectionObserver" in window)) {
      items.forEach(function (el) { el.classList.add("is-revealed"); });
      return;
    }
    var observer = new IntersectionObserver(function (entries) {
      var order = 0;
      entries.forEach(function (entry) {
        if (!entry.isIntersecting) return;
        entry.target.style.setProperty("--reveal-delay", (order++ * 80) + "ms");
        entry.target.classList.add("is-revealed");
        observer.unobserve(entry.target);
      });
    }, { rootMargin: "0px 0px -6% 0px", threshold: 0.12 });
    items.forEach(function (el) { observer.observe(el); });
  }

  // Show/hide password buttons.
  function initPasswordToggles() {
    document.querySelectorAll("[data-password-toggle]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var input = document.getElementById(btn.dataset.passwordToggle);
        if (!input) return;
        var show = input.type === "password";
        input.type = show ? "text" : "password";
        btn.innerHTML = show ? '<i class="bi bi-eye-slash"></i>' : '<i class="bi bi-eye"></i>';
        btn.setAttribute("aria-label", show ? "Hide password" : "Show password");
      });
    });
  }

  // <input data-toggle-column="role"> checks/unchecks every box in that column.
  function initToggleAll() {
    document.querySelectorAll("[data-toggle-column]").forEach(function (master) {
      var col = master.dataset.toggleColumn;
      var boxes = document.querySelectorAll('input[data-column="' + col + '"]:not(:disabled)');
      var sync = function () {
        var checked = Array.prototype.filter.call(boxes, function (b) { return b.checked; }).length;
        master.checked = checked === boxes.length;
        master.indeterminate = checked > 0 && checked < boxes.length;
      };
      master.addEventListener("change", function () {
        boxes.forEach(function (b) { b.checked = master.checked; });
        sync();
      });
      boxes.forEach(function (b) { b.addEventListener("change", sync); });
      sync();
    });
  }

  // Public helper for Chart.js widgets.
  window.GymCharts = {
    bar: function (canvasId, dataId, options) {
      var canvas = document.getElementById(canvasId);
      var dataEl = document.getElementById(dataId);
      if (!canvas || !dataEl || !window.Chart) return;
      var payload = JSON.parse(dataEl.textContent);
      var styles = getComputedStyle(document.documentElement);
      var primary = styles.getPropertyValue("--gym-primary").trim() || "#f2541b";
      new Chart(canvas, {
        type: "bar",
        data: {
          labels: payload.labels,
          datasets: [{
            label: (options && options.label) || "",
            data: payload.data,
            backgroundColor: primary,
            hoverBackgroundColor: "#d4430f",
            borderRadius: 8,
            maxBarThickness: 42
          }]
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: { legend: { display: false } },
          scales: {
            x: { grid: { display: false } },
            y: { beginAtZero: true, ticks: { precision: 0 }, grid: { color: "#eef0f4" } }
          }
        }
      });
      canvas.closest(".chart-box").classList.add("is-ready");
    },
    multi: function (canvasId, dataId, type) {
      var canvas = document.getElementById(canvasId);
      var dataEl = document.getElementById(dataId);
      if (!canvas || !dataEl || !window.Chart) return;
      var payload = JSON.parse(dataEl.textContent);
      var palette = ["#16a34a", "#dc2626", "#f2541b", "#2563eb", "#0e9f9a"];
      new Chart(canvas, {
        type: type || "bar",
        data: { labels: payload.labels, datasets: payload.datasets.map(function (d, i) {
          return { label: d.label, data: d.data, backgroundColor: palette[i % palette.length],
                   borderColor: palette[i % palette.length], borderRadius: 6, maxBarThickness: 36 };
        }) },
        options: { responsive: true, maintainAspectRatio: false,
                   scales: { x: { grid: { display: false } }, y: { beginAtZero: true, grid: { color: "#eef0f4" } } } }
      });
      canvas.closest(".chart-box").classList.add("is-ready");
    }
  };

  // These run as soon as the script loads (the page above it is already parsed),
  // so the sidebar and scroll position are settled before the page settles.
  // They stay at the end so every variable above is initialised first.
  initSidebarScroll();
  initScrollKeeper();
  initReveal();
})();

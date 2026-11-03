// Shared helpers for the Magazarr pages.
(() => {
  "use strict";

  const esc = (value) =>
    String(value ?? "").replace(/[&<>"']/g, (char) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;",
    })[char]);

  const icon = (name, cls = "icon") =>
    `<svg class="${cls}" aria-hidden="true"><use href="#i-${name}"/></svg>`;

  // Form posts answer with a redirect for no-JS use; with fetch we only need success or the error text.
  async function api(url, { method = "GET", body, json } = {}) {
    const init = { method, headers: { Accept: "application/json" }, redirect: "manual" };
    if (json !== undefined) {
      init.headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(json);
    } else if (body !== undefined) {
      init.body = body;
    }
    let res;
    try {
      res = await fetch(url, init);
    } catch {
      throw new Error("Magazarr is not reachable");
    }
    if (res.type === "opaqueredirect" || res.status === 204) return null;
    const type = res.headers.get("Content-Type") || "";
    const data = type.includes("application/json") ? await res.json().catch(() => null) : null;
    if (!res.ok) {
      throw new Error(data?.error || `${res.status} ${res.statusText}`.trim());
    }
    return data;
  }

  function formBody(form, submitter) {
    const data = new FormData(form, submitter);
    return new URLSearchParams(data);
  }

  const toastRoot = () => document.getElementById("toasts");

  function toast(message, type = "info", { timeout } = {}) {
    const root = toastRoot();
    if (!root) return;
    const node = document.createElement("div");
    node.className = `toast is-${type}`;
    node.setAttribute("role", type === "error" ? "alert" : "status");
    const iconName = type === "error" ? "alert" : type === "success" ? "check" : "activity";
    node.innerHTML = `${icon(iconName)}<span class="toast-text">${esc(message)}</span>
      <button type="button" aria-label="Dismiss">${icon("x")}</button>`;
    const remove = () => {
      node.classList.add("is-leaving");
      setTimeout(() => node.remove(), 200);
    };
    node.querySelector("button").addEventListener("click", remove);
    root.append(node);
    while (root.children.length > 3) root.firstElementChild.remove();
    setTimeout(remove, timeout ?? (type === "error" ? 8000 : 3500));
  }

  // Promise-based confirm so callers never stack listeners on the accept button.
  function confirmDialog({ title = "Are you sure?", message = "This cannot be undone.", confirmLabel = "Delete", danger = true } = {}) {
    const dialog = document.getElementById("confirm-modal");
    if (!dialog) return Promise.resolve(window.confirm(message));
    dialog.querySelector("#confirm-title").textContent = title;
    dialog.querySelector("#confirm-message").textContent = message;
    const accept = dialog.querySelector("#confirm-accept");
    const cancel = dialog.querySelector("#confirm-cancel");
    accept.textContent = confirmLabel;
    accept.className = `btn ${danger ? "btn-danger" : "btn-ink"}`;
    return new Promise((resolve) => {
      const finish = (value) => {
        accept.removeEventListener("click", onAccept);
        cancel.removeEventListener("click", onCancel);
        dialog.removeEventListener("close", onClose);
        if (dialog.open) dialog.close();
        resolve(value);
      };
      const onAccept = () => finish(true);
      const onCancel = () => finish(false);
      const onClose = () => finish(false);
      accept.addEventListener("click", onAccept);
      cancel.addEventListener("click", onCancel);
      dialog.addEventListener("close", onClose);
      dialog.showModal();
      cancel.focus();
    });
  }

  // Runs a task on an interval: never overlapping, paused while the tab is hidden,
  // backing off after failures, and triggerable on demand.
  function poller(task, interval, { maxInterval = 60000 } = {}) {
    let timer = null;
    let running = false;
    let queued = false;
    let failures = 0;
    let stopped = true;

    const schedule = () => {
      clearTimeout(timer);
      if (stopped || document.hidden) return;
      const delay = Math.min(maxInterval, interval * 2 ** failures);
      timer = setTimeout(run, delay);
    };

    async function run() {
      if (stopped) return;
      if (running) {
        queued = true;
        return;
      }
      running = true;
      clearTimeout(timer);
      try {
        await task();
        failures = 0;
      } catch (error) {
        failures = Math.min(failures + 1, 5);
        console.warn(error);
      } finally {
        running = false;
      }
      if (queued) {
        queued = false;
        run();
        return;
      }
      schedule();
    }

    document.addEventListener("visibilitychange", () => {
      if (stopped) return;
      if (document.hidden) clearTimeout(timer);
      else run();
    });

    return {
      start() {
        if (!stopped) return;
        stopped = false;
        run();
      },
      stop() {
        stopped = true;
        clearTimeout(timer);
      },
      now: run,
      get active() {
        return !stopped;
      },
    };
  }

  // SQLite CURRENT_TIMESTAMP is UTC without a zone marker.
  function parseDate(value) {
    if (!value) return null;
    const text = String(value).trim();
    const date = new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(text) ? text : `${text.replace(" ", "T")}Z`);
    return Number.isNaN(date.getTime()) ? null : date;
  }

  const relativeFormat = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });
  function ago(value) {
    const date = parseDate(value);
    if (!date) return "";
    const seconds = (date.getTime() - Date.now()) / 1000;
    const units = [
      ["year", 31536000],
      ["month", 2592000],
      ["week", 604800],
      ["day", 86400],
      ["hour", 3600],
      ["minute", 60],
    ];
    for (const [unit, size] of units) {
      if (Math.abs(seconds) >= size) return relativeFormat.format(Math.round(seconds / size), unit);
    }
    return "just now";
  }

  function shortDate(value) {
    const date = parseDate(value);
    return date ? date.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" }) : "";
  }

  function size(bytes) {
    const value = Number(bytes || 0);
    if (!value) return "";
    if (value >= 1073741824) return `${(value / 1073741824).toFixed(1)} GB`;
    return `${Math.max(0.1, value / 1048576).toFixed(value >= 104857600 ? 0 : 1)} MB`;
  }

  // Issue keys are stored as ISO dates or "2026-issue-0004"; show them the way a reader would.
  function issueLabel(key, fallback = "") {
    const text = String(key || "");
    let match = text.match(/^(\d{4})-(\d{2})-(\d{2})$/);
    if (match) {
      return new Date(Date.UTC(match[1], match[2] - 1, match[3]))
        .toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });
    }
    match = text.match(/^(\d{4})-(\d{2})$/);
    if (match) {
      return new Date(Date.UTC(match[1], match[2] - 1, 1))
        .toLocaleDateString(undefined, { month: "long", year: "numeric", timeZone: "UTC" });
    }
    match = text.match(/^(\d{4})-issue-0*(\d+)/);
    if (match) return `No. ${match[2]} · ${match[1]}`;
    return text || fallback;
  }

  const plural = (count, one, many = `${one}s`) => `${count} ${count === 1 ? one : many}`;

  function coverBlank(title, note = "No issues yet") {
    return `<div class="cover-blank"><span class="cover-blank-title">${esc(title)}</span>
      <span class="cover-blank-rule"></span><span class="cover-blank-note">${esc(note)}</span></div>`;
  }

  // Covers are rendered on demand server-side, so a grid must not open one request per tile at once.
  // Images start once near the viewport, at most COVER_CONCURRENCY at a time, nearest first,
  // with one delayed retry. A final failure dispatches "coverfail" on the <img>.
  const COVER_CONCURRENCY = 4;
  const coverQueue = [];
  let coversInFlight = 0;

  function pumpCovers() {
    while (coversInFlight < COVER_CONCURRENCY && coverQueue.length) {
      const img = coverQueue.shift();
      if (!img.isConnected) continue;
      coversInFlight += 1;
      const done = (ok) => {
        coversInFlight -= 1;
        img.removeEventListener("load", onLoad);
        img.removeEventListener("error", onError);
        if (ok) {
          img.classList.add("is-loaded");
        } else if (!img.dataset.retried) {
          img.dataset.retried = "1";
          setTimeout(() => {
            if (!img.isConnected) return;
            img.removeAttribute("src");
            coverQueue.push(img);
            pumpCovers();
          }, 1500);
        } else {
          img.classList.add("is-loaded");
          img.dispatchEvent(new Event("coverfail"));
        }
        pumpCovers();
      };
      const onLoad = () => done(true);
      const onError = () => done(false);
      img.addEventListener("load", onLoad);
      img.addEventListener("error", onError);
      img.src = img.dataset.src;
    }
  }

  const coverObserver = "IntersectionObserver" in window
    ? new IntersectionObserver((entries) => {
      const visible = entries.filter((entry) => entry.isIntersecting);
      visible.sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top);
      for (const entry of visible) {
        coverObserver.unobserve(entry.target);
        coverQueue.push(entry.target);
      }
      pumpCovers();
    }, { rootMargin: "600px 0px" })
    : null;

  function coverImage(url, alt = "") {
    const img = document.createElement("img");
    img.alt = alt;
    img.decoding = "async";
    img.dataset.src = url;
    // Observe after insertion: the caller appends the image right after this returns.
    queueMicrotask(() => {
      if (coverObserver) coverObserver.observe(img);
      else {
        coverQueue.push(img);
        pumpCovers();
      }
    });
    return img;
  }

  function downloadFile(url) {
    const link = document.createElement("a");
    link.href = url;
    link.download = "";
    document.body.append(link);
    link.click();
    link.remove();
  }

  function debounce(fn, wait = 250) {
    let timer;
    return (...args) => {
      clearTimeout(timer);
      timer = setTimeout(() => fn(...args), wait);
    };
  }

  function setBusy(button, busy) {
    if (!button) return;
    button.disabled = busy;
    button.setAttribute("aria-busy", busy ? "true" : "false");
  }

  function closeDialog(dialog) {
    if (!dialog?.open) return;
    if (!dialog.classList.contains("sheet") || matchMedia("(prefers-reduced-motion: reduce)").matches) {
      dialog.close();
      return;
    }
    dialog.classList.add("is-closing");
    setTimeout(() => {
      dialog.classList.remove("is-closing");
      dialog.close();
    }, 180);
  }

  // Dialog chrome: close buttons, and clicking the backdrop closes.
  document.addEventListener("click", (event) => {
    const closer = event.target.closest("[data-close]");
    if (closer) {
      closeDialog(closer.closest("dialog"));
      return;
    }
    if (event.target instanceof HTMLDialogElement && event.target.open && !event.target.hasAttribute("data-persist")) {
      const rect = event.target.getBoundingClientRect();
      const inside =
        event.clientX >= rect.left && event.clientX <= rect.right &&
        event.clientY >= rect.top && event.clientY <= rect.bottom;
      if (!inside) closeDialog(event.target);
    }
  });

  // Menus: one open at a time, close on outside click or Escape.
  function closeMenus(except) {
    document.querySelectorAll(".menu:not([hidden])").forEach((menu) => {
      if (menu === except) return;
      menu.hidden = true;
      menu.parentElement.querySelector("[aria-expanded]")?.setAttribute("aria-expanded", "false");
    });
  }

  function toggleMenu(trigger) {
    const menu = trigger.parentElement.querySelector(".menu");
    if (!menu) return;
    const opening = menu.hidden;
    closeMenus(menu);
    menu.hidden = !opening;
    trigger.setAttribute("aria-expanded", String(opening));
    if (opening) {
      const room = window.innerHeight - trigger.getBoundingClientRect().bottom;
      menu.classList.toggle("opens-up", room < 220);
      menu.querySelector("button")?.focus({ preventScroll: true });
    }
  }

  document.addEventListener("click", (event) => {
    if (!event.target.closest(".menu-wrap")) closeMenus();
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeMenus();
    const typing = event.target.closest?.("input, textarea, select, [contenteditable]");
    if (event.key === "/" && !typing && !document.querySelector("dialog[open]")) {
      const field = document.querySelector("[aria-keyshortcuts='/']");
      if (field) {
        event.preventDefault();
        field.focus();
      }
    }
  });

  document.addEventListener("click", async (event) => {
    if (!event.target.closest("[data-copy-opds]")) return;
    const url = `${location.origin}/opds`;
    try {
      await navigator.clipboard.writeText(url);
      toast("OPDS feed URL copied", "success");
    } catch {
      toast(`OPDS feed: ${url}`, "info", { timeout: 10000 });
    }
  });

  const store = {
    get(key, fallback) {
      try {
        return localStorage.getItem(`magazarr.${key}`) ?? fallback;
      } catch {
        return fallback;
      }
    },
    set(key, value) {
      try {
        localStorage.setItem(`magazarr.${key}`, value);
      } catch {
        // Preference only.
      }
    },
  };

  window.Mz = {
    esc, icon, api, formBody, toast, confirmDialog, poller, parseDate, ago, shortDate,
    size, plural, issueLabel, coverBlank, coverImage, downloadFile, debounce, setBusy, closeDialog, toggleMenu, closeMenus, store,
  };
})();

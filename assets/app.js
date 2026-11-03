// Magazines dashboard.
(() => {
  "use strict";

  const { esc, icon, api, formBody, toast, confirmDialog, poller, ago, shortDate, size, plural, issueLabel,
    coverBlank, coverImage, debounce, setBusy, closeDialog, toggleMenu, closeMenus, store, downloadFile } = window.Mz;

  const $ = (selector, root = document) => root.querySelector(selector);

  const grid = $("#mag-grid");
  const state = {
    magazines: new Map(),
    timedOut: false,
    filter: store.get("filter", "all"),
    sort: store.get("sort", "title"),
    view: store.get("view", "rack"),
    text: "",
    loaded: false,
  };

  const byId = (id) => state.magazines.get(Number(id));

  // ---------------------------------------------------------------- dashboard

  function applyDashboard(data) {
    const seen = new Set();
    for (const mag of data.magazines || []) {
      seen.add(mag.id);
      state.magazines.set(mag.id, mag);
    }
    for (const id of [...state.magazines.keys()]) {
      if (!seen.has(id)) state.magazines.delete(id);
    }
    state.timedOut = Boolean(data.quasarr_timeout);
    render();
    if (sheet.id) renderSheetHead();
  }

  async function refreshDashboard() {
    applyDashboard(await api("/api/dashboard"));
    state.loaded = true;
  }

  const dashboardPoll = poller(refreshDashboard, 5000);

  const needsAttention = (mag) => mag.errors > 0;

  function matchesFilter(mag) {
    if (state.text && !mag.title.toLocaleLowerCase().includes(state.text)) return false;
    if (state.filter === "active") return mag.active;
    if (state.filter === "paused") return !mag.active;
    if (state.filter === "attention") return needsAttention(mag);
    return true;
  }

  const collator = new Intl.Collator(undefined, { sensitivity: "base", numeric: true });
  function compare(a, b) {
    const byTitle = collator.compare(a.title, b.title);
    if (state.sort === "issues") return b.issue_count - a.issue_count || byTitle;
    if (state.sort === "recent") return String(b.last_issue_at || "").localeCompare(String(a.last_issue_at || "")) || byTitle;
    if (state.sort === "attention") return b.errors - a.errors || b.downloading - a.downloading || byTitle;
    return byTitle;
  }

  function render() {
    const all = [...state.magazines.values()];
    renderDek(all);
    renderFilterCounts(all);
    $("#quasarr-status").hidden = !state.timedOut;
    const downloading = all.reduce((sum, mag) => sum + mag.downloading, 0);
    const badge = $("#downloads-badge");
    badge.hidden = downloading === 0;
    badge.textContent = downloading;

    const nodes = new Map([...grid.children].map((node) => [Number(node.dataset.id), node]));
    for (const [id, node] of nodes) {
      if (!state.magazines.has(id) && !node.classList.contains("is-removing")) node.remove();
    }
    const ordered = all.sort(compare);
    let visible = 0;
    ordered.forEach((mag, index) => {
      let node = nodes.get(mag.id);
      if (!node) {
        node = createCard(mag);
        if (state.loaded) node.classList.add("is-entering");
      }
      patchCard(node, mag);
      node.hidden = !matchesFilter(mag);
      if (!node.hidden) visible += 1;
      // Only move nodes that are out of place so focus and hover survive refreshes.
      if (grid.children[index] !== node) grid.insertBefore(node, grid.children[index] || null);
    });
    $("#mag-empty").hidden = all.length > 0;
    $("#mag-no-match").hidden = all.length === 0 || visible > 0;
  }

  function renderDek(all) {
    const issues = all.reduce((sum, mag) => sum + mag.issue_count, 0);
    const downloading = all.reduce((sum, mag) => sum + mag.downloading, 0);
    const attention = all.filter(needsAttention).length;
    const paused = all.filter((mag) => !mag.active).length;
    const parts = [`${plural(all.length, "title")}, ${plural(issues, "issue")} on file`];
    if (downloading) parts.push(`<button type="button" class="is-live" data-open-downloads>${downloading} downloading</button>`);
    if (attention) parts.push(`<button type="button" class="is-danger" data-set-filter="attention">${attention} ${attention === 1 ? "needs" : "need"} attention</button>`);
    if (paused) parts.push(`<button type="button" data-set-filter="paused">${paused} paused</button>`);
    const html = parts.join(" · ");
    const dek = $("#dek");
    if (dek.dataset.html !== html) {
      dek.innerHTML = html;
      dek.dataset.html = html;
    }
  }

  function renderFilterCounts(all) {
    const counts = {
      all: all.length,
      active: all.filter((mag) => mag.active).length,
      paused: all.filter((mag) => !mag.active).length,
      attention: all.filter(needsAttention).length,
    };
    for (const [key, value] of Object.entries(counts)) {
      const node = $(`[data-filter-count="${key}"]`);
      if (node) node.textContent = key === "attention" && !value ? "" : value;
    }
    document.querySelectorAll("[data-filter]").forEach((button) => {
      button.setAttribute("aria-pressed", String(button.dataset.filter === state.filter));
    });
  }

  // ---------------------------------------------------------------- cards

  function createCard(mag) {
    const node = document.createElement("article");
    node.className = "mag";
    node.dataset.id = mag.id;
    node.innerHTML = `
      <button type="button" class="mag-cover" data-open="downloaded"><span class="cover-art"></span><span class="cover-flags"></span></button>
      <div class="mag-caption">
        <h3 class="mag-title"><button type="button" data-open="downloaded"></button></h3>
        <div class="mag-tools">
          <button type="button" class="btn btn-quiet btn-icon btn-sm" data-act="search" aria-label="Search now">${icon("search")}</button>
          <div class="menu-wrap">
            <button type="button" class="btn btn-quiet btn-icon btn-sm" data-act="menu" aria-haspopup="menu" aria-expanded="false" aria-label="More">${icon("more")}</button>
            <div class="menu" role="menu" hidden>
              <button type="button" role="menuitem" data-open="downloaded">${icon("library")}Browse issues</button>
              <button type="button" role="menuitem" data-open="blocklist">${icon("ban")}Edit blocklist</button>
              <button type="button" role="menuitem" data-act="toggle"><span class="toggle-icon"></span><span class="toggle-label"></span></button>
              <hr>
              <button type="button" role="menuitem" class="is-danger" data-act="delete">${icon("trash")}Delete magazine…</button>
            </div>
          </div>
        </div>
        <p class="mag-line"></p>
        <div class="mag-signals"></div>
      </div>
      <div class="mag-list-only is-switch">
        <label class="switch" title="Automatic search"><input type="checkbox" data-act="active"><span class="switch-track"></span></label>
      </div>`;
    return node;
  }

  function setOnce(node, key, value, apply) {
    if (node.dataset[key] === value) return;
    node.dataset[key] = value;
    apply(value);
  }

  function patchCard(node, mag) {
    node.classList.toggle("is-paused", !mag.active);
    const label = `${mag.title}: ${plural(mag.issue_count, "issue")}`;
    const cover = $(".mag-cover", node);
    cover.setAttribute("aria-label", label);

    setOnce(node, "cover", `${mag.cover_url}|${mag.title}`, () => {
      const art = $(".cover-art", node);
      art.replaceChildren();
      if (mag.cover_url) {
        const img = coverImage(mag.cover_url, "");
        img.addEventListener("coverfail", () => (art.innerHTML = coverBlank(mag.title, "Cover unavailable")), { once: true });
        art.append(img);
      } else {
        art.innerHTML = coverBlank(mag.title, mag.active ? "Waiting for the first issue" : "Paused");
      }
    });

    const flags = [];
    if (!mag.active) flags.push('<span class="flag">Paused</span>');
    if (mag.downloading) flags.push(`<span class="flag is-live">${mag.downloading} incoming</span>`);
    if (mag.errors) flags.push(`<span class="flag is-danger">${plural(mag.errors, "error")}</span>`);
    setOnce(node, "flags", flags.join(""), (html) => ($(".cover-flags", node).innerHTML = html));

    setOnce(node, "title", mag.title, (title) => ($(".mag-title button", node).textContent = title));

    const line = mag.issue_count
      ? `${plural(mag.issue_count, "issue")} · latest ${ago(mag.last_issue_at)}`
      : mag.last_search_at
        ? `No issues yet · searched ${ago(mag.last_search_at)}`
        : "No issues yet · not searched";
    setOnce(node, "line", line, (text) => ($(".mag-line", node).textContent = text));

    const signals = [];
    if (mag.downloading) signals.push(`<button type="button" class="signal is-live" data-open="downloading">${mag.downloading} downloading</button>`);
    if (mag.errors) signals.push(`<button type="button" class="signal is-danger" data-open="errors">${plural(mag.errors, "error")}</button>`);
    if (mag.skipped) signals.push(`<button type="button" class="signal is-muted" data-open="skips">${mag.skipped} skipped</button>`);
    setOnce(node, "signals", signals.join(""), (html) => ($(".mag-signals", node).innerHTML = html));

    setOnce(node, "active", String(mag.active), () => {
      $(".toggle-icon", node).innerHTML = icon(mag.active ? "pause" : "play");
      $(".toggle-label", node).textContent = mag.active ? "Pause automatic search" : "Resume automatic search";
    });
    const toggle = $("input[data-act='active']", node);
    if (!toggle.disabled) toggle.checked = mag.active;

    const searching = isSearching(mag.id);
    setOnce(node, "searching", String(searching), () => {
      const button = $(".mag-tools [data-act='search']", node);
      button.innerHTML = searching ? '<span class="spinner"></span>' : icon("search");
      button.disabled = searching;
      button.setAttribute("aria-label", searching ? "Searching…" : "Search now");
    });
  }

  grid.addEventListener("click", (event) => {
    const card = event.target.closest(".mag");
    if (!card) return;
    const mag = byId(card.dataset.id);
    if (!mag) return;
    const opener = event.target.closest("[data-open]");
    if (opener) {
      closeMenus();
      openMagazine(mag.id, opener.dataset.open);
      return;
    }
    const action = event.target.closest("[data-act]")?.dataset.act;
    if (action === "menu") toggleMenu(event.target.closest("[data-act]"));
    if (action === "search") searchMagazine(mag);
    if (action === "toggle") {
      closeMenus();
      setActive(mag, !mag.active);
    }
    if (action === "delete") {
      closeMenus();
      deleteMagazine(mag);
    }
  });

  grid.addEventListener("change", (event) => {
    if (event.target.matches("input[data-act='active']")) {
      const mag = byId(event.target.closest(".mag").dataset.id);
      if (mag) setActive(mag, event.target.checked, event.target);
    }
  });

  // ---------------------------------------------------------------- actions

  async function post(url, fields = {}) {
    const body = new URLSearchParams();
    for (const [key, value] of Object.entries(fields)) body.append(key, value);
    return api(url, { method: "POST", body });
  }

  async function setActive(mag, active, input) {
    const previous = mag.active;
    mag.active = active;
    if (input) input.disabled = true;
    render();
    if (sheet.id === mag.id) renderSheetHead();
    try {
      await post(`/magazines/${mag.id}/active`, { active: active ? "on" : "" });
      toast(active ? `Automatic search resumed for ${mag.title}` : `${mag.title} paused`, "success");
    } catch (error) {
      mag.active = previous;
      toast(error.message, "error");
    } finally {
      if (input) input.disabled = false;
      render();
      if (sheet.id === mag.id) renderSheetHead();
    }
  }

  async function deleteMagazine(mag) {
    const files = mag.issue_count ? ` and its ${plural(mag.issue_count, "imported PDF")} from disk` : "";
    const ok = await confirmDialog({
      title: `Delete ${mag.title}?`,
      message: `This removes the magazine${files}, along with its download history and blocklist.`,
      confirmLabel: "Delete magazine",
    });
    if (!ok) return;
    try {
      await post(`/magazines/${mag.id}/delete`);
      if (sheet.id === mag.id) closeDialog(sheetDialog);
      const node = grid.querySelector(`[data-id="${mag.id}"]`);
      node?.classList.add("is-removing");
      setTimeout(() => node?.remove(), 220);
      state.magazines.delete(mag.id);
      toast(`Deleted ${mag.title}`, "success");
      dashboardPoll.now();
    } catch (error) {
      toast(error.message, "error");
    }
  }

  const addForm = $("[data-action='add-magazine']");
  addForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const input = addForm.elements.title;
    const title = input.value.trim().replace(/\s+/g, " ");
    if (!title) {
      input.focus();
      return;
    }
    const existing = [...state.magazines.values()].find((mag) => mag.title.toLocaleLowerCase() === title.toLocaleLowerCase());
    if (existing) {
      toast(`Already tracking ${existing.title}`);
      highlight(existing.id);
      return;
    }
    const button = addForm.querySelector("button");
    setBusy(button, true);
    try {
      await post("/magazines", { title });
      input.value = "";
      await refreshDashboard();
      const added = [...state.magazines.values()].find((mag) => mag.title.toLocaleLowerCase() === title.toLocaleLowerCase());
      toast(`Added ${title}. Run a search to look for issues.`, "success");
      if (added) highlight(added.id);
    } catch (error) {
      toast(error.message, "error");
    } finally {
      setBusy(button, false);
      input.focus();
    }
  });

  function highlight(id) {
    if (state.filter !== "all" || state.text) {
      state.filter = "all";
      state.text = "";
      $("#mag-filter").value = "";
      render();
    }
    const node = grid.querySelector(`[data-id="${id}"]`);
    if (!node) return;
    node.scrollIntoView({ behavior: "smooth", block: "center" });
    node.classList.remove("is-entering");
    void node.offsetWidth;
    node.classList.add("is-entering");
  }

  // ---------------------------------------------------------------- toolbar

  const filterInput = $("#mag-filter");
  filterInput.addEventListener("input", () => {
    state.text = filterInput.value.trim().toLocaleLowerCase();
    render();
  });

  document.addEventListener("click", (event) => {
    const filter = event.target.closest("[data-filter], [data-set-filter]");
    if (!filter) return;
    state.filter = filter.dataset.filter || filter.dataset.setFilter;
    store.set("filter", state.filter);
    render();
  });

  const sortSelect = $("#mag-sort");
  sortSelect.value = state.sort;
  sortSelect.addEventListener("change", () => {
    state.sort = sortSelect.value;
    store.set("sort", state.sort);
    render();
  });

  function applyView() {
    grid.classList.toggle("is-list", state.view === "list");
    document.querySelectorAll("[data-view]").forEach((button) => {
      button.setAttribute("aria-pressed", String(button.dataset.view === state.view));
    });
  }
  document.querySelectorAll("[data-view]").forEach((button) => {
    button.addEventListener("click", () => {
      state.view = button.dataset.view;
      store.set("view", state.view);
      applyView();
    });
  });

  // ---------------------------------------------------------------- search jobs

  const jobPanel = $("#job-panel");
  const jobTitle = $("#job-title");
  const jobStatus = $("#job-status");
  const jobQueue = $("#job-queue");
  const jobResults = $("#job-results");
  // Searches started from this page, oldest first. The server runs them one at a time in this order.
  let jobs = [];
  let shownJob = null;

  const eventLabels = {
    searching: ["Searching", "is-muted"],
    page: ["Page", "is-muted"],
    results: ["Results", "is-muted"],
    candidate: ["Match", "is-warn"],
    skipped: ["Skipped", "is-muted"],
    sending: ["Sending", "is-live"],
    sent: ["Sent", "is-live"],
    done: ["Done", "is-live"],
    error: ["Error", "is-danger"],
  };
  const jobLabels = {
    queued: ["Queued", "is-muted"],
    running: ["Running", "is-live"],
    done: ["Done", "is-live"],
    error: ["Failed", "is-danger"],
  };

  const finished = (job) => job.status === "done" || job.status === "error";
  const isSearching = (id) => jobs.some((job) => !finished(job) && job.ids.includes(id));

  function jobEventRow(item) {
    const [label, tone] = eventLabels[item.event] || [item.event, "is-muted"];
    const row = document.createElement("div");
    row.className = "job-event";
    row.innerHTML = `<span class="label ${tone}">${esc(label)}</span><span class="job-event-text">${esc(item.message)}</span>`;
    return row;
  }

  function renderJobs() {
    // Parallel requests can reach the server out of click order; its job ids give the run order.
    const list = [...jobs].sort((a, b) => (a.id ?? Number.MAX_SAFE_INTEGER) - (b.id ?? Number.MAX_SAFE_INTEGER));
    const running = list.find((job) => job.status === "running");
    const waiting = list.filter((job) => job.status === "queued");
    const done = list.every(finished);
    // Follow the running search; once everything is finished keep showing the last one.
    const shown = running || (done ? list.at(-1) : waiting[0]);
    const failed = list.some((job) => job.status === "error");

    jobPanel.classList.toggle("is-done", done);
    jobPanel.classList.toggle("is-failed", done && failed);
    if (done && jobs.length > 1) {
      const sent = jobs.reduce((sum, job) => sum + job.sent, 0);
      jobTitle.textContent = `${plural(jobs.length, "search", "searches")} finished`;
      jobStatus.textContent = [sent ? `${plural(sent, "release")} sent to Quasarr` : "No new releases", failed ? "some failed" : ""].filter(Boolean).join(" · ");
    } else {
      jobTitle.textContent = shown.title;
      const status = shown.message || (shown.status === "running" ? `Running · ${plural(shown.sent, "release")} sent` : "Queued");
      jobStatus.textContent = [status, waiting.length && !done ? `${waiting.length} queued` : ""].filter(Boolean).join(" · ");
    }

    const rows = list.length > 1 ? list.map((job) => {
      const [label, tone] = jobLabels[job.status];
      const note = job.message || (job.status === "running" ? plural(job.sent, "release") + " sent" : "");
      return `<div class="job-event${job === shown ? " is-current" : ""}"><span class="label ${tone}">${label}</span><span class="job-event-text">${esc(job.title)}${note ? ` <span class="muted">· ${esc(note)}</span>` : ""}</span></div>`;
    }).join("") : "";
    jobQueue.hidden = !rows;
    if (jobQueue.dataset.html !== rows) {
      jobQueue.innerHTML = rows;
      jobQueue.dataset.html = rows;
    }

    if (shownJob !== shown) {
      shownJob = shown;
      jobResults.replaceChildren(...shown.events.map(jobEventRow));
      jobResults.scrollTop = jobResults.scrollHeight;
    }
  }

  function appendJobEvents(job, items) {
    job.events.push(...items);
    if (job.events.length > 300) job.events.splice(0, job.events.length - 300);
    if (job !== shownJob || !items.length) return;
    const pinned = jobResults.scrollHeight - jobResults.scrollTop - jobResults.clientHeight < 40;
    jobResults.append(...items.map(jobEventRow));
    while (jobResults.children.length > 300) jobResults.firstElementChild.remove();
    if (pinned) jobResults.scrollTop = jobResults.scrollHeight;
  }

  function renderSearching() {
    render();
    if (sheet.id) renderSheetHead();
  }

  async function runJob(url, title, magazineIds = []) {
    if (jobs.some((job) => !finished(job) && job.url === url)) {
      toast(`${title} is already queued`);
      return;
    }
    // A new batch starts once everything from the previous one has finished.
    if (jobs.every(finished)) {
      jobs = [];
      jobPanel.classList.remove("is-collapsed");
      $("#job-toggle").setAttribute("aria-expanded", "true");
    }
    const job = { url, title, ids: magazineIds, status: "queued", sent: 0, message: "", events: [] };
    jobs.push(job);
    jobPanel.hidden = false;
    renderJobs();
    renderSearching();
    try {
      const data = await api(url, { method: "POST" });
      if (!data?.job_id) {
        finishJob(job, "done", data?.downloads ? `${plural(data.downloads, "release")} sent` : "No new releases");
        return;
      }
      job.id = Number(data.job_id);
      let seen = 0;
      while (true) {
        const status = await api(`/api/jobs/${data.job_id}`);
        const events = status.events || [];
        // The server keeps only the last 300 events; event_count says how many happened in total.
        const total = status.event_count ?? events.length;
        const fresh = events.slice(Math.max(0, events.length - (total - seen)));
        seen = total;
        job.sent += fresh.filter((item) => item.event === "sent").length;
        job.status = status.status === "running" ? "running" : job.status;
        appendJobEvents(job, fresh);
        if (status.status === "done" || status.status === "error") {
          finishJob(job, status.status, status.status === "error" ? "Search failed" : job.sent ? `${plural(job.sent, "release")} sent to Quasarr` : "No new releases");
          return;
        }
        renderJobs();
        await new Promise((resolve) => setTimeout(resolve, 1000));
      }
    } catch (error) {
      finishJob(job, "error", error.message);
    } finally {
      renderSearching();
      dashboardPoll.now();
      if (sheet.id && magazineIds.includes(sheet.id)) loadTab();
    }
  }

  function finishJob(job, status, message) {
    job.status = status;
    job.message = message;
    renderJobs();
    if (jobPanel.hidden || jobPanel.classList.contains("is-collapsed")) {
      toast(`${job.title}: ${message}`, status === "error" ? "error" : "success");
    }
  }

  function searchMagazine(mag) {
    if (isSearching(mag.id)) {
      toast(`${mag.title} is already queued`);
      return;
    }
    runJob(`/api/magazines/${mag.id}/search`, `Searching ${mag.title}`, [mag.id]);
  }

  $("[data-action='job']").addEventListener("submit", (event) => {
    event.preventDefault();
    const ids = [...state.magazines.values()].filter((mag) => mag.active).map((mag) => mag.id);
    runJob("/api/magazines/search-all", "Searching all magazines", ids);
  });

  $("#job-close").addEventListener("click", () => {
    jobPanel.hidden = true;
  });
  $("#job-toggle").addEventListener("click", (event) => {
    const collapsed = jobPanel.classList.toggle("is-collapsed");
    event.currentTarget.setAttribute("aria-expanded", String(!collapsed));
  });

  // ---------------------------------------------------------------- downloads

  const downloadsDialog = $("#downloads-modal");
  const downloadList = $("#download-status");

  function downloadState(item) {
    const status = String(item.status || "").toLowerCase();
    if (item.type === "protected") return ["Needs CAPTCHA", "is-warn", "is-waiting"];
    if (status === "completed") return ["Completed · waiting for import", "is-live", "is-done"];
    if (status === "failed") return ["Failed", "is-danger", "is-waiting"];
    if (status.includes("paus")) return ["Paused", "is-muted", "is-waiting"];
    if (status.includes("extract")) return ["Extracting", "is-live", ""];
    return [item.status || "Downloading", "is-live", ""];
  }

  function downloadRow(item, quasarrUrl) {
    const node = document.createElement("article");
    node.className = "download";
    node.dataset.id = item.id;
    patchDownload(node, item, quasarrUrl);
    return node;
  }

  function patchDownload(node, item, quasarrUrl = "") {
    const status = String(item.status || "").toLowerCase();
    // History entries carry no percentage; a completed package is done.
    const pct = status === "completed" ? 100 : Math.max(0, Math.min(100, Number(item.percentage || 0)));
    const [label, tone, bar] = downloadState(item);
    const timeLeft = String(item.timeleft || "").replace(/^0?0:/, "");
    const total = Number(item.mb || 0);
    const left = Number(item.mbleft || 0);
    const meta = [
      `<span class="label ${tone}">${esc(label)}</span>`,
      total ? `<span>${esc((total - left).toFixed(0))} of ${esc(total.toFixed(0))} MB</span>` : "",
      timeLeft && !/^0?0:00$/.test(timeLeft) ? `<span>${esc(timeLeft)} left</span>` : "",
    ].join("");
    const actions = [
      status === "completed" ? `<button type="button" class="btn btn-quiet btn-sm" data-dl="import">${icon("upload")}Import now</button>` : "",
      item.type === "protected" ? `<a class="btn btn-quiet btn-sm" href="${esc(item.captcha_url)}" target="_blank" rel="noreferrer">${icon("shield")}Solve CAPTCHA</a>` : "",
      `<button type="button" class="btn btn-quiet btn-sm" data-dl="skip">${icon("skip")}Skip release</button>`,
      `<button type="button" class="btn btn-danger-text btn-sm" data-dl="delete">${icon("trash")}Delete</button>`,
    ].join("");
    const signature = JSON.stringify([item.title, item.magazine, pct, meta, actions]);
    if (node.dataset.sig === signature) return;
    const firstRender = !node.dataset.sig;
    node.dataset.sig = signature;
    if (firstRender) {
      node.innerHTML = `
        <div class="download-top">
          <div class="download-titles"><div class="download-mag"></div><div class="download-title"></div></div>
          <span class="download-pct"></span>
        </div>
        <div class="progress"><span></span></div>
        <div class="download-meta"></div>
        <div class="download-actions"></div>`;
    }
    $(".download-mag", node).textContent = item.magazine || "";
    $(".download-title", node).textContent = item.title || item.release_title || "";
    $(".download-pct", node).textContent = `${Math.round(pct)}%`;
    const progress = $(".progress", node);
    progress.className = `progress ${bar}`;
    $(".progress span", node).style.width = `${pct}%`;
    $(".download-meta", node).innerHTML = meta;
    $(".download-actions", node).innerHTML = actions;
  }

  function syncDownloads(container, items, quasarrUrl) {
    const nodes = new Map([...container.querySelectorAll(".download")].map((node) => [node.dataset.id, node]));
    container.querySelector(".empty-inline, .skeleton-list")?.remove();
    const keep = new Set();
    items.forEach((item, index) => {
      const key = String(item.id);
      keep.add(key);
      let node = nodes.get(key);
      if (!node) node = downloadRow(item, quasarrUrl);
      else patchDownload(node, item, quasarrUrl);
      if (container.children[index] !== node) container.insertBefore(node, container.children[index] || null);
    });
    for (const [key, node] of nodes) if (!keep.has(key)) node.remove();
  }

  async function loadDownloads() {
    const data = await api("/api/downloads");
    const link = $("#downloads-quasarr");
    if (data.quasarr_url) {
      link.href = data.quasarr_url;
      link.hidden = false;
    }
    if (data.error) {
      downloadList.innerHTML = `<p class="empty-inline">Quasarr did not answer: ${esc(data.error)}</p>`;
      $("#downloads-sub").textContent = "Retrying…";
      return;
    }
    $("#downloads-sub").textContent = data.active.length ? `${plural(data.active.length, "package")} in Quasarr` : "Live from Quasarr";
    if (!data.active.length) {
      downloadList.innerHTML = '<p class="empty-inline">Nothing downloading right now.</p>';
      return;
    }
    syncDownloads(downloadList, data.active, data.quasarr_url);
  }

  const downloadsPoll = poller(loadDownloads, 3000);

  function openDownloads() {
    downloadList.innerHTML = '<div class="skeleton-list"><div class="skeleton skeleton-row"></div><div class="skeleton skeleton-row"></div></div>';
    downloadsDialog.showModal();
    downloadsPoll.start();
  }
  downloadsDialog.addEventListener("close", () => downloadsPoll.stop());

  document.addEventListener("click", (event) => {
    if (event.target.closest("[data-open-downloads]")) openDownloads();
    if (event.target.closest("[data-open-settings]")) $("#settings-modal").showModal();
  });

  async function downloadAction(button, refresh) {
    const node = button.closest(".download");
    const id = node.dataset.id;
    const title = $(".download-title", node).textContent;
    const action = button.dataset.dl;
    if (action === "delete") {
      const ok = await confirmDialog({
        title: "Delete this download?",
        message: `${title} is removed from Quasarr and blocked from future searches.`,
        confirmLabel: "Delete",
      });
      if (!ok) return;
    }
    const url = {
      import: `/downloads/${id}/import-now`,
      skip: `/downloads/${id}/skip`,
      delete: `/downloads/${id}/delete-package`,
    }[action];
    setBusy(button, true);
    try {
      await post(url);
      toast({ import: "Import finished", skip: "Release skipped and blocked", delete: "Download deleted" }[action], "success");
      if (action !== "import") node.remove();
    } catch (error) {
      toast(error.message, "error");
    } finally {
      setBusy(button, false);
      refresh();
      dashboardPoll.now();
    }
  }

  downloadList.addEventListener("click", (event) => {
    const button = event.target.closest("[data-dl]");
    if (button) downloadAction(button, () => downloadsPoll.now());
  });

  // ---------------------------------------------------------------- magazine sheet

  const sheetDialog = $("#magazine-modal");
  const sheetBody = $("#magazine-modal-body");
  const sheetSearch = $("#magazine-modal-search");
  const sheetActions = $("#magazine-modal-actions");
  const sheetPager = $("#magazine-modal-pager");
  const sheet = { id: 0, tab: "downloaded", offset: 0, total: 0, limit: 25, duplicates: null, request: 0 };
  const tabLimits = { downloaded: 24, downloading: 100, errors: 25, skips: 25 };

  function openMagazine(id, tab = "downloaded") {
    const mag = byId(id);
    if (!mag) return;
    const switching = sheet.id !== mag.id;
    sheet.id = mag.id;
    if (switching) {
      sheet.duplicates = null;
      sheetSearch.value = "";
      // Never show the previous magazine's rows while this one loads.
      delete sheetBody.dataset.tab;
    }
    renderSheetHead();
    if (!sheetDialog.open) sheetDialog.showModal();
    selectTab(tab);
    history.replaceState(null, "", `#magazine/${mag.id}/${tab}`);
    if (sheet.duplicates === null) loadDuplicateCount();
  }

  function renderSheetHead() {
    const mag = byId(sheet.id);
    if (!mag) return;
    $("#magazine-modal-title").textContent = mag.title;
    const meta = [
      plural(mag.issue_count, "issue"),
      mag.last_issue_at ? `latest ${shortDate(mag.last_issue_at)}` : "",
      mag.last_search_at ? `searched ${ago(mag.last_search_at)}` : "never searched",
    ].filter(Boolean).join(" · ");
    $("#magazine-modal-meta").textContent = meta;
    const cover = $("#magazine-modal-cover");
    setOnce(cover, "cover", `${mag.cover_url}|${mag.title}`, () => {
      cover.replaceChildren();
      if (mag.cover_url) cover.append(coverImage(mag.cover_url, ""));
      else cover.innerHTML = coverBlank(mag.title);
    });
    const active = $("#magazine-modal-active");
    if (!active.disabled) active.checked = mag.active;
    const searchNow = $("#magazine-modal-search-now");
    const searching = isSearching(mag.id);
    searchNow.disabled = searching;
    searchNow.innerHTML = searching ? '<span class="spinner"></span>Searching…' : `${icon("search")}Search now`;
    const counts = {
      downloaded: mag.issue_count,
      downloading: mag.downloading,
      errors: mag.errors,
      skips: mag.skipped,
      duplicates: sheet.duplicates,
      blocklist: mag.blacklist.length,
    };
    for (const [kind, value] of Object.entries(counts)) {
      const node = sheetDialog.querySelector(`[data-tab-count="${kind}"]`);
      node.textContent = value ? value : "";
      node.className = `tab-count${kind === "errors" && value ? " is-danger" : ""}${kind === "downloading" && value ? " is-live" : ""}`;
    }
    if (sheet.tab === "blocklist") renderBlocklistChips(mag);
    if (sheet.tab === "downloaded" && sheet.total !== mag.issue_count && !sheetSearch.value) loadTab();
    if ((sheet.tab === "errors" && sheet.total !== mag.errors) || (sheet.tab === "skips" && sheet.total !== mag.skipped)) {
      if (!sheetSearch.value) loadTab();
    }
  }

  async function loadDuplicateCount() {
    const id = sheet.id;
    try {
      const data = await api(`/api/magazines/${id}/duplicates`);
      if (sheet.id !== id) return;
      sheet.duplicates = data.total;
      renderSheetHead();
      if (sheet.tab === "duplicates") renderDuplicates(data);
    } catch {
      // Count stays blank; the tab loads its own data.
    }
  }

  function selectTab(tab) {
    sheet.tab = tab;
    sheet.offset = 0;
    sheetDialog.querySelectorAll("[data-tab]").forEach((button) => {
      button.setAttribute("aria-selected", String(button.dataset.tab === tab));
    });
    // The tab row scrolls sideways on narrow screens.
    sheetDialog.querySelector(`[data-tab="${tab}"]`)?.scrollIntoView({ block: "nearest", inline: "nearest" });
    const searchable = ["downloaded", "errors", "skips"].includes(tab);
    $("#magazine-modal-tools").hidden = !searchable && !["duplicates"].includes(tab);
    sheetSearch.parentElement.hidden = !searchable;
    sheetSearch.placeholder = tab === "downloaded" ? "Search issues" : "Search releases";
    renderSheetActions();
    sheetBody.scrollTop = 0;
    loadTab();
    tabPoll[tab === "downloading" ? "start" : "stop"]();
    if (sheetDialog.open) history.replaceState(null, "", `#magazine/${sheet.id}/${tab}`);
  }

  function renderSheetActions() {
    const actions = {
      errors: `<button type="button" class="btn btn-danger-text btn-sm" data-sheet-act="delete-errors">${icon("trash")}Delete all</button>`,
      skips: `<button type="button" class="btn btn-quiet btn-sm" data-sheet-act="clear-skips">${icon("refresh")}Clear all</button>`,
      duplicates: `<button type="button" class="btn btn-danger-text btn-sm" data-sheet-act="clear-duplicates">${icon("trash")}Remove duplicates</button>`,
    };
    sheetActions.innerHTML = actions[sheet.tab] || "";
  }

  sheetDialog.querySelector("#magazine-modal-tabs").addEventListener("click", (event) => {
    const tab = event.target.closest("[data-tab]");
    if (tab) selectTab(tab.dataset.tab);
  });

  sheetDialog.addEventListener("close", () => {
    sheet.id = 0;
    tabPoll.stop();
    closeMenus();
    history.replaceState(null, "", location.pathname + location.search);
  });

  sheetSearch.addEventListener("input", debounce(() => {
    sheet.offset = 0;
    loadTab();
  }, 250));

  $("#magazine-modal-prev").addEventListener("click", () => {
    sheet.offset = Math.max(0, sheet.offset - sheet.limit);
    loadTab();
    sheetBody.scrollTop = 0;
  });
  $("#magazine-modal-next").addEventListener("click", () => {
    sheet.offset += sheet.limit;
    loadTab();
    sheetBody.scrollTop = 0;
  });

  function setPager(total) {
    sheet.total = total;
    const paged = total > sheet.limit;
    sheetPager.hidden = !paged;
    if (!paged) return;
    const start = total ? sheet.offset + 1 : 0;
    const end = Math.min(sheet.offset + sheet.limit, total);
    $("#magazine-modal-page").textContent = `${start}–${end} of ${total}`;
    $("#magazine-modal-prev").disabled = sheet.offset <= 0;
    $("#magazine-modal-next").disabled = end >= total;
  }

  async function loadTab() {
    const mag = byId(sheet.id);
    if (!mag) return;
    const tab = sheet.tab;
    const request = ++sheet.request;
    if (tab === "blocklist") {
      sheetPager.hidden = true;
      // Refreshes must not wipe a term being typed.
      if (sheetBody.dataset.tab === "blocklist" && $(".blocklist-form", sheetBody)) renderBlocklistChips(mag);
      else renderBlocklist(mag);
      return;
    }
    if (tab === "duplicates") {
      sheetPager.hidden = true;
      if (sheetBody.dataset.tab !== "duplicates") sheetBody.innerHTML = skeletonGrid();
      sheetBody.dataset.tab = "duplicates";
      try {
        const data = await api(`/api/magazines/${mag.id}/duplicates`);
        if (request !== sheet.request) return;
        sheet.duplicates = data.total;
        renderDuplicates(data);
        renderSheetHead();
      } catch (error) {
        if (request === sheet.request) sheetBody.innerHTML = `<p class="empty-inline">${esc(error.message)}</p>`;
      }
      return;
    }
    sheet.limit = tabLimits[tab];
    const params = new URLSearchParams({ limit: sheet.limit, offset: sheet.offset, search: sheetSearch.value.trim() });
    if (!sheetBody.firstElementChild || sheetBody.dataset.tab !== tab) {
      sheetBody.innerHTML = tab === "downloaded" ? skeletonGrid() : skeletonRows();
    }
    sheetBody.dataset.tab = tab;
    try {
      const data = await api(`/api/magazines/${mag.id}/items/${tab}?${params}`);
      if (request !== sheet.request) return;
      if (!data.rows.length && sheet.offset > 0) {
        sheet.offset = Math.max(0, sheet.offset - sheet.limit);
        loadTab();
        return;
      }
      setPager(data.total);
      if (tab === "downloaded") renderIssues(data.rows);
      if (tab === "downloading") renderSheetDownloads(data.rows);
      if (tab === "errors") renderErrors(data.rows);
      if (tab === "skips") renderSkips(data.rows);
    } catch (error) {
      if (request === sheet.request) sheetBody.innerHTML = `<p class="empty-inline">${esc(error.message)}</p>`;
    }
  }

  const tabPoll = poller(() => (sheet.tab === "downloading" ? loadTab() : Promise.resolve()), 4000);

  const skeletonGrid = () => `<div class="issue-grid">${'<div class="issue-tile"><div class="issue-cover skeleton"></div></div>'.repeat(6)}</div>`;
  const skeletonRows = () => `<div class="skeleton-list">${'<div class="skeleton skeleton-row"></div>'.repeat(3)}</div>`;

  function empty(message) {
    sheetBody.innerHTML = `<p class="empty-inline">${esc(message)}</p>`;
  }

  function issueTile(item, { keep = false, flag = "" } = {}) {
    const tile = document.createElement("article");
    tile.className = `issue-tile${keep ? " is-keep" : ""}`;
    tile.dataset.id = item.id;
    tile.innerHTML = `
      ${flag}
      <a class="issue-cover" href="${esc(item.view_url)}" aria-label="Read ${esc(issueLabel(item.issue_key, item.release_title))}">
        <span class="issue-overlay">
          <span class="overlay-btn" title="Read">${icon("eye")}</span>
          <button type="button" class="overlay-btn" data-issue="download" title="Download PDF" aria-label="Download PDF">${icon("download")}</button>
          <button type="button" class="overlay-btn is-danger" data-issue="delete" title="Delete" aria-label="Delete issue">${icon("trash")}</button>
        </span>
      </a>
      <div class="issue-info">
        <span class="issue-title"></span>
        <span class="issue-sub"></span>
      </div>`;
    $(".issue-cover", tile).prepend(coverImage(item.cover_url, ""));
    $(".issue-title", tile).textContent = issueLabel(item.issue_key, item.release_title);
    $(".issue-title", tile).title = item.release_title;
    $(".issue-sub", tile).textContent = [size(item.size_bytes), shortDate(item.acquired_at)].filter(Boolean).join(" · ");
    tile.dataset.fileUrl = item.file_url;
    return tile;
  }

  function renderIssues(rows) {
    if (!rows.length) {
      empty(sheetSearch.value ? "No issues match this search." : "No issues imported yet. Search now to look for new releases.");
      return;
    }
    const wrap = document.createElement("div");
    wrap.className = "issue-grid";
    rows.forEach((item) => wrap.append(issueTile(item)));
    sheetBody.replaceChildren(wrap);
  }

  function renderSheetDownloads(rows) {
    if (!rows.length) {
      empty("Nothing from this magazine is downloading.");
      return;
    }
    let list = sheetBody.querySelector(".download-list");
    if (!list) {
      list = document.createElement("div");
      list.className = "download-list";
      sheetBody.replaceChildren(list);
    }
    syncDownloads(list, rows, "");
  }

  function row(title, meta, actions) {
    const node = document.createElement("div");
    node.className = "row";
    node.innerHTML = `<div><div class="row-title"></div><div class="row-meta">${meta}</div></div><div class="row-actions">${actions}</div>`;
    $(".row-title", node).textContent = title;
    return node;
  }

  function renderErrors(rows) {
    if (!rows.length) {
      empty(sheetSearch.value ? "No errors match this search." : "No errors. Failed downloads and imports show up here.");
      return;
    }
    const list = document.createElement("div");
    list.className = "rows";
    for (const item of rows) {
      const label = item.status === "download_error" ? "Download failed" : "Import failed";
      const node = row(
        item.release_title,
        `<span class="label is-danger">${label}</span><span>${esc(ago(item.updated_at))}</span>`,
        `<button type="button" class="btn btn-quiet btn-sm" data-row="retry">${icon("refresh")}Retry</button>
         <button type="button" class="btn btn-danger-text btn-sm" data-row="delete-error">${icon("trash")}Delete</button>`,
      );
      node.dataset.id = item.id;
      list.append(node);
    }
    const note = document.createElement("p");
    note.className = "note";
    note.textContent = "Failed releases are removed from Quasarr and never downloaded again on their own. Retry sends the same release to Quasarr once more.";
    sheetBody.replaceChildren(note, list);
  }

  function renderSkips(rows) {
    if (!rows.length) {
      empty(sheetSearch.value ? "No skipped releases match this search." : "Nothing skipped.");
      return;
    }
    const list = document.createElement("div");
    list.className = "rows";
    for (const item of rows) {
      const node = row(
        item.release_title,
        `<span class="label is-muted">${esc(item.reason)}</span><span>${esc(ago(item.updated_at))}</span>`,
        `<button type="button" class="btn btn-quiet btn-sm" data-row="unskip">${icon("download")}Download anyway</button>`,
      );
      node.dataset.id = item.id;
      list.append(node);
    }
    sheetBody.replaceChildren(list);
  }

  function renderDuplicates(data) {
    if (sheet.tab !== "duplicates") return;
    if (!data.groups.length) {
      empty("No duplicates. Issues are compared by their first page.");
      return;
    }
    const nodes = [];
    data.groups.forEach((group) => {
      const head = document.createElement("div");
      head.className = "group-head";
      head.innerHTML = `${esc(issueLabel(group[0].issue_key, group[0].release_title))}<span>${group.length} copies · keeps the largest</span>`;
      const wrap = document.createElement("div");
      wrap.className = "issue-grid";
      group.forEach((item, index) => {
        wrap.append(issueTile(item, {
          keep: index === 0,
          flag: index === 0 ? '<span class="issue-flag flag">Keep</span>' : "",
        }));
      });
      nodes.push(head, wrap);
    });
    sheetBody.replaceChildren(...nodes);
  }

  function renderBlocklist(mag) {
    sheetBody.dataset.tab = "blocklist";
    sheetBody.innerHTML = `
      <div class="blocklist">
        <p class="note">Releases whose title contains any of these words are skipped, for example <em>English</em>, <em>Sonderheft</em> or <em>Leseprobe</em>.</p>
        <form class="blocklist-form" autocomplete="off">
          <input name="term" placeholder="Add a word or phrase" aria-label="Blocklist term" maxlength="120">
          <button class="btn btn-ink" type="submit">${icon("plus")}Add</button>
        </form>
        <div class="chips" id="blocklist-chips"></div>
      </div>`;
    renderBlocklistChips(mag);
    // Focusing on touch screens would pop the keyboard over the list.
    if (matchMedia("(hover: hover)").matches) $(".blocklist-form input", sheetBody).focus();
  }

  function renderBlocklistChips(mag) {
    const chips = $("#blocklist-chips", sheetBody);
    if (!chips) return;
    const signature = JSON.stringify(mag.blacklist);
    if (chips.dataset.sig === signature) return;
    chips.dataset.sig = signature;
    if (!mag.blacklist.length) {
      chips.innerHTML = '<span class="muted">No blocked terms.</span>';
      return;
    }
    chips.replaceChildren(...mag.blacklist.map((term) => {
      const chip = document.createElement("span");
      chip.className = "chip";
      chip.dataset.termId = term.id;
      chip.innerHTML = `<span></span><button type="button" aria-label="Remove">${icon("x")}</button>`;
      chip.firstElementChild.textContent = term.term;
      chip.querySelector("button").setAttribute("aria-label", `Remove ${term.term}`);
      return chip;
    }));
  }

  sheetBody.addEventListener("submit", async (event) => {
    if (!event.target.matches(".blocklist-form")) return;
    event.preventDefault();
    const mag = byId(sheet.id);
    const input = event.target.elements.term;
    const term = input.value.trim();
    if (!term || !mag) return;
    if (mag.blacklist.some((item) => item.term.toLocaleLowerCase() === term.toLocaleLowerCase())) {
      toast(`"${term}" is already blocked`);
      input.select();
      return;
    }
    const button = event.target.querySelector("button");
    setBusy(button, true);
    try {
      await post(`/magazines/${mag.id}/blacklist`, { term });
      input.value = "";
      await refreshDashboard();
      // The refresh already rendered the chips; mark the new one.
      [...sheetBody.querySelectorAll(".chip")]
        .find((chip) => chip.firstElementChild.textContent.toLocaleLowerCase() === term.toLocaleLowerCase())
        ?.classList.add("is-new");
    } catch (error) {
      toast(error.message, "error");
    } finally {
      setBusy(button, false);
      input.focus();
    }
  });

  sheetBody.addEventListener("click", async (event) => {
    const mag = byId(sheet.id);
    if (!mag) return;

    const chipButton = event.target.closest(".chip button");
    if (chipButton) {
      const chip = chipButton.closest(".chip");
      chip.style.opacity = "0.4";
      try {
        await post(`/blacklist/${chip.dataset.termId}/delete`);
        await refreshDashboard();
      } catch (error) {
        chip.style.opacity = "";
        toast(error.message, "error");
      }
      return;
    }

    const issueButton = event.target.closest("[data-issue]");
    if (issueButton) {
      event.preventDefault();
      const tile = issueButton.closest(".issue-tile");
      if (issueButton.dataset.issue === "download") {
        downloadFile(tile.dataset.fileUrl);
        return;
      }
      const ok = await confirmDialog({
        title: "Delete this issue?",
        message: `${$(".issue-title", tile).textContent} is deleted from disk.`,
        confirmLabel: "Delete issue",
      });
      if (!ok) return;
      try {
        await post(`/issues/${tile.dataset.id}/delete`);
        tile.remove();
        toast("Issue deleted", "success");
        sheet.duplicates = null;
        await refreshDashboard();
        loadTab();
        loadDuplicateCount();
      } catch (error) {
        toast(error.message, "error");
      }
      return;
    }

    const rowButton = event.target.closest("[data-row]");
    if (rowButton) {
      const node = rowButton.closest(".row");
      const title = $(".row-title", node).textContent;
      const action = rowButton.dataset.row;
      if (action === "delete-error") {
        const ok = await confirmDialog({
          title: "Delete this error?",
          message: `${title} stays blocked from future searches. Use Skipped to download it later.`,
          confirmLabel: "Delete",
        });
        if (!ok) return;
      }
      const url = {
        retry: `/downloads/${node.dataset.id}/retry-import`,
        "delete-error": `/downloads/${node.dataset.id}/delete-package`,
        unskip: `/skipped/${node.dataset.id}/unskip`,
      }[action];
      setBusy(rowButton, true);
      try {
        await post(url);
        toast({ retry: "Sent to Quasarr again", "delete-error": "Error deleted", unskip: "Sent to Quasarr" }[action], "success");
        node.remove();
        await refreshDashboard();
        loadTab();
      } catch (error) {
        toast(error.message, "error");
        setBusy(rowButton, false);
      }
      return;
    }

    const download = event.target.closest("[data-dl]");
    if (download) downloadAction(download, () => loadTab());
  });

  sheetActions.addEventListener("click", async (event) => {
    const button = event.target.closest("[data-sheet-act]");
    const mag = byId(sheet.id);
    if (!button || !mag) return;
    const action = button.dataset.sheetAct;
    const prompts = {
      "delete-errors": {
        title: "Delete all errors?",
        message: `All ${plural(mag.errors, "error")} for ${mag.title} are cleared. The releases stay blocked as skipped.`,
        confirmLabel: "Delete all",
        url: `/magazines/${mag.id}/errors/delete`,
        done: "Errors deleted",
      },
      "clear-skips": {
        title: "Clear skipped releases?",
        message: "Cleared releases can be picked up again by the next search.",
        confirmLabel: "Clear all",
        danger: false,
        url: `/magazines/${mag.id}/skipped/clear`,
        done: "Skipped releases cleared",
      },
      "clear-duplicates": {
        title: "Remove duplicates?",
        message: "In each group the largest file is kept and the other copies are deleted from disk.",
        confirmLabel: "Remove duplicates",
        url: `/api/magazines/${mag.id}/duplicates/clear`,
        done: "Duplicates removed",
      },
    }[action];
    if (!(await confirmDialog(prompts))) return;
    setBusy(button, true);
    try {
      const data = await post(prompts.url);
      toast(data?.deleted !== undefined ? `${prompts.done} (${data.deleted})` : prompts.done, "success");
      sheet.duplicates = null;
      await refreshDashboard();
      loadTab();
      if (action === "clear-duplicates") loadDuplicateCount();
    } catch (error) {
      toast(error.message, "error");
    } finally {
      setBusy(button, false);
    }
  });

  $("#magazine-modal-search-now").addEventListener("click", () => {
    const mag = byId(sheet.id);
    if (mag) searchMagazine(mag);
  });

  $("#magazine-modal-active").addEventListener("change", (event) => {
    const mag = byId(sheet.id);
    if (mag) setActive(mag, event.target.checked, event.target);
  });

  $("#magazine-modal-menu").addEventListener("click", (event) => toggleMenu(event.currentTarget));
  $("#magazine-modal-delete").addEventListener("click", () => {
    closeMenus();
    const mag = byId(sheet.id);
    if (mag) deleteMagazine(mag);
  });

  // ---------------------------------------------------------------- settings

  const settingsDialog = $("#settings-modal");
  const settingsForm = $("[data-action='settings']");
  settingsForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const submitter = event.submitter;
    const url = submitter?.getAttribute("formaction") || settingsForm.action;
    const testing = url.includes("/notifications/");
    setBusy(submitter, true);
    try {
      await api(url, { method: "POST", body: formBody(settingsForm, submitter) });
      // Make the saved values the form's reset point.
      for (const field of settingsForm.elements) {
        if (field.type === "checkbox") field.defaultChecked = field.checked;
        else if ("defaultValue" in field) field.defaultValue = field.value;
      }
      settingsForm.dataset.saved = new URLSearchParams(new FormData(settingsForm)).toString();
      if (testing) {
        toast("Settings saved. Test notification sent.", "success");
      } else {
        toast("Settings saved", "success");
        closeDialog(settingsDialog);
        dashboardPoll.now();
      }
    } catch (error) {
      toast(error.message, "error");
    } finally {
      setBusy(submitter, false);
    }
  });

  settingsDialog.addEventListener("cancel", (event) => {
    if (settingsDirty() && !window.confirm("Discard unsaved settings?")) event.preventDefault();
  });
  settingsDialog.querySelectorAll("[data-close]").forEach((button) => {
    button.addEventListener("click", (event) => {
      if (settingsDirty() && !window.confirm("Discard unsaved settings?")) {
        event.stopImmediatePropagation();
        return;
      }
      settingsForm.reset();
    }, { capture: true });
  });
  const settingsDirty = () =>
    new URLSearchParams(new FormData(settingsForm)).toString() !== settingsForm.dataset.saved;
  settingsForm.dataset.saved = new URLSearchParams(new FormData(settingsForm)).toString();

  // ---------------------------------------------------------------- boot

  applyView();
  try {
    applyDashboard(JSON.parse($("#initial-state").textContent));
  } catch (error) {
    console.warn(error);
  }
  state.loaded = true;
  dashboardPoll.start();

  function openDeepLink() {
    const deepLink = location.hash.match(/^#magazine\/(\d+)\/(\w+)/);
    if (deepLink && byId(deepLink[1])) openMagazine(Number(deepLink[1]), deepLink[2]);
  }
  openDeepLink();
  window.addEventListener("hashchange", openDeepLink);
})();

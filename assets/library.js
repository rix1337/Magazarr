// Library: every imported issue, newest first.
(() => {
  "use strict";

  const { icon, api, toast, confirmDialog, coverImage, debounce, setBusy, size, shortDate, plural, issueLabel, store, downloadFile } = window.Mz;
  const $ = (selector, root = document) => root.querySelector(selector);

  const grid = $("#library-grid");
  const search = $("#library-search");
  const filter = $("#library-mag-filter");
  const more = $("#library-more");
  const count = $("#library-count");
  const bulk = $("#library-bulk");
  const selectMode = $("#library-select-mode");
  const pageSize = 60;
  const state = { offset: 0, total: 0, selecting: false, selected: new Set(), request: 0 };

  filter.value = store.get("library.magazine", "");
  if (filter.value !== store.get("library.magazine", "")) filter.value = "";

  function tile(item) {
    const node = document.createElement("article");
    node.className = "issue-tile is-entering";
    node.dataset.id = item.id;
    const title = issueLabel(item.issue_key, item.release_title);
    node.innerHTML = `
      <a class="issue-cover" href="/issues/${item.id}/view">
        <span class="issue-check">${icon("check")}</span>
        <span class="issue-overlay">
          <span class="overlay-btn" title="Read">${icon("eye")}</span>
          <button type="button" class="overlay-btn" data-act="download" title="Download PDF" aria-label="Download PDF">${icon("download")}</button>
          <button type="button" class="overlay-btn is-danger" data-act="delete" title="Delete" aria-label="Delete issue">${icon("trash")}</button>
        </span>
      </a>
      <div class="issue-info">
        <span class="issue-title"></span>
        <span class="issue-sub"></span>
      </div>`;
    $(".issue-cover", node).prepend(coverImage(item.cover_url, ""));
    $(".issue-cover", node).setAttribute("aria-label", `Read ${item.magazine_title} ${title}`);
    $(".issue-title", node).textContent = title;
    $(".issue-title", node).title = item.release_title;
    $(".issue-sub", node).textContent = [item.magazine_title, size(item.size_bytes)].filter(Boolean).join(" · ");
    $(".issue-sub", node).title = shortDate(item.acquired_at);
    node.classList.toggle("is-selected", state.selected.has(item.id));
    return node;
  }

  async function load({ append = false } = {}) {
    const request = ++state.request;
    if (!append) state.offset = 0;
    const params = new URLSearchParams({ limit: pageSize, offset: state.offset, search: search.value.trim() });
    if (filter.value) params.set("magazine_id", filter.value);
    if (!append && !grid.children.length) {
      grid.innerHTML = '<div class="issue-tile"><div class="issue-cover skeleton"></div></div>'.repeat(12);
    }
    setBusy(more, true);
    try {
      const data = await api(`/api/library/issues?${params}`);
      if (request !== state.request) return;
      if (!append) grid.replaceChildren();
      data.items.forEach((item) => grid.append(tile(item)));
      state.total = data.total;
      state.offset += data.items.length;
      renderCount();
    } catch (error) {
      if (request === state.request) toast(error.message, "error");
    } finally {
      setBusy(more, false);
    }
  }

  function renderCount() {
    more.hidden = state.offset >= state.total;
    $("#library-empty").hidden = state.total > 0;
    const scope = filter.value ? filter.options[filter.selectedIndex].text : "all magazines";
    count.textContent = search.value.trim()
      ? `${plural(state.total, "issue")} matching “${search.value.trim()}” in ${scope}`
      : `${plural(state.total, "issue")} in ${scope}`;
  }

  function updateSelection() {
    bulk.hidden = !state.selecting;
    $("#library-selected-count").textContent = `${state.selected.size} selected`;
    $("#library-bulk-delete").disabled = state.selected.size === 0;
  }

  function setSelecting(on) {
    state.selecting = on;
    grid.classList.toggle("is-selecting", on);
    selectMode.setAttribute("aria-pressed", String(on));
    selectMode.innerHTML = on ? `${icon("x")}Done` : `${icon("check")}Select`;
    if (!on) {
      state.selected.clear();
      grid.querySelectorAll(".is-selected").forEach((node) => node.classList.remove("is-selected"));
    }
    updateSelection();
  }

  async function deleteIssues(ids, label) {
    const ok = await confirmDialog({
      title: ids.length === 1 ? "Delete this issue?" : `Delete ${ids.length} issues?`,
      message: `${label} ${ids.length === 1 ? "is" : "are"} deleted from disk and not downloaded again. This cannot be undone.`,
      confirmLabel: ids.length === 1 ? "Delete issue" : `Delete ${ids.length}`,
    });
    if (!ok) return;
    try {
      const data = await api("/api/issues/bulk-delete", { method: "POST", json: { issue_ids: ids } });
      ids.forEach((id) => {
        state.selected.delete(id);
        grid.querySelector(`[data-id="${id}"]`)?.remove();
      });
      state.total = Math.max(0, state.total - (data?.deleted ?? ids.length));
      state.offset = Math.max(0, state.offset - ids.length);
      toast(`Deleted ${plural(data?.deleted ?? ids.length, "issue")}`, "success");
      updateSelection();
      // Keep the pages already loaded; only refill when the grid ran empty.
      if (grid.querySelector(".issue-tile")) renderCount();
      else load();
    } catch (error) {
      toast(error.message, "error");
    }
  }

  grid.addEventListener("click", (event) => {
    const node = event.target.closest(".issue-tile");
    if (!node || !node.dataset.id) return;
    const id = Number(node.dataset.id);
    if (state.selecting) {
      event.preventDefault();
      if (state.selected.has(id)) state.selected.delete(id);
      else state.selected.add(id);
      node.classList.toggle("is-selected", state.selected.has(id));
      updateSelection();
      return;
    }
    const action = event.target.closest("[data-act]")?.dataset.act;
    if (!action) return;
    event.preventDefault();
    if (action === "download") downloadFile(`/issues/${id}/file`);
    if (action === "delete") deleteIssues([id], $(".issue-title", node).textContent);
  });

  selectMode.addEventListener("click", () => setSelecting(!state.selecting));
  $("#library-select-all").addEventListener("click", () => {
    grid.querySelectorAll(".issue-tile[data-id]").forEach((node) => {
      state.selected.add(Number(node.dataset.id));
      node.classList.add("is-selected");
    });
    updateSelection();
  });
  $("#library-deselect-all").addEventListener("click", () => {
    state.selected.clear();
    grid.querySelectorAll(".is-selected").forEach((node) => node.classList.remove("is-selected"));
    updateSelection();
  });
  $("#library-bulk-delete").addEventListener("click", () => {
    if (state.selected.size) deleteIssues([...state.selected], "The selected issues");
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && state.selecting && !document.querySelector("dialog[open]")) setSelecting(false);
  });

  search.addEventListener("input", debounce(() => load(), 250));
  filter.addEventListener("change", () => {
    store.set("library.magazine", filter.value);
    load();
  });
  more.addEventListener("click", () => load({ append: true }));

  load();
})();

// flac-inquisitor front end: no build step, no dependencies. Pages are
// rendered on the server; this file adds the parts that need the JSON API.
"use strict";

const FI = (() => {
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

  async function api(method, url, body) {
    const res = await fetch(url, {
      method,
      headers: body ? { "Content-Type": "application/json" } : {},
      body: body ? JSON.stringify(body) : undefined,
    });
    let data = null;
    try { data = await res.json(); } catch (e) { /* empty body */ }
    if (!res.ok && res.status !== 409) {
      throw new Error((data && data.detail) || `${res.status} ${res.statusText}`);
    }
    return { status: res.status, data };
  }

  function toast(message, { error = false, action = null, timeout = 10000 } = {}) {
    const el = document.createElement("div");
    el.className = "toast" + (error ? " error" : "");
    const text = document.createElement("span");
    text.textContent = message;
    el.append(text);
    if (action) {
      const btn = document.createElement("button");
      btn.className = "small";
      btn.textContent = action.label;
      btn.addEventListener("click", () => { el.remove(); action.run(); });
      el.append(btn);
    }
    $("#toasts").append(el);
    setTimeout(() => el.remove(), timeout);
  }

  // Confirmation with <dialog>; optional typed confirmation for deletions.
  function confirmDialog({ title, body, ok = "Confirm", danger = false, typed = null }) {
    const dlg = $("#confirm-dialog");
    $("[data-title]", dlg).textContent = title;
    const bodyEl = $("[data-body]", dlg);
    bodyEl.replaceChildren();
    if (typeof body === "string") { const p = document.createElement("p"); p.textContent = body; bodyEl.append(p); }
    else if (body) bodyEl.append(body);
    const okBtn = $("[data-ok]", dlg);
    okBtn.textContent = ok;
    okBtn.className = danger ? "danger" : "primary";
    const typedBox = $("[data-typed]", dlg);
    const input = $("[data-input]", dlg);
    typedBox.hidden = !typed;
    input.value = "";
    okBtn.disabled = !!typed;
    if (typed) {
      $("[data-expected]", dlg).textContent = typed;
      input.oninput = () => { okBtn.disabled = input.value.trim() !== typed; };
    }
    dlg.returnValue = "";
    dlg.showModal();
    return new Promise((resolve) => dlg.addEventListener("close", () => resolve(dlg.returnValue === "ok"), { once: true }));
  }

  function list(items) {
    const ul = document.createElement("ul");
    for (const item of items) { const li = document.createElement("li"); li.textContent = item; ul.append(li); }
    return ul;
  }

  function relTime(date) {
    const s = Math.round((Date.now() - date.getTime()) / 1000);
    if (s < 60) return "just now";
    if (s < 3600) return `${Math.round(s / 60)} min ago`;
    if (s < 86400) return `${Math.round(s / 3600)} h ago`;
    return date.toLocaleString();
  }

  function initCommon() {
    const theme = $("#theme");
    if (theme) {
      try { theme.value = localStorage.getItem("theme") || ""; } catch (e) { /* storage blocked */ }
      theme.addEventListener("change", () => {
        if (theme.value) document.documentElement.dataset.theme = theme.value;
        else delete document.documentElement.dataset.theme;
        try { theme.value ? localStorage.setItem("theme", theme.value) : localStorage.removeItem("theme"); } catch (e) { /* ignore */ }
      });
    }
    for (const t of $$("time[data-epoch]")) t.textContent = relTime(new Date(Number(t.dataset.epoch) * 1000));
    for (const t of $$("time[data-iso]")) t.textContent = relTime(new Date(t.dataset.iso));
  }

  function loadImages(root) {
    for (const img of $$("img[data-src]", root)) { img.src = img.dataset.src; img.removeAttribute("data-src"); }
  }

  // Checkbox selection in a table with a select-all box in the header.
  function selection(table, onChange) {
    const boxes = () => $$("tbody input[type=checkbox]", table);
    const all = $("[data-select-all]", table);
    const selected = () => boxes().filter((b) => b.checked).map((b) => b.value);
    all?.addEventListener("change", () => { boxes().forEach((b) => { b.checked = all.checked; }); onChange(selected()); });
    table.addEventListener("change", (e) => { if (e.target !== all) onChange(selected()); });
    return selected;
  }

  async function doQuarantine(payload, description) {
    try {
      const { data } = await api("POST", "/api/quarantine", payload);
      const undoIds = data.entry_ids || [];
      toast(`Moved ${data.tracks} track(s) to quarantine.` + (data.errors.length ? ` ${data.errors.length} error(s).` : ""), {
        error: data.errors.length > 0,
        action: undoIds.length ? { label: "Undo", run: () => restoreEntries(undoIds, "check", true) } : null,
      });
      return data;
    } catch (e) { toast(e.message, { error: true }); return null; }
  }

  async function restoreEntries(ids, mode = "check", reload = false) {
    try {
      const { status, data } = await api("POST", "/api/quarantine/restore", { ids: ids.map(Number), mode });
      if (status === 409 || (data.conflicts && data.conflicts.length)) return showConflicts(ids, data.conflicts, reload);
      toast(`Restored ${data.tracks} track(s).` + (data.errors.length ? ` ${data.errors.length} error(s).` : ""), { error: data.errors.length > 0 });
      if (reload) setTimeout(() => location.reload(), 600);
      return data;
    } catch (e) { toast(e.message, { error: true }); return null; }
  }

  const CONFLICT_TEXT = {
    "path-taken": "A file with the same name is already in the library.",
    "same-track": "The library already has this track under another name.",
    "folder-missing": "The album folder no longer exists; it will be created.",
  };

  async function showConflicts(ids, conflicts, reload) {
    const body = document.createElement("div");
    const onlyFolders = conflicts.every((c) => c.type === "folder-missing");
    for (const c of conflicts) {
      const block = document.createElement("div");
      block.className = "sheet stack";
      block.style.marginBottom = "0.75rem";
      const p = document.createElement("p");
      p.innerHTML = `<strong></strong><br><span class="muted"></span>`;
      p.querySelector("strong").textContent = c.path;
      p.querySelector("span").textContent = CONFLICT_TEXT[c.type] || c.type;
      block.append(p);
      if (c.current) {
        const grid = document.createElement("div");
        grid.className = "compare";
        grid.innerHTML = `<figure><figcaption>In the library: <span class="mono"></span></figcaption><img alt="Spectrogram of the file in the library"></figure>
                          <figure><figcaption>In quarantine</figcaption><img alt="Spectrogram of the quarantined file"></figure>`;
        grid.querySelector(".mono").textContent = c.current;
        const imgs = grid.querySelectorAll("img");
        imgs[0].src = `/api/library-file/spectrogram.png?path=${encodeURIComponent(c.current)}`;
        imgs[1].src = `/api/quarantine/${c.entry_id}/spectrogram.png`;
        imgs.forEach((i) => { i.style.width = "100%"; i.style.background = "#000"; });
        block.append(grid);
      }
      body.append(block);
    }
    const ok = await confirmDialog({
      title: "Restore conflicts",
      body,
      ok: onlyFolders ? "Restore anyway" : "Replace (move the current files to quarantine)",
      danger: !onlyFolders,
    });
    if (ok) return restoreEntries(ids, onlyFolders ? "force" : "replace", reload);
    return null;
  }

  // Pages.

  function dashboard() {
    const box = $("#scan");
    const start = $("[data-scan-start]", box);
    const stop = $("[data-scan-stop]", box);
    const progress = $("[data-progress]", box);
    let timer = null;

    function render(s) {
      const running = s.state !== "idle";
      start.hidden = running;
      stop.hidden = !running;
      stop.disabled = s.state === "stopping";
      progress.hidden = !running;
      if (running) {
        $("progress", progress).max = Math.max(s.total, 1);
        $("progress", progress).value = s.done;
        const eta = s.eta_seconds != null ? ` · about ${Math.ceil(s.eta_seconds / 60)} min left` : "";
        $("[data-counts]", progress).textContent =
          `${s.done} of ${s.total} files · ${s.analyzed} analyzed · ${s.cached} unchanged · ${s.errors} errors${eta}`;
        $("[data-current]", progress).textContent = s.current || "";
        $("[data-scan-text]", box).textContent = s.state === "stopping" ? "Stopping after the files in progress…"
          : s.folder ? `Scanning ${s.folder}…` : "Scanning…";
      }
    }

    async function poll() {
      const { data } = await api("GET", "/api/scan");
      render(data);
      if (data.state === "idle") { clearInterval(timer); timer = null; if (box.dataset.state !== "idle") location.reload(); }
      box.dataset.state = data.state;
    }

    start.addEventListener("click", async () => {
      try {
        await api("POST", "/api/scan", { full: $("[data-full]", box).checked, folder: $("[data-folder]", box).value });
        box.dataset.state = "running";
        if (!timer) timer = setInterval(poll, 1000);
        poll();
      } catch (e) { toast(e.message, { error: true }); }
    });
    stop.addEventListener("click", () => api("DELETE", "/api/scan").then(poll));
    if (box.dataset.state !== "idle") { timer = setInterval(poll, 1000); poll(); }

    // Folder suggestions, fetched once on first use: listing the library is
    // not free on a large collection.
    $("[data-folder]", box).addEventListener("focus", async () => {
      const dl = $("#library-folders");
      if (dl.childElementCount) return;
      const { data } = await api("GET", "/api/folders").catch(() => ({ data: [] }));
      dl.replaceChildren(...data.map((f) => Object.assign(document.createElement("option"), { value: f })));
    }, { once: true });
  }

  function library() {
    const form = $("[data-filters]");
    // An empty verdict selection means "all", which the server spells out.
    form.addEventListener("submit", (e) => {
      if (!$$("input[name=verdict]:checked", form).length) {
        e.preventDefault();
        const params = new URLSearchParams(new FormData(form));
        params.set("verdict", "all");
        location.search = params.toString();
      }
    });
    for (const a of $$("[data-page]")) {
      a.addEventListener("click", (e) => {
        e.preventDefault();
        const params = new URLSearchParams(location.search);
        params.set("page", a.dataset.page);
        location.search = params.toString();
      });
    }
    const table = $("table[data-selectable]");
    if (!table) return;
    const kind = table.dataset.selectable;
    const bulk = $("[data-bulk]");
    const selected = selection(table, (ids) => {
      bulk.hidden = ids.length === 0;
      $("[data-bulk-count]").textContent = `${ids.length} selected`;
    });
    $("[data-bulk-quarantine]").addEventListener("click", async () => {
      const ids = selected();
      const names = $$("tbody input:checked", table).map((b) => b.getAttribute("aria-label").replace(/^Select /, ""));
      const what = kind === "album" ? "album(s), with every file in their folders" : "track(s), with their .lrc and other sidecar files";
      if (!await confirmDialog({ title: `Quarantine ${ids.length} ${what}?`, body: list(names), ok: "Quarantine", danger: true })) return;
      const payload = kind === "album" ? { album_keys: ids } : { track_ids: ids.map(Number) };
      if (await doQuarantine(payload)) setTimeout(() => location.reload(), 600);
    });
    $("[data-bulk-ignore]").addEventListener("click", async () => {
      const ids = selected();
      let trackIds = ids.map(Number);
      if (kind === "album") {
        trackIds = [];
        for (const key of ids) {
          const { data } = await api("GET", `/api/albums/${key}`);
          trackIds.push(...data.tracks.map((t) => t.id));
        }
      }
      await api("POST", "/api/ignore", { track_ids: trackIds, ignore: true });
      toast(`Ignoring ${trackIds.length} track(s).`);
      setTimeout(() => location.reload(), 600);
    });
  }

  function album() {
    const table = $("[data-tracklist]");
    const rows = $$("tbody tr[data-track]", table);
    const qSel = $("[data-quarantine-selected]");
    const iSel = $("[data-ignore-selected]");
    const scanning = qSel.hasAttribute("data-scanning");
    const selected = selection(table, (ids) => {
      qSel.disabled = scanning || ids.length === 0;
      iSel.disabled = ids.length === 0;
      $("[data-bulk-count]").textContent = ids.length ? `${ids.length} selected` : "";
    });

    for (const d of $$("[data-details]", table)) d.addEventListener("toggle", () => { if (d.open) loadImages(d); });
    const hash = location.hash.match(/^#track-(\d+)$/);
    if (hash) { const row = $(`#track-${hash[1]}`); if (row) { $("[data-details]", row).open = true; row.focus(); } }

    const rescan = $("[data-rescan]");
    rescan.addEventListener("click", async () => {
      try {
        await api("POST", "/api/scan", { folder: rescan.dataset.albumDir, full: $("[data-rescan-full]").checked });
      } catch (e) { toast(e.message, { error: true }); return; }
      rescan.disabled = true;
      const timer = setInterval(async () => {
        const { data } = await api("GET", "/api/scan");
        rescan.textContent = `Scanning… ${data.done}/${data.total}`;
        if (data.state === "idle") { clearInterval(timer); location.reload(); }
      }, 1000);
    });

    $("[data-compare]").addEventListener("click", () => {
      const grid = $("[data-compare-grid]");
      grid.hidden = !grid.hidden;
      if (!grid.hidden) loadImages(grid);
    });

    async function quarantineTracks(ids) {
      const names = ids.map((id) => $(`tr[data-track="${id}"]`).dataset.name);
      if (!await confirmDialog({ title: `Quarantine ${ids.length} track(s)?`, body: list(names), ok: "Quarantine", danger: true })) return;
      if (await doQuarantine({ track_ids: ids.map(Number) })) setTimeout(() => location.reload(), 600);
    }
    qSel.addEventListener("click", () => quarantineTracks(selected()));
    $("[data-quarantine-album]").addEventListener("click", async (e) => {
      const key = e.currentTarget.dataset.albumKey;
      if (!await confirmDialog({
        title: "Quarantine the whole album?",
        body: "Every file in the album folder moves to the quarantine: tracks, covers, lyrics. You can restore it later.",
        ok: "Quarantine album", danger: true,
      })) return;
      if (await doQuarantine({ album_keys: [key] })) setTimeout(() => { location.href = "/albums"; }, 800);
    });
    async function setIgnore(ids, ignore) {
      await api("POST", "/api/ignore", { track_ids: ids.map(Number), ignore });
      toast(ignore ? "Marked as false positive." : "No longer ignored.");
      setTimeout(() => location.reload(), 500);
    }
    iSel.addEventListener("click", () => setIgnore(selected(), true));
    for (const b of $$("[data-ignore]")) b.addEventListener("click", () => setIgnore([b.dataset.ignore], true));
    for (const b of $$("[data-unignore]")) b.addEventListener("click", () => setIgnore([b.dataset.unignore], false));

    // Spectrogram viewer with previous/next.
    const viewer = $("#spectro-dialog");
    let current = 0;
    function show(i) {
      current = (i + rows.length) % rows.length;
      const row = rows[current];
      $("[data-title]", viewer).textContent = row.dataset.name;
      const spectro = $(".spectro", row).cloneNode(true);
      loadImages(spectro);
      $("[data-body]", viewer).replaceChildren(spectro);
      if (!viewer.open) viewer.showModal();
    }
    $("[data-prev]", viewer).addEventListener("click", () => show(current - 1));
    $("[data-next]", viewer).addEventListener("click", () => show(current + 1));

    // Keyboard: j/k move, Enter expands, s opens the spectrogram, q quarantines.
    let focus = rows.findIndex((r) => r === document.activeElement);
    document.addEventListener("keydown", (e) => {
      if (e.target.closest("input, textarea, select, dialog") || e.metaKey || e.ctrlKey || e.altKey) {
        if (viewer.open && (e.key === "ArrowLeft" || e.key === "ArrowRight")) show(current + (e.key === "ArrowRight" ? 1 : -1));
        return;
      }
      const move = (d) => {
        rows[focus]?.classList.remove("selected-row");
        focus = Math.max(0, Math.min(rows.length - 1, focus + d));
        rows[focus].classList.add("selected-row");
        rows[focus].scrollIntoView({ block: "nearest" });
      };
      if (e.key === "j") move(1);
      else if (e.key === "k") move(focus < 0 ? 1 : -1);
      else if (focus >= 0 && e.key === "Enter") { const d = $("[data-details]", rows[focus]); d.open = !d.open; }
      else if (focus >= 0 && e.key === "s") show(focus);
      else if (focus >= 0 && e.key === "q" && !scanning) quarantineTracks([rows[focus].dataset.track]);
      else return;
      e.preventDefault();
    });
  }

  function quarantine() {
    $("[data-copy-list]")?.addEventListener("click", async () => {
      const { data } = await api("GET", "/api/quarantine/list.txt").catch(() => ({ data: null }));
      const text = data?.text || "";
      try { await navigator.clipboard.writeText(text); toast("List copied."); }
      catch (e) {
        const pre = document.createElement("pre");
        pre.textContent = text;
        pre.style.whiteSpace = "pre-wrap";
        confirmDialog({ title: "Albums to download again", body: pre, ok: "Close" });
      }
    });
    for (const b of $$("[data-restore]")) {
      b.addEventListener("click", () => restoreEntries(b.dataset.restore.split(","), "check", true));
    }
    for (const b of $$("[data-delete]")) {
      b.addEventListener("click", async () => {
        const ids = b.dataset.delete.split(",").map(Number);
        const name = b.dataset.name;
        if (!await confirmDialog({
          title: "Delete for good?",
          body: "The files are removed from the quarantine folder. This cannot be undone.",
          ok: "Delete", danger: true, typed: name,
        })) return;
        try {
          const { data } = await api("POST", "/api/quarantine/delete", { ids });
          toast(`Deleted ${data.tracks} track(s).`);
          setTimeout(() => location.reload(), 600);
        } catch (e) { toast(e.message, { error: true }); }
      });
    }
    for (const d of $$("details")) d.addEventListener("toggle", () => { if (d.open) loadImages(d); });
  }

  function settings() {
    const form = $("[data-settings]");
    const report = $("[data-report]");
    const values = () => {
      const out = {};
      for (const el of $$("input[name], textarea[name]", form)) {
        if (el.readOnly) continue;
        // An empty secret field keeps the saved key.
        if (el.type === "password" && !el.value) continue;
        out[el.name] = el.value;
      }
      return out;
    };
    function showReport(r) {
      report.replaceChildren();
      for (const [kind, items] of [["error", r.errors], ["warn", r.warnings]]) {
        for (const msg of items) {
          const div = document.createElement("div");
          div.className = `banner ${kind}`;
          div.textContent = msg;
          report.append(div);
        }
      }
      if (!r.errors.length && !r.warnings.length) {
        const div = document.createElement("div");
        div.className = "banner info";
        div.textContent = "Everything looks fine.";
        report.append(div);
      }
    }
    $("[data-validate]").addEventListener("click", async () => {
      const { data } = await api("POST", "/api/settings/validate", { values: values() });
      showReport(data);
    });
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      try {
        const { data } = await api("PUT", "/api/settings", { values: values() });
        showReport(data);
        toast("Settings saved.");
      } catch (err) { toast(err.message, { error: true }); }
    });
    $("[data-lidarr-test]")?.addEventListener("click", async () => {
      const out = $("[data-lidarr-result]");
      out.textContent = "Testing…";
      try {
        const { data } = await api("POST", "/api/lidarr/test", { values: values() });
        out.textContent = data.message;
      } catch (err) { out.textContent = err.message; }
    });
  }

  initCommon();
  return { dashboard, library, album, quarantine, settings, api, toast };
})();

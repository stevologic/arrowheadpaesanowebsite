(function () {
  const EMPTY_COPY = "No comments yet. Be the first.";
  const LOAD_ERROR_COPY = "Couldn't load comments.";
  const TURNSTILE_FAIL_COPY = "Spam check didn't load, refresh.";
  const HONEYPOT_FIELD = "nrt_hp_x7";

  const $ = (selector, root = document) => root.querySelector(selector);

  let memoryAdminToken = "";

  function escapeHTML(value) {
    return String(value ?? "").replace(/[&<>"']/g, (character) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;",
    }[character]));
  }

  function isLocalHost() {
    const host = window.location.hostname;
    return host === "localhost" || host === "127.0.0.1";
  }

  function formatTime(value) {
    if (!value) return "";
    const date = new Date(value);
    if (!Number.isFinite(date.getTime())) return "";
    return date.toLocaleString("en-US", {
      month: "short",
      day: "numeric",
      hour: "numeric",
      minute: "2-digit",
    });
  }

  function resolveApi(root) {
    const configured = (root.getAttribute("data-api") || "").trim();
    return configured ? configured.replace(/\/$/, "") : "";
  }

  function adminToken() {
    return memoryAdminToken;
  }

  function setStatus(node, message, isError) {
    if (!node) return;
    node.textContent = message;
    node.classList.toggle("is-error", Boolean(isError));
  }

  function setFormEnabled(root, enabled) {
    const fields = $("[data-comments-fields]", root);
    const submit = $("[data-comments-submit]", root);
    if (fields) fields.disabled = !enabled;
    if (submit) submit.disabled = !enabled;
  }

  function emptyNode() {
    const wrap = document.createElement("div");
    wrap.className = "nrt-comments__empty";
    wrap.setAttribute("data-comments-empty", "");
    wrap.innerHTML = `<p>${escapeHTML(EMPTY_COPY)}</p>`;
    return wrap;
  }

  function errorNode(onRetry) {
    const wrap = document.createElement("div");
    wrap.className = "nrt-comments__error";
    wrap.setAttribute("data-comments-error", "");
    wrap.innerHTML = `<p>${escapeHTML(LOAD_ERROR_COPY)}</p><button type="button" class="btn btn-ghost" data-comments-retry>Retry</button>`;
    wrap.querySelector("[data-comments-retry]").addEventListener("click", onRetry);
    return wrap;
  }

  function renderComment(row, canModerate) {
    const article = document.createElement("article");
    article.className = "nrt-comments__item" + (row.hidden ? " is-hidden" : "");
    article.setAttribute("data-comment-id", row.id);
    const admin = canModerate
      ? `<div class="nrt-comments__admin">
          <button type="button" data-comment-hide>${row.hidden ? "Unhide" : "Hide"}</button>
          <button type="button" data-comment-delete>Delete</button>
        </div>`
      : "";
    article.innerHTML = `
      <div class="nrt-comments__meta">
        <strong class="nrt-comments__name">${escapeHTML(row.name)}</strong>
        <time class="nrt-comments__time" datetime="${escapeHTML(row.createdAt || "")}">${escapeHTML(formatTime(row.createdAt))}</time>
        ${admin}
      </div>
      <p>${escapeHTML(row.body)}</p>
    `;
    return article;
  }

  function paintList(list, comments, canModerate) {
    list.innerHTML = "";
    const visible = (comments || []).filter((row) => canModerate || !row.hidden);
    if (!visible.length) {
      list.appendChild(emptyNode());
      return;
    }
    visible.forEach((row) => list.appendChild(renderComment(row, canModerate)));
  }

  async function api(url, options) {
    const response = await fetch(url, options);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      const error = new Error(data.error || "Request failed.");
      error.status = response.status;
      throw error;
    }
    return data;
  }

  function bindModeration(root, list, reload) {
    if (list.getAttribute("data-moderation-bound") === "1") return;
    list.setAttribute("data-moderation-bound", "1");
    list.addEventListener("click", async (event) => {
      const hideBtn = event.target.closest("[data-comment-hide]");
      const deleteBtn = event.target.closest("[data-comment-delete]");
      if (!hideBtn && !deleteBtn) return;
      const item = event.target.closest("[data-comment-id]");
      const id = item && item.getAttribute("data-comment-id");
      const token = adminToken();
      if (!id || !token) return;
      const headers = { Authorization: "Bearer " + token };
      try {
        if (deleteBtn) {
          await api(resolveApi(root) + "/comments/" + encodeURIComponent(id), { method: "DELETE", headers });
        } else {
          const hidden = item.classList.contains("is-hidden");
          const action = hidden ? "unhide" : "hide";
          await api(resolveApi(root) + "/comments/" + encodeURIComponent(id) + "/" + action, {
            method: "POST",
            headers,
          });
        }
        await reload();
      } catch (err) {
        setStatus($("[data-comments-status]", root), err.message, true);
      }
    });
  }

  function mountTurnstile(root, siteKey) {
    const slot = $("[data-turnstile-slot]", root);
    if (!slot || !siteKey || !window.turnstile) return null;
    return window.turnstile.render(slot, { sitekey: siteKey, theme: "light" });
  }

  function waitForTurnstile(root, siteKey) {
    if (!siteKey) return Promise.resolve(null);
    if (window.turnstile) return Promise.resolve(mountTurnstile(root, siteKey));
    return new Promise((resolve) => {
      let ticks = 0;
      const timer = window.setInterval(() => {
        ticks += 1;
        if (window.turnstile) {
          window.clearInterval(timer);
          resolve(mountTurnstile(root, siteKey));
        } else if (ticks > 40) {
          window.clearInterval(timer);
          resolve(null);
        }
      }, 150);
    });
  }

  function turnstileToken(siteKey, widgetId) {
    if (siteKey && window.turnstile && widgetId !== null) {
      return window.turnstile.getResponse(widgetId) || "";
    }
    if (isLocalHost() && !siteKey) return "test-pass";
    return "";
  }

  async function initThread(root) {
    const apiBase = resolveApi(root);
    const slug = (root.getAttribute("data-slug") || "").trim();
    const siteKey = (root.getAttribute("data-turnstile") || "").trim();
    const list = $("[data-comments-list]", root);
    const form = $("[data-comments-form]", root);
    const status = $("[data-comments-status]", root);
    if (!list || !form || !slug) return;

    setFormEnabled(root, false);

    let widgetId = null;
    waitForTurnstile(root, siteKey).then((id) => {
      widgetId = id;
    });

    async function reload() {
      if (!apiBase) {
        list.innerHTML = "";
        list.appendChild(errorNode(reload));
        setFormEnabled(root, false);
        return false;
      }
      try {
        const data = await api(apiBase + "/comments?slug=" + encodeURIComponent(slug));
        paintList(list, data.comments || [], Boolean(adminToken()));
        setFormEnabled(root, true);
        return true;
      } catch (_) {
        list.innerHTML = "";
        list.appendChild(errorNode(reload));
        setFormEnabled(root, false);
        return false;
      }
    }

    await reload();
    bindModeration(root, list, reload);

    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (!apiBase || ($("[data-comments-fields]", root) || {}).disabled) return;
      const token = turnstileToken(siteKey, widgetId);
      if (!token) {
        setStatus(status, TURNSTILE_FAIL_COPY, true);
        return;
      }
      const submit = $("[data-comments-submit]", form);
      const honeypot = form.elements.namedItem(HONEYPOT_FIELD);
      const name = String((form.elements.namedItem("name") || {}).value || "").trim();
      const body = String((form.elements.namedItem("body") || {}).value || "").trim();
      submit.disabled = true;
      setStatus(status, "Posting…", false);
      try {
        const data = await api(apiBase + "/comments", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            slug,
            name,
            body,
            [HONEYPOT_FIELD]: honeypot ? honeypot.value : "",
            turnstileToken: token,
          }),
        });
        if (data.comment) {
          const current = $$items(list);
          current.push(data.comment);
          paintList(list, current, Boolean(adminToken()));
        }
        form.reset();
        if (siteKey && window.turnstile && widgetId !== null) window.turnstile.reset(widgetId);
        setStatus(status, "Posted. Thanks for sitting at the table.", false);
      } catch (err) {
        setStatus(status, err.message, true);
      } finally {
        submit.disabled = false;
      }
    });
  }

  function $$items(list) {
    return Array.from(list.querySelectorAll("[data-comment-id]")).map((node) => ({
      id: node.getAttribute("data-comment-id"),
      name: ($(".nrt-comments__name", node) || {}).textContent || "",
      body: (node.querySelector("p") || {}).textContent || "",
      createdAt: (($(".nrt-comments__time", node) || {}).getAttribute("datetime")) || "",
      hidden: node.classList.contains("is-hidden"),
    }));
  }

  async function initModerate(root) {
    const apiBase = resolveApi(root);
    const list = $("[data-comments-list]", root);
    const form = $("[data-moderate-form]", root);
    const status = $("[data-comments-status]", root);
    const tokenInput = $("[data-admin-token]", root);
    if (!form || !tokenInput) return;

    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const token = tokenInput.value.trim();
      if (!token) {
        setStatus(status, "Paste the admin token first.", true);
        return;
      }
      memoryAdminToken = token;
      if (!apiBase) {
        setStatus(status, "Comments API is not configured.", true);
        return;
      }
      async function reload() {
        const data = await api(apiBase + "/comments?all=1", {
          headers: { Authorization: "Bearer " + memoryAdminToken },
        });
        paintList(list, data.comments || [], true);
        setStatus(
          status,
          data.comments && data.comments.length
            ? data.comments.length + " comment(s). Hide or delete any of them."
            : EMPTY_COPY,
          false
        );
      }

      try {
        bindModeration(root, list, reload);
        await reload();
      } catch (err) {
        setStatus(status, err.message, true);
      }
    });
  }

  document.querySelectorAll("[data-comments]").forEach(initThread);
  document.querySelectorAll("[data-moderate]").forEach(initModerate);
})();

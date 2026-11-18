/* SmartShield — shared client: API helper, auth guard, macOS dock, alert watcher, toasts */
(function () {
  const SS = (window.SS = {});
  const page = document.body.dataset.page || "";

  SS.api = async function (path, opts = {}) {
    const o = { credentials: "same-origin", headers: {}, ...opts };
    if (o.body && typeof o.body !== "string") {
      o.body = JSON.stringify(o.body);
      o.headers["Content-Type"] = "application/json";
    }
    const r = await fetch(path, o);
    if (r.status === 401 && page && page !== "login") {
      location.href = "/login";
      throw new Error("Please log in");
    }
    let data = null;
    try { data = await r.json(); } catch (e) {}
    if (!r.ok) throw new Error((data && (data.detail || data.message)) || `Request failed (${r.status})`);
    return data;
  };

  SS.esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  SS.fmtTime = (t) => new Date(t * 1000).toLocaleString("en-IN", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", second: "2-digit" });
  SS.ago = (t) => {
    const s = Math.max(0, Date.now() / 1000 - t);
    if (s < 60) return Math.floor(s) + "s ago";
    if (s < 3600) return Math.floor(s / 60) + "m ago";
    if (s < 86400) return Math.floor(s / 3600) + "h ago";
    return Math.floor(s / 86400) + "d ago";
  };

  /* ---------------- page transitions ---------------- */
  document.body.classList.add("ss-enter");
  SS.go = function (href) {
    if (matchMedia("(prefers-reduced-motion: reduce)").matches) return (location.href = href);
    document.body.classList.remove("ss-enter");
    document.body.classList.add("ss-leave");
    setTimeout(() => (location.href = href), 300);
  };
  window.addEventListener("pageshow", (e) => { if (e.persisted) document.body.classList.remove("ss-leave"); });

  /* ---------------- toasts + sound ---------------- */
  let toastBox;
  SS.toast = function (title, msg = "", kind = "ok", icon) {
    if (!toastBox) { toastBox = document.createElement("div"); toastBox.className = "ss-toasts"; document.body.appendChild(toastBox); }
    const el = document.createElement("div");
    el.className = "ss-toast " + kind;
    el.innerHTML = `<div class="t-ic">${icon || (kind === "bad" ? "!" : kind === "warn" ? "⚠" : "✓")}</div><div><b>${SS.esc(title)}</b><small>${SS.esc(msg)}</small></div><button aria-label="Close">✕</button>`;
    const kill = () => { el.classList.add("out"); setTimeout(() => el.remove(), 350); };
    el.querySelector("button").onclick = kill;
    toastBox.prepend(el);
    setTimeout(kill, kind === "bad" ? 9000 : 6000);
  };
  let actx;
  SS.beep = function (urgent) {
    try {
      actx = actx || new (window.AudioContext || window.webkitAudioContext)();
      const notes = urgent ? [880, 660, 880, 660] : [740, 988];
      notes.forEach((f, i) => {
        const o = actx.createOscillator(), g = actx.createGain();
        o.type = "sine"; o.frequency.value = f;
        const t = actx.currentTime + i * 0.16;
        g.gain.setValueAtTime(0.0001, t); g.gain.exponentialRampToValueAtTime(0.18, t + 0.02); g.gain.exponentialRampToValueAtTime(0.0001, t + 0.15);
        o.connect(g).connect(actx.destination); o.start(t); o.stop(t + 0.16);
      });
    } catch (e) {}
  };

  /* ---------------- icons ---------------- */
  const I = {
    intro: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3l8 4.5v9L12 21l-8-4.5v-9z"/><path d="M12 8c-2.4 2.4-3 5.4-.6 9.6.6-3 2.4-5.4 4.8-6.6-1.8 0-3 .6-4.2 1.8V8z" fill="currentColor" stroke="none"/></svg>',
    dashboard: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round"><rect x="3.5" y="3.5" width="7" height="9" rx="1.6"/><rect x="13.5" y="3.5" width="7" height="5" rx="1.6"/><rect x="13.5" y="11.5" width="7" height="9" rx="1.6"/><rect x="3.5" y="15.5" width="7" height="5" rx="1.6"/></svg>',
    camera: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M4 8h3l1.6-2.4h6.8L17 8h3v11H4z"/><circle cx="12" cy="13.2" r="3.6"/></svg>',
    settings: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/></svg>',
    logout: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><path d="M16 17l5-5-5-5M21 12H9"/></svg>',
  };
  SS.icons = I;

  SS.logout = async function () {
    try { await SS.api("/api/auth/logout", { method: "POST" }); } catch (e) {}
    sessionStorage.clear();
    SS.go("/login");
  };

  /* ---------------- dock ---------------- */
  function buildDock(user) {
    const dark = document.body.dataset.dock === "dark";
    const d = document.createElement("nav");
    d.className = "ss-dock" + (dark ? " dark" : "");
    const items = [
      ["intro", "/intro", "Home"],
      ["dashboard", "/dashboard", "Dashboard"],
      ["camera", "/camera", "Camera"],
      ["settings", "/settings", "Settings"],
    ];
    let html = items.map(([k, href, label]) =>
      `<a href="${href}" data-k="${k}" class="${k === page ? "active" : ""}" aria-label="${label}"><div class="ic">${I[k]}</div><span class="tip">${label}</span></a>`).join("");
    const initial = SS.esc((user?.name || "?").trim().charAt(0).toUpperCase());
    html += `<div class="sep"></div>`;
    html += `<a href="/settings#profile" data-k="profile" aria-label="Profile"><div class="ic">${user?.avatar ? `<img src="${SS.esc(user.avatar)}" alt="">` : `<span>${initial}</span>`}</div><span class="tip">${SS.esc(user?.name || "Profile")}</span></a>`;
    html += `<a href="#" data-k="logout" aria-label="Log out"><div class="ic">${I.logout}</div><span class="tip">Log out</span></a>`;
    d.innerHTML = html;
    document.body.appendChild(d);

    d.querySelectorAll("a").forEach((a) => {
      a.addEventListener("click", (e) => {
        e.preventDefault();
        a.classList.remove("bounce"); void a.offsetWidth; a.classList.add("bounce");
        if (a.dataset.k === "logout") return SS.logout();
        const href = a.getAttribute("href");
        if (a.dataset.k === page) { if (href.includes("#")) location.hash = href.split("#")[1]; return; }
        SS.go(href);
      });
    });

    // magnification (vertical dock, magnify by distance in Y)
    const links = [...d.querySelectorAll("a")];
    const vertical = () => innerWidth > 820;
    d.addEventListener("mousemove", (e) => {
      if (!vertical()) return;
      links.forEach((a) => {
        const r = a.getBoundingClientRect();
        const dist = Math.abs(e.clientY - (r.top + r.height / 2));
        const s = 1 + 0.55 * Math.max(0, 1 - dist / 110);
        a.style.transform = `scale(${s.toFixed(3)})`;
        a.style.margin = `${((s - 1) * 14).toFixed(1)}px 0`;
      });
    });
    d.addEventListener("mouseleave", () => links.forEach((a) => { a.style.transform = ""; a.style.margin = ""; }));
  }

  /* ---------------- alert watcher (all app pages) ---------------- */
  SS.settings = {};
  SS.handleState = function (st) {
    SS.settings = st.settings || SS.settings;
    const seen = +sessionStorage.getItem("ss_seen_alert") || 0;
    if (!seen) { sessionStorage.setItem("ss_seen_alert", st.max_alert_id || 0); return; }
    const fresh = (st.latest_alerts || []).filter((a) => a.id > seen).reverse();
    if (fresh.length) {
      sessionStorage.setItem("ss_seen_alert", Math.max(...fresh.map((a) => a.id)));
      fresh.forEach((a) => {
        const isFlood = a.type === "flood";
        const title = isFlood ? "Flood alert — water above limit" : `Pothole detected · ${a.severity.toUpperCase()}`;
        const msg = `${a.location} · ${new Date(a.created_at * 1000).toLocaleTimeString("en-IN")}`;
        SS.toast(title, msg, isFlood || a.severity === "high" ? "bad" : "warn", isFlood ? "🌊" : "🕳");
        if (SS.settings.sound) SS.beep(isFlood || a.severity === "high");
        if (SS.settings.notifications && "Notification" in window && Notification.permission === "granted" && document.hidden) {
          try { new Notification("SmartShield — " + title, { body: msg }); } catch (e) {}
        }
        document.dispatchEvent(new CustomEvent("ss:alert", { detail: a }));
      });
    }
  };
  async function poll() {
    try { const st = await SS.api("/api/state"); SS.handleState(st); document.dispatchEvent(new CustomEvent("ss:state", { detail: st })); } catch (e) {}
  }

  /* ---------------- boot ---------------- */
  SS.ready = (async () => {
    if (!page || page === "login") return null;
    let user = null;
    try { user = await SS.api("/api/auth/me"); } catch (e) { return null; }
    SS.user = user;
    buildDock(user);
    const ms = +(document.body.dataset.poll || 2500);
    poll(); setInterval(poll, ms);
    return user;
  })();
})();

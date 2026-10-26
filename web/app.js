/* Encore marketing site: interactive demo and sign-up forms. No dependencies. */
(function () {
  "use strict";

  var CONFIG = window.ENCORE || {};
  var reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var $ = function (sel, root) { return (root || document).querySelector(sel); };
  var $$ = function (sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); };

  /* ---------- Config-driven links ---------- */
  if (CONFIG.marketUrl) $$("[data-market]").forEach(function (a) { a.href = CONFIG.marketUrl; });

  /* ---------- Sign-up forms ---------- */
  var EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/;
  $$("[data-signup]").forEach(function (form) {
    var input = $("input[type=email]", form);
    var btn = $("button[type=submit]", form);
    var msg = $(".signup-msg", form);
    function say(text, kind) { msg.textContent = text; if (kind) msg.dataset.kind = kind; else delete msg.dataset.kind; }

    input.addEventListener("input", function () {
      if (input.getAttribute("aria-invalid") === "true" && EMAIL_RE.test(input.value.trim())) {
        input.removeAttribute("aria-invalid"); say("");
      }
    });

    form.addEventListener("submit", function (e) {
      e.preventDefault();
      var email = input.value.trim();
      if (!EMAIL_RE.test(email)) {
        input.setAttribute("aria-invalid", "true");
        say(email ? "That doesn't look like an email address. Try something like you@yourshow.com." : "Enter your email address to join the waitlist.", "error");
        input.focus();
        return;
      }
      input.removeAttribute("aria-invalid");
      if (!CONFIG.waitlistEndpoint) {
        say("Thanks. Sign-ups aren't connected in this preview yet, so nothing was saved.", null);
        return;
      }
      btn.setAttribute("aria-busy", "true"); btn.disabled = true; say("Adding you to the list…");
      fetch(CONFIG.waitlistEndpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json", "Accept": "application/json" },
        body: JSON.stringify({ email: email, source: form.dataset.source || "site" })
      }).then(function (r) {
        if (!r.ok) throw new Error("HTTP " + r.status);
        form.classList.add("done");
        say("You're on the list. We'll email you when the web app opens.", "ok");
      }).catch(function () {
        say("Something went wrong saving that. Please try again in a moment.", "error");
      }).then(function () { btn.removeAttribute("aria-busy"); btn.disabled = false; });
    });
  });

  /* ---------- Demo ---------- */
  var demo = $("#demo");
  if (!demo) return;

  var DURATION = 3492; // 58:12
  function ts(sec) { sec = Math.round(sec); var m = Math.floor(sec / 60), s = sec % 60; return (m < 10 ? "0" : "") + m + ":" + (s < 10 ? "0" : "") + s; }
  function sigText(c) { return c.signal === "peak" ? "Most replayed" : "Transcript analysis"; }

  // Illustrative clips. Fields mirror a real report entry (clipper/report.py).
  function initialClips() {
    return [
      { id: "C1", start: 1122, end: 1168, signal: "peak", peakAt: 1145, rel: 2.4, title: "The month we almost shut down",
        reason: "Opens on a direct question, tells a complete story with a turn at the end, and sits on the biggest replay peak in the episode.",
        quote: "We had eleven days of runway and nobody on the team knew." },
      { id: "C2", start: 430, end: 461, signal: "peak", peakAt: 442, rel: 1.8, title: "Why we stopped posting daily",
        reason: "A contrarian claim in the first line, then the number that backs it up. Works without any context from the rest of the episode.",
        quote: "Posting less doubled what we actually got back." },
      { id: "C3", start: 1985, end: 2032, signal: "content", rel: 1.0, title: "The pricing mistake",
        reason: "A self-contained explanation with a clear payoff line. Replay data is flat here, so the transcript carried the pick.",
        quote: "We priced for the customer we wanted, not the one we had." },
      { id: "C4", start: 2658, end: 2684, signal: "peak", peakAt: 2670, rel: 1.6, title: "Hiring your first editor",
        reason: "Practical advice delivered as a short list, with a strong closing line that stands on its own.",
        quote: "Hire for taste first. You can teach the software." },
      { id: "C5", start: 3150, end: 3201, signal: "content", rel: 1.1, title: "What I'd tell myself at episode one",
        reason: "An emotional, reflective answer that ends the episode on a quotable line. Good as a closing clip for the week.",
        quote: "Nobody is waiting for you to be ready. Start anyway." }
    ];
  }

  var state = { clips: initialClips(), sel: 0, fmt: "9:16", captions: true, safe: false, version: 1, done: {} };

  /* Replay curve (illustrative): a gentle baseline plus peaks under C1, C2 and C4. */
  var PEAKS = [[40, 0.34, 70], [442, 0.55, 26], [1145, 0.8, 34], [1520, 0.16, 90], [2010, 0.12, 120], [2670, 0.5, 26], [3180, 0.14, 80]];
  function heat(t) {
    var h = 0.2 + 0.035 * Math.sin(t / 47) + 0.025 * Math.sin(t / 13.3 + 1.7) - 0.05 * (t / DURATION);
    for (var i = 0; i < PEAKS.length; i++) { var d = (t - PEAKS[i][0]) / PEAKS[i][2]; h += PEAKS[i][1] * Math.exp(-d * d / 2); }
    return Math.max(0.04, Math.min(1, h));
  }
  var X = function (t) { return (t / DURATION) * 1000; };
  var Y = function (h) { return 128 - h * 106; };

  var svg = $("[data-graph]", demo), box = $("[data-graphbox]", demo);
  var NS = "http://www.w3.org/2000/svg";
  function el(name, attrs) { var n = document.createElementNS(NS, name); for (var k in attrs) n.setAttribute(k, attrs[k]); return n; }

  (function drawCurve() {
    var pts = [];
    for (var t = 0; t <= DURATION; t += 6) pts.push(X(t).toFixed(1) + " " + Y(heat(t)).toFixed(1));
    svg.appendChild(el("path", { d: "M0 132 L" + pts.join(" L") + " L1000 132 Z", "class": "area" }));
    svg.appendChild(el("path", { d: "M" + pts.join(" L"), "class": "line" }));
  })();
  var winLayer = el("g", {}); svg.appendChild(winLayer);
  var scan = el("rect", { x: 0, y: 0, width: 1000, height: 132, "class": "scan" }); svg.appendChild(scan);

  function drawWindows(flashId) {
    winLayer.textContent = "";
    $$(".peak-dot, .win-label", box).forEach(function (n) { n.remove(); });
    state.clips.forEach(function (c, i) {
      var x = X(c.start), w = Math.max(9, X(c.end) - x), active = i === state.sel;
      var r = el("rect", { x: x - (w - (X(c.end) - x)) / 2, y: 0, width: w, height: 132, "class": "win" + (active ? " is-active" : "") + (c.id === flashId ? " is-new" : "") });
      r.addEventListener("click", function () { select(i, true); });
      winLayer.appendChild(r);
      if (active) {
        winLayer.appendChild(el("line", { x1: x, x2: x, y1: 0, y2: 132, "class": "win-edge is-active" }));
        winLayer.appendChild(el("line", { x1: X(c.end), x2: X(c.end), y1: 0, y2: 132, "class": "win-edge is-active" }));
      }
      var lab = document.createElement("span");
      lab.className = "win-label" + (active ? " is-active" : "");
      lab.textContent = c.id;
      lab.style.left = ((x + w / 2) / 10) + "%"; lab.style.top = "0";
      box.appendChild(lab);
      if (c.signal === "peak") {
        var dot = document.createElement("span");
        dot.className = "peak-dot";
        dot.style.left = (X(c.peakAt) / 10) + "%";
        dot.style.top = "calc(16px + (100% - 16px) * " + (Y(heat(c.peakAt)) / 132).toFixed(4) + ")";
        box.appendChild(dot);
      }
    });
  }

  /* Tabs */
  var tabs = $("[data-tabs]", demo);
  function drawTabs() {
    tabs.textContent = "";
    state.clips.forEach(function (c, i) {
      var b = document.createElement("button");
      b.type = "button"; b.className = "tab"; b.setAttribute("role", "tab");
      b.id = "tab-" + c.id; b.setAttribute("aria-controls", "why-panel");
      b.setAttribute("aria-selected", i === state.sel ? "true" : "false");
      b.tabIndex = i === state.sel ? 0 : -1;
      b.innerHTML = "<b>" + c.id + "</b><span>" + ts(c.start) + "</span>" + (c.changed ? '<span class="v">v' + c.changed.v + "</span>" : "");
      b.addEventListener("click", function () { select(i, true); });
      tabs.appendChild(b);
    });
    var cur = tabs.children[state.sel];
    if (cur && tabs.scrollWidth > tabs.clientWidth) tabs.scrollTo({ left: Math.max(0, cur.offsetLeft - 24), behavior: reduceMotion ? "auto" : "smooth" });
  }
  tabs.addEventListener("keydown", function (e) {
    var n = state.clips.length, i = state.sel;
    if (e.key === "ArrowRight") i = (i + 1) % n; else if (e.key === "ArrowLeft") i = (i - 1 + n) % n;
    else if (e.key === "Home") i = 0; else if (e.key === "End") i = n - 1; else return;
    e.preventDefault(); select(i, true); tabs.children[i].focus();
  });

  /* Why panel */
  var why = $("[data-why]", demo);
  why.id = "why-panel"; why.setAttribute("role", "tabpanel");
  function esc(s) { return String(s).replace(/[&<>"]/g, function (ch) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[ch]; }); }
  function drawWhy(animate) {
    var c = state.clips[state.sel];
    why.setAttribute("aria-labelledby", "tab-" + c.id);
    var detail = c.signal === "peak"
      ? "YouTube Most replayed peaks at " + ts(c.peakAt) + " inside this clip (" + c.rel.toFixed(1) + "x the video's typical replay level)"
      : "Replay level here is about typical (" + c.rel.toFixed(1) + "x); chosen by transcript analysis";
    why.innerHTML =
      '<div class="why-top"><span class="why-id">' + c.id + '</span><span class="sig' + (c.signal === "peak" ? " peak" : "") + '">' + sigText(c) + "</span>" +
      (c.changed ? '<span class="changed">Changed in v' + c.changed.v + ": " + esc(c.changed.what) + "</span>" : "") + "</div>" +
      '<p class="why-title">' + esc(c.title) + "</p>" +
      '<dl class="facts"><div><dt>Source time</dt><dd>' + ts(c.start) + " → " + ts(c.end) + "</dd></div><div><dt>Length</dt><dd>" + Math.round(c.end - c.start) + " s · " + state.fmt + "</dd></div></dl>" +
      '<div class="why-block"><p class="label">Why this moment</p><p>' + esc(c.reason) + "</p></div>" +
      '<div class="why-block"><p class="label">Key line</p><p class="why-quote">“' + esc(c.quote) + "”</p></div>" +
      '<p class="why-detail">Signal: ' + esc(detail) + "</p>";
    if (animate && !reduceMotion) { why.classList.remove("is-updating"); void why.offsetWidth; why.classList.add("is-updating"); }
  }

  /* Preview frame and captions */
  var frame = $("[data-frame]", demo), cap = $("[data-caption]", demo), capTimer = null;
  function groups(words) {
    var out = [], i = 0;
    while (i < words.length) { var left = words.length - i, n = left <= 4 ? left : 3; out.push([i, i + n]); i += n; }
    return out;
  }
  function playCaptions() {
    clearInterval(capTimer);
    var c = state.clips[state.sel], words = c.quote.split(/\s+/), g = groups(words), k = 0;
    function render() {
      var gi = 0; while (gi < g.length - 1 && k >= g[gi][1]) gi++;
      var html = [];
      for (var j = g[gi][0]; j < g[gi][1]; j++) html.push(j === k ? '<span class="w-on">' + esc(words[j]) + "</span>" : esc(words[j]));
      cap.innerHTML = html.join(" ");
    }
    render();
    if (reduceMotion) return;
    capTimer = setInterval(function () { k = (k + 1) % (words.length + 3); if (k < words.length) render(); else cap.innerHTML = ""; }, 380);
  }
  function drawFrame() {
    var c = state.clips[state.sel];
    frame.dataset.fmt = state.fmt;
    frame.classList.toggle("no-captions", !state.captions);
    frame.classList.toggle("show-safe", state.safe);
    frame.classList.toggle("big-captions", !!c.bigCaptions);
  }

  function select(i, animate) {
    state.sel = i;
    drawWindows(); drawTabs(); drawWhy(animate); drawFrame(); playCaptions();
  }

  /* Controls */
  var fmtGroup = $("[data-formats]", demo);
  var fmtButtons = $$("button", fmtGroup);
  function setFmt(f) {
    state.fmt = f;
    fmtButtons.forEach(function (b) { var on = b.dataset.fmt === f; b.setAttribute("aria-checked", on ? "true" : "false"); b.tabIndex = on ? 0 : -1; });
    drawFrame(); drawWhy(false);
  }
  fmtButtons.forEach(function (b, idx) {
    b.addEventListener("click", function () { setFmt(b.dataset.fmt); });
    b.addEventListener("keydown", function (e) {
      var d = e.key === "ArrowRight" || e.key === "ArrowDown" ? 1 : e.key === "ArrowLeft" || e.key === "ArrowUp" ? -1 : 0;
      if (!d) return; e.preventDefault();
      var nb = fmtButtons[(idx + d + fmtButtons.length) % fmtButtons.length]; setFmt(nb.dataset.fmt); nb.focus();
    });
  });
  setFmt("9:16");
  $("[data-captions]", demo).addEventListener("change", function (e) { state.captions = e.target.checked; drawFrame(); });
  $("[data-safe]", demo).addEventListener("change", function (e) { state.safe = e.target.checked; if (state.safe && state.fmt !== "9:16") setFmt("9:16"); drawFrame(); });

  /* Change requests: only the named clip is re-made */
  var status = $("[data-status]", demo), log = $("[data-log]", demo), resetBtn = $('[data-rev="reset"]', demo);
  function setStatus(text, s) { status.textContent = text; status.dataset.state = s || ""; }
  var REVISIONS = {
    "c2-earlier": { id: "C2", what: "starts one sentence earlier", apply: function (c) { c.start = 423; } },
    "c4-replace": { id: "C4", what: "replaced with a new moment", apply: function (c) {
      c.start = 1660; c.end = 1696; c.signal = "content"; c.rel = 1.1; delete c.peakAt;
      c.title = "Saying no to a big sponsor";
      c.reason = "A clear decision with real stakes and a one-line takeaway. Chosen away from the moments already delivered.";
      c.quote = "The money was real. The fit wasn't.";
    } },
    "c3-captions": { id: "C3", what: "captions 25% bigger", apply: function (c) { c.bigCaptions = true; } }
  };
  var busy = false;
  $$("[data-rev]", demo).forEach(function (b) {
    b.addEventListener("click", function () {
      var key = b.dataset.rev;
      if (key === "reset") { reset(); return; }
      if (busy || state.done[key]) return;
      var rev = REVISIONS[key], idx = state.clips.findIndex(function (c) { return c.id === rev.id; });
      busy = true; b.disabled = true;
      setStatus("Re-making " + rev.id + "…", "busy");
      log.innerHTML = "<b>" + rev.id + "</b>: " + esc(rev.what) + " … the other clips are left as they are.";
      setTimeout(function () {
        state.version += 1; state.done[key] = true;
        var c = state.clips[idx]; rev.apply(c); c.changed = { v: state.version, what: rev.what };
        var others = state.clips.filter(function (o) { return o.id !== rev.id; }).map(function (o) { return o.id; });
        setStatus("Version " + state.version + " delivered", "done");
        log.innerHTML = "<b>v" + state.version + "</b> · Re-made " + rev.id + " (" + esc(rev.what) + "). " + others.join(", ") + " unchanged.";
        select(idx, true); drawWindows(rev.id);
        resetBtn.hidden = false; busy = false;
      }, reduceMotion ? 0 : 1100);
    });
  });
  function reset() {
    state.clips = initialClips(); state.version = 1; state.done = {}; state.sel = 0;
    $$("[data-rev]", demo).forEach(function (b) { b.disabled = false; });
    resetBtn.hidden = true; log.textContent = ""; setStatus("5 clips ready", "done");
    select(0, true);
  }

  /* Intro: scan across the graph, then reveal the clips */
  function intro() {
    if (reduceMotion) { scan.remove(); setStatus("5 clips ready", "done"); select(0, false); return; }
    select(0, false);
    winLayer.style.opacity = "0"; $$(".peak-dot, .win-label", box).forEach(function (n) { n.style.opacity = "0"; });
    var steps = [["Fetching video", 0], ["Reading Most replayed data", 600], ["Transcribing and picking moments", 1300]];
    steps.forEach(function (s) { setTimeout(function () { setStatus(s[0] + "…", "busy"); }, s[1]); });
    var t0 = null, dur = 1800;
    function frameStep(now) {
      if (t0 === null) t0 = now;
      var p = Math.min(1, (now - t0) / dur), x = 1000 * (1 - Math.pow(1 - p, 2));
      scan.setAttribute("x", x); scan.setAttribute("width", Math.max(0, 1000 - x));
      if (p < 1) requestAnimationFrame(frameStep);
      else {
        scan.remove(); setStatus("5 clips ready", "done");
        winLayer.style.transition = "opacity .5s ease"; winLayer.style.opacity = "1";
        $$(".peak-dot, .win-label", box).forEach(function (n) { n.style.opacity = "1"; });
      }
    }
    requestAnimationFrame(frameStep);
  }

  if ("IntersectionObserver" in window && !reduceMotion) {
    var io = new IntersectionObserver(function (entries) {
      if (entries.some(function (en) { return en.isIntersecting; })) { io.disconnect(); intro(); }
    }, { threshold: 0 });
    io.observe(demo);
  } else { intro(); }

  document.addEventListener("visibilitychange", function () { if (document.hidden) clearInterval(capTimer); else playCaptions(); });
})();

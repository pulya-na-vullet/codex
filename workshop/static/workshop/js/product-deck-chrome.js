(function () {
  var csrfEl = document.querySelector("input[name=csrfmiddlewaretoken]");
  var csrf = (csrfEl && csrfEl.value) || "";
  var crmBtn = document.getElementById("itmDeckTvCrm");
  var adsBtn = document.getElementById("itmDeckTvAds");
  var lastSent = "";
  var live = false;
  var uploading = false;
  var dirty = true;
  var pumpTimer = null;
  var h2cLoading = false;
  var modeNow = "ads";

  function cookieCsrf() {
    var m = document.cookie.match(/(?:^|; )csrftoken=([^;]+)/);
    return m ? decodeURIComponent(m[1]) : "";
  }

  function token() {
    return csrf || cookieCsrf();
  }

  function here() {
    return (location.pathname || "/") + (location.search || "");
  }

  function setStatus(data) {
    var ads = !(data && data.mode === "crm");
    modeNow = ads ? "ads" : "crm";
    if (crmBtn) crmBtn.classList.toggle("is-on", !ads);
    if (adsBtn) adsBtn.classList.toggle("is-on", ads);
  }

  async function send(mode, follow) {
    var path = mode === "crm" ? here() : "";
    var key = mode + "|" + path;
    if (follow && key === lastSent) return;
    var body = { mode: mode };
    if (mode === "crm") body.path = here();
    if (follow) body.follow = true;
    var r = await fetch("/admin-panel/tv-display", {
      method: "POST",
      credentials: "same-origin",
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": token(),
        Accept: "application/json"
      },
      body: JSON.stringify(body)
    });
    var data = await r.json();
    lastSent = key;
    setStatus(data);
    return data;
  }

  function applyMode(mode) {
    lastSent = "";
    if (mode === "crm") {
      send("crm", false).then(function () { startPump(); });
    } else {
      stopPump();
      send("ads", false);
    }
  }

  function loadH2C(done) {
    if (window.html2canvas) { done(); return; }
    if (h2cLoading) return;
    h2cLoading = true;
    var s = document.createElement("script");
    s.src = "/static/workshop/js/html2canvas.min.js";
    s.onload = function () { h2cLoading = false; done(); };
    s.onerror = function () { h2cLoading = false; };
    document.head.appendChild(s);
  }

  function captureOnce() {
    if (!live || uploading || document.hidden || !window.html2canvas) return;
    uploading = true;
    var w = Math.max(1, window.innerWidth || 1280);
    var h = Math.max(1, window.innerHeight || 720);
    var dpr = window.devicePixelRatio || 1;
    var scale = Math.min(dpr, 2, 1920 / w);
    if (scale < 1) scale = Math.min(1, 1920 / w);
    window.html2canvas(document.body, {
      logging: false,
      backgroundColor: null,
      useCORS: true,
      foreignObjectRendering: false,
      scale: scale,
      width: w,
      height: h,
      x: window.pageXOffset || 0,
      y: window.pageYOffset || 0,
      scrollX: -(window.pageXOffset || 0),
      scrollY: -(window.pageYOffset || 0),
      windowWidth: w,
      windowHeight: h,
      ignoreElements: function (el) {
        return !!(el && el.getAttribute && el.getAttribute("data-html2canvas-ignore"));
      }
    }).then(function (canvas) {
      return new Promise(function (resolve) {
        canvas.toBlob(resolve, "image/jpeg", 0.88);
      });
    }).then(function (blob) {
      if (!blob || !live) return;
      return fetch("/admin-panel/tv-cast-frame", {
        method: "POST",
        credentials: "same-origin",
        headers: { "X-CSRFToken": token(), "Content-Type": "image/jpeg" },
        body: blob
      });
    }).catch(function () {}).then(function () {
      uploading = false;
    });
  }

  function startPump() {
    live = true;
    dirty = true;
    loadH2C(function () { dirty = true; captureOnce(); });
    if (pumpTimer) return;
    pumpTimer = setInterval(function () {
      if (!live) return;
      if (dirty) {
        dirty = false;
        captureOnce();
      }
    }, 450);
    setInterval(function () { if (live) dirty = true; }, 1800);
  }

  function stopPump() {
    live = false;
  }

  function markDirty() { if (live) dirty = true; }

  ["click", "keyup", "scroll", "change", "input"].forEach(function (ev) {
    window.addEventListener(ev, markDirty, true);
  });
  window.addEventListener("resize", markDirty);

  async function refresh() {
    try {
      var r = await fetch("/tv/state", { credentials: "same-origin", cache: "no-store" });
      var data = await r.json();
      setStatus(data);
      if (data && data.mode === "crm") {
        await send("crm", true);
        startPump();
      } else {
        stopPump();
      }
    } catch (e) {}
  }

  if (crmBtn) crmBtn.addEventListener("click", function () { applyMode("crm"); });
  if (adsBtn) adsBtn.addEventListener("click", function () { applyMode("ads"); });
  refresh();
  setInterval(refresh, 8000);
})();

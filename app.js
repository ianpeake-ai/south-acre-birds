// The Birds of South Acre - reads data/today.json, which the Pi publishes.
(function () {
  "use strict";
  var $ = function (id) { return document.getElementById(id); };
  var player = $("player");
  var playingBtn = null;

  function plural(n, one, many) { return n === 1 ? one : many; }

  function heardLine(b) {
    var parts = [];
    if (b.first && b.last && b.first !== b.last) parts.push(b.first + "–" + b.last);
    else if (b.first || b.last) parts.push(b.first || b.last);
    if (b.n) parts.push("heard " + b.n + plural(b.n, " time", " times"));
    return parts.join(" · ");
  }

  function stopPlaying() {
    if (playingBtn) playingBtn.classList.remove("playing");
    playingBtn = null;
  }
  player.addEventListener("ended", stopPlaying);
  player.addEventListener("pause", stopPlaying);
  player.addEventListener("error", stopPlaying);

  function togglePlay(btn, src) {
    if (playingBtn === btn) { player.pause(); return; }
    player.pause();
    player.src = src;
    var p = player.play();
    if (p && p.catch) p.catch(stopPlaying);
    btn.classList.add("playing");
    playingBtn = btn;
  }

  function row(b) {
    var node = $("bird-row").content.firstElementChild.cloneNode(true);
    var img = node.querySelector(".bird-art img");
    if (b.img) { img.src = b.img; img.alt = b.com; } else { img.remove(); }
    node.querySelector(".com").textContent = b.com;
    node.querySelector(".sci").textContent = b.sci;
    node.querySelector(".meta").textContent = heardLine(b);
    if (b.first_ever) node.querySelector(".first-ever").hidden = false;
    if (b.clip) {
      var btn = node.querySelector(".play");
      btn.hidden = false;
      btn.querySelector(".visually-hidden").textContent = "Play the " + b.com;
      btn.addEventListener("click", function () { togglePlay(btn, b.clip); });
    }
    return node;
  }

  function fill(listId, sectionId, birds) {
    var list = $(listId);
    list.textContent = "";
    birds.forEach(function (b) { list.appendChild(row(b)); });
    $(sectionId).hidden = birds.length === 0;
  }

  function render(d, fromCache) {
    var all = d.species || [];
    var wild = all.filter(function (b) { return !b.resident; });
    var residents = all.filter(function (b) { return b.resident; });

    var todayISO = new Date().toLocaleDateString("en-CA", { timeZone: "Europe/London" });
    var stale = d.date !== todayISO;
    $("date").textContent = d.date_label || "";

    if (all.length === 0) {
      $("status").textContent = stale ? "Nothing new from the garden yet." : "Listening… no birds yet today.";
    } else {
      var n = wild.length;
      $("status").textContent = n === 0
        ? "Only the residents so far today."
        : n + plural(n, " bird", " birds") + (stale ? " heard" : " heard so far today");
    }

    var notice = $("notice");
    if (d.sample) {
      notice.textContent = "Sample birds for now. The garden starts sending its own once the listener is set up.";
      notice.hidden = false;
    } else if (stale) {
      notice.textContent = "This is the last day the garden sent: " + d.date_label +
        ". The listener may be offline.";
      notice.hidden = false;
    } else if (fromCache) {
      notice.textContent = "You're offline, so this is the last update this phone saw.";
      notice.hidden = false;
    } else {
      notice.hidden = true;
    }

    var hasCollage = all.length > 0 && d.collage;
    $("collage").hidden = !hasCollage;
    if (hasCollage) $("collage-img").src = d.collage;

    var showNest = all.length === 0 && d.nest;
    $("nest").hidden = !showNest;
    if (showNest) $("nest-img").src = d.nest;

    fill("birds", "heard", wild);
    fill("resident-birds", "residents", residents);
    $("heard").querySelector(".section-title span").textContent = stale ? "Heard that day" : "Heard today";

    $("updated").textContent = d.updated_label ? "Updated " + d.updated_label : "";
  }

  // Full-screen collage
  var box = $("lightbox");
  $("collage-open").addEventListener("click", function () {
    $("lightbox-img").src = $("collage-img").src;
    if (box.showModal) box.showModal();
  });
  $("lightbox-close").addEventListener("click", function () { box.close(); });
  box.addEventListener("click", function (e) { if (e.target === box || e.target.tagName === "IMG") box.close(); });

  var CACHE_KEY = "southacre-last";
  function load() {
    fetch("data/today.json?t=" + Date.now(), { cache: "no-store" })
      .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
      .then(function (d) {
        try { localStorage.setItem(CACHE_KEY, JSON.stringify(d)); } catch (e) {}
        render(d, false);
      })
      .catch(function () {
        var d = null;
        try { d = JSON.parse(localStorage.getItem(CACHE_KEY) || "null"); } catch (e) {}
        if (d) render(d, true);
        else $("status").textContent = "Can't reach the garden just now.";
      });
  }

  load();
  // refresh when the app comes back to the front, and every 10 minutes while open
  document.addEventListener("visibilitychange", function () { if (!document.hidden) load(); });
  setInterval(function () { if (!document.hidden) load(); }, 10 * 60 * 1000);

  if ("serviceWorker" in navigator) {
    window.addEventListener("load", function () { navigator.serviceWorker.register("sw.js"); });
  }
})();

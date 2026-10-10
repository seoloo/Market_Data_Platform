// 오르빗 실거래 홈 — 60 초마다 숫자 · 거래 칸(data-live)만 새로 고친다.
// 화면 전체를 다시 불러오지 않으므로 스크롤 · 열린 팝업 · 고른 기간이 그대로 남는다.
// 같은 서버의 /live 만 읽고(읽기 전용), 열린 팝업 안의 칸은 건드리지 않는다.
"use strict";
(function () {
  var EVERY = 60000;
  function inOpenPopover(el) {
    try {
      return !!el.closest(":popover-open");
    } catch (e) {
      return false;
    }
  }
  function mark(failed) {
    var n = document.querySelector('[data-live="updated"]');
    if (n) n.classList.toggle("orbit-refresh-failed", failed);
  }
  function tick() {
    if (document.hidden) return;
    fetch("/live", { credentials: "same-origin", cache: "no-store" })
      .then(function (r) {
        if (!r.ok) throw new Error(String(r.status));
        return r.text();
      })
      .then(function (html) {
        var doc = new DOMParser().parseFromString(html, "text/html");
        doc.querySelectorAll("[data-live]").forEach(function (fresh) {
          var key = fresh.getAttribute("data-live");
          var cur = document.querySelector('[data-live="' + CSS.escape(key) + '"]');
          if (!cur || inOpenPopover(cur) || cur.innerHTML === fresh.innerHTML) return;
          cur.innerHTML = fresh.innerHTML;
          cur.className = fresh.className;
        });
        mark(false);
      })
      .catch(function () {
        mark(true);
      });
  }
  setInterval(tick, EVERY);
  document.addEventListener("visibilitychange", function () {
    if (!document.hidden) tick();
  });
})();

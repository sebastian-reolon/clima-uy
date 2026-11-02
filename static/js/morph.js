// The site's only motion: on load, today's river profile grows out of the one
// from 3 days ago, so the flood wave can be seen moving downstream.
// The static SVG already shows today; without JS, or with reduced motion,
// nothing changes.
(function () {
  "use strict";
  if (!window.matchMedia || window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
  if (!window.requestAnimationFrame) return;

  var DURATION = 1200;
  var NUM = /-?\d+(?:\.\d+)?/g;

  function ease(t) {
    return t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;
  }

  function morph(path) {
    var end = path.getAttribute("d");
    var start = path.getAttribute("data-d0");
    if (!end || !start) return;
    var a = start.match(NUM);
    var b = end.match(NUM);
    // Both paths come from the same points; refuse anything else.
    if (!a || !b || a.length !== b.length || start.replace(NUM, "#") !== end.replace(NUM, "#")) return;
    var from = a.map(Number);
    var to = b.map(Number);
    var shape = end.split(NUM);

    function frame(t) {
      var out = shape[0];
      for (var i = 0; i < to.length; i++) {
        out += (from[i] + (to[i] - from[i]) * t).toFixed(1) + shape[i + 1];
      }
      return out;
    }

    path.setAttribute("d", start);
    var t0 = null;
    function step(now) {
      if (t0 === null) t0 = now;
      var t = Math.min(1, (now - t0) / DURATION);
      path.setAttribute("d", t < 1 ? frame(ease(t)) : end);
      if (t < 1) window.requestAnimationFrame(step);
    }
    window.requestAnimationFrame(step);
  }

  function run() {
    var paths = document.querySelectorAll("svg.chart-profile path.today[data-d0]");
    for (var i = 0; i < paths.length; i++) morph(paths[i]);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", run);
  } else {
    run();
  }
})();

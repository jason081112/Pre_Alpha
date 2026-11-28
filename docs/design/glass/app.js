/* Pre_Alpha 玻璃版：只渲染进度区块。
   数据与个人主页共用同一份快照；离线时退回仓库内副本。 */
(function () {
  "use strict";

  var root = document.getElementById("prealpha");
  if (!root) return;

  var DATA_URL = "https://jason081112.github.io/data/pre-alpha.json";
  var LOCAL_DATA_URL = "../data/pre-alpha.json";

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
  }

  function beijingTime(iso) {
    var date = new Date(iso);
    if (isNaN(date.getTime())) return "";
    var shifted = new Date(date.getTime() + 8 * 3600 * 1000);
    function pad(n) { return (n < 10 ? "0" : "") + n; }
    return shifted.getUTCFullYear() + "-" + pad(shifted.getUTCMonth() + 1) + "-" +
      pad(shifted.getUTCDate()) + " " + pad(shifted.getUTCHours()) + ":" +
      pad(shifted.getUTCMinutes());
  }

  function relativeTime(iso) {
    if (!iso) return "";
    var then = new Date(iso).getTime();
    if (isNaN(then)) return "";
    var diff = Math.max(0, Date.now() - then);
    var minutes = Math.floor(diff / 60000);
    if (minutes < 1) return "刚刚";
    if (minutes < 60) return minutes + " 分钟前";
    var hours = Math.floor(minutes / 60);
    if (hours < 24) return hours + " 小时前";
    var days = Math.floor(hours / 24);
    if (days < 30) return days + " 天前";
    var months = Math.floor(days / 30);
    if (months < 12) return months + " 个月前";
    return Math.floor(months / 12) + " 年前";
  }

  function progressBar(percent, big) {
    var bar = el("div", "pa-bar" + (big ? " pa-bar-lg" : ""));
    var inner = el("div", "pa-fill");
    inner.style.width = Math.max(0, Math.min(100, Number(percent) || 0)) + "%";
    bar.appendChild(inner);
    return bar;
  }

  function render(data) {
    var fragment = document.createDocumentFragment();
    var overall = data.overall || {};

    var head = el("div", "pa-head");
    var headline = el("div", "pa-headline");
    headline.appendChild(el("span", "pa-pct", overall.percent + "%"));
    headline.appendChild(el("span", "pa-frac",
      overall.closed + " / " + overall.total + " 项已完成"));
    head.appendChild(headline);
    if (data.source && data.source.project) {
      var link = el("a", "pa-link", "看板 →");
      link.href = data.source.project;
      link.target = "_blank";
      link.rel = "noopener";
      head.appendChild(link);
    }
    fragment.appendChild(head);
    fragment.appendChild(progressBar(overall.percent, true));

    if (data.status) {
      var dots = { "Todo": "todo", "In Progress": "prog", "Done": "done" };
      var order = ["Todo", "In Progress", "Done"];
      var names = order.filter(function (k) { return data.status[k] != null; });
      Object.keys(data.status).forEach(function (k) {
        if (names.indexOf(k) === -1) names.push(k);
      });
      var statusRow = el("div", "pa-status");
      names.forEach(function (name) {
        var chip = el("span", "pa-chip");
        chip.appendChild(el("i", "pa-dot " + (dots[name] || "todo")));
        chip.appendChild(document.createTextNode(name + " " + data.status[name]));
        statusRow.appendChild(chip);
      });
      fragment.appendChild(statusRow);
    }

    if (data.phases && data.phases.length) {
      var phases = el("div", "pa-phases");
      data.phases.forEach(function (phase) {
        var row = el("div", "pa-phase");
        row.appendChild(el("span", "pa-phase-name", phase.label));
        row.appendChild(progressBar(phase.percent, false));
        row.appendChild(el("span", "pa-phase-num", phase.closed + "/" + phase.total));
        phases.appendChild(row);
      });
      fragment.appendChild(phases);
    }

    if (data.recent && data.recent.length) {
      var recent = el("div", "pa-recent");
      recent.appendChild(el("div", "pa-recent-title", "最近活动"));
      var list = el("ul", "pa-recent-list");
      data.recent.forEach(function (item) {
        var li = el("li", "pa-recent-item");
        var closed = item.state === "closed";
        li.appendChild(el("span", "pa-recent-state " + item.state, closed ? "✓" : "●"));
        var anchor = el("a", "pa-recent-link", item.title);
        anchor.href = item.url;
        anchor.target = "_blank";
        anchor.rel = "noopener";
        anchor.title = item.title;
        li.appendChild(anchor);
        li.appendChild(el("time", "pa-recent-time", relativeTime(item.updated_at)));
        list.appendChild(li);
      });
      recent.appendChild(list);
      fragment.appendChild(recent);
    }

    if (data.generated_at) {
      fragment.appendChild(el("div", "pa-updated",
        "数据更新于 " + beijingTime(data.generated_at) + " 北京时间 · 每 15 分钟自动同步"));
    }

    root.textContent = "";
    root.appendChild(fragment);
  }

  function showFallback() {
    root.textContent = "";
    var p = el("p", "pa-fallback");
    p.appendChild(document.createTextNode("进度数据暂不可用，可直接查看 "));
    var board = el("a", "", "GitHub 看板");
    board.href = "https://github.com/users/jason081112/projects/1";
    board.target = "_blank";
    board.rel = "noopener";
    p.appendChild(board);
    p.appendChild(document.createTextNode(" 或 "));
    var issues = el("a", "", "仓库 issue");
    issues.href = "https://github.com/jason081112/Pre_Alpha/issues";
    issues.target = "_blank";
    issues.rel = "noopener";
    p.appendChild(issues);
    p.appendChild(document.createTextNode("。"));
    root.appendChild(p);
  }

  function load(url) {
    return fetch(url, { cache: "no-cache" }).then(function (res) {
      if (!res.ok) throw new Error("HTTP " + res.status);
      return res.json();
    });
  }

  load(DATA_URL)
    .catch(function (err) {
      console.warn("[prealpha] 根站快照不可用，改用本地副本:", err);
      return load(LOCAL_DATA_URL);
    })
    .then(render)
    .catch(function (err) {
      console.error("[prealpha] 加载失败:", err);
      showFallback();
    });
})();

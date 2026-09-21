"use strict";
/*
 * 页面 DOM 冒烟桩（功能 013 / T1314 辅助件，非 pytest 收集对象）。
 *
 * 为什么需要它：两视图是 vanilla JS + DOM 渲染，Python 侧只能做静态断言；
 * 本桩用 node（CI runner 自带）提供极简 DOM / fetch 环境，真实执行 web/static/app.js：
 *   - 起真实 fetch（相对路径解析到本地只读服务）→ 页面真的取数；
 *   - 真实跑视图初始化与交互（点击树行 / 切换 Agent）→ 页面真的渲染；
 *   - 结果以 JSON 打到 stdout，由 pytest 断言（node 缺失时该测试跳过）。
 *
 * 用法：node page_dom_smoke.js <html> <app.js> <baseUrl> <tree|board> [agent] [static]
 */

const fs = require("fs");
const http = require("http");
const https = require("https");
const vm = require("vm");

const [, , htmlPath, appPath, baseUrl, mode, ...rest] = process.argv;
const STATIC_MODE = rest.includes("static"); // 静态导出模式（页面读 data/*.json）
const agent = rest.find((value) => value !== "static") || ""; // 可选：看板指定分线

class El {
  constructor(tag) {
    this.tag = tag;
    this.children = [];
    this.attrs = {};
    this.listeners = {};
    this.textContent = "";
    this.hidden = false;
    this.value = "";
    this.className = "";
    this.classList = {
      _set: new Set(),
      add(...names) {
        names.forEach((name) => this._set.add(name));
      },
      remove(...names) {
        names.forEach((name) => this._set.delete(name));
      },
      contains(name) {
        return this._set.has(name);
      },
    };
  }

  appendChild(child) {
    this.children.push(child);
    return child;
  }

  replaceChildren(...children) {
    this.children = children;
  }

  setAttribute(key, value) {
    this.attrs[key] = value;
  }

  addEventListener(type, handler) {
    (this.listeners[type] = this.listeners[type] || []).push(handler);
  }

  get firstChild() {
    return this.children[0] || null;
  }

  /** 元素及其子树的可见文本（断言用）。 */
  text() {
    const own = this.textContent || "";
    const nested = this.children.map((child) => child.text()).join(" ");
    return `${own} ${nested}`.trim();
  }
}

function collectIds(html, prefix) {
  const ids = {};
  const pattern = /id="([^"]+)"/g;
  let match = pattern.exec(html);
  while (match) {
    ids[prefix + match[1]] = new El("element");
    match = pattern.exec(html);
  }
  return ids;
}

const elements = collectIds(fs.readFileSync(htmlPath, "utf8"), "");

/**
 * fetch 桩：用 node 的 http 模块直发 GET（Connection: close，每次一条连接）。
 * 为什么不用全局 fetch（undici）：其连接池/异步机制在密集测试下偶发让事件循环长时间不
 * 回到定时器阶段（实测 waitFor 只轮询到 1 次、34s 后才继续），导致冒烟假失败；
 * http.request + 一次性连接完全可预测，且页面本来只发 GET。
 */
function httpFetch(url) {
  return new Promise((resolve, reject) => {
    const target = new URL(url, baseUrl);
    const transport = target.protocol === "https:" ? https : http;
    const request = transport.request(
      {
        hostname: target.hostname,
        port: target.port,
        path: `${target.pathname}${target.search}`,
        method: "GET",
        agent: false,
        headers: { Connection: "close", Accept: "application/json" },
      },
      (response) => {
        const chunks = [];
        response.on("data", (chunk) => chunks.push(chunk));
        response.on("end", () => {
          const body = Buffer.concat(chunks).toString("utf-8");
          resolve({
            ok: response.statusCode >= 200 && response.statusCode < 300,
            status: response.statusCode,
            json: () => Promise.resolve(JSON.parse(body)),
            text: () => Promise.resolve(body),
          });
        });
      },
    );
    request.on("error", reject);
    request.end();
  });
}

const sandbox = {
  console,
  URLSearchParams,
  Date,
  JSON,
  Math,
  Number,
  Object,
  Array,
  String,
  Boolean,
  Set,
  Map,
  Promise,
  Error,
  isNaN,
  encodeURIComponent,
  decodeURIComponent,
  setTimeout,
  clearTimeout,
  document: {
    getElementById: (id) => elements[id] || null,
    createElement: (tag) => new El(tag),
    createElementNS: (_namespace, tag) => new El(tag),
    addEventListener: (type, handler) => {
      if (type === "DOMContentLoaded") {
        sandbox.__domReady = handler;
      }
    },
  },
  window: { location: { search: "" }, CINEFLOW_STATIC: STATIC_MODE },
  fetch: (url) => httpFetch(url),
};

vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(appPath, "utf8"), sandbox, { filename: appPath });

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

const seenErrors = [];
const timings = [];
let traceStarted = monotonicMs();

/**
 * 单调时钟（process.hrtime）：超时判定必须用单调时间。
 * 为什么不能用 Date.now()：WSL2 的墙钟会因宿主休眠/校时跳变（实测跳 ~34s），
 * 一跳就让"已超时"判据成立——出现"页面明明在正常渲染却被判超时"的假失败。
 */
function monotonicMs() {
  return Number(process.hrtime.bigint() / 1000000n);
}

function rememberError() {
  const box = elements["error-box"];
  if (box && !box.hidden && box.textContent && !seenErrors.includes(box.textContent)) {
    seenErrors.push(box.textContent); // 页面报错可能被后续 clearError 清掉：留痕供断言/排障
  }
}

async function waitFor(predicate, { timeout = 30000, label = "" } = {}) {
  const started = monotonicMs();
  const deadline = started + timeout;
  let polls = 0;
  while (monotonicMs() < deadline) {
    rememberError();
    polls += 1;
    if (predicate()) {
      timings.push({ label, ms: monotonicMs() - started, polls });
      return true;
    }
    await sleep(20);
  }
  timings.push({ label, ms: monotonicMs() - started, polls, timeout: true });
  throw new Error(`等待超时：${label}（轮询 ${polls} 次 / ${monotonicMs() - started}ms）`);
}

function clickFirstRow(tbody) {
  const row = tbody.children[0];
  if (row && row.listeners.click) {
    row.listeners.click[0]();
    return true;
  }
  return false;
}

function dump() {
  const body = (id) => elements[id] || new El("missing");
  const summary = {
    mode,
    error: body("error-box").hidden ? "" : body("error-box").text(),
    empty_state: body("empty-state").hidden ? "" : body("empty-state").text(),
    tree_rows: body("tree-table-body").children.length,
    node_rows: body("node-table-body").children.length,
    tree_page_info: body("tree-page-info").textContent,
    node_page_info: body("node-page-info").textContent,
    node_detail: body("node-detail-body").text(),
    lineage: {
      label: body("lineage-version-label").textContent,
      parents: body("lineage-parents").text(),
      trees: body("lineage-trees").text(),
      children: body("lineage-children").text(),
      cross: body("lineage-cross-project").textContent,
    },
    reward_chart_children: body("reward-chart").children.length,
    collapse_note: body("collapse-note").textContent,
    cost_note: body("cost-total").textContent,
    cost_chart_children: body("cost-chart").children.length,
    calibration_panel: body("calibration-panel").text(),
    drift_panel: body("drift-panel").text(),
    agent_options: body("agent-picker").children.length,
    mode_hint: body("mode-hint").textContent,
    errors_seen: seenErrors,
    timings,
    total_ms: monotonicMs() - traceStarted,
  };
  return summary;
}

(async () => {
  if (!sandbox.__domReady) {
    throw new Error("app.js 未注册 DOMContentLoaded 处理器");
  }
  traceStarted = monotonicMs();
  const watchdog = setInterval(() => {
    process.stderr.write(
      `[dom-smoke] ${monotonicMs() - traceStarted}ms 树${elements["tree-table-body"].children.length}` +
        ` 节点${elements["node-table-body"].children.length} 选项${elements["agent-picker"].children.length}\n`,
    );
  }, 2000);
  watchdog.unref?.();
  sandbox.__domReady();
  if (mode === "tree") {
    await waitFor(() => elements["tree-table-body"].children.length > 0, {
      label: "树清单渲染",
    });
    clickFirstRow(elements["tree-table-body"]);
    await waitFor(() => elements["node-table-body"].children.length > 0, {
      label: "节点列表渲染",
    });
    clickFirstRow(elements["node-table-body"]);
    await waitFor(() => elements["node-detail-body"].children.length > 2, {
      label: "节点详情渲染",
    });
    await waitFor(() => elements["lineage-trees"].children.length > 0, {
      label: "谱系渲染",
    });
  } else {
    await waitFor(() => elements["agent-picker"].children.length > 0, {
      label: "Agent 选项渲染",
    });
    if (agent) {
      // 切换到指定分线（看板按 Agent 分线，用例要断言的线未必是首个）
      const picker = elements["agent-picker"];
      picker.value = agent;
      const handlers = picker.listeners.change || [];
      handlers.forEach((handler) => handler());
      // 分线切换完成的判据：成本小计已按该 Agent 重算（其后曲线/标注均已就绪）
      await waitFor(() => (elements["cost-total"].textContent || "").includes(agent), {
        label: `分线 ${agent} 渲染`,
      });
    }
    await waitFor(() => elements["reward-chart"].children.length > 0, {
      label: "曲线渲染",
    });
    await waitFor(() => elements["calibration-panel"].children.length > 0, {
      label: "摘要面板渲染",
    });
  }
  await sleep(50); // 让尾部异步渲染收口
  clearInterval(watchdog);
  process.stdout.write(JSON.stringify(dump()));
})().catch((error) => {
  // 失败也打出当前 DOM 快照：便于定位"哪一步没渲染出来"
  process.stdout.write(
    JSON.stringify({ ...dump(), harness_error: String(error), stack: error.stack || "" }),
  );
  process.exitCode = 1;
});

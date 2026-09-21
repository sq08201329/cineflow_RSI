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
  fetch: (url, options) =>
    fetch(new URL(url, baseUrl), options).then((response) => ({
      ok: response.ok,
      status: response.status,
      json: () => response.json(),
      text: () => response.text(),
    })),
};

vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(appPath, "utf8"), sandbox, { filename: appPath });

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function waitFor(predicate, { timeout = 4000, label = "" } = {}) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    if (predicate()) {
      return true;
    }
    await sleep(20);
  }
  throw new Error(`等待超时：${label}`);
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
  };
  return summary;
}

(async () => {
  if (!sandbox.__domReady) {
    throw new Error("app.js 未注册 DOMContentLoaded 处理器");
  }
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
  process.stdout.write(JSON.stringify(dump()));
})().catch((error) => {
  // 失败也打出当前 DOM 快照：便于定位"哪一步没渲染出来"
  process.stdout.write(
    JSON.stringify({ ...dump(), harness_error: String(error), stack: error.stack || "" }),
  );
  process.exitCode = 1;
});

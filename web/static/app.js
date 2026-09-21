"use strict";
/*
 * CineFlow 只读前端（发现树浏览器 + 进化曲线看板）——vanilla JS、零外部库、只发 GET。
 *
 * 只读纪律（宪章 v1.1.0 原则五新条款）：页面不承载任何写入口（无表单、无写请求），
 * 录入/审批/部署永远走 CLI；所有取数走只读接口（web/server.py 的路由表，全部 GET）。
 *
 * 双数据源（同一套页面，两种部署形态）：
 *   1. 在线：fetch /api/*（服务由 web/server.py 提供；token 配置后从 location.search 读取并附加）；
 *   2. 离线：静态导出目录（web/export.py 预生成的 data/*.json，相对路径 fetch）——
 *      由导出页注入 window.CINEFLOW_STATIC = true 切换；两种形态取的是**同一查询层**的数据。
 *
 * 页面只在存在对应根元素时初始化对应视图（#tree-browser-root / #board-root）；
 * 图表为手绘 SVG（createElementNS），不引任何图表库。
 */
(() => {
  const TOKEN_PARAM = "token";
  const STATIC_MODE = window.CINEFLOW_STATIC === true;
  const DEFAULT_PAGE_SIZE = 10;
  const SVG_NS = "http://www.w3.org/2000/svg"; // 手绘 SVG 命名空间（不引图表库）

  /* ------------------------------------------------------------------ *
   * 数据面路径
   * ------------------------------------------------------------------ */

  // 在线接口路径：每一项都必须登记在 web/server.py 的 ROUTES 内（页面冒烟测试静态断言）
  const API = {
    facets: "/api/facets",
    trees: "/api/trees",
    treeNodes: (treeId) => `/api/trees/${encodeURIComponent(treeId)}/nodes`,
    node: (nodeId) => `/api/nodes/${encodeURIComponent(nodeId)}`,
    lineage: (version) => `/api/lineage/${encodeURIComponent(version)}`,
    evolution: (agentId) => `/api/evolution/${encodeURIComponent(agentId)}`,
    costs: "/api/costs",
    summary: "/api/summary",
  };

  // 离线快照文件：由 web/export.py 生成（键与导出清单一一对应）
  const STATIC_DATA = {
    facets: "data/facets.json",
    trees: "data/trees.json",
    nodes: "data/nodes.json",
    node_details: "data/node_details.json",
    lineage: "data/lineage.json",
    evolution: "data/evolution.json",
    costs: "data/costs.json",
    summary: "data/summary.json",
  };

  const token = new URLSearchParams(window.location.search).get(TOKEN_PARAM);

  function withToken(url) {
    if (!token) {
      return url;
    }
    const separator = url.includes("?") ? "&" : "?";
    return `${url}${separator}${TOKEN_PARAM}=${encodeURIComponent(token)}`;
  }

  // 只发 GET：fetch 选项里不设 method（写请求一律被服务拒绝，页面也无可写入口）
  async function loadJson(url) {
    const response = await fetch(url, { headers: { Accept: "application/json" } });
    if (!response.ok) {
      const detail = await response.text();
      throw new Error(`${url} → HTTP ${response.status}：${detail.slice(0, 200)}`);
    }
    return response.json();
  }

  function apiOrStatic(url, staticKey) {
    return STATIC_MODE ? loadJson(STATIC_DATA[staticKey]) : loadJson(withToken(url));
  }

  /* ------------------------------------------------------------------ *
   * 取数（在线走接口 / 离线走快照；口径与字段完全一致）
   * ------------------------------------------------------------------ */

  async function fetchFacets() {
    return apiOrStatic(API.facets, "facets");
  }

  async function fetchTrees(filters, page, pageSize) {
    if (!STATIC_MODE) {
      const query = new URLSearchParams({
        page: String(page),
        page_size: String(pageSize),
      });
      Object.keys(filters).forEach((key) => {
        if (filters[key] !== "") {
          query.set(key, String(filters[key]));
        }
      });
      return loadJson(withToken(`${API.trees}?${query.toString()}`));
    }
    const snapshot = await loadJson(STATIC_DATA.trees);
    const matched = filterTrees(snapshot.items || [], filters);
    return paginateSnapshot(matched, page, pageSize, matched.length);
  }

  async function fetchNodes(treeId, filters, page, pageSize) {
    if (!STATIC_MODE) {
      const query = new URLSearchParams({
        page: String(page),
        page_size: String(pageSize),
      });
      if (filters.depth !== "") {
        query.set("depth", String(filters.depth));
      }
      return loadJson(withToken(`${API.treeNodes(treeId)}?${query.toString()}`));
    }
    const snapshot = await loadJson(STATIC_DATA.nodes);
    const entry = ((snapshot.trees || {})[treeId] || { items: [] }).items || [];
    const matched =
      filters.depth === ""
        ? entry
        : entry.filter((item) => item.depth === Number(filters.depth));
    return paginateSnapshot(matched, page, pageSize, matched.length);
  }

  async function fetchNodeDetail(nodeId) {
    if (!STATIC_MODE) {
      return loadJson(withToken(API.node(nodeId)));
    }
    const snapshot = await loadJson(STATIC_DATA.node_details);
    const detail = (snapshot.nodes || {})[nodeId];
    if (!detail) {
      throw new Error(`离线快照缺该节点详情：${nodeId}`);
    }
    return detail;
  }

  async function fetchLineage(policyVersion) {
    if (!STATIC_MODE) {
      return loadJson(withToken(API.lineage(policyVersion)));
    }
    const snapshot = await loadJson(STATIC_DATA.lineage);
    const payload = (snapshot.versions || {})[policyVersion];
    if (!payload) {
      throw new Error(`离线快照缺该版本谱系：${policyVersion}`);
    }
    return payload;
  }

  async function fetchEvolution(agentId) {
    if (!STATIC_MODE) {
      return loadJson(withToken(API.evolution(agentId)));
    }
    const snapshot = await loadJson(STATIC_DATA.evolution);
    return (snapshot.agents || {})[agentId] || null;
  }

  async function fetchCosts() {
    return apiOrStatic(API.costs, "costs");
  }

  async function fetchSummary() {
    return apiOrStatic(API.summary, "summary");
  }

  // 离线快照的客户端过滤/分页：与在线接口同口径（三维过滤 + page/page_size）
  function filterTrees(items, filters) {
    return items.filter(
      (item) =>
        (!filters.project_id || item.project_id === filters.project_id) &&
        (!filters.agent_id || item.agent_id === filters.agent_id) &&
        (!filters.policy_version || item.policy_version === filters.policy_version),
    );
  }

  function paginateSnapshot(items, page, pageSize, total) {
    const start = (page - 1) * pageSize;
    return {
      items: items.slice(start, start + pageSize),
      page,
      page_size: pageSize,
      total,
    };
  }

  /* ------------------------------------------------------------------ *
   * DOM / 渲染工具
   * ------------------------------------------------------------------ */

  const $ = (id) => document.getElementById(id);

  function el(tag, text, className) {
    const node = document.createElement(tag);
    if (text !== undefined && text !== null) {
      node.textContent = String(text);
    }
    if (className) {
      node.className = className;
    }
    return node;
  }

  function fillTable(tbody, rows, columns, onSelect) {
    tbody.replaceChildren();
    rows.forEach((row) => {
      const tr = document.createElement("tr");
      columns.forEach((column) => {
        const td = el("td", column.value(row));
        if (column.className) {
          td.className = column.className;
        }
        tr.appendChild(td);
      });
      if (onSelect) {
        tr.addEventListener("click", () => onSelect(row, tr));
      }
      tbody.appendChild(tr);
    });
  }

  function markSelected(tbody, tr) {
    Array.from(tbody.children).forEach((row) => row.classList.remove("selected"));
    if (tr) {
      tr.classList.add("selected");
    }
  }

  function formatTimestamp(epochSeconds) {
    if (epochSeconds === null || epochSeconds === undefined) {
      return "—";
    }
    return `${new Date(epochSeconds * 1000).toISOString().replace("T", " ").slice(0, 19)} UTC`;
  }

  function formatNumber(value, digits) {
    if (value === null || value === undefined) {
      return "—";
    }
    return Number(value).toFixed(digits);
  }

  function setEmptyState(message) {
    const box = $("empty-state");
    if (!box) {
      return;
    }
    box.textContent = message;
    box.hidden = !message;
  }

  function clearError() {
    const box = $("error-box");
    if (box) {
      box.hidden = true;
      box.textContent = "";
    }
  }

  function showError(error) {
    const box = $("error-box");
    if (!box) {
      return;
    }
    box.textContent = `读取失败：${error.message}`;
    box.hidden = false;
  }

  /* ------------------------------------------------------------------ *
   * 手绘 SVG 图表工具（零图表库：折线 + 柱状，固定 viewBox 坐标）
   * ------------------------------------------------------------------ */

  const CHART = { width: 640, height: 220, padLeft: 48, padRight: 16, padTop: 16, padBottom: 32 };

  function svgElement(tag, attributes) {
    const node = document.createElementNS(SVG_NS, tag);
    Object.keys(attributes || {}).forEach((key) => {
      node.setAttribute(key, String(attributes[key]));
    });
    return node;
  }

  function chartText(content, x, y, extra) {
    const node = svgElement("text", { x, y, fill: "#8fa0b3", "font-size": 11, ...(extra || {}) });
    node.textContent = content;
    return node;
  }

  function chartFrame(svg) {
    svg.replaceChildren();
    const plot = {
      x: CHART.padLeft,
      y: CHART.padTop,
      width: CHART.width - CHART.padLeft - CHART.padRight,
      height: CHART.height - CHART.padTop - CHART.padBottom,
    };
    svg.appendChild(
      svgElement("rect", {
        x: plot.x,
        y: plot.y,
        width: plot.width,
        height: plot.height,
        fill: "none",
        stroke: "#26303c",
      }),
    );
    return plot;
  }

  function emptyChart(svg, message) {
    chartFrame(svg);
    svg.appendChild(chartText(message, CHART.padLeft + 8, CHART.padTop + 24));
  }

  /** 折线图：values 为逐点数值，labels 为横轴标签；flags 为逐点标记（塌缩轮次）。 */
  function drawLineChart(svg, values, labels, flags) {
    if (!values.length) {
      emptyChart(svg, "（无数据：如实空态）");
      return;
    }
    const plot = chartFrame(svg);
    const max = Math.max(...values, 0);
    const min = Math.min(...values, 0);
    const span = max - min || 1;
    const stepX = values.length > 1 ? plot.width / (values.length - 1) : 0;
    const toX = (index) => plot.x + index * stepX;
    const toY = (value) => plot.y + plot.height - ((value - min) / span) * plot.height;

    svg.appendChild(chartText(max.toFixed(3), 6, plot.y + 4));
    svg.appendChild(chartText(min.toFixed(3), 6, plot.y + plot.height));

    const path = values
      .map((value, index) => `${index === 0 ? "M" : "L"} ${toX(index)} ${toY(value)}`)
      .join(" ");
    svg.appendChild(svgElement("path", { d: path, fill: "none", stroke: "#4ea1ff", "stroke-width": 2 }));

    values.forEach((value, index) => {
      const collapsed = Boolean((flags || [])[index]);
      svg.appendChild(
        svgElement("circle", {
          cx: toX(index),
          cy: toY(value),
          r: collapsed ? 5 : 3,
          fill: collapsed ? "#ff5a5a" : "#4ea1ff",
        }),
      );
      svg.appendChild(chartText(labels[index], toX(index) - 6, plot.y + plot.height + 16));
      if (collapsed) {
        svg.appendChild(chartText("塌缩", toX(index) - 12, toY(value) - 8, { fill: "#ff5a5a" }));
      }
    });
  }

  /** 柱状图：bars 为 [{label, value}]。 */
  function drawBarChart(svg, bars, unit) {
    if (!bars.length) {
      emptyChart(svg, "（无数据：如实空态）");
      return;
    }
    const plot = chartFrame(svg);
    const max = Math.max(...bars.map((bar) => bar.value), 0) || 1;
    const slot = plot.width / bars.length;
    const barWidth = Math.max(8, slot * 0.5);
    bars.forEach((bar, index) => {
      const height = (bar.value / max) * plot.height;
      const x = plot.x + slot * index + (slot - barWidth) / 2;
      svg.appendChild(
        svgElement("rect", {
          x,
          y: plot.y + plot.height - height,
          width: barWidth,
          height,
          fill: "#35d07f",
        }),
      );
      svg.appendChild(
        chartText(bar.value.toFixed(3), x - 4, plot.y + plot.height - height - 4),
      );
      svg.appendChild(chartText(bar.label, x - 4, plot.y + plot.height + 16));
    });
    svg.appendChild(chartText(`${unit}（上限 ${max.toFixed(3)}）`, 6, plot.y + 4));
  }

  const DRIFT_BADGES = {
    normal: { label: "normal（正常）", className: "badge badge-normal" },
    suspect: { label: "suspect（疑似漂移）", className: "badge badge-suspect" },
    confirmed_drift: { label: "confirmed_drift（确认漂移）", className: "badge badge-confirmed_drift" },
  };

  function badge(status) {
    const spec = DRIFT_BADGES[status] || { label: status || "未知", className: "badge" };
    return el("span", spec.label, spec.className);
  }

  /* ------------------------------------------------------------------ *
   * 树浏览器视图（US2）
   * ------------------------------------------------------------------ */

  const treeBrowser = {
    state: {
      filters: { project_id: "", agent_id: "", policy_version: "", depth: "" },
      treePage: 1,
      nodePage: 1,
      pageSize: DEFAULT_PAGE_SIZE,
      selectedTree: null,
      selectedNode: null,
    },

    async init() {
      const facets = await fetchFacets();
      this.fillSelect($("filter-project"), facets.projects, "全部项目");
      this.fillSelect($("filter-agent"), facets.agents, "全部 Agent");
      this.fillSelect($("filter-version"), facets.policy_versions, "全部版本");
      this.bindEvents();
      await this.refreshTrees();
    },

    fillSelect(select, values, placeholder) {
      if (!select) {
        return;
      }
      const current = select.value;
      select.replaceChildren(el("option", placeholder));
      select.firstChild.value = "";
      values.forEach((value) => {
        const option = el("option", value);
        option.value = value;
        select.appendChild(option);
      });
      select.value = values.includes(current) ? current : "";
    },

    bindEvents() {
      const apply = $("apply-filters");
      if (apply) {
        apply.addEventListener("click", () => {
          this.state.filters = {
            project_id: $("filter-project").value,
            agent_id: $("filter-agent").value,
            policy_version: $("filter-version").value,
            depth: $("filter-depth") ? $("filter-depth").value : "",
          };
          this.state.treePage = 1;
          this.refreshTrees().catch(showError);
        });
      }
      const reset = $("reset-filters");
      if (reset) {
        reset.addEventListener("click", () => {
          this.state.filters = {
            project_id: "",
            agent_id: "",
            policy_version: "",
            depth: "",
          };
          this.state.treePage = 1;
          ["filter-project", "filter-agent", "filter-version", "filter-depth"].forEach((id) => {
            const select = $(id);
            if (select) {
              select.value = "";
            }
          });
          this.refreshTrees().catch(showError);
        });
      }
      this.bindPager("tree-page-prev", "tree-page-next", "treePage", () => this.refreshTrees());
      this.bindPager("node-page-prev", "node-page-next", "nodePage", () => this.refreshNodes());
    },

    bindPager(prevId, nextId, key, reload) {
      const prev = $(prevId);
      const next = $(nextId);
      if (prev) {
        prev.addEventListener("click", () => {
          if (this.state[key] > 1) {
            this.state[key] -= 1;
            reload().catch(showError);
          }
        });
      }
      if (next) {
        next.addEventListener("click", () => {
          this.state[key] += 1;
          reload().catch(showError);
        });
      }
    },

    async refreshTrees() {
      clearError();
      const page = await fetchTrees(this.state.filters, this.state.treePage, this.state.pageSize);
      fillTable(
        $("tree-table-body"),
        page.items,
        [
          { value: (row) => row.tree_id },
          { value: (row) => row.project_id },
          { value: (row) => row.agent_id },
          { value: (row) => row.policy_version },
          { value: (row) => row.node_count },
          { value: (row) => (row.form === null ? "（未标注）" : row.form) },
          { value: (row) => formatTimestamp(row.created_at) },
        ],
        (row, tr) => {
          markSelected($("tree-table-body"), tr);
          this.selectTree(row);
        },
      );
      $("tree-page-info").textContent = `第 ${page.page} 页 · 共 ${page.total} 棵`;
      setEmptyState(page.total === 0 ? "该过滤条件下没有发现树（如实空态）" : "");
      if (page.items.length === 0) {
        this.clearSelection();
      }
    },

    selectTree(tree) {
      this.state.selectedTree = tree;
      this.state.nodePage = 1;
      $("node-tree-label").textContent = `（${tree.tree_id}）`;
      this.refreshNodes().catch(showError);
      this.refreshLineage(tree.policy_version).catch(showError);
    },

    clearSelection() {
      this.state.selectedTree = null;
      fillTable($("node-table-body"), [], []);
      $("node-tree-label").textContent = "";
    },

    async refreshNodes() {
      if (!this.state.selectedTree) {
        return;
      }
      clearError();
      const page = await fetchNodes(
        this.state.selectedTree.tree_id,
        this.state.filters,
        this.state.nodePage,
        this.state.pageSize,
      );
      fillTable(
        $("node-table-body"),
        page.items,
        [
          { value: (row) => row.node_id },
          { value: (row) => row.depth },
          { value: (row) => formatNumber(row.score, 3) },
          { value: (row) => formatNumber(row.cost_usd, 4) },
          { value: (row) => row.status },
          { value: (row) => formatTimestamp(row.created_at) },
        ],
        (row, tr) => {
          markSelected($("node-table-body"), tr);
          this.selectNode(row);
        },
      );
      $("node-page-info").textContent = `第 ${page.page} 页 · 共 ${page.total} 个节点`;
    },

    selectNode(row) {
      this.state.selectedNode = row;
      this.refreshNodeDetail(row.node_id).catch(showError);
    },

    async refreshNodeDetail(nodeId) {
      clearError();
      const detail = await fetchNodeDetail(nodeId);
      const body = $("node-detail-body");
      body.replaceChildren();
      const fields = [
        ["节点", detail.node_id],
        ["所属树", detail.tree_id],
        ["父节点", detail.parent_id === null ? "（根）" : detail.parent_id],
        ["深度", detail.depth],
        ["状态", detail.status],
        ["得分", formatNumber(detail.score, 3)],
        ["成本（USD）", formatNumber(detail.cost_usd, 4)],
        ["创建时间", formatTimestamp(detail.created_at)],
      ];
      fields.forEach(([label, value]) => {
        body.appendChild(el("dt", label));
        body.appendChild(el("dd", value));
      });

      body.appendChild(el("dt", "提示"));
      body.appendChild(el("dd", detail.prompt || "（空）"));

      body.appendChild(el("dt", "观测键"));
      body.appendChild(el("dd", (detail.observation_keys || []).join("、") || "（无）"));

      body.appendChild(el("dt", "工件引用"));
      const artifact = detail.artifact || {};
      const artifactText = `${artifact.hash || "—"}（元信息键：${
        (artifact.metadata_keys || []).join("、") || "无"
      }）`;
      body.appendChild(el("dd", artifactText));

      body.appendChild(el("dt", "分量（评估器@版本）"));
      const breakdownList = el("ul");
      (detail.eval_breakdown || []).forEach((entry) => {
        const diagnostics = (entry.diagnostics_keys || []).join("、") || "无诊断键";
        breakdownList.appendChild(
          el(
            "li",
            `${entry.evaluator_key}：${formatNumber(entry.score, 3)}（${diagnostics}）`,
          ),
        );
      });
      const breakdownCell = el("dd");
      breakdownCell.appendChild(
        breakdownList.children.length ? breakdownList : el("span", "（无分量）"),
      );
      body.appendChild(breakdownCell);

      body.appendChild(el("dt", "成本明细"));
      const cost = detail.cost || {};
      const costText = [
        `LLM 调用 ${cost.llm_calls ?? "—"}`,
        `tokens ${cost.llm_tokens ?? "—"}`,
        `生成 API ${cost.generation_api_calls ?? "—"} 次`,
        `生成成本 ${formatNumber(cost.generation_api_cost_usd, 4)} USD`,
        `人评 ${cost.human_review_minutes ?? "—"} 分钟`,
        `墙钟 ${formatNumber(cost.wall_clock_seconds, 2)} 秒`,
      ].join(" · ");
      body.appendChild(el("dd", costText));
    },

    async refreshLineage(policyVersion) {
      clearError();
      $("lineage-version-label").textContent = `（${policyVersion}）`;
      const payload = await fetchLineage(policyVersion);
      const renderList = (listId, rows, describe) => {
        const list = $(listId);
        list.replaceChildren();
        if (!rows.length) {
          list.appendChild(el("li", "（无）", "muted"));
          return;
        }
        rows.forEach((row) => list.appendChild(el("li", describe(row))));
      };
      renderList("lineage-parents", payload.parents || [], (row) => row.version);
      renderList(
        "lineage-trees",
        payload.trees || [],
        (row) => `${row.tree_id}（${row.project_id} / ${row.agent_id}）`,
      );
      renderList(
        "lineage-children",
        payload.children || [],
        (row) => `${row.version}（${row.project_id || "无树归属"}）`,
      );
      $("lineage-cross-project").textContent = payload.cross_project
        ? "跨项目版本：产出树分布在多个项目（归属逐行标注）"
        : "非跨项目版本：产出树归属单一项目（跨项目字段为空列表）";
    },
  };

  /* ------------------------------------------------------------------ *
   * 进化曲线看板视图（US3）
   * ------------------------------------------------------------------ */

  const evolutionBoard = {
    state: { agentId: "", evolution: null, costs: null, summary: null },

    async init() {
      const [costs, summary] = await Promise.all([fetchCosts(), fetchSummary()]);
      this.state.costs = costs;
      this.state.summary = summary;
      const agents = Array.from(
        new Set([...(costs.agents || []).map((row) => row.agent_id), ...Object.keys(this.state.evolution || {})]),
      ).sort();
      this.fillAgentPicker(agents);
      this.bindEvents();
      this.renderSummary();
      await this.refreshAgent();
    },

    fillAgentPicker(agents) {
      const picker = $("agent-picker");
      if (!picker) {
        return;
      }
      picker.replaceChildren();
      if (!agents.length) {
        picker.appendChild(el("option", "（无 Agent 数据）"));
        return;
      }
      agents.forEach((agentId) => {
        const option = el("option", agentId);
        option.value = agentId;
        picker.appendChild(option);
      });
      this.state.agentId = agents[0];
      picker.value = agents[0];
    },

    bindEvents() {
      const picker = $("agent-picker");
      if (picker) {
        picker.addEventListener("change", () => {
          this.state.agentId = picker.value;
          this.refreshAgent().catch(showError);
        });
      }
      const refresh = $("refresh-board");
      if (refresh) {
        refresh.addEventListener("click", () => this.refreshAll().catch(showError));
      }
    },

    async refreshAll() {
      const [costs, summary] = await Promise.all([fetchCosts(), fetchSummary()]);
      this.state.costs = costs;
      this.state.summary = summary;
      this.renderSummary();
      this.renderCosts();
      await this.refreshAgent();
    },

    async refreshAgent() {
      clearError();
      const agentId = this.state.agentId || $("agent-picker").value;
      if (!agentId) {
        this.renderEmptyBoard();
        return;
      }
      this.state.agentId = agentId;
      this.state.evolution = await fetchEvolution(agentId);
      this.renderReward(agentId);
      this.renderCosts();
      this.renderAgentNote(agentId);
      setEmptyState(
        this.state.evolution && this.state.evolution.rounds.length
          ? ""
          : "该 Agent 暂无做梦轮次数据（如实空态，不伪造曲线）",
      );
    },

    renderEmptyBoard() {
      emptyChart($("reward-chart"), "（无 Agent 数据）");
      emptyChart($("cost-chart"), "（无 Agent 数据）");
      setEmptyState("暂无可展示的 Agent（如实空态）");
    },

    renderAgentNote(agentId) {
      const note = $("board-agent-note");
      if (note) {
        note.textContent = `当前分线：${agentId}（曲线读 dreaming/history 轮次报告）`;
      }
    },

    renderReward(agentId) {
      const evolution = this.state.evolution;
      const chart = $("reward-chart");
      const note = $("collapse-note");
      if (!evolution || !evolution.rounds.length) {
        emptyChart(chart, "（无轮次数据）");
        if (note) {
          note.textContent = "";
        }
        return;
      }
      const scored = evolution.rounds.filter((row) => row.best_reward !== null);
      const labels = scored.map((row) => `R${row.round}`);
      const values = scored.map((row) => Number(row.best_reward));
      const flags = scored.map((row) => row.collapse_flag);
      drawLineChart(chart, values, labels, flags);
      if (note) {
        const collapse = evolution.collapse || {};
        note.textContent = collapse.collapsed
          ? `塌缩标注：第 ${collapse.start_round} 轮起连续 ${collapse.window} 轮 reward < 首轮基线 × ${collapse.threshold}`
          : `未判塌缩（窗口 ${collapse.window} 轮 / 阈值 ${collapse.threshold}）`;
      }
    },

    renderCosts() {
      const costs = this.state.costs || { items: [] };
      const agentId = this.state.agentId;
      const rows = (costs.items || []).filter((row) => !agentId || row.agent_id === agentId);
      drawBarChart(
        $("cost-chart"),
        rows.map((row) => ({ label: `${row.period}`, value: Number(row.cost_usd) })),
        "USD",
      );
      const total = rows.reduce((sum, row) => sum + Number(row.cost_usd), 0);
      const subtotalRow = (costs.agents || []).find((row) => row.agent_id === agentId);
      const note = $("cost-total");
      if (note) {
        note.textContent = rows.length
          ? `${agentId} 合计 ${total.toFixed(4)} USD（${subtotalRow ? subtotalRow.node_count : 0} 个节点）· 全库合计 ${Number(
              costs.total_usd || 0,
            ).toFixed(4)} USD`
          : `${agentId} 暂无成本数据（如实空态）`;
      }
    },

    renderSummary() {
      const summary = this.state.summary || null;
      const calibration = (summary && summary.calibration) || null;
      const drift = (summary && summary.drift) || null;
      this.renderCalibration(calibration);
      this.renderDrift(drift);
    },

    renderCalibration(calibration) {
      const panel = $("calibration-panel");
      panel.replaceChildren();
      if (!calibration || !calibration.agents.length) {
        panel.appendChild(el("p", "无信度报告（如实空态：010 未产出本周期报告）", "muted"));
        return;
      }
      panel.appendChild(
        el(
          "p",
          `周期 ${calibration.period} · 信度目标 ${calibration.target} · 总体${
            calibration.meets === true ? "达标" : calibration.meets === false ? "未达标" : "无结论"
          }`,
          calibration.meets === false ? "error" : "muted",
        ),
      );
      const list = el("ul");
      calibration.agents.forEach((entry) => {
        entry.evaluators.forEach((evaluator) => {
          const text = `${entry.agent_id} / ${evaluator.evaluator_key}：${evaluator.metric} = ${formatNumber(
            evaluator.value,
            3,
          )}（样本 ${evaluator.samples}）${evaluator.meets_target ? "达标" : "未达标"}`;
          list.appendChild(el("li", text, evaluator.meets_target ? "" : "error"));
        });
      });
      panel.appendChild(list);
      if (calibration.alerts && calibration.alerts.length) {
        panel.appendChild(el("p", `告警 ${calibration.alerts.length} 条（010 报表）`, "error"));
      }
    },

    renderDrift(drift) {
      const panel = $("drift-panel");
      panel.replaceChildren();
      if (!drift) {
        panel.appendChild(el("p", "无漂移登记（如实空态）", "muted"));
        return;
      }
      if (!drift.items.length) {
        panel.appendChild(el("p", "无漂移登记（如实空态：012 从未检出漂移）", "muted"));
      }
      const list = el("ul");
      drift.items.forEach((item) => {
        const li = el("li");
        li.appendChild(badge(item.status));
        li.appendChild(el("span", ` ${item.evaluator_key}（自 ${item.since || "—"}）`));
        list.appendChild(li);
      });
      panel.appendChild(list);
      const alerts = drift.alerts || [];
      panel.appendChild(
        el(
          "p",
          alerts.length
            ? `告警：${alerts.map((alert) => `${alert.evaluator_key}[${alert.level}${alert.double_signal ? "/双信号" : ""}]`).join("、")}`
            : "无告警（判定未超阈或未双信号）",
          alerts.length ? "error" : "muted",
        ),
      );
      panel.appendChild(el("p", `来源：${drift.source === "report" ? `012 报表（${drift.period}）` : drift.source === "registry" ? "状态登记" : "—"}`, "muted"));
    },
  };

  /* ------------------------------------------------------------------ *
   * 入口：按页面根元素选择视图
   * ------------------------------------------------------------------ */

  const VIEWS = { treeBrowser, evolutionBoard };

  document.addEventListener("DOMContentLoaded", () => {
    if (STATIC_MODE) {
      const hint = $("mode-hint");
      if (hint) {
        hint.textContent = "离线静态快照（静态导出目录）· 只读视图";
      }
    }
    const name = $("tree-browser-root") ? "treeBrowser" : $("board-root") ? "evolutionBoard" : null;
    const view = name ? VIEWS[name] : null;
    if (view) {
      view.init().catch(showError);
    }
  });
})();

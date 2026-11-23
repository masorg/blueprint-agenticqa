/* Agentic QA dashboard - Alpine component.

   One component owns the whole SPA: sidebar navigation, the four list views,
   and the run-detail workspace. The durable SSE event log (seq-based replay) is
   the source of truth for a live run, so opening /runs/{id} directly rebuilds
   the full view from a snapshot and then streams. No build step; Alpine +
   pyramid3D() do the rendering. */

const META = [
  { layer: "unit", label: "Unit", order: 0, agentic_only: false },
  { layer: "property", label: "Property-based", order: 1, agentic_only: false },
  { layer: "trajectory", label: "Trajectory", order: 2, agentic_only: true },
  { layer: "llm_judge", label: "LLM-as-judge", order: 3, agentic_only: true },
  { layer: "adversarial", label: "Adversarial / Safety", order: 4, agentic_only: false },
];
const TERMINAL = ["done", "failed", "cancelled"];

const ICONS = {
  grid: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/></svg>',
  git: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="6" cy="6" r="3"/><circle cx="6" cy="18" r="3"/><path d="M18 9a9 9 0 0 1-9 9"/><circle cx="18" cy="6" r="3"/></svg>',
  userCheck: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="m16 11 2 2 4-4"/></svg>',
  ban: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><path d="m4.9 4.9 14.2 14.2"/></svg>',
  plus: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12h14M12 5v14"/></svg>',
  sun: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>',
  moon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z"/></svg>',
  activity: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 12h-4l-3 9L9 3l-3 9H2"/></svg>',
  gauge: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m12 14 4-4"/><path d="M3.34 19a10 10 0 1 1 17.32 0"/></svg>',
  check: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6 9 17l-5-5"/></svg>',
  alert: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 9v4M12 17h.01"/><path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z"/></svg>',
  close: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 6 6 18M6 6l12 12"/></svg>',
  cog: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>',
};

function app() {
  return {
    // ---- static config ----
    icons: ICONS,
    providerLabel: "Heuristic",
    githubLabel: "",
    llmModal: false,
    llmSaving: false,
    llmForm: { provider: "openai", model: "", base_url: "", api_key: "" },
    llmVendors: [],
    nav: [
      { id: "overview", label: "Overview", icon: "grid" },
      { id: "pulls", label: "Pull Requests", icon: "git" },
      { id: "review", label: "Review Queue", icon: "userCheck" },
      { id: "blocked", label: "Blocked", icon: "ban" },
    ],

    // ---- reactive state ----
    theme: "light",
    view: "overview",
    detailOpen: false,
    runs: [],
    stats: { open: 0, review: 0, blocked: 0, passed: 0, total: 0, pass_rate: 0 },
    current: null,
    selectedLayer: null,
    conn: "idle",
    log: [],
    modal: false,
    submitting: false,
    deciding: false,
    form: { bug: false },
    reviewMode: "demo",
    githubReady: false,
    ghRepos: [],
    ghRepo: "",
    ghPulls: [],
    ghNumber: null,
    ghLoading: false,
    _es: null,

    // ---- boot ----
    init() {
      this.applyTheme();
      this.loadStats();
      this.loadRuns();
      this.loadMeta();
      const m = location.pathname.match(/^\/runs\/([A-Za-z0-9]+)/);
      if (m) this.openRun(m[1]);
      window.addEventListener("popstate", () => {
        const mm = location.pathname.match(/^\/runs\/([A-Za-z0-9]+)/);
        if (mm) this.openRun(mm[1]);
        else this.closeDetail();
      });
    },

    // ---- theme ----
    applyTheme() {
      let t = null;
      try { t = localStorage.getItem("aqa-theme"); } catch (_) {}
      if (!t) t = window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
      this.theme = t;
      document.documentElement.setAttribute("data-theme", t);
    },
    toggleTheme() {
      this.theme = this.theme === "dark" ? "light" : "dark";
      document.documentElement.setAttribute("data-theme", this.theme);
      try { localStorage.setItem("aqa-theme", this.theme); } catch (_) {}
    },

    // ---- navigation ----
    setView(v) { this.view = v; this.closeDetail(true); },
    navLabel(v) { const n = this.nav.find((x) => x.id === v); return n ? n.label : ""; },
    badge(id) {
      if (id === "review") return this.listFor("review").length || "";
      if (id === "blocked") return this.listFor("blocked").length || "";
      return "";
    },

    // ---- data loads ----
    async loadStats() {
      try { this.stats = await (await fetch("/api/v1/stats")).json(); } catch (_) {}
    },
    async loadRuns() {
      try { this.runs = await (await fetch("/api/v1/runs")).json(); } catch (_) {}
    },
    async loadMeta() {
      try {
        const m = await (await fetch("/api/v1/meta")).json();
        this.providerLabel = m.llm.mode === "model"
          ? `${m.llm.label} · ${m.llm.model}`
          : "Heuristic";
        this.githubLabel = { app: "GitHub App", pat: "Bot token", disabled: "Not connected" }[m.github.mode] || m.github.mode;
        this.githubReady = m.github.mode !== "disabled";
        this.ghRepos = m.github.review_repos || [];
        if (this.ghRepos.length && !this.ghRepo) this.ghRepo = this.ghRepos[0];
      } catch (_) {}
    },

    // ---- model config ----
    async openLlmModal() {
      try {
        const c = await (await fetch("/api/v1/config/llm")).json();
        this.llmVendors = c.vendors || [];
        this.llmForm = {
          provider: c.current.provider === "stub" ? "openai" : (c.current.provider || "openai"),
          model: c.current.model || "",
          base_url: c.current.base_url || "",
          api_key: "",
        };
      } catch (_) {}
      this.llmModal = true;
    },
    vendorHint(kind) {
      const v = this.llmVendors.find((x) => x.id === this.llmForm.provider);
      return v ? (kind === "endpoint" ? v.endpoint_hint : v.model_hint) : "";
    },
    async saveLlm() {
      this.llmSaving = true;
      try {
        const res = await fetch("/api/v1/config/llm", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(this.llmForm),
        });
        if (!res.ok) { const e = await res.json().catch(() => ({})); throw new Error(e.detail || "failed"); }
        await this.loadMeta();
        this.llmModal = false;
      } catch (e) {
        alert("Could not apply model config: " + e.message);
      } finally {
        this.llmSaving = false;
      }
    },
    refresh() { this.loadStats(); this.loadRuns(); },

    // ---- list helpers ----
    get recent() { return this.runs.slice(0, 6); },
    listFor(v) {
      if (v === "pulls") return this.runs;
      if (v === "review") return this.runs.filter((r) => r.category === "review");
      if (v === "blocked") return this.runs.filter((r) => r.category === "blocked");
      return this.runs;
    },
    emptyText(v) {
      if (v === "review") return "Nothing waiting on a human right now.";
      if (v === "blocked") return "No blocked runs - all clear.";
      return "No pull requests yet. Start one with “New review”.";
    },

    // ---- status pills ----
    statePill(r) {
      if (r.state === "awaiting_approval") return "warn";
      if (r.state === "failed" || r.state === "cancelled") return "bad";
      if (r.state === "done") return r.approval_state === "rejected" ? "bad" : "ok";
      return "run";
    },
    stateLabel(r) {
      if (r.state === "awaiting_approval") return "Needs review";
      if (r.state === "failed") return "Blocked";
      if (r.state === "cancelled") return "Cancelled";
      if (r.state === "done")
        return r.approval_state === "rejected" ? "Rejected"
          : r.approval_state === "approved" ? "Approved" : "Passed";
      if (r.state === "queued") return "Queued";
      return "In progress";
    },
    statePillState(s, v, a) { return this.statePill({ state: s, approval_state: a }); },
    stateLabelDetail(c) { return this.stateLabel({ state: c.runState, approval_state: c.approvalState }); },

    // ---- detail: open / close ----
    async openRun(id) {
      this._closeSSE();
      this.detailOpen = true;
      this.selectedLayer = null;
      this.log = [];
      this.current = null;
      let snap;
      try { snap = await (await fetch("/api/v1/runs/" + id)).json(); }
      catch (_) { this.detailOpen = false; return; }
      this.current = this.hydrate(snap);
      if (location.pathname !== "/runs/" + id) history.pushState({}, "", "/runs/" + id);
      if (!this.isTerminal(snap.state)) this.connectSSE(id);
      else this.conn = "idle";
    },
    closeDetail(keepUrl) {
      this._closeSSE();
      this.detailOpen = false;
      this.current = null;
      this.selectedLayer = null;
      this.conn = "idle";
      if (!keepUrl && location.pathname !== "/") history.pushState({}, "", "/");
      this.refresh();
    },
    isTerminal(s) { return TERMINAL.includes(s); },

    // ---- snapshot -> view model ----
    blankLayers() {
      const layers = {};
      for (const m of META) {
        layers[m.layer] = {
          ...m, applicable: !m.agentic_only, reason: "",
          state: "pending", planned_count: 0, passed: 0, failed: 0, skipped: 0,
          error: "", results: [],
        };
      }
      return layers;
    },
    hydrate(r) {
      const layers = this.blankLayers();
      const routing = (r.routing && r.routing.layers) || {};
      const runs = r.layer_runs || {};
      for (const m of META) {
        const rt = routing[m.layer];
        const lr = runs[m.layer];
        const L = layers[m.layer];
        if (rt) { L.applicable = rt.applicable; L.reason = rt.reason || ""; }
        if (lr) {
          L.state = lr.state; L.planned_count = lr.planned_count || 0;
          L.passed = lr.passed || 0; L.failed = lr.failed || 0;
          L.skipped = lr.skipped || 0; L.error = lr.error || "";
          L.results = lr.results || [];
        }
      }
      return {
        id: r.id,
        repo: r.repo ? (r.repo.owner || r.repo.name ? `${r.repo.owner}/${r.repo.name}` : "(local)") : "(local)",
        prNumber: r.pr ? r.pr.number : null,
        prTitle: r.pr ? r.pr.title : "",
        prBody: r.pr ? (r.pr.body || "") : "",
        prUrl: (r.pr && r.pr.url) || (
          r.source === "github" && r.repo && r.repo.owner && r.repo.name && r.pr && r.pr.number
            ? `https://github.com/${r.repo.owner}/${r.repo.name}/pull/${r.pr.number}` : ""),
        headSha: r.pr && r.pr.head_sha ? r.pr.head_sha.slice(0, 7) : "",
        runState: r.state,
        verdict: r.verdict || null,
        approvalState: (r.approval && r.approval.state) || "not_required",
        repoKind: (r.routing && r.routing.repo_kind) || "plain_service",
        routingSummary: (r.routing && r.routing.summary) || "",
        triage: r.triage_report || "",
        error: r.error || "",
        layers,
      };
    },

    // ---- pyramid + layer views ----
    get layerList() {
      if (!this.current) return [];
      return META.map((m) => this.current.layers[m.layer]).sort((a, b) => b.order - a.order);
    },
    get pyramid() {
      if (!this.current) return { viewBox: window.pyramidViewBox, svg: "" };
      return window.pyramid3D(Object.values(this.current.layers), {
        selectedLayer: this.selectedLayer, mode: "run",
      });
    },
    // Click delegation for the per-run pyramid (bands + labels are injected via
    // x-html, so we read data-layer off the clicked element rather than binding
    // @click on each).
    onBandClick(ev) {
      const g = ev.target.closest("[data-layer]");
      if (!g || g.classList.contains("dim")) return;
      const layer = g.getAttribute("data-layer");
      this.selectedLayer = this.selectedLayer === layer ? null : layer;
    },
    // A static "anatomy" pyramid for the Overview landing: shows the five layers
    // and which two apply only to agentic systems (rendered dimmed/conditional).
    get overviewPyramid() {
      const DESC = {
        unit: "Isolated functions",
        property: "Invariants on inputs",
        trajectory: "Tool-call ordering",
        llm_judge: "Rubric-scored output",
        adversarial: "Security & fuzzing",
      };
      // Anatomy showcase: show ALL five layers lit (the full capability). The
      // two agentic-only layers keep their ✦ marker + the hero copy explains
      // they apply only to agentic changes; dimming is reserved for real runs.
      const layers = META.map((m) => ({
        ...m,
        applicable: true,
        state: "planned",
        planned_count: 0, passed: 0, failed: 0, skipped: 0,
      }));
      return window.pyramid3D(layers, { mode: "anatomy", desc: DESC, interactive: false });
    },
    get selectedTests() {
      if (!this.current || !this.selectedLayer) return [];
      return this.current.layers[this.selectedLayer].results || [];
    },
    layerName(id) { const m = META.find((x) => x.layer === id); return m ? m.label : id; },
    sym(o) { return { passed: "✓", failed: "✗", error: "✗", skipped: "-" }[o] || "•"; },

    // ---- verdict / approval ----
    verdictClass(c) {
      if (c.verdict === "auto_approve") return "ok";
      if (c.approvalState === "approved") return "ok";
      if (c.approvalState === "rejected") return "bad";
      return "warn";
    },
    approvalBlurb(c) {
      if (c.approvalState === "approved") return "Reviewed and approved - cleared to merge.";
      if (c.approvalState === "rejected") return "Reviewed and rejected.";
      return "One or more layers failed. Review the results below and decide.";
    },
    async resolve(approve) {
      if (!this.current) return;
      this.deciding = true;
      try {
        await fetch(`/api/v1/runs/${this.current.id}/${approve ? "approve" : "reject"}`, {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ actor: "in-app-user" }),
        });
      } finally {
        // If SSE is live it will push approval.state + run.done; otherwise reflect now.
        if (!this._es) {
          this.current.approvalState = approve ? "approved" : "rejected";
          this.current.runState = "done";
          this.refresh();
        }
        this.deciding = false;
      }
    },

    // ---- new review ----
    openModal() {
      this.form.bug = false;
      if (this.reviewMode === "github") this.ensurePulls();
      this.modal = true;
    },
    ensurePulls() {
      if (this.ghRepo && !this.ghPulls.length) this.loadPulls();
    },
    async loadPulls() {
      if (!this.ghRepo) return;
      this.ghLoading = true; this.ghPulls = []; this.ghNumber = null;
      try {
        const r = await fetch("/api/v1/github/pulls?repo=" + encodeURIComponent(this.ghRepo));
        if (r.ok) {
          this.ghPulls = await r.json();
          if (this.ghPulls.length) this.ghNumber = this.ghPulls[0].number;
        }
      } catch (_) {} finally { this.ghLoading = false; }
    },
    async reviewPr() {
      if (!this.ghNumber) return;
      this.submitting = true;
      try {
        const res = await fetch("/api/v1/github/review", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ repo: this.ghRepo, number: this.ghNumber }),
        });
        if (!res.ok) { const e = await res.json().catch(() => ({})); throw new Error(e.detail || res.status); }
        const data = await res.json();
        this.modal = false;
        await this.openRun(data.run_id);
      } catch (e) {
        alert("Could not start the review: " + e.message);
      } finally {
        this.submitting = false;
      }
    },
    async trigger() {
      this.submitting = true;
      const bug = this.form.bug;
      const body = {
        source: "local",
        local_path: "demo/inventory-orders-api",
        repo: { owner: "demo", name: "inventory-orders-api" },
        pr: {
          number: Math.floor(Math.random() * 900) + 100,
          title: bug ? "Optimize stock check in order creation" : "Refactor order creation flow",
        },
        env: bug ? { INVENTORY_ORDERS_BUG: "1" } : {},
      };
      try {
        const res = await fetch("/api/v1/runs", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
        if (!res.ok) { this.submitting = false; alert("Trigger failed: " + res.status); return; }
        const data = await res.json();
        this.modal = false;
        this.form.bug = false;
        await this.openRun(data.run_id);
      } catch (_) {
        alert("Could not start the review.");
      } finally {
        this.submitting = false;
      }
    },

    // ---- SSE ----
    get connLabel() {
      return this.conn === "live" ? "Streaming" : this.conn === "err" ? "Disconnected" : "Idle";
    },
    _closeSSE() { if (this._es) { this._es.close(); this._es = null; } },
    pushLog(text, cls) {
      this.log.push({ text, cls: cls || "" });
      this.$nextTick(() => { const el = this.$refs.log; if (el) el.scrollTop = el.scrollHeight; });
    },
    connectSSE(id) {
      this._closeSSE();
      const es = new EventSource("/api/v1/runs/" + id + "/events");
      this._es = es;

      es.addEventListener("run.state", (e) => {
        const d = JSON.parse(e.data);
        if (!this.current) return;
        this.current.runState = d.state;
        if (d.verdict) this.current.verdict = d.verdict;
      });
      es.addEventListener("route.decision", (e) => {
        const d = JSON.parse(e.data);
        if (!this.current) return;
        this.current.repoKind = d.repo_kind;
        this.current.routingSummary = d.summary;
        for (const [layer, r] of Object.entries(d.layers)) {
          if (this.current.layers[layer]) {
            this.current.layers[layer].applicable = r.applicable;
            this.current.layers[layer].reason = r.reason || "";
          }
        }
      });
      es.addEventListener("plan.ready", (e) => {
        const d = JSON.parse(e.data);
        if (!this.current) return;
        for (const p of d.layers) {
          const L = this.current.layers[p.layer];
          if (!L) continue;
          L.applicable = p.applicable;
          L.planned_count = p.planned_count;
          if (p.applicable && p.planned_count === 0) L.state = "skipped";
        }
      });
      es.addEventListener("layer.state", (e) => {
        const d = JSON.parse(e.data);
        if (!this.current) return;
        const L = this.current.layers[d.layer];
        if (!L) return;
        L.state = d.state; L.planned_count = d.planned_count;
        L.passed = d.passed; L.failed = d.failed; L.skipped = d.skipped; L.error = d.error;
      });
      es.addEventListener("test.result", (e) => {
        const d = JSON.parse(e.data);
        if (!this.current) return;
        const L = this.current.layers[d.layer];
        if (L) {
          L.state = "running";
          L.planned_count = d.planned_count;
          L.passed = d.running.passed; L.failed = d.running.failed; L.skipped = d.running.skipped;
          L.results = [...L.results, { node_id: d.node_id, outcome: d.outcome, duration: d.duration, message: d.message }];
        }
        this.pushLog(`${this.sym(d.outcome)}  [${d.layer}]  ${d.node_id}`,
          d.outcome === "passed" ? "pass" : d.outcome === "skipped" ? "skip" : "fail");
      });
      es.addEventListener("triage.report", (e) => {
        const d = JSON.parse(e.data);
        if (this.current) this.current.triage = d.report;
      });
      es.addEventListener("approval.state", (e) => {
        const d = JSON.parse(e.data);
        if (!this.current) return;
        this.current.approvalState = d.approval_state;
        if (d.verdict) this.current.verdict = d.verdict;
      });
      es.addEventListener("run.log", (e) => this.pushLog("•  " + JSON.parse(e.data).message, "sys"));
      es.addEventListener("error", (e) => {
        if (e.data) {
          const d = JSON.parse(e.data);
          if (this.current) this.current.error = d.error;
          this.pushLog("!  " + d.error, "fail");
        }
      });
      es.addEventListener("run.done", (e) => {
        const d = JSON.parse(e.data);
        if (this.current) {
          this.current.runState = d.state;
          if (d.verdict) this.current.verdict = d.verdict;
        }
        this._closeSSE();
        this.conn = "idle";
        this.refresh();
      });
      es.onopen = () => { this.conn = "live"; };
      es.onerror = () => { if (es.readyState === EventSource.CLOSED) this.conn = "err"; };
    },

    // ---- misc formatting ----
    ago(iso) {
      if (!iso) return "";
      const then = new Date(iso).getTime();
      if (isNaN(then)) return "";
      const s = Math.max(0, Math.floor((Date.now() - then) / 1000));
      if (s < 60) return "just now";
      const m = Math.floor(s / 60);
      if (m < 60) return m + "m ago";
      const h = Math.floor(m / 60);
      if (h < 24) return h + "h ago";
      const d = Math.floor(h / 24);
      return d + "d ago";
    },
    md(src) {
      if (!src) return "";
      src = String(src).replace(/[\u2014\u2013]/g, "-");   // strip em/en dashes (by codepoint) everywhere in the UI
      const esc = (s) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
      const inline = (s) => esc(s)
        .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
        .replace(/\*(.+?)\*/g, "<em>$1</em>")
        .replace(/`([^`]+)`/g, "<code>$1</code>");
      const out = [];
      let inList = false;
      for (const raw of src.split("\n")) {
        const line = raw.trimEnd();
        if (line.startsWith("- ")) {
          if (!inList) { out.push("<ul>"); inList = true; }
          out.push("<li>" + inline(line.slice(2)) + "</li>");
        } else {
          if (inList) { out.push("</ul>"); inList = false; }
          if (line) out.push("<p>" + inline(line) + "</p>");
        }
      }
      if (inList) out.push("</ul>");
      return out.join("");
    },
  };
}

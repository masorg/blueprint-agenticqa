"""Paper figures, drawn from the eval report JSON files.

Every number in a figure (and, via ``write_macros``, in the paper's text) comes
from a report file, so prose and plots cannot drift from the data. Figures are
vector PDFs sized for the IEEE template (3.5in column / 7.16in page width) and
stay legible in grayscale: state is always carried by a printed label, not by
colour alone.

Colours follow the reference palette (validated for colour-vision deficiency):
categorical blue / orange for two series, the status "critical" and "serious"
steps for FAIL / ERR cells, neutral greys for the rest.
"""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import Rectangle  # noqa: E402

from .mutation import wilson  # noqa: E402

SURFACE = "#fcfcfb"
INK, INK2, MUTED = "#0b0b0b", "#52514e", "#898781"
GRID, AXIS = "#e1e0d9", "#c3c2b7"
BLUE, ORANGE = "#2a78d6", "#eb6834"
CRITICAL, SERIOUS, NEUTRAL = "#d03b3b", "#ec835a", "#f0efec"
DE_EMPHASIS = "#c3c2b7"

COLUMN, PAGE = 3.5, 7.16  # inches, IEEE two-column

LAYERS = ["unit", "property", "trajectory", "llm_judge", "adversarial"]
LAYER_LABEL = {"unit": "Unit", "property": "Property", "trajectory": "Trajectory",
               "llm_judge": "LLM-judge", "adversarial": "Adversarial"}
AGENTIC_ONLY = {"trajectory", "llm_judge"}


def _style() -> None:
    plt.rcParams.update({
        "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
        "font.family": ["Helvetica", "Arial", "DejaVu Sans"], "font.size": 7.5,
        "hatch.linewidth": 0.6, "text.color": INK, "axes.labelcolor": INK2, "xtick.color": MUTED, "ytick.color": INK2,
        "axes.edgecolor": AXIS, "axes.linewidth": 0.6, "axes.spines.top": False,
        "axes.spines.right": False, "pdf.fonttype": 42, "ps.fonttype": 42,
    })


def _save(fig, out: Path, name: str) -> list[Path]:
    out.mkdir(parents=True, exist_ok=True)
    paths = []
    for ext in ("pdf", "png"):
        p = out / f"{name}.{ext}"
        # No embedded timestamp: regenerating from the same evidence is byte-identical.
        meta = {"CreationDate": None, "ModDate": None} if ext == "pdf" else None
        fig.savefig(p, dpi=200, bbox_inches="tight", pad_inches=0.04, metadata=meta)
        paths.append(p)
    plt.close(fig)
    return paths


def load(path: str | Path) -> dict:
    return json.loads(Path(path).read_text())


# --------------------------------------------------------------------------- #
# F1 - layer catch matrix
# --------------------------------------------------------------------------- #
_CELL = {  # state -> (fill, text colour, label)
    "failed": (CRITICAL, "#ffffff", "FAIL"),
    "error": (SERIOUS, INK, "ERR"),
    "passed": (NEUTRAL, INK2, "pass"),
    "skipped": (NEUTRAL, MUTED, "skip"),
    "pending": (None, MUTED, "·"),
    "n/a": (None, MUTED, "·"),
}

_SHORT = {
    "clean-baseline": "clean",
    "stock-check-off-by-one": "off-by-one stock check",
    "stale-stock-bound-property-only": "stale stock bound",
    "name-length-bound-removed-adversarial-only": "name-length bound removed",
    "support-clean-baseline": "clean",
    "support-trajectory-only": "tool-order violation",
    "support-judge-only": "stale ungrounded answer",
    "support-adversarial-only": "self-granted approval",
}


# The app's own pyramid palette (src/agenticqa/web/static/pyramid.js): deep blue at the base, easing to
# Cloudera orange at the apex. The paper's Figure 1 reuses it so figure and product match.
# Black-and-white cues for Figure 1: a fill pattern on each layer and a marker shape at its label.
LAYER_HATCH = {"unit": "", "property": "....", "trajectory": "////", "llm_judge": "xxxx", "adversarial": "++++"}
LAYER_MARKER = {"unit": "o", "property": "s", "trajectory": "^", "llm_judge": "D", "adversarial": "*"}

APP_PALETTE = {"unit": "#0e5289", "property": "#2181a4", "trajectory": "#38a597",
               "llm_judge": "#dd9038", "adversarial": "#f96302"}


def _shade(hex_color: str, amt: float) -> tuple[float, float, float]:
    """Same lighten/darken rule as pyramid.js `shade`: blend toward white (amt>0) or black (amt<0)."""
    n = int(hex_color.lstrip("#"), 16)
    r, g, b = (n >> 16) & 255, (n >> 8) & 255, n & 255
    t = 0 if amt < 0 else 255
    p = abs(amt)
    return tuple((c + (t - c) * p) / 255 for c in (r, g, b))


def framework_diagram(out: Path, name: str = "f0_framework") -> list[Path]:
    """The framework in one picture: a change flows left to right through the five stages of the review
    pipeline, and the test pyramid (the app's own 3D pyramid) sits inside the stage that runs the tests.
    Drawn from code, not from data."""
    import numpy as np
    from matplotlib.patches import Circle, FancyArrowPatch, FancyBboxPatch, Polygon

    _style()
    fig, ax = plt.subplots(figsize=(PAGE, 2.75))
    ax.set_xlim(0, 100)
    ax.set_ylim(0, 40)
    ax.axis("off")

    def badge(x, y, n):
        ax.add_patch(Circle((x, y), 1.5, facecolor=INK, edgecolor="white", linewidth=0.8, zorder=6))
        ax.text(x, y - 0.05, str(n), ha="center", va="center", fontsize=6.4, fontweight="bold", color="white",
                zorder=7)

    def box(x, y, w, h, title, sub, n=None, model=False, fill=NEUTRAL):
        ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.25,rounding_size=1.2",
                                    linewidth=0.9, edgecolor=BLUE if model else INK, facecolor=fill,
                                    linestyle=(0, (3, 2)) if model else "-"))
        ax.text(x + w / 2, y + h - 3.6, title, ha="center", va="center", fontsize=7.2, fontweight="bold",
                color=INK)
        if sub:
            ax.text(x + w / 2, y + (h - 7.0) / 2, sub, ha="center", va="center", fontsize=6.0, color=INK2,
                    linespacing=1.3)
        if n:
            badge(x + 0.3, y + h - 0.1, n)

    def arrow(x0, x1, y):
        ax.add_patch(FancyArrowPatch((x0, y), (x1, y), arrowstyle="-|>", mutation_scale=7, linewidth=0.9,
                                     color=INK2, shrinkA=0, shrinkB=0))

    ym, bh, by = 20.0, 22.0, 9.0           # centre line, height and bottom of the ordinary boxes
    gap = 2.0
    x = 0.6
    box(x, by, 8.0, bh, "Change", "a pull\nrequest to\nproject P", fill="white")
    arrow(x + 8.0 + 0.4, x + 8.0 + gap - 0.1, ym)
    x += 8.0 + gap
    box(x, by, 11.5, bh, "Plan", "router and\nclassifier\nlabel each\ntest with\na layer", n=1, model=True)
    arrow(x + 11.5 + 0.4, x + 11.5 + gap - 0.1, ym)
    x += 11.5 + gap

    # ---- stage 2: execute, with the test pyramid (drawn as in the app, src/agenticqa/web/static/pyramid.js)
    fw, fy, fh = 32.0, 3.5, 33.0
    box(x, fy, fw, fh, "Execute", "", n=2)
    ax.text(x + fw / 2, fy + fh - 6.9, "the tests run layer by layer in isolation", ha="center", va="center",
            fontsize=5.9, color=INK2)
    k = 0.05
    H, W0, DX, DY = 58 * k, 150 * k, 30 * k, 16 * k
    cx, base_y = x + 22.0, fy + 8.2
    half = lambda lvl: W0 * (1 - lvl / 5)
    ax.add_patch(plt.matplotlib.patches.Ellipse((cx + DX / 2, base_y - 0.6), 2 * W0 * 1.03, 1.3,
                                                facecolor=(0.06, 0.09, 0.16, 0.10), edgecolor="none"))
    for i, layer in enumerate(LAYERS):
        col = APP_PALETTE[layer]
        yb, yt = base_y + i * H, base_y + (i + 1) * H
        wb, wt = half(i), half(i + 1)
        ax.add_patch(Polygon([[cx + wb, yb], [cx + wt, yt], [cx + wt + DX, yt + DY], [cx + wb + DX, yb + DY]],
                             closed=True, facecolor=_shade(col, -0.30), edgecolor="none"))
        front = Polygon([[cx - wb, yb], [cx + wb, yb], [cx + wt, yt], [cx - wt, yt]], closed=True,
                        facecolor="none", edgecolor="none")
        ax.add_patch(front)
        top, bottom = np.array(_shade(col, 0.20)), np.array(_shade(col, -0.06))
        im = ax.imshow(np.linspace(top, bottom, 64)[:, None, :].repeat(2, axis=1),
                       extent=[cx - wb, cx + wb, yb, yt], origin="upper", aspect="auto", zorder=2)
        im.set_clip_path(front)
        # A pattern per layer, so the layers stay distinguishable when printed in black and white.
        ax.add_patch(Polygon([[cx - wb, yb], [cx + wb, yb], [cx + wt, yt], [cx - wt, yt]], closed=True,
                             facecolor="none", edgecolor=(1, 1, 1, 0.85), linewidth=0,
                             hatch=LAYER_HATCH[layer], zorder=3))
        mid, wmid = (yb + yt) / 2, half(i + 0.5)
        ax.plot([x + 10.9, cx - wmid - 0.4], [mid, mid], color=col, linewidth=0.9, alpha=0.6, zorder=1)
        ax.plot([cx - wmid - 0.4], [mid], LAYER_MARKER[layer], color=col, markersize=3.4, zorder=4,
                markeredgecolor="white", markeredgewidth=0.4)
        star = " ✦" if layer in AGENTIC_ONLY else ""
        ax.text(x + 10.5, mid, LAYER_LABEL[layer] + star, ha="right", va="center", fontsize=6.2,
                fontweight="bold", color=INK2)
    ax.set_xlim(0, 100)
    ax.set_ylim(0, 40)
    ax.text(x + 1.4, fy + 1.6, "✦ projects with agentic components only", ha="left", va="center",
            fontsize=5.6, color="#f96302")
    arrow(x + fw + 0.4, x + fw + gap - 0.1, ym)
    x += fw + gap

    box(x, by, 11.5, bh, "Verdict rule", "any failing\nlayer: human\nreview, else\nauto-approve", n=3)
    arrow(x + 11.5 + 0.4, x + 11.5 + gap - 0.1, ym)
    x += 11.5 + gap
    box(x, by, 10.5, bh, "PR status", "pending,\nsuccess,\nfailure or\nerror", n=4)
    arrow(x + 10.5 + 0.4, x + 10.5 + gap - 0.1, ym)
    x += 10.5 + gap
    box(x, by, 12.0, bh, "Escalation", "triage report;\nreviewer sends\n/approve or\n/reject", n=5, model=True)

    # ---- legend, one strip along the bottom
    ax.add_patch(FancyBboxPatch((0.6, 0.6), 2.8, 1.8, boxstyle="round,pad=0.1", linewidth=0.9,
                                edgecolor=BLUE, facecolor=NEUTRAL, linestyle=(0, (3, 2))))
    ax.text(4.2, 1.5, "a language model may take part", va="center", fontsize=5.8, color=INK2)
    ax.add_patch(FancyBboxPatch((28.0, 0.6), 2.8, 1.8, boxstyle="round,pad=0.1", linewidth=0.9,
                                edgecolor=INK, facecolor=NEUTRAL))
    ax.text(31.6, 1.5, "a fixed rule", va="center", fontsize=5.8, color=INK2)
    return _save(fig, out, name)


def selection_savings(mut: dict, out: Path, name: str = "f7_selection") -> list[Path]:
    """Share of each project's tests that GATE runs for a mutant, by selection level (lower is better).
    Hatches keep the three levels apart in black and white."""
    _style()
    pp = mut["per_project"]
    ran = [(n, v) for n, v in pp.items() if v.get("valid_mutants")]
    ran.sort(key=lambda kv: kv[1]["mean_share_name"])
    fig, ax = plt.subplots(figsize=(COLUMN, 0.46 + 0.30 * len(ran)))
    levels = [("mean_share_file", "File-level reach", DE_EMPHASIS, ""),
              ("mean_share_namefile", "Names, whole test files", "#9db8d2", "////"),
              ("mean_share_name", "Names, per test (GATE)", BLUE, "")]
    h = 0.26
    for i, (proj, v) in enumerate(ran):
        for k, (key, _, color, hatch) in enumerate(levels):
            y = i + (k - 1) * (h + 0.02)
            ax.barh(y, 100 * v[key], height=h, color=color, hatch=hatch, edgecolor="white" if hatch else color, lw=0)
        ax.text(101.5, i, f"{v['missed_name']}", va="center", fontsize=6.5, color=INK2)
    ax.set_yticks(range(len(ran)), [f"{n} ({v['n_tests']:,})" for n, v in ran])
    ax.set_xlim(0, 108)
    ax.set_xlabel("Tests run per mutant (% of the suite). Right column: failures missed.")
    ax.xaxis.grid(True, color=GRID, lw=0.5)
    ax.set_axisbelow(True)
    ax.tick_params(length=0)
    ax.legend(handles=[Rectangle((0, 0), 1, 1, facecolor=c, hatch=hh, edgecolor="white", lw=0) for _, _, c, hh in levels],
              labels=[l for _, l, _, _ in levels], loc="lower left", bbox_to_anchor=(0, 1.0), ncol=1, frameon=False,
              fontsize=6.5, handlelength=1.4, borderaxespad=0.2)
    return _save(fig, out, name)



def catch_matrix(groups: list[tuple[str, dict]], out: Path, name: str = "f1_catch_matrix") -> list[Path]:
    """groups: [(system title, eval report), ...]. One row per scenario."""
    _style()
    rows: list[tuple[str, str, dict]] = []  # (group, scenario label, states)
    for title, report in groups:
        for s in report["scenarios"]:
            rows.append((title, _SHORT.get(s["scenario"], s["scenario"]), s["layers"]["states"]))
    cw, ch, left, top, hdr = 0.95, 0.30, 1.95, 0.62, 0.30
    n_groups = len({g for g, _, _ in rows})
    width, height = left + cw * len(LAYERS), top + ch * len(rows) + (hdr + 0.1) * n_groups
    fig, ax = plt.subplots(figsize=(PAGE, height + 0.15))
    ax.set_xlim(0, width)
    ax.set_ylim(height, 0)
    ax.axis("off")

    for j, layer in enumerate(LAYERS):
        x = left + cw * (j + 0.5)
        ax.text(x, top - 0.30, LAYER_LABEL[layer], ha="center", va="center", fontsize=7.5,
                fontweight="bold", color=INK)
        if layer in AGENTIC_ONLY:
            ax.text(x, top - 0.12, "agentic only", ha="center", va="center", fontsize=6, color=MUTED)

    y, last_group = top, None
    for group, label, states in rows:
        if group != last_group:
            if last_group is not None:
                y += 0.1
            ax.text(0.0, y + hdr / 2, group, ha="left", va="center", fontsize=7.5, fontweight="bold", color=INK)
            ax.plot([0, width], [y + hdr, y + hdr], color=GRID, lw=0.6)
            y += hdr
            last_group = group
        ax.text(left - 0.12, y + ch / 2, label, ha="right", va="center", fontsize=7.5, color=INK2)
        for j, layer in enumerate(LAYERS):
            fill, tcol, text = _CELL.get(states.get(layer, "n/a"), (None, MUTED, "?"))
            x = left + cw * j
            if fill:
                ax.add_patch(Rectangle((x + 0.03, y + 0.03), cw - 0.06, ch - 0.06, color=fill, lw=0))
            ax.text(x + cw / 2, y + ch / 2, text, ha="center", va="center", fontsize=7.5, color=tcol,
                    fontweight="bold" if text == "FAIL" else "normal")
        y += ch
    return _save(fig, out, name)


# --------------------------------------------------------------------------- #
# F2 - worst-case routing: violations before vs after the fix
# --------------------------------------------------------------------------- #
def adversary_before_after(before: dict, after: dict, out: Path, name: str = "f2_adversary") -> list[Path]:
    _style()
    b, a = before["summary"], after["summary"]
    cats = [("i1_verdict_violations", "Wrong verdict"),
            ("i2_failure_count_violations", "Failing tests lost"),
            ("i3_coverage_violations", "Tests not executed")]
    fig, ax = plt.subplots(figsize=(COLUMN, 1.95))
    h = 0.34
    for i, (key, label) in enumerate(cats):
        for k, (summ, color) in enumerate(((b, ORANGE), (a, BLUE))):
            n, v = summ["n_cases"], summ[key]
            y = i + (k - 0.5) * (h + 0.04)
            ax.barh(y, 100 * v / n, height=h, color=color, lw=0)
            if v == 0:  # a zero-length bar is invisible: mark it so "none" reads as a result
                ax.plot([0.35], [y], marker="|", markersize=7, color=color, mew=1.6)
            ax.text(100 * v / n + 1.2, y, f"{v}/{n}", va="center", fontsize=7, color=INK2)
    ax.set_yticks(range(len(cats)), [c[1] for c in cats])
    ax.invert_yaxis()
    ax.set_xlim(0, 60)
    ax.set_xlabel("Hostile router/classifier cases violating the invariant (%)")
    ax.xaxis.grid(True, color=GRID, lw=0.5)
    ax.set_axisbelow(True)
    ax.tick_params(length=0)
    ax.spines["left"].set_color(AXIS)
    ax.legend(handles=[Rectangle((0, 0), 1, 1, color=ORANGE), Rectangle((0, 0), 1, 1, color=BLUE)],
              labels=["Before fix", "After fix"], loc="lower left", bbox_to_anchor=(0, 1.0),
              ncol=2, frameon=False, fontsize=7, handlelength=1.2, borderaxespad=0.2)
    return _save(fig, out, name)


# --------------------------------------------------------------------------- #
# F3 / F4 - mutation experiment
# --------------------------------------------------------------------------- #
_BASELINE_LABEL = {
    "unit-only": "Unit only",
    "unit+property": "Unit + property",
    "unit+adversarial": "Unit + adversarial\n(scope-style)",
    "full-pyramid": "Full pyramid",
}
_BASELINE_ORDER = ["unit-only", "unit+property", "unit+adversarial", "full-pyramid"]
_SYSTEM_TITLE = {"inventory": "Inventory service (3 layers apply)", "support": "Support assistant (5 layers apply)"}


def mutation_baselines(report: dict, out: Path, name: str = "f3_mutation_killrate") -> list[Path]:
    _style()
    systems = [s for s in ("inventory", "support") if s in report["summary"]]
    fig, axes = plt.subplots(1, len(systems), figsize=(PAGE, 1.9), sharex=True, squeeze=False)
    for ax, system in zip(axes[0], systems):
        base = report["summary"][system]["baselines"]
        n_valid = report["summary"][system]["n_valid"]
        for i, key in enumerate(_BASELINE_ORDER):
            d = base[key]
            color = BLUE if key == "full-pyramid" else DE_EMPHASIS
            ax.barh(i, 100 * d["rate"], height=0.55, color=color, lw=0)
            lo, hi = d["ci95"]
            ax.plot([100 * lo, 100 * hi], [i, i], color=INK2, lw=0.8)
            ax.text(100 * hi + 1.5, i, f"{d['k']}/{d['n']}", va="center", fontsize=7, color=INK2)
        ax.set_yticks(range(len(_BASELINE_ORDER)), [_BASELINE_LABEL[k] for k in _BASELINE_ORDER])
        ax.invert_yaxis()
        ax.set_xlim(0, 118)
        ax.set_title(_SYSTEM_TITLE[system] + f", {n_valid} valid mutants", fontsize=7.5, loc="left", color=INK)
        ax.xaxis.grid(True, color=GRID, lw=0.5)
        ax.set_axisbelow(True)
        ax.tick_params(length=0)
        ax.set_xlabel("Mutants killed (%), 95% Wilson interval")
    fig.tight_layout(w_pad=2.0)
    return _save(fig, out, name)


def mutation_unique(report: dict, out: Path, name: str = "f4_mutation_unique") -> list[Path]:
    """Mutants only one layer kills: what the pyramid loses without that layer."""
    _style()
    systems = [s for s in ("inventory", "support") if s in report["summary"]]
    fig, axes = plt.subplots(1, len(systems), figsize=(PAGE, 2.0), sharey=True, squeeze=False)
    top = max(d["kills"] for system in systems for d in report["summary"][system]["per_layer"].values())
    for ax, system in zip(axes[0], systems):
        per = report["summary"][system]["per_layer"]
        shown = [layer for layer in LAYERS if system == "support" or layer not in AGENTIC_ONLY]
        for i, layer in enumerate(shown):
            d = per[layer]
            ax.bar(i, d["kills"], width=0.55, color=DE_EMPHASIS, lw=0)
            ax.bar(i, d["unique_kills"], width=0.55, color=BLUE, lw=0)
            ax.text(i, d["kills"] + top * 0.03, f"{d['unique_kills']} / {d['kills']}", ha="center", fontsize=7, color=INK2)
        ax.set_xticks(range(len(shown)), [LAYER_LABEL[layer] for layer in shown])
        ax.set_ylim(0, top * 1.18)
        ax.set_title(_SYSTEM_TITLE[system], fontsize=7.5, loc="left", color=INK, pad=6)
        ax.yaxis.grid(True, color=GRID, lw=0.5)
        ax.set_axisbelow(True)
        ax.tick_params(length=0)
    axes[0][0].set_ylabel("Mutants killed")
    fig.legend(handles=[Rectangle((0, 0), 1, 1, color=DE_EMPHASIS), Rectangle((0, 0), 1, 1, color=BLUE)],
               labels=["Killed by this layer (label: unique / total)", "Killed only by this layer"],
               loc="lower center", ncol=2, frameon=False, fontsize=7, bbox_to_anchor=(0.5, -0.06))
    fig.tight_layout(w_pad=2.0, rect=(0, 0.06, 1, 1))
    return _save(fig, out, name)


# --------------------------------------------------------------------------- #
# F5 - routing accuracy on real commits
# --------------------------------------------------------------------------- #
_ROUTER_LABEL = {
    "manifest": "Manifest signal\n(deterministic)",
    "openai-gpt-4o-mini": "gpt-4o-mini",
    "mistral-mistral-large-latest": "mistral-large",
    "openai-gpt-4o": "gpt-4o",
    "mistral-mistral-small-latest": "mistral-small",
    "stub": "Keyword heuristic\n(deterministic)",
}
_ROUTER_ORDER = ["manifest", "openai-gpt-4o-mini", "mistral-mistral-large-latest", "openai-gpt-4o",
                 "mistral-mistral-small-latest", "stub"]


def routing_accuracy(analysis: dict, out: Path, name: str = "f5_routing") -> list[Path]:
    """Per-commit accuracy with repo-clustered 95% intervals; models shown with the repo name
    visible (circle) and masked (diamond); the two deterministic routers have one marker."""
    _style()
    r = analysis["routers"]
    fig, ax = plt.subplots(figsize=(PAGE, 2.9))
    heur = r["stub"]["per_commit"]["rate"]
    ax.axvline(100 * heur, color=AXIS, lw=0.8, ls=(0, (3, 3)), zorder=0)
    ax.text(100 * heur + 0.6, -0.62, "heuristic", fontsize=6.5, color=MUTED, va="bottom")
    for i, key in enumerate(_ROUTER_ORDER):
        variants = [(key, "visible", BLUE, "o")]
        if key + "-masked" in r:
            variants.append((key + "-masked", "masked", ORANGE, "D"))
        for k, (rk, cond, color, marker) in enumerate(variants):
            row = r[rk]["per_commit"]
            y = i + (k - (len(variants) - 1) / 2) * 0.28
            lo, hi = row["cluster"]["ci95"]
            colr = DE_EMPHASIS if key in ("stub",) else color
            if key == "manifest":
                colr = BLUE
            ax.plot([100 * lo, 100 * hi], [y, y], color=INK2, lw=0.8, zorder=1)
            ax.plot([100 * row["rate"]], [y], marker=marker, ms=5.5, color=colr, zorder=2,
                    mfc=colr if cond == "visible" else SURFACE, mew=1.3)
            ax.text(100 * hi + 1.2, y, f"{100 * row['rate']:.0f}%", va="center", fontsize=6.8, color=INK2)
    ax.set_yticks(range(len(_ROUTER_ORDER)), [_ROUTER_LABEL[k] for k in _ROUTER_ORDER])
    ax.invert_yaxis()
    ax.set_xlim(35, 108)
    ax.set_xlabel("Repo-kind accuracy on 144 real commits from 18 repositories (95% interval, resampling repositories)")
    ax.xaxis.grid(True, color=GRID, lw=0.5)
    ax.set_axisbelow(True)
    ax.tick_params(length=0)
    ax.legend(handles=[plt.Line2D([], [], marker="o", color=BLUE, ls="", ms=5.5, label="repo name visible"),
                       plt.Line2D([], [], marker="D", color=ORANGE, mfc=SURFACE, mew=1.3, ls="", ms=5.5,
                                  label="repo name masked")],
              loc="lower left", bbox_to_anchor=(0, 1.0), ncol=2, frameon=False, fontsize=7, borderaxespad=0.2)
    return _save(fig, out, name)


# --------------------------------------------------------------------------- #
# F6 - what persuasion payloads did (E5)
# --------------------------------------------------------------------------- #
_PAYLOAD_LABEL = {
    "authority": "Authority claim", "urgency": "Urgency", "system-override": "Direct instruction",
    "flaky-dismiss": "'Known flaky, don't mention'", "fake-results": "Fabricated results",
    "routing-misdirect": "Routing misdirection", "diff-comment": "Hidden in the diff",
}
_MODEL_ORDER = [("gpt-4o-mini", "gpt-4o-mini"), ("gpt-4o", "gpt-4o"),
                ("mistral-small", "mistral-small"), ("mistral-large", "mistral-large")]


def _heat(ax, grid, payloads, vmax, ramp, title):
    for r_, pay in enumerate(payloads):
        for c_, (_, _) in enumerate(_MODEL_ORDER):
            v, n = grid[pay][c_]
            t = 0 if vmax == 0 else v / n
            fill = ramp(t) if v else NEUTRAL
            ax.add_patch(Rectangle((c_ + 0.04, r_ + 0.06), 0.92, 0.88, color=fill, lw=0))
            dark = v and t >= 0.3
            ax.text(c_ + 0.5, r_ + 0.5, f"{v}/{n}", ha="center", va="center", fontsize=7,
                    color="#ffffff" if dark else INK2, fontweight="bold" if v else "normal")
    ax.set_xlim(0, len(_MODEL_ORDER))
    ax.set_ylim(len(payloads), 0)
    ax.set_xticks([i + 0.5 for i in range(len(_MODEL_ORDER))], [m[1] for m in _MODEL_ORDER], fontsize=6.8)
    ax.xaxis.tick_top()
    ax.set_title(title, fontsize=7.5, loc="left", color=INK, pad=22)
    for sp in ax.spines.values():
        sp.set_visible(False)
    ax.tick_params(length=0)


def _ramp(lo_hex, hi_hex):
    a = [int(lo_hex[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(hi_hex[i:i + 2], 16) for i in (1, 3, 5)]
    return lambda t: "#%02x%02x%02x" % tuple(round(a[i] + (b[i] - a[i]) * min(1.0, t * 1.6)) for i in range(3))


def injection_heatmaps(reports: dict[str, dict], out: Path, name: str = "f6_injection") -> list[Path]:
    """reports: model -> E5 report (summaries already recomputed). Left: how often the payload
    changed the router's decision. Right: how often an LLM-decided verdict approved a failing change."""
    _style()
    payloads = list(_PAYLOAD_LABEL)
    routing = {p: [] for p in payloads}
    approve = {p: [] for p in payloads}
    for key, _ in _MODEL_ORDER:
        by = reports[key]["injection"]["by_payload"]
        for p in payloads:
            routing[p].append((by[p]["routing_flips"], by[p]["n"]))
            approve[p].append((by[p]["baseline_false_approves"], by[p]["n_with_failures"]))
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(PAGE, 2.6), gridspec_kw={"wspace": 0.05})
    _heat(a1, routing, payloads, 8, _ramp("#cde2fb", "#184f95"), "Router's decision changed by the payload")
    _heat(a2, approve, payloads, 6, _ramp("#fbd9c9", "#a8431a"),
          "Failing change approved by an LLM-decided verdict")
    a1.set_yticks([i + 0.5 for i in range(len(payloads))], [_PAYLOAD_LABEL[p] for p in payloads], fontsize=7)
    a2.set_yticks([])
    fig.text(0.0, -0.04, "Pipeline verdict flips or false approvals, every cell: 0 / 224 injected runs.",
             fontsize=7, color=INK2)
    return _save(fig, out, name)


# --------------------------------------------------------------------------- #
# LaTeX number macros: the only way a number should reach the paper's prose
# --------------------------------------------------------------------------- #
def _pct(x: float | None) -> str:
    return "n/a" if x is None else f"{100 * x:.1f}"


def no_model_comparison(stub: dict, models: dict[str, dict]) -> dict:
    """What the models add over the no-model rules, on the SAME scenarios.

    `stub` is a run with the plain heuristic router/classifier; `models` are runs of the same
    corpus with an LLM in those roles. The verdict is identical by design, so the interesting
    columns are the two things a model could still improve: naming the layer that failed, and
    recognising an agent system. Scenarios are grouped by the bug they started from, because the
    seven text variants of one bug are not independent cases."""

    def score(rep: dict) -> dict:
        n = len(rep["scenarios"])
        exact = sum(1 for s in rep["scenarios"]
                    if set(s["layers"]["expected_failing"]) == set(s["layers"]["actual_failing"]))
        kind = sum(1 for s in rep["scenarios"] if s["routing"]["repo_kind_correct"])
        verdict = sum(1 for s in rep["scenarios"] if s["verdict_correct"])
        by_bug: dict[str, bool] = {}
        for s in rep["scenarios"]:
            bug = s["scenario"].split("+inj-")[0]
            ok = set(s["layers"]["expected_failing"]) == set(s["layers"]["actual_failing"])
            by_bug[bug] = by_bug.get(bug, True) and ok
        return {"n": n, "verdict_ok": verdict, "layers_exact": exact, "repo_kind_ok": kind,
                "bugs": len(by_bug), "bugs_all_layers_exact": sum(by_bug.values()),
                "tokens": rep.get("total_llm_tokens"), "seconds": rep["total_wall_seconds"]}

    base = score(stub)
    return {"stub": base, "models": {k: score(v) for k, v in models.items()}}


def selection_macros(mut: dict | None, field: dict | None, aidev: dict | None, demo: dict | None,
                     attack: dict | None = None, advice: dict | None = None) -> dict[str, object]:
    """Numbers of the test-selection study (summaries from `selection_exp`), named for the prose."""
    m: dict[str, object] = {}
    if mut:
        m.update(selMutProjects=mut["n_projects"], selMutProjectsRan=mut["n_projects_ran"],
                 selMutValid=mut["valid_mutants"], selMutKilled=mut["killed_by_full_suite"],
                 selMutSurvivors=mut["survivors"], selMutRunErrors=mut["selected_run_errors"],
                 selMutDisagree=mut["disagreements_between_derived_and_actual"],
                 selMutTimeSavedPct=_pct(mut["time_saved_share"]) if mut["time_saved_share"] is not None else "n/a",
                 selMutSecFull=f"{mut['seconds_full']:,.0f}", selMutSecSel=f"{mut['seconds_selected']:,.0f}",
                 selMutFullFallbackK=mut["full_run_fallback_share"]["k"])
        fr = mut.get("first_run")
        if fr:
            m.update(selRunValid=fr["valid"], selRunDisagree=fr["disagree"], selRunErrors=fr["errors"])
        for lvl, tag in (("file", "File"), ("name-file", "NameF"), ("name", "Name"), ("name+advice", "Adv"),
                         ("name-ignoring-dynamic-loading", "NoDyn"), ("dynamic", "Dyn")):
            if lvl not in mut["levels"]:
                continue
            d = mut["levels"][lvl]
            miss = d["missed_failures"]
            m[f"selMiss{tag}K"] = miss["k"]
            m[f"selMiss{tag}Upper"] = _pct(miss["ci95"][1])
            m[f"selCaughtTests{tag}K"] = d["caught_by_selected_tests"]["k"]
            m[f"selSaved{tag}Pct"] = _pct(d["tests_saved_share"])
            m[f"selShareMean{tag}Pct"] = _pct(d["selected_share"]["mean"])
            m[f"selShareMedian{tag}Pct"] = _pct(d["selected_share"]["median"])
            m[f"selAllTests{tag}Pct"] = _pct(d["all_tests_selected_share"]["rate"])
            m[f"selExtraRev{tag}K"] = d["extra_reviews_green_ci"]["k"]
            m[f"selExtraRev{tag}N"] = d["extra_reviews_green_ci"]["n"]
        pp = mut["per_project"]
        for n, v in pp.items():
            if v.get("mean_share_name") is not None:
                tag = "".join(w.capitalize() for w in n.split("-"))
                m[f"selShare{tag}Pct"] = _pct(v["mean_share_name"])
        saved = [1 - v["mean_share_name"] for v in pp.values() if v.get("mean_share_name") is not None]
        if saved:
            m["selMinSavedPct"], m["selMaxSavedPct"] = _pct(min(saved)), _pct(max(saved))
        gains = [(n, v["mean_share_file"] - v["mean_share_name"]) for n, v in pp.items()
                 if v.get("mean_share_name") is not None]
        if gains:
            best = max(gains, key=lambda x: x[1])
            m["selBestProject"] = best[0]
            m["selBestProjectName"] = _pct(pp[best[0]]["mean_share_name"])
            m["selBestProjectFile"] = _pct(pp[best[0]]["mean_share_file"])
    if field:
        o = field["outcomes"]
        m.update(selFieldAttempted=field["attempted"], selFieldAnalysed=field["analysed"],
                 selFieldWithTests=field["with_tests"],
                 selFieldSubset=o.get("run a subset", 0),
                 selFieldInertOnly=o.get("no test needed (documentation or other inert files only)", 0),
                 selFieldFullRun=o.get("run all tests (configuration or unreadable change)", 0),
                 selFieldReview=o.get("needs review (a changed file is reached by no test)", 0),
                 selFieldReviewPct=_pct(field["reviewed_for_missing_evidence"]["rate"]),
                 selFieldReviewN=field["reviewed_for_missing_evidence"]["n"],
                 selFieldSubsetMedianPct=_pct(field["subset_selected_share"]["median"])
                 if field["subset_selected_share"]["median"] is not None else "n/a",
                 selFieldTestsTotal=f"{field['tests_total_all']:,}", selFieldTestsSelected=f"{field['tests_selected_all']:,}",
                 selFieldSavedPct=_pct(1 - field["tests_selected_all"] / field["tests_total_all"])
                 if field["tests_total_all"] else "n/a")
    if aidev:
        m.update(selAidevN=f"{aidev['python_prs_with_file_lists']:,}",
                 selAidevInertPct=_pct(aidev["only_inert_files"]["rate"]),
                 selAidevConfigPct=_pct(aidev["touches_build_or_test_config"]["rate"]),
                 selAidevPythonPct=_pct(aidev["touches_python_source"]["rate"]),
                 selAidevOtherPct=_pct(aidev["touches_other_files"]["rate"]),
                 selAidevMedianFiles=int(aidev["median_files_changed"]))
    if demo:
        for sys_, tag in (("inventory", "Inv"), ("support", "Sup")):
            if sys_ in demo:
                m[f"selDemo{tag}FilePct"] = _pct(demo[sys_]["file"]["mean_selected_share"])
                m[f"selDemo{tag}NamePct"] = _pct(demo[sys_]["name"]["mean_selected_share"])
    ag = (mut or {}).get("_holdout", {}).get("agentic_scan")
    if ag:
        m.update(agScanProjects=ag["projects"], agScanFiles=f"{ag['test_files']:,}", agScanCue=ag["cue_named"],
                 agScanWithAny=ag["projects_with_any"])
    st_ = (mut or {}).get("_holdout", {}).get("shadow_stale")
    if st_:
        d2 = st_["dynamic"]
        regress_projects = sum(1 for v in st_["per_project"].values() if v["regressions"])
        m.update(stRan=st_["ran"], stRegress=st_["prs_introducing_failures"], stIntroTests=st_["introduced_tests"],
                 stMissK=st_["missed_prs"]["k"], stUpper=_pct(st_["missed_prs"]["ci95"][1]), stByTest=st_["caught_by_selected_test"],
                 stByReview=st_["caught_by_review"], stSavedPct=_pct(st_["tests_saved_share"]), stTimePct=_pct(st_["time_saved_share"]),
                 stReviewPct=_pct(st_["review_share"]["rate"]), stReviewK=st_["review_share"]["k"], stNoTest=st_["no_test_needed"],
                 stFullK=st_["full_run_share"]["k"], stFlaky=st_["flaky_tests_filtered"], stRegressProjects=regress_projects,
                 stDynPRs=d2["prs"], stDynRegress=d2["regressions"], stDynMissK=d2["missed"]["k"], stDynUpper=_pct(d2["missed"]["ci95"][1]),
                 stDynSavedPct=_pct(d2["tests_saved_share"]), stDynTimePct=_pct(d2["time_saved_share"]),
                 stDynReviewPct=_pct(d2["review_share"]["rate"]), stDynReviewK=d2["review_share"]["k"],
                 stMapAgeMedian=st_.get("map_age_median", "n/a"), stMapAgeMax=st_.get("map_age_max", "n/a"),
                 stMaps=st_.get("maps_ok", 0), stMapProjects=st_.get("maps_attempted", 0))
    sh = (mut or {}).get("_holdout", {}).get("shadow")
    if sh:
        d = sh["dynamic"]
        m.update(shAttempted=f"{sh['attempted']:,}", shRan=sh["ran"], shProjects=sh["projects_ran"],
                 shEmpty=sh["status"].get("empty_diff", 0),
                 shNotRun=sh["attempted"] - sh["ran"] - sh["status"].get("empty_diff", 0),
                 shRegress=sh["prs_introducing_failures"], shIntroTests=sh["introduced_tests"],
                 shMissK=sh["missed_prs"]["k"], shUpper=_pct(sh["missed_prs"]["ci95"][1]),
                 shByTest=sh["caught_by_selected_test"], shByReview=sh["caught_by_review"], shByFull=sh["caught_by_full_run"],
                 shFlaky=sh["flaky_tests_filtered"], shSavedPct=_pct(sh["tests_saved_share"]),
                 shTimePct=_pct(sh["time_saved_share"]), shReviewK=sh["review_share"]["k"],
                 shReviewPct=_pct(sh["review_share"]["rate"]), shCleanReviewPct=_pct(sh["clean_prs_sent_to_review"]["rate"]),
                 shFullK=sh["full_run_share"]["k"], shNoTest=sh["no_test_needed"],
                 shDynPRs=d["prs"], shDynRegress=d["regressions"], shDynMissK=d["missed"]["k"],
                 shDynUpper=_pct(d["missed"]["ci95"][1]), shDynSavedPct=_pct(d["tests_saved_share"]),
                 shDynTimePct=_pct(d["time_saved_share"]), shDynReviewPct=_pct(d["review_share"]["rate"]),
                 shDynReviewK=d["review_share"]["k"], shStaticSameSavedPct=_pct(d["static_tests_saved_share_same_prs"]),
                 shFullHours=f"{sh.get('seconds_full_total', 0) / 3600:.1f}", shCovHours=f"{sh.get('coverage_seconds_total', 0) / 3600:.1f}",
                 shCovPRs=sh.get("coverage_runs", 0),
                 shRegressProjects=sum(1 for v in sh.get("per_project", {}).values() if v["regressions"]))
    dv = (mut or {}).get("_holdout", {}).get("shadow_derived")
    if dv:
        k, sp, fs = dv["regression_kinds"], dv["same_prs"], dv["final_selector"]
        m.update(dvFull=k.get("full_run", 0), dvOwnTest=k.get("failing_test_changed_by_pr", 0),
                 dvAllSel=k.get("all_tests_selected", 0), dvHard=k.get("tested_the_selector", 0),
                 dvDistinct=dv["distinct_failure_sets"], dvCoreMiss=len(dv["core_missed"]),
                 dvUWK=dv["unreached_or_weakened"]["k"],
                 dvUWPct=_pct(dv["unreached_or_weakened"]["k"] / dv["unreached_or_weakened"]["n"]),
                 dvEscK=dv["escalated_any_reason"]["k"], dvEscPct=_pct(dv["escalated_any_reason"]["k"] / dv["escalated_any_reason"]["n"]),
                 dvEscCleanK=dv["escalated_without_failure"]["k"],
                 dvEscCleanPct=_pct(dv["escalated_without_failure"]["k"] / dv["escalated_without_failure"]["n"]),
                 dvSameN=sp["n"], dvSameStaticPct=_pct(sp["static_final"]), dvSameStoredPct=_pct(sp["stored"]),
                 dvSameFreshPct=_pct(sp["fresh"]), dvSameStoredRevPct=_pct(sp["stored_review"]),
                 dvSameFreshRevPct=_pct(sp["fresh_review"]),
                 dvFinalPRs=fs["prs"], dvFinalChanged=fs["changed_selection"], dvFinalFewer=fs["fewer_selected"],
                 dvFinalSavedOld=_pct(fs["saved_replay"]), dvFinalSavedNew=_pct(fs["saved_final"]),
                 dvFinalNoTestOld=fs["no_test_replay"], dvFinalNoTestNew=fs["no_test_final"],
                 dvBaseFailing=dv["base_failing_prs"], dvNoTestOOS=dv["no_test_with_out_of_scope"],
                 dvCiRed=dv["ci_red"], dvCiGreen=dv["ci_green"], dvRevRed=dv["gate_review_ci_red"],
                 dvRevGreen=dv["gate_review_ci_green"], dvRevGreenPct=_pct(dv["gate_review_ci_green"] / max(1, dv["ci_green"])),
                 dvRevAny=dv["gate_review_any"], dvRevAnyPct=_pct(dv["gate_review_any"] / max(1, dv["ci_red"] + dv["ci_green"])),
                 dvCiRedPct=_pct(dv["ci_red"] / max(1, dv["ci_red"] + dv["ci_green"])),
                 dvApproveRed=dv["ci_red"] - dv["gate_review_ci_red"])
        sd = dv["same_prs_detail"]
        m.update(dvSameReg=sd["regressions"], dvSameMissStatic=sd["missed_static"], dvSameMissStored=sd["missed_stored"],
                 dvSameMissFresh=sd["missed_fresh"], dvSameTimeStatic=_pct(sd["time_static"]),
                 dvSameTimeStored=_pct(sd["time_stored"]), dvSameTimeFresh=_pct(sd["time_fresh"]),
                 dvSameStaticRevPct=_pct(sd["static_review"]))
    sw_all = (mut or {}).get("_holdout", {}).get("swebench")
    if sw_all and "all_selected" in sw_all:
        m["selSweAllSel"] = sw_all["all_selected"]
    lab_path = Path(__file__).resolve().parents[3] / "eval/data/escalation-labels.json"
    if lab_path.exists():  # escalations labelled from their diffs against a fixed rubric
        lab = json.loads(lab_path.read_text())
        wl = [x["label"] for x in lab["W"]]
        ul = [x["label"] for x in lab["U"]]
        m.update(lbWN=len(wl), lbWReal=wl.count("real_weakening"), lbWMoved=wl.count("moved_or_refactored"),
                 lbWLegit=wl.count("legit_removal"), lbWFP=wl.count("detector_false_positive"),
                 lbUN=len(ul), lbUReal=ul.count("real_gap"), lbUNoise=ul.count("non_shipping_noise"),
                 lbUTestLike=ul.count("new_untested_test_like_file"))
    m["tmVersion"] = "2.2.0"
    ab = (mut or {}).get("_holdout", {}).get("ablation")
    if ab:
        am, asw, ash = ab["mutants"], ab["swebench"], ab["shadow"]
        dev = set((mut or {}).get("per_project", {}))
        split = lambda ids: (sum(1 for i in ids if i.rsplit("-", 1)[0] in dev), sum(1 for i in ids if i.rsplit("-", 1)[0] not in dev))  # noqa: E731
        (m["abMutOnlyUDev"], m["abMutOnlyUHold"]) = split(am["static"].get("ids", []))
        (m["abMutOnlyUDynDev"], m["abMutOnlyUDynHold"]) = split(am["dynamic"].get("ids", []))
        m.update(abMutOnlyU=am["static"]["failures_caught_only_by_U"], abMutOnlyUDyn=am["dynamic"]["failures_caught_only_by_U"],
                 abMutSilent=am["static"]["silent_approvals_avoided"], abMutSilentDyn=am["dynamic"]["silent_approvals_avoided"],
                 abMutSurv=am["survivors"], abTmMiss=am.get("testmon_misses", 0), abTmCaughtU=am.get("testmon_misses_caught_by_U_static", 0),
                 abSweOnlyU=asw["caught_only_by_U"], abShOnlyU=ash["static"]["regressions_caught_only_by_U"],
                 abShOnlyUDyn=ash["dynamic"]["regressions_caught_only_by_U"],
                 abShSilentU=ash["static"]["silent_approvals_avoided_U"], abShSilentUDyn=ash["dynamic"]["silent_approvals_avoided_U"],
                 abShSilentW=ash["static"]["silent_approvals_avoided_W"], abShRegW=ash["static"]["regressions_also_flagged_by_W"],
                 abPlainTotal=am["static"]["failures_caught_only_by_U"] + asw["caught_only_by_U"] + ash["static"]["regressions_caught_only_by_U"])
    wk = (mut or {}).get("_holdout", {}).get("weakened")
    if wk:
        m.update(wkShadowN=wk["shadow"]["analysed"], wkShadowK=wk["shadow"]["weakened_prs"],
                 wkShadowPct=_pct(wk["shadow"]["share"]), wkAgentN=wk["agent"]["analysed"],
                 wkAgentK=wk["agent"]["weakened_prs"], wkAgentPct=_pct(wk["agent"]["share"]))
    sw = (mut or {}).get("_holdout", {}).get("swebench")
    if sw:
        m.update(selSweAttempted=sw["attempted"], selSweUsable=sw["usable"], selSweNotLocated=sw["exposing_tests_not_located"],
                 selSweCaughtK=sw["caught"]["k"], selSweMissK=sw["missed"]["k"], selSweUpper=_pct(sw["missed"]["ci95"][1]),
                 selSweByTestK=sw["caught_by_a_selected_test"]["k"], selSweReview=sw["caught_by_review"],
                 selSweFull=sw["caught_by_full_run"], selSweShareMeanPct=_pct(sw["selected_share"]["mean"]),
                 selSweFilePct=_pct(sw["file_level_selected_share"]), selSweRepos=len(sw["by_repo"]),
                 selSweTargetsPct=_pct(sw["targets_selected_share"]))
    tmn = (mut or {}).get("_holdout", {}).get("testmon")
    if tmn:
        m.update(selTmProjects=len(tmn["projects"]), selTmMutants=tmn["mutants"], selTmKilled=tmn["killed"],
                 selTmMissK=tmn["testmon_missed"]["k"], selTmUpper=_pct(tmn["testmon_missed"]["ci95"][1]),
                 selTmSharePct=_pct(tmn["testmon_tests_share_mean"]), selTmGateSharePct=_pct(tmn["gate_tests_share_mean"]),
                 selTmGateMissK=tmn["gate_missed"]["k"])
        hold_names = set(json.loads((Path(__file__).resolve().parents[3] / "eval/data/holdout-projects.json").read_text())["projects"])
        pp = tmn["per_project"]
        def _wshare(names):
            tot = sum(v["mutants"] for n, v in pp.items() if n in names)
            return _pct(sum(v["testmon_share"] * v["mutants"] for n, v in pp.items() if n in names) / tot) if tot else "n/a"
        m.update(selTmHoldSharePct=_wshare(hold_names), selTmDevSharePct=_wshare(set(pp) - hold_names))
        m.update(selTmHoldKilled=sum(v["killed"] for n, v in pp.items() if n in hold_names),
                 selTmHoldMissK=sum(v["testmon_missed"] for n, v in pp.items() if n in hold_names),
                 selTmHoldGateMissK=sum(v["gate_missed"] for n, v in pp.items() if n in hold_names),
                 selTmDevMissK=sum(v["testmon_missed"] for n, v in pp.items() if n not in hold_names),
                 selTmDevKilled=sum(v["killed"] for n, v in pp.items() if n not in hold_names))
    for key, tag in (("holdout", "Hold"), ("holdout_batch3", "HoldFresh"), ("holdout_first_run", "First")):
        h = (mut or {}).get("_holdout", {}).get(key)
        if not h:
            continue
        d = h["levels"]["name"]
        m.update({f"sel{tag}Attempted": h["attempted"], f"sel{tag}Ran": h["n_projects_ran"], f"sel{tag}Valid": h["valid_mutants"],
                  f"sel{tag}Killed": h["killed_by_full_suite"], f"sel{tag}MissK": d["missed_failures"]["k"],
                  f"sel{tag}Upper": _pct(d["missed_failures"]["ci95"][1]),
                  f"sel{tag}MissFileK": h["levels"]["file"]["missed_failures"]["k"],
                  f"sel{tag}SavedPct": _pct(d["tests_saved_share"]),
                  f"sel{tag}TimePct": _pct(h["time_saved_share"]) if h["time_saved_share"] is not None else "n/a"})
        if "dynamic" in h["levels"]:
            dd = h["levels"]["dynamic"]
            m[f"sel{tag}FileSharePct"] = _pct(h["levels"]["file"]["selected_share"]["mean"])
            m.update({f"sel{tag}DynMissK": dd["missed_failures"]["k"], f"sel{tag}DynUpper": _pct(dd["missed_failures"]["ci95"][1]),
                      f"sel{tag}DynSavedPct": _pct(dd["tests_saved_share"]),
                      f"sel{tag}DynSharePct": _pct(dd["selected_share"]["mean"]),
                      f"sel{tag}SharePct": _pct(d["selected_share"]["mean"]),
                      f"sel{tag}ReviewK": d["extra_reviews_green_ci"]["k"], f"sel{tag}DynReviewK": dd["extra_reviews_green_ci"]["k"],
                      f"sel{tag}GreenN": d["extra_reviews_green_ci"]["n"]})
    if attack:
        m["selAtkTimeouts"] = attack.get("invalid_timeouts", 0)
        m.update(selAtkCases=attack["n_cases"], selAtkProjects=attack["n_projects"], selAtkMutants=attack["n_mutants"],
                 selAtkM1=attack["m1_violations"], selAtkM2=attack["m2_violations"],
                 selAtkUpper=_pct(attack["upper_bound_95"]), selAtkHonestFlagged=attack["honest_flagged_cases"])
    if advice:
        m.update(selAdvMutants=advice["n_mutants"], selAdvMeanAdded=advice["mean_added"],
                 selAdvWithAdditions=advice["mutants_with_additions"],
                 selAdvOutsideCore=advice["failing_tests_outside_core"], selAdvNamed=advice["named_by_model"],
                 selAdvTokens=f"{advice['mean_tokens']:,.0f}", selAdvSeconds=advice["mean_seconds"])
    if field and field["tests_total_all"]:
        tot = field["tests_total_all"]
        rb = field["review_breakdown"]
        m.update(selFieldRevNoTests=rb["no_tests"], selFieldRevNonPython=rb["non_python_only"],
                 selFieldRevPython=rb["python_unmentioned"],
                 selFieldSavedFilePct=_pct(1 - field["tests_selected_file_level"] / tot),
                 selFieldSavedNoDynPct=_pct(1 - field["tests_selected_ignoring_dynamic"] / tot),
                 selFieldNoTests=field["no_tests_in_repository"])
    return m


def load_selection_summaries(manifest: dict, rd) -> tuple:
    """Pool the per-project mutant reports and read the other selection reports named in the manifest."""
    sel = manifest.get("selection")
    if not sel:
        return None, None, None, None, None, None
    from .selection_exp import summarize_mutants

    projects = []
    for rel in sel.get("mutants", []):
        projects += rd(rel)["results"]
    mut = summarize_mutants(projects) if projects else None
    runs = []
    for rel in sel.get("mutants_run", []):
        runs += rd(rel)["results"]
    if mut is not None and runs:
        first = summarize_mutants(runs)
        mut["first_run"] = {"valid": first["valid_mutants"], "disagree": first["disagreements_between_derived_and_actual"],
                            "errors": first["selected_run_errors"], "killed": first["killed_by_full_suite"]}
    from .selection_exp import summarize_field

    field = summarize_field(rd(sel["field"])["results"]) if "field" in sel else None
    aidev = rd(sel["aidev"])["summary"] if "aidev" in sel else None
    demo = rd(sel["demo"])["summary"] if "demo" in sel else None
    from .selection_exp import summarize_advice, summarize_attack

    atk_projects = [r for rel in sel.get("attack", []) for r in rd(rel)["results"]]
    attack = summarize_attack(atk_projects) if atk_projects else None
    adv_studies = [r for rel in sel.get("advice", []) for r in rd(rel)["results"]]
    advice = summarize_advice(adv_studies) if adv_studies else None
    extra = {}
    from .selection_exp import summarize_testmon
    from .swebench_exp import summarize as summarize_swe

    if sel.get("ablation"):
        extra["ablation"] = rd(sel["ablation"])["summary"]
    if sel.get("weakened"):
        extra["weakened"] = rd(sel["weakened"])["summary"]
    if sel.get("agentic_scan"):
        extra["agentic_scan"] = rd(sel["agentic_scan"])["summary"]
    if sel.get("shadow_stale_dir"):
        from .shadow_exp import summarize as summarize_shadow2

        base3 = Path(__file__).resolve().parents[3]
        rows = [json.loads(f.read_text()) for f in sorted((base3 / sel["shadow_stale_dir"]).glob("*.json"))]
        if rows:
            sm = summarize_shadow2(rows)
            ages = sorted(r["map_age_commits"] for r in rows if r.get("status") == "ran" and isinstance(r.get("map_age_commits"), int)
                          and r["map_age_commits"] >= 0)
            if ages:
                sm["map_age_median"], sm["map_age_max"] = ages[len(ages) // 2], ages[-1]
            maps = [json.loads(f.read_text()) for f in sorted((base3 / sel.get("shadow_maps_dir", "eval/shadow-maps")).glob("*.json"))]
            sm["maps_ok"], sm["maps_attempted"] = sum(1 for x in maps if x.get("status") == "ok"), len(maps)
            extra["shadow_stale"] = sm
    if sel.get("shadow_dir"):
        from .shadow_exp import summarize as summarize_shadow

        rows = [json.loads(f.read_text()) for f in sorted((Path(__file__).resolve().parents[3] / sel["shadow_dir"]).glob("*.json"))]
        if rows:
            extra["shadow"] = summarize_shadow(rows)
            from .shadow_exp import derived

            stale = [json.loads(f.read_text()) for f in sorted((Path(__file__).resolve().parents[3]
                                                                 / sel.get("shadow_stale_dir", "eval/shadow-stale")).glob("*.json"))]
            wk_rows = rd(sel["weakened"])["results"] if sel.get("weakened") else []
            extra["shadow_derived"] = derived(rows, stale, wk_rows)
    swe_rows = [r for rel in sel.get("swebench", []) for r in rd(rel)["results"]]
    if swe_rows:
        extra["swebench"] = summarize_swe(swe_rows)
        extra["swebench"]["all_selected"] = sum(1 for r in swe_rows if r.get("status") == "ok" and r.get("n_targets")
                                                and r["selected"] >= r["n_tests"])
    tm = [r for rel in sel.get("testmon", []) for r in rd(rel)["results"]]
    if tm:
        extra["testmon"] = summarize_testmon(tm)
    for key in ("holdout", "holdout_batch3", "holdout_first_run"):
        rs = [r for rel in sel.get(key, []) for r in rd(rel)["results"]]
        if rs:
            extra[key] = summarize_mutants(rs)
            extra[key]["attempted"] = len(rs)
    if mut is not None:
        mut["_holdout"] = extra
    return mut, field, aidev, demo, attack, advice



def build_macros(
    *, adversary_before: dict | None, adversary_after: dict | None, mutation: dict | None,
    routing: dict | None = None, injection: dict[str, dict] | None = None,
    adversary_real: dict[str, tuple[dict, dict | None]] | None = None,
    real_suites: list[dict] | None = None,
    aidev: dict | None = None,
    noise: dict[str, dict] | None = None,
    field: dict | None = None,
    layouts: dict | None = None,
    no_model: dict | None = None,
) -> dict[str, object]:
    """Numbers cited in the paper's text, named so prose can only quote a report.
    LaTeX macro names must be letters only, hence the camelCase without digits."""
    m: dict[str, object] = {}
    if adversary_after:
        a = adversary_after["summary"]
        m.update(advCasesAfter=a["n_cases"], advViolationsAfter=a["violations"],
                 advScenarios=a["n_scenarios"], advRouterCases=a["n_router_only_cases"],
                 advClassifierCases=a["n_classifier_adversary_cases"])
        m["advUpperBoundPct"] = _pct(wilson_upper(a["violations"], a["n_cases"]))
    if adversary_before:
        b = adversary_before["summary"]
        m.update(advCasesBefore=b["n_cases"], advViolationsBefore=b["violations"],
                 advVerdictBefore=b["i1_verdict_violations"], advFailuresBefore=b["i2_failure_count_violations"],
                 advCoverageBefore=b["i3_coverage_violations"])
    if mutation:
        s = mutation["summary"]["all"]
        m.update(mutGenerated=s["n_generated"], mutValid=s["n_valid"], mutKilled=s["n_killed"],
                 mutSurvived=s["n_survived"])
        for key, name in (("unit-only", "Unit"), ("unit+property", "UnitProp"),
                          ("unit+adversarial", "UnitAdv"), ("full-pyramid", "Full")):
            d = s["baselines"][key]
            m[f"mutRate{name}"] = _pct(d["rate"])
            m[f"mutLo{name}"], m[f"mutHi{name}"] = _pct(d["ci95"][0]), _pct(d["ci95"][1])
        for key, name in (("unit-only", "Unit"), ("unit+property", "UnitProp"), ("unit+adversarial", "UnitAdv")):
            d = s["paired_vs_full"][key]
            m[f"mutOnlyFullVs{name}"], m[f"mutPVs{name}"] = d["only_full"], f"{d['p_value']:.2g}"
        for system, sname in (("inventory", "Inv"), ("support", "Sup")):
            if system in mutation["summary"]:
                ss = mutation["summary"][system]
                m[f"mutValid{sname}"] = ss["n_valid"]
                m[f"mutRateFull{sname}"] = _pct(ss["baselines"]["full-pyramid"]["rate"])
                m[f"mutRateUnit{sname}"] = _pct(ss["baselines"]["unit-only"]["rate"])
        cap = {"unit": "Unit", "property": "Property", "trajectory": "Trajectory",
               "llm_judge": "Judge", "adversarial": "Adversarial"}
        for layer, d in s["per_layer"].items():
            m[f"mutKills{cap[layer]}"], m[f"mutUnique{cap[layer]}"] = d["kills"], d["unique_kills"]

    if routing:
        cap = {"manifest": "Manifest", "stub": "Heuristic", "openai-gpt-4o-mini": "Mini", "openai-gpt-4o": "GptO",
               "mistral-mistral-large-latest": "MistralLarge", "mistral-mistral-small-latest": "MistralSmall",
               "manifest+diff": "ManifestDiff"}
        for key, nm in cap.items():
            if key in routing["routers"]:
                m[f"routAcc{nm}"] = _pct(routing["routers"][key]["per_commit"]["rate"])
                lo, hi = routing["routers"][key]["per_commit"]["cluster"]["ci95"]
                m[f"routLo{nm}"], m[f"routHi{nm}"] = _pct(lo), _pct(hi)
                m[f"routAgRecall{nm}"] = _pct(routing["routers"][key]["agentic_recall"]["rate"])
            if key + "-masked" in routing["routers"]:
                m[f"routAccMasked{nm}"] = _pct(routing["routers"][key + "-masked"]["per_commit"]["rate"])
        m["routCommits"] = routing["n"]
        for key, d in routing["name_effect"].items():
            nm = cap.get(key)
            if nm:
                ad = d["accuracy_diff"]
                m[f"routNameEffect{nm}"] = f"{100 * ad['mean']:.1f}"
                m[f"routNameEffectLo{nm}"], m[f"routNameEffectHi{nm}"] = _pct(ad["ci95"][0]), _pct(ad["ci95"][1])
        for key, d in routing["paired_vs_heuristic"].items():
            nm = cap.get(key)
            if nm and not key.endswith("-masked"):
                ad = d["accuracy_diff"]
                m[f"routVsHeur{nm}"] = f"{100 * ad['mean']:.1f}"
                m[f"routVsHeurLo{nm}"], m[f"routVsHeurHi{nm}"] = _pct(ad["ci95"][0]), _pct(ad["ci95"][1])
    if adversary_real:
        for name, (after, before) in adversary_real.items():
            nm = name.capitalize()
            a = after["summary"]
            m[f"advReal{nm}Cases"], m[f"advReal{nm}Violations"] = a["n_cases"], a["violations"]
            if before:
                m[f"advReal{nm}ViolationsBefore"] = before["summary"]["violations"]
    if injection:
        from .injection import fisher_exact_two_sided

        runs = sum(r["injection"]["overall"]["n"] for r in injection.values())
        ctrl = sum(r["injection"]["control"]["n"] for r in injection.values())
        with_fail = sum(r["injection"]["overall"]["n_with_failures"] for r in injection.values())
        base_fa = sum(r["injection"]["overall"]["baseline_false_approves"] for r in injection.values())
        flaky = [r["injection"]["by_payload"]["flaky-dismiss"] for r in injection.values()]
        fl_h, fl_n = sum(x["baseline_false_approves"] for x in flaky), sum(x["n_with_failures"] for x in flaky)
        mis = [r["injection"]["by_payload"]["routing-misdirect"] for r in injection.values()]
        m.update(
            injRuns=runs, injControlRuns=ctrl, injModels=len(injection),
            injPipelineFalseApproves=sum(r["injection"]["overall"]["false_approves"] for r in injection.values()),
            injPipelineFlips=sum(r["injection"]["overall"]["verdict_flips"] for r in injection.values()),
            injWithFailures=with_fail,
            injNarrativeSafe=sum(r["injection"]["overall"]["narrative_declares_safe"] for r in injection.values()),
            injNarrativeOmit=sum(r["injection"]["overall"]["narrative_omits_failure"] for r in injection.values()),
            injBaselineFalseApproves=base_fa, injBaselinePct=_pct(base_fa / with_fail),
            injBaselineControlFalse=sum(r["injection"]["control"]["baseline_false_approves"] for r in injection.values()),
            injFlakyHits=fl_h, injFlakyN=fl_n, injFlakyPct=f"{100 * fl_h / fl_n:.0f}",
            injOtherHits=base_fa - fl_h, injOtherN=with_fail - fl_n,
            injFlakyP=f"{fisher_exact_two_sided(fl_h, fl_n - fl_h, base_fa - fl_h, with_fail - fl_n - (base_fa - fl_h)):.1e}",
            injRoutingFlips=sum(r["injection"]["overall"]["routing_flips"] for r in injection.values()),
            injMisdirectFlips=sum(x["routing_flips"] for x in mis), injMisdirectN=sum(x["n"] for x in mis),
        )
        lo, hi = wilson(base_fa, with_fail)
        m["injBaselineLo"], m["injBaselineHi"] = _pct(lo), _pct(hi)
        m["injGateUpperPct"] = _pct(wilson(0, with_fail)[1])
    if real_suites:
        ran = [r for r in real_suites if r["status"] == "ran"]
        m.update(
            realSuitesTried=len(real_suites), realSuitesRan=len(ran),
            realSuitesCouldNot=len(real_suites) - len(ran),
            realCases=sum(r["cases"] for r in ran), realViolations=sum(r["violations"] for r in ran),
            realTestsTotal=f"{sum(r['tests'] for r in ran):,}",
            realSuitesWithBaselineFailures=sum(1 for r in ran if r["baseline_failed"]),
        )
    if aidev:
        o = aidev["prevalence"]["overall"]
        m.update(aidevPRs=f"{o['n']:,}", aidevLong=f"{o['says_tests_pass']['of']:,}",
                 aidevSaysPassPct=_pct(o["says_tests_pass"]["share"]),
                 aidevNoBreakagePct=_pct(o["says_no_breakage"]["share"]),
                 aidevApprovedByPct=_pct(o["claims_review_or_approval"]["share"]),
                 aidevReadyPct=_pct(o["says_ready"]["share"]))
        names = {"Claude_Code": "ClaudeCode", "Copilot": "Copilot", "Devin": "Devin", "Cursor": "Cursor",
                 "Google_Jules": "Jules", "OpenAI_Codex": "Codex"}
        for agent, short in names.items():
            b = aidev["prevalence"]["by_agent"].get(agent)
            if b:
                m[f"aidevPass{short}"] = _pct(b["says_tests_pass"]["share"])
    if noise:
        plain_names = ("stock-check-off-by-one", "stale-stock-bound-property-only",
                       "name-length-bound-removed-adversarial-only")
        cap = {"openai-gpt-4o-mini": "Mini", "openai-gpt-4o": "GptO",
               "mistral-mistral-small-latest": "MistralSmall", "mistral-mistral-large-latest": "MistralLarge"}
        tot_flips = tot_calls = plain_flips = plain_calls = 0
        for key, rep in noise.items():
            s_ = rep["summary"]
            nm = cap.get(key, key)
            m[f"noisePct{nm}"] = _pct(s_["flip_rate"])
            lo, hi = s_["cluster_by_description"]["ci95"]
            m[f"noiseLo{nm}"], m[f"noiseHi{nm}"] = _pct(lo), _pct(hi)
            n_desc = len({r["description_id"] for r in rep["rows"]})
            tot_flips += s_["flips"]
            tot_calls += s_["calls"]
            plain_flips += sum(v for k, v in s_["by_scenario"].items() if k in plain_names)
            plain_calls += n_desc * len(plain_names)
            m[f"noiseDescriptions"] = n_desc
        m.update(noiseFlips=tot_flips, noiseCalls=tot_calls, noisePlainPct=_pct(plain_flips / plain_calls),
                 noiseScenarios=len(next(iter(noise.values()))["summary"]["by_scenario"]),
                 noiseOtherPct=_pct((tot_flips - plain_flips) / (tot_calls - plain_calls)))

    if field:
        f = field["summary"]
        fun = f["funnel"]
        why = f["failure_reasons"]
        m.update(
            fieldAttempted=f["attempted"], fieldRepos=f["distinct_repos_attempted"], fieldRan=f["ran"],
            fieldRanPct=_pct(f["ran_share"]["rate"]), fieldRanLo=_pct(f["ran_share"]["ci95"][0]),
            fieldRanHi=_pct(f["ran_share"]["ci95"][1]), fieldRanRepos=f["distinct_repos_ran"],
            fieldFlagged=f["head_flagged"]["k"], fieldWithBase=f["with_base"],
            fieldIntroduced=f["introduced_failures"]["k"],
            fieldCollectionFailed=fun.get("collection_failed", 0), fieldBuildFailed=fun.get("build_or_run_failed", 0),
            fieldNoTests=fun.get("no_tests", 0),
            fieldWhyMissingPkg=why.get("tests import a package we did not install", 0),
            fieldWhyInstall=why.get("dependencies could not be installed", 0),
            fieldWhyTimeout=why.get("timeout while building or collecting", 0),
            fieldWhyRunner=why.get("the test runner found no pytest-style tests", 0),
        )
        m["fieldWhyOther"] = f["attempted"] - f["ran"] - sum(m[k] for k in (
            "fieldWhyMissingPkg", "fieldWhyInstall", "fieldWhyTimeout", "fieldWhyRunner", "fieldNoTests"))
    if layouts:
        t = layouts["tally"]
        m.update(
            fieldMissRepos=len(layouts["projects"]),
            fieldMissStandard=sum(v for k, v in t.items() if k.startswith("standard")),
            fieldMissPoetry=sum(v for k, v in t.items() if k.startswith("Poetry")),
            fieldMissNone=sum(v for k, v in t.items() if k.startswith("no [project]")),
            fieldMissReqOnly=sum(v for k, v in t.items() if k.startswith("requirements.txt only")),
        )
    if no_model:
        b, ms = no_model["stub"], list(no_model["models"].values())
        lo = lambda key: min(x[key] for x in ms)  # noqa: E731
        hi = lambda key: max(x[key] for x in ms)  # noqa: E731
        m.update(
            nmN=b["n"], nmBugs=b["bugs"], nmStubVerdict=b["verdict_ok"],
            nmStubLayers=b["layers_exact"], nmStubKind=b["repo_kind_ok"],
            nmStubBugsExact=b["bugs_all_layers_exact"], nmStubBugsMissed=b["bugs"] - b["bugs_all_layers_exact"],
            nmModelVerdictMin=lo("verdict_ok"), nmModelLayersMin=lo("layers_exact"), nmModelLayersMax=hi("layers_exact"),
            nmModelKindMin=lo("repo_kind_ok"), nmModelKindMax=hi("repo_kind_ok"),
            nmStubLayerMisses=b["n"] - b["layers_exact"],
            nmModelMissMin=b["n"] - hi("layers_exact"), nmModelMissMax=b["n"] - lo("layers_exact"),
            nmStubSeconds=round(b["seconds"]), nmModelSecondsMin=round(lo("seconds")),
            nmModelSecondsMax=round(hi("seconds")),
            nmTokensMin=f"{round(lo('tokens') / 1000):,}", nmTokensMax=f"{round(hi('tokens') / 1000):,}",
        )
    return m


def wilson_upper(k: int, n: int, z: float = 1.96) -> float:
    from .mutation import wilson

    return wilson(k, n, z)[1]


def recompute_mutation_summary(report: dict) -> dict:
    """Rebuild the summaries from the per-mutant records, so figures and macros
    never depend on a derived field an older run happened to store (or lack)."""
    from . import mutation as mut

    results = [
        mut.MutantResult(m["system"], m["id"], m["operator"], m["line"], m["description"],
                         states=m["states"], failed_tests=m["failed_tests"])
        for m in report["mutants"]
    ]
    systems = sorted({r.system for r in results})
    summary = {"all": mut.summarize(results)}
    summary.update({name: mut.summarize([r for r in results if r.system == name]) for name in systems})
    return {**report, "summary": summary}


def render_all(manifest_path: Path, out: Path) -> list[Path]:
    """Render every figure and the number macros from the evidence a manifest pins.

    The manifest names the exact report file behind each figure, so a figure is
    traceable to a run rather than to "whatever was latest"."""
    manifest = json.loads(Path(manifest_path).read_text())
    root = Path(manifest_path).resolve().parents[2]  # <repo>/docs/figures/evidence.json
    rd = lambda rel: load(root / rel)  # noqa: E731
    written: list[Path] = []
    if "catch_matrix" in manifest:
        written += catch_matrix([(title, rd(rel)) for title, rel in manifest["catch_matrix"]], out)
    written += framework_diagram(out)
    before = rd(manifest["adversary_before"]) if "adversary_before" in manifest else None
    after = rd(manifest["adversary_after"]) if "adversary_after" in manifest else None
    if before and after:
        written += adversary_before_after(before, after, out)
    mutation = recompute_mutation_summary(rd(manifest["mutation"])) if "mutation" in manifest else None
    if mutation:
        written += mutation_baselines(mutation, out) + mutation_unique(mutation, out)
    routing = None
    if "routing" in manifest:
        from .routing_analysis import analyze

        routing = analyze([rd(rel) for rel in manifest["routing"]])
        written += routing_accuracy(routing, out)
    injection = None
    if "injection" in manifest:
        from .injection import recompute_narrative_flags

        injection = {model: recompute_narrative_flags(rd(rel)) for model, rel in manifest["injection"].items()}
        written += injection_heatmaps(injection, out)
    adv_real = {
        name: (rd(spec["after"]), rd(spec["before"]) if "before" in spec else None)
        for name, spec in manifest.get("adversary_real", {}).items()
    }
    real_suites = []
    for entry in manifest.get("real_suites", []):
        rep = rd(entry["report"])
        if rep.get("could_not_run"):
            real_suites.append({"name": entry["name"], "status": "could_not_run", "reason": rep["reason"]})
            continue
        c0 = rep["cases"][0]
        real_suites.append({"name": entry["name"], "status": "ran", "tests": c0["baseline_total"],
                            "baseline_failed": c0["baseline_failed"], "cases": rep["summary"]["n_cases"],
                            "violations": rep["summary"]["violations"]})
    aidev = rd(manifest["aidev_claims"]) if "aidev_claims" in manifest else None
    noise = {}
    for entry in manifest.get("route_noise", []):
        rep = rd(entry)
        if rep.get("valid", True):
            noise[rep["router"]] = rep
    field = rd(manifest["field_study"]) if "field_study" in manifest else None
    macros = build_macros(adversary_before=before, adversary_after=after, mutation=mutation,
                          routing=routing, injection=injection, adversary_real=adv_real or None,
                          real_suites=real_suites or None, aidev=aidev, noise=noise or None, field=field,
                          layouts=rd(manifest["field_layouts"]) if "field_layouts" in manifest else None,
                          no_model=no_model_comparison(rd(manifest["no_model_baseline"]), injection)
                          if "no_model_baseline" in manifest and injection else None)
    mut, sfield, saidev, sdemo, sattack, sadvice = load_selection_summaries(manifest, rd)
    macros.update(selection_macros(mut, sfield, saidev, sdemo, sattack, sadvice))
    if mut and mut.get("valid_mutants"):
        written += selection_savings(mut, out)
    write_macros(out / "numbers.tex", macros)
    written.append(out / "numbers.tex")
    return written


def write_macros(path: Path, macros: dict[str, object]) -> None:
    lines = ["% Generated by `agenticqa eval-plot` from the report JSON files. Do not edit."]
    for name, value in sorted(macros.items()):
        lines.append(f"\\newcommand{{\\{name}}}{{{value}}}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n")

"""CSV, PNG and PDF exports, all built from a finished analysis result."""
from __future__ import annotations

import csv
import io

import matplotlib

matplotlib.use("Agg")  # no display on a server
import matplotlib.pyplot as plt  # noqa: E402
from reportlab.lib import colors  # noqa: E402
from reportlab.lib.pagesizes import A4  # noqa: E402
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet  # noqa: E402
from reportlab.lib.units import cm  # noqa: E402
from reportlab.platypus import (Image, Paragraph, SimpleDocTemplate, Spacer,  # noqa: E402
                                Table, TableStyle)
from xml.sax.saxutils import escape  # noqa: E402

YEARLY_COLS = ["year", "events", "heatwave_days", "max_duration", "cumulative_excess",
               "mean_peak_tmax", "coverage", "complete"]
EVENT_COLS = ["start", "end", "duration", "peak_tmax", "mean_excess", "cumulative_excess"]


def _safe_cell(v):
    """Neutralise spreadsheet formula injection in text cells (OWASP CSV injection)."""
    if isinstance(v, str) and v[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + v
    return "" if v is None else v


def to_csv(result: dict, table: str) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    rows, cols = (result["yearly"], YEARLY_COLS) if table == "yearly" else (result["events"], EVENT_COLS)
    w.writerow(cols)
    for r in rows:
        w.writerow([_safe_cell(r.get(c)) for c in cols])
    return buf.getvalue()


def trend_png(result: dict, metric: str, width_in: float = 8, height_in: float = 3.2) -> bytes:
    yearly = result["yearly"]
    trend = result["trends"][metric]
    years = [r["year"] for r in yearly]
    vals = [r[metric] for r in yearly]

    fig, ax = plt.subplots(figsize=(width_in, height_in), dpi=150)
    ax.bar(years, [v if v is not None else 0 for v in vals], color="#e8a07a", width=0.8,
           label="Yearly value")
    roll = trend["rolling_mean"]
    ax.plot(years, [v if v is not None else float("nan") for v in roll], color="#b8432f",
            linewidth=2, label=f"{trend['rolling_window']}-yr rolling mean")
    if trend.get("sen"):
        s = trend["sen"]
        ax.plot([years[0], years[-1]],
                [s["intercept"] + s["slope_per_year"] * y for y in (years[0], years[-1])],
                color="#333333", linestyle="--", linewidth=1.5,
                label=f"Sen trend (MK p={trend['mann_kendall']['p_value']:.3f})")
    ax.set_title(result["metrics"][metric], fontsize=11, loc="left")
    ax.spines[["top", "right"]].set_visible(False)
    ax.grid(axis="y", alpha=0.3)
    ax.legend(fontsize=8, frameon=False, loc="upper left")
    fig.tight_layout()
    out = io.BytesIO()
    fig.savefig(out, format="png")
    plt.close(fig)
    return out.getvalue()


def to_pdf(result: dict) -> bytes:
    styles = getSampleStyleSheet()
    body = ParagraphStyle("body", parent=styles["BodyText"], fontSize=9.5, leading=13)
    small = ParagraphStyle("small", parent=body, fontSize=8, leading=10.5, textColor=colors.grey)
    h2 = styles["Heading2"]
    meta, ins, thr = result["meta"], result["insights"], result["threshold"]

    def P(text, style=body):
        return Paragraph(escape(str(text)), style)

    story = [
        Paragraph("Heatwave Trend Report", styles["Title"]),
        P(f"Region: {meta['region']['label']}  |  {meta['start_year']}-{meta['end_year']}"),
        P(f"Definition: {meta['min_days']}+ consecutive days above the {thr['description']} "
          f"(baseline {thr['baseline'][0]}-{thr['baseline'][1]})."),
        Spacer(1, 0.3 * cm),
        Paragraph("Key finding", h2),
    ]
    if ins["headline"]:
        story.append(P(ins["headline"]["text"]))
    for s in ins["supporting"]:
        story.append(P("• " + s["text"]))
    for f in ins["facts"]:
        story.append(P("• " + f))

    for metric in ("events", "heatwave_days"):
        story.append(Spacer(1, 0.3 * cm))
        story.append(Image(io.BytesIO(trend_png(result, metric)), width=17 * cm, height=6.8 * cm))

    story += [Paragraph("Caveats", h2)] + [P("• " + c) for c in ins["caveats"]]

    story.append(Paragraph("Yearly values", h2))
    rows = [["Year", "Events", "HW days", "Longest", "°C·days", "Coverage"]]
    for r in result["yearly"]:
        rows.append([r["year"], *("–" if r[k] is None else r[k] for k in
                                  ("events", "heatwave_days", "max_duration", "cumulative_excess")),
                     f"{r['coverage']:.0%}"])
    t = Table(rows, repeatRows=1)
    t.setStyle(TableStyle([
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f2e6df")),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#cccccc")),
        ("ALIGN", (1, 1), (-1, -1), "RIGHT"),
    ]))
    story.append(t)

    top = sorted(result["events"], key=lambda e: -e["cumulative_excess"])[:10]
    if top:
        story.append(Paragraph("Ten most intense events", h2))
        rows = [["Start", "End", "Days", "Peak °C", "°C·days"]] + [
            [e["start"], e["end"], e["duration"], e["peak_tmax"], e["cumulative_excess"]] for e in top]
        t2 = Table(rows, repeatRows=1)
        t2.setStyle(TableStyle([
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f2e6df")),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#cccccc")),
        ]))
        story.append(t2)

    ds, q = meta["data_source"], result["quality"]
    story += [
        Spacer(1, 0.4 * cm),
        P(f"Data: {ds['name']}, {ds['dataset']}, {ds['resolution']}, {ds['variable']}. "
          f"Quality control: {q['interpolated']} days interpolated, {q['spikes_removed']} spikes and "
          f"{q['out_of_range_removed']} impossible values removed, {q['still_missing']} days still missing. "
          f"Generated {meta['generated_at']} (analysis v{meta['analysis_version']}).", small),
    ]
    out = io.BytesIO()
    SimpleDocTemplate(out, pagesize=A4, leftMargin=2 * cm, rightMargin=2 * cm,
                      topMargin=1.8 * cm, bottomMargin=1.8 * cm,
                      title="Heatwave Trend Report").build(story)
    return out.getvalue()

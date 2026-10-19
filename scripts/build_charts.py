"""
สร้างกราฟ PNG ทีละกอง + ไฟล์สรุปผลตอบแทน สำหรับหน้าเว็บ docs/

อ่าน  : funds.csv, data/nav/*.csv, data/fetch_report.json
เขียน : docs/charts/<กอง>.png, docs/data.json
"""

import csv
import json
import os
import re
from datetime import date, timedelta
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
NAV_DIR = ROOT / "data" / "nav"
OUT = ROOT / "docs"
CHARTS = OUT / "charts"

LOOKBACK_DAYS = int(os.getenv("LOOKBACK_DAYS", "1095"))   # 3 ปี
WEEKLY = os.getenv("WEEKLY", "1") == "1"   # 1 = ใช้ NAV สิ้นสัปดาห์ (เหมือนกราฟเดิม)

# ขนาดเท่ากรอบกราฟในรายงาน: 520 x 212 pt = 7.22 x 2.94 นิ้ว
FIG_W, FIG_H, DPI = 7.22, 2.94, 300

NAVY = "#1b3560"          # เส้นกองที่แนะนำ
PEERS = ["#eb6834", "#1baf7a", "#eda100", "#e87ba4"]   # เส้นเปรียบเทียบ ลำดับคงที่
RED = "#d0273e"
INK, MUTED, GRID = "#1f2937", "#6b7280", "#e5e7eb"


def slug(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-").upper()


def read_funds():
    with (ROOT / "funds.csv").open(encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    out = []
    for r in rows:
        name = (r.get("fund") or "").strip()
        if not name:
            continue
        try:
            rec = date.fromisoformat((r.get("recommend_date") or "").strip())
        except ValueError:
            rec = None
        peers = [p.strip() for p in re.split(r"[;|]", r.get("compare_with") or "") if p.strip()]
        out.append({"name": name, "rec": rec, "peers": peers})
    return out


def load_nav(name):
    p = NAV_DIR / f"{slug(name)}.csv"
    if not p.exists():
        return None
    s = pd.read_csv(p, parse_dates=["date"]).set_index("date")["nav"].sort_index()
    return s if len(s) else None


def nav_on_or_before(s: pd.Series, d):
    sub = s[s.index <= pd.Timestamp(d)]
    return float(sub.iloc[-1]) if len(sub) else None


def pct(a, b):
    return None if a is None or b is None or b == 0 else (a / b - 1) * 100


def returns(s: pd.Series, rec):
    last_d = s.index[-1]
    last = float(s.iloc[-1])
    out = {"as_of": last_d.date().isoformat(), "nav": round(last, 4)}
    for k, days in [("1W", 7), ("1M", 30), ("3M", 91), ("6M", 182), ("1Y", 365)]:
        out[k] = pct(last, nav_on_or_before(s, last_d - timedelta(days=days)))
    out["since_rec"] = pct(last, nav_on_or_before(s, rec)) if rec else None
    return out


def fmt_pct(v):
    return "–" if v is None else f"{v:+.2f}%"


def draw(fund, series: dict, start: pd.Timestamp, path: Path):
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 7})
    fig, ax = plt.subplots(figsize=(FIG_W, FIG_H), dpi=DPI)
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")

    ends = []
    order = [(fund["name"], NAVY, 2.0, 3)] + [
        (p, PEERS[i % len(PEERS)], 1.2, 2) for i, p in enumerate(fund["peers"]) if p in series
    ]
    for name, color, lw, z in order:
        s = series[name]
        s = s[s.index >= start]
        if WEEKLY:
            s = s.resample("W-FRI").last().dropna()
            # ให้จุดสุดท้ายเป็นวันล่าสุดจริง ไม่ใช่วันศุกร์ล่วงหน้า
            last = series[name]
            s.index = s.index.where(s.index <= last.index[-1], last.index[-1])
        if len(s) < 2:
            continue
        y = (s / s.iloc[0] - 1) * 100
        ax.plot(y.index, y.values, color=color, lw=lw, zorder=z,
                solid_capstyle="round", label=f"{name}  {y.iloc[-1]:+.2f}%")
        ends.append((y.index[-1], y.iloc[-1], color, z))

    # จุดปลายเส้น
    for x, v, c, z in ends:
        ax.scatter([x], [v], s=14, color=c, zorder=z + 1, edgecolors="white", linewidths=0.8)

    ax.axhline(0, color="#9ca3af", lw=0.8, zorder=1)

    # เว้นที่ว่างด้านบนให้ป้ายวันแนะนำ ไม่ทับเส้น
    lo, hi = ax.get_ylim()
    lo, hi = min(lo, 0), max(hi, 0)
    rng = hi - lo or 1
    ax.set_ylim(lo - rng * 0.04, hi + rng * 0.12)

    # เส้นวันแนะนำ
    if fund["rec"] and pd.Timestamp(fund["rec"]) >= start:
        rx = pd.Timestamp(fund["rec"])
        ax.axvline(rx, color=RED, lw=1, ls=(0, (3, 2)), zorder=2)
        x0, x1 = mdates.date2num(start), mdates.date2num(pd.Timestamp(max(s.index[-1] for s in series.values())))
        near_right = (mdates.date2num(rx) - x0) / max(x1 - x0, 1) > 0.72
        ax.annotate(f"Recommended {fund['rec'].strftime('%d/%m/%Y')}", xy=(rx, 1), xycoords=("data", "axes fraction"),
                    xytext=(-3 if near_right else 3, -2), textcoords="offset points", va="top",
                    ha="right" if near_right else "left", fontsize=6.2, color=RED,
                    bbox=dict(boxstyle="square,pad=0.15", fc="white", ec="none"))

    # แกน
    ax.yaxis.tick_right()
    ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:.0f}%"))
    ax.xaxis.set_major_locator(mdates.MonthLocator(bymonth=(1, 4, 7, 10)))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %y"))
    ax.tick_params(colors=MUTED, length=0, labelsize=6.5)
    ax.grid(axis="y", color=GRID, lw=0.6)
    ax.set_axisbelow(True)
    for sp in ax.spines.values():
        sp.set_visible(False)
    ax.spines["bottom"].set_visible(True)
    ax.spines["bottom"].set_color("#d1d5db")
    ax.margins(x=0.01)

    # ชื่อกองมุมซ้ายบน
    ax.text(0.012, 0.985, fund["name"], transform=ax.transAxes, ha="left", va="top",
            fontsize=8.5, fontweight="bold", color=NAVY)


    fig.subplots_adjust(left=0.02, right=0.93, top=0.97, bottom=0.11)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=DPI, facecolor="white")
    plt.close(fig)


def main():
    funds = read_funds()
    report_p = ROOT / "data" / "fetch_report.json"
    report = json.loads(report_p.read_text()) if report_p.exists() else {"funds": {}, "missing": {}}
    today = date.today()

    CHARTS.mkdir(parents=True, exist_ok=True)
    for old in CHARTS.glob("*.png"):
        old.unlink()

    out = {"updated": today.isoformat(), "funds": []}
    for f in funds:
        series = {}
        for n in [f["name"], *f["peers"]]:
            s = load_nav(n)
            if s is not None:
                series[n] = s
        entry = {
            "name": f["name"],
            "recommend_date": f["rec"].isoformat() if f["rec"] else None,
            "peers": [p for p in f["peers"] if p in series],
            "missing": {n: report.get("missing", {}).get(n, "ยังไม่มีข้อมูล NAV")
                        for n in [f["name"], *f["peers"]] if n not in series},
            "warnings": [f"{n}: {report['funds'][n]['warning']}" for n in [f["name"], *f["peers"]]
                         if report.get("funds", {}).get(n, {}).get("warning")],
            "info": report.get("funds", {}).get(f["name"], {}),
        }
        if f["name"] in series:
            start_d = today - timedelta(days=LOOKBACK_DAYS)
            if f["rec"]:
                start_d = min(start_d, f["rec"] - timedelta(days=31))
            start = max(pd.Timestamp(start_d), series[f["name"]].index[0])
            img = f"charts/{slug(f['name'])}.png"
            draw(f, series, start, OUT / img)
            entry["image"] = img
            entry["returns"] = returns(series[f["name"]], f["rec"])
            print(f"chart {f['name']}: since rec {fmt_pct(entry['returns']['since_rec'])}")
        out["funds"].append(entry)

    (OUT / "data.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()

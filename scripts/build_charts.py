"""
สร้างกราฟ PNG ทีละกอง + ไฟล์สรุปผลตอบแทน สำหรับหน้าเว็บ docs/

อ่าน  : funds.csv, data/nav/*.csv, data/fetch_report.json
เขียน : docs/charts/<กอง>.png, docs/data.json
"""

import csv
import hashlib
import json
import os
import re
from datetime import date, timedelta
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
NAV_DIR = ROOT / "data" / "nav"
OUT = ROOT / "docs"
CHARTS = OUT / "charts"

LOOKBACK_DAYS = int(os.getenv("LOOKBACK_DAYS", "365"))   # ใช้เฉพาะกองที่ไม่ได้ใส่วันแนะนำ

# ขนาดเท่ากรอบกราฟในรายงาน: 520 x 212 pt = 7.22 x 2.94 นิ้ว
FIG_W, FIG_H, DPI = 7.22, 2.94, 300

NAVY = "#1b3560"          # เส้นกองที่แนะนำ
PEERS = ["#eb6834", "#1baf7a", "#eda100", "#e87ba4"]   # เส้นเปรียบเทียบ ลำดับคงที่
RED = "#d0273e"
INK, MUTED, GRID, FRAME = "#1f2937", "#6b7280", "#e8eaee", "#d9dde3"


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


def zigzag(v, th):
    """จุดกลับตัวแบบ ZigZag: นับเป็นจุดกลับตัวเมื่อราคาย้อนจากจุดสุดขั้วเกิน th (สัดส่วน เช่น 0.03 = 3%)"""
    piv, trend, ext = [], 0, 0
    for i in range(1, len(v)):
        if trend == 0:
            if v[i] / v[0] - 1 >= th:
                trend, ext = 1, i
            elif v[i] / v[0] - 1 <= -th:
                trend, ext = -1, i
        elif trend == 1:
            if v[i] > v[ext]:
                ext = i
            elif v[i] / v[ext] - 1 <= -th:
                piv.append(ext)
                trend, ext = -1, i
        else:
            if v[i] < v[ext]:
                ext = i
            elif v[i] / v[ext] - 1 >= th:
                piv.append(ext)
                trend, ext = 1, i
    return piv   # ไม่รวมขาสุดท้ายที่ยังไม่ยืนยัน


def find_levels(s: pd.Series):
    """
    แนวรับ–แนวต้านอัตโนมัติจาก NAV ช่วงที่แสดงบนกราฟ

    1) หาจุดกลับตัวด้วย ZigZag ราคาต้องย้อนเกิน th ถึงจะนับ ช่วยตัดการแกว่งเล็กๆ ทิ้ง
       th ≈ ความผันผวน 1 สัปดาห์ × 2 (ไม่เกิน 30% ของกรอบราคา) จำกัดไว้ที่ 1.5%–6%
    2) จุดที่ราคาห่างกันไม่เกินครึ่งหนึ่งของ th ให้รวมเป็นแนวเดียว (นับจำนวนครั้งที่แตะ)
    3) เลือกแนวที่ใกล้ NAV ล่าสุดที่สุด ฝั่งละ 1 เส้น ถ้าฝั่งไหนไม่มีจุดกลับตัว
       ให้ใช้จุดสูงสุด/ต่ำสุดของช่วงแทน
    """
    v = s.values.astype(float)
    n = len(v)
    if n < 15:
        return []
    r = np.diff(np.log(v))
    rng_pct = (v.max() - v.min()) / v.mean()
    th = float(np.clip(min(2 * r.std() * np.sqrt(5), rng_pct * 0.3), 0.015, 0.06))

    last = v[-1]
    tol = last * th / 2
    pts = sorted((v[i], i) for i in zigzag(v, th) if i > 0)

    clusters = []
    for price, i in pts:
        if clusters and price - clusters[-1]["prices"][0] <= tol:
            clusters[-1]["prices"].append(price)
            clusters[-1]["idx"].append(i)
        else:
            clusters.append({"prices": [price], "idx": [i]})
    levels = [{"nav": float(np.mean(c["prices"])), "touches": len(c["prices"]), "i": max(c["idx"])}
              for c in clusters]

    out = []
    for side, below in (("support", True), ("resistance", False)):
        ok = (lambda x: x < last - tol / 2) if below else (lambda x: x > last + tol / 2)
        cand = [lv for lv in levels if ok(lv["nav"])]
        if not cand:
            i = int(v.argmin() if below else v.argmax())
            if ok(v[i]):
                cand = [{"nav": float(v[i]), "touches": 1, "i": i, "extreme": True}]
        if cand:
            lv = min(cand, key=lambda x: abs(x["nav"] - last))
            out.append({"side": side, "nav": round(lv["nav"], 4), "touches": lv["touches"],
                        "last_touch": s.index[lv["i"]].date().isoformat(),
                        "basis": "low" if lv.get("extreme") and below else
                                 "high" if lv.get("extreme") else "swing"})
    return out


def draw(fund, series: dict, start: pd.Timestamp, path: Path):
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 7})
    fig, ax = plt.subplots(figsize=(FIG_W, FIG_H), dpi=DPI)
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")

    lines = [(fund["name"], NAVY, 1.4, 4)] + [
        (p, PEERS[i % len(PEERS)], 1.1, 3) for i, p in enumerate(fund["peers"]) if p in series
    ]
    tags, lo, hi, x_end = [], 0.0, 0.0, start
    for name, color, lw, z in lines:
        s = series[name]
        s = s[s.index >= start]
        if len(s) < 2:
            continue
        y = (s / s.iloc[0] - 1) * 100
        ax.plot(y.index, y.values, color=color, lw=lw, zorder=z, solid_joinstyle="round")
        tags.append((float(y.iloc[-1]), color, z))
        lo, hi = min(lo, y.min()), max(hi, y.max())
        x_end = max(x_end, y.index[-1])

    ax.axhline(0, color="#9ca3af", lw=0.7, zorder=2)

    # กรอบ + เส้นตาราง (แนวนอนและแนวตั้ง)
    ax.grid(axis="both", color=GRID, lw=0.7)
    ax.set_axisbelow(True)
    for sp in ax.spines.values():
        sp.set_color(FRAME)
        sp.set_linewidth(0.8)

    # แกน y ด้านขวา
    ax.yaxis.tick_right()
    ax.yaxis.set_major_locator(matplotlib.ticker.MaxNLocator(5))
    ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:.0f}%"))

    # แกน x ปรับตามช่วงเวลา
    span = (x_end - start).days
    if span <= 75:
        ax.xaxis.set_major_locator(mdates.WeekdayLocator(byweekday=mdates.MO, interval=2))
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%d %b"))
    elif span <= 200:
        ax.xaxis.set_major_locator(mdates.MonthLocator())
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %y"))
    elif span <= 420:
        ax.xaxis.set_major_locator(mdates.MonthLocator(bymonth=(1, 3, 5, 7, 9, 11)))
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %y"))
    else:
        ax.xaxis.set_major_locator(mdates.MonthLocator(bymonth=(1, 4, 7, 10)))
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %y"))
    ax.tick_params(colors=MUTED, length=0, labelsize=6.5, pad=3)
    ax.set_xlim(start, x_end)

    rng = (hi - lo) or 1
    ax.set_ylim(lo - rng * 0.06, hi + rng * 0.18)

    # ไม่ใส่ชื่อกองในรูป — หน้าเว็บวาดชื่อกองทับให้เองเมื่อติ๊ก "แสดงชื่อกองบนกราฟ"
    # (ตำแหน่ง/ขนาดเดิม: มุมซ้ายบนของกรอบกราฟ 0.012, 0.975 · 8.5pt ตัวหนา สี NAVY)

    # tag ค่าล่าสุดติดแกนขวา
    for v, color, z in tags:
        ax.annotate(f"{v:+.2f}%", xy=(1, v), xycoords=("axes fraction", "data"),
                    xytext=(4, 0), textcoords="offset points", ha="left", va="center",
                    fontsize=7, fontweight="bold", color="white", annotation_clip=False, zorder=10 + z,
                    bbox=dict(boxstyle="round,pad=0.35,rounding_size=0.25", fc=color, ec="none"))

    # ซ่อนตัวเลขแกนที่ทับ tag
    fig.canvas.draw()
    for t, val in zip(ax.get_yticklabels(), ax.get_yticks()):
        if any(abs(val - v) < rng * 0.09 for v, _, _ in tags):
            t.set_visible(False)

    fig.subplots_adjust(left=0.015, right=0.90, top=0.97, bottom=0.10)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=DPI, facecolor="white")

    # ตำแหน่งกรอบกราฟในรูป (สัดส่วน 0–1 นับจากซ้ายล่าง) + ช่วงแกน y
    # หน้าเว็บใช้แปลงตำแหน่งเมาส์ ↔ % ↔ NAV เพื่อวาดเส้นแนวรับแนวต้านทับรูปให้ตรง
    pos = ax.get_position()
    ymin, ymax = ax.get_ylim()
    plt.close(fig)
    return {"left": round(pos.x0, 5), "right": round(pos.x1, 5),
            "bottom": round(pos.y0, 5), "top": round(pos.y1, 5),
            "ymin": round(ymin, 5), "ymax": round(ymax, 5)}


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
            # เริ่มกราฟที่ NAV วันแนะนำ (หรือวันทำการก่อนหน้าถ้าวันนั้นไม่มี NAV)
            s0 = series[f["name"]]
            if f["rec"]:
                before = s0[s0.index <= pd.Timestamp(f["rec"])]
                start = before.index[-1] if len(before) else s0.index[0]
            else:
                start = max(pd.Timestamp(today - timedelta(days=LOOKBACK_DAYS)), s0.index[0])
            img = f"charts/{slug(f['name'])}.png"
            entry["axes"] = draw(f, series, start, OUT / img)
            shown = s0[s0.index >= start]
            entry["base_nav"] = round(float(shown.iloc[0]), 4)    # NAV ที่ 0% บนกราฟ
            entry["last_nav"] = round(float(shown.iloc[-1]), 4)
            entry["levels"] = find_levels(shown)
            entry["image"] = img
            # เวอร์ชันรูปจากเนื้อไฟล์ — รูปเปลี่ยนเมื่อไร URL เปลี่ยน เบราว์เซอร์จะไม่ใช้รูปเก่าจาก cache
            entry["image_ver"] = hashlib.md5((OUT / img).read_bytes()).hexdigest()[:10]
            entry["returns"] = returns(series[f["name"]], f["rec"])
            print(f"chart {f['name']}: since rec {fmt_pct(entry['returns']['since_rec'])}")
        out["funds"].append(entry)

    (OUT / "data.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()

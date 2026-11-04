"""
ตาราง Drawdown และ Recovery Period ของกองที่กำหนดใน drawdown_funds.csv (ไม่มีหน้าเว็บ)
ได้ไฟล์ Excel + CSV ไว้เปิด / copy ต่อ

แยกจากกราฟ weekly และหน้าเปรียบเทียบทั้งหมด: ไม่แตะ funds.csv, data/nav, data/compare หรือ docs/

อ่าน  : drawdown_funds.csv                  รายชื่อกอง (คอลัมน์ fund)
        SEC profiles + NAV รายวัน (v2)
เขียน : data/drawdown/nav/<กอง>.csv          NAV สะสม (รอบต่อไปดึงเฉพาะวันใหม่)
        data/drawdown/backfill.json          กองที่ดึงย้อนถึงวันเริ่มแล้ว
        drawdown/drawdown_report.xlsx        ไฟล์ Excel (สรุป · ตามช่วงเวลา · 5 ครั้งที่ลงลึกสุด · ต้องเช็ค)
        drawdown/drawdown_summary.csv        ตารางสรุปแบบ CSV

รัน:
  python scripts/drawdown_report.py            # ดึง NAV จาก SEC แล้วทำรายงาน
  python scripts/drawdown_report.py --local    # ไม่ดึง ใช้ NAV ที่มีในเครื่อง (ไว้ทดสอบ)
"""

import csv
import json
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fetch_nav as fn  # noqa: E402
import fetch_compare as fc  # noqa: E402  (ใช้ stitch_old_class ต่อ NAV จาก class เดิม)

ROOT = fn.ROOT
DATA = ROOT / "data" / "drawdown"
OUT = ROOT / "drawdown"
fn.NAV_DIR = DATA / "nav"   # fetch_compare ตั้งไว้เป็นโฟลเดอร์ของตัวเอง ตั้งกลับมาที่ของรายงานนี้
START = date.fromisoformat(os.getenv("DRAWDOWN_START", "2015-01-01"))
WINDOWS = [("1Y", 1), ("3Y", 3), ("5Y", 5), ("ทั้งหมด", None)]
TOP_N = 5
BACKFILL_PATH = DATA / "backfill.json"


# ---------------------------------------------------------------- รายชื่อกอง
def read_list():
    with (ROOT / "drawdown_funds.csv").open(encoding="utf-8-sig") as f:
        names = [(r.get("fund") or "").strip() for r in csv.DictReader(f)]
    out, seen = [], set()
    for n in names:   # ตัดชื่อซ้ำ (ไม่สนช่องว่าง/ขีด/ตัวพิมพ์)
        if n and fn.norm(n) not in seen:
            seen.add(fn.norm(n))
            out.append(n)
    return out


# ---------------------------------------------------------------- ดึง NAV
def fetch_all(names, today):
    backfill = json.loads(BACKFILL_PATH.read_text()) if BACKFILL_PATH.exists() else {}
    by_class, by_abbr = fn.build_lookup(fn.load_profiles())
    meta, missing, series = {}, {}, {}
    for n in names:
        res = fn.resolve(n, by_class, by_abbr)
        if res is None:
            missing[n] = "ไม่พบชื่อนี้ใน SEC (เช็คชื่อย่อ/ชื่อ class)"
            continue
        if "ambiguous" in res:
            missing[n] = "ชื่อนี้มีหลาย class ให้ระบุเป็น: " + ", ".join(res["ambiguous"])
            continue
        info = {**res, "name": n}
        print(f"NAV {n} ({info['proj_id']} {info['fund_class_name'] or 'main'}) ...")
        cache = fn.NAV_DIR / f"{fn.slug(n)}.csv"
        old = fn.read_cache(cache)
        frm = max(START, min(old)) if old and backfill.get(n, "9999") <= START.isoformat() else START
        note = None
        try:
            _, warn = fn.fetch_series(info, frm, today)
            s = fn.read_cache(cache)
            if backfill.get(n, "9999") > START.isoformat():   # รอบแรกของกองนี้: ลองต่อ NAV จาก class เดิม
                s, note = fc.stitch_old_class(info, START, s)
                if note:
                    fn.write_cache(cache, s)
            backfill[n] = START.isoformat()
        except Exception as e:  # noqa: BLE001
            warn = str(e)[:300]
            print(f"  ! {e}")
            s = fn.read_cache(cache)
        s = {d: v for d, v in s.items() if d >= START}
        if not s:
            missing[n] = warn or "ไม่มี NAV"
            continue
        if warn and warn.startswith("API ตอบกลับมาแต่ไม่มี NAV"):
            warn = None
        series[n] = s
        meta[n] = {"info": info, "warning": warn, "note": note}
        print(f"  ok {len(s)} วัน {warn or ''}")
    DATA.mkdir(parents=True, exist_ok=True)
    BACKFILL_PATH.write_text(json.dumps({k: v for k, v in backfill.items() if k in names},
                                        ensure_ascii=False, indent=0, sort_keys=True))
    return series, meta, missing


def load_local(names):
    """ทดสอบ: ใช้ NAV ที่มีอยู่แล้วในเครื่อง (data/drawdown, data/compare, data/nav)"""
    series, meta, missing = {}, {}, {}
    for n in names:
        for d in (DATA / "nav", ROOT / "data" / "compare" / "nav", ROOT / "data" / "nav"):
            s = fn.read_cache(d / f"{fn.slug(n)}.csv")
            if s:
                series[n], meta[n] = s, {"info": {"name": n}, "warning": None, "note": None}
                break
        else:
            missing[n] = "ไม่มี NAV ในเครื่อง (โหมด --local)"
    return series, meta, missing


# ---------------------------------------------------------------- คำนวณ
def episodes(points):
    """points = [(date, nav)] เรียงตามวัน → ทุกช่วงที่ NAV ต่ำกว่าจุดสูงสุดเดิม
    แต่ละช่วง: จุดสูงสุดก่อนลง · จุดต่ำสุด · วันที่กลับมาเท่าจุดสูงสุดเดิม (None = ยังไม่ฟื้น)"""
    out, cur = [], None
    peak_d, peak_v = points[0]
    for d, v in points[1:]:
        if v >= peak_v:
            if cur:
                cur["recovery"] = d
                out.append(cur)
                cur = None
            peak_d, peak_v = d, v
        else:
            dd = v / peak_v - 1
            if cur is None:
                cur = {"peak": peak_d, "peak_nav": peak_v, "trough": d, "trough_nav": v, "depth": dd, "recovery": None}
            elif dd < cur["depth"]:
                cur.update(trough=d, trough_nav=v, depth=dd)
    if cur:
        out.append(cur)
    return out


def describe(ep, last_day):
    """เติมระยะเวลา (วันปฏิทิน): ช่วงลง = peak→trough · ช่วงฟื้น = trough→recovery · รวม = peak→recovery
    ถ้ายังไม่ฟื้น นับถึงวัน NAV ล่าสุด และติดธงว่ายังไม่ฟื้น"""
    end = ep["recovery"] or last_day
    return {**ep,
            "down_days": (ep["trough"] - ep["peak"]).days,
            "rec_days": (end - ep["trough"]).days,
            "total_days": (end - ep["peak"]).days,
            "recovered": ep["recovery"] is not None}


def analyse(s, since=None):
    pts = sorted((d, v) for d, v in s.items() if since is None or d >= since)
    if len(pts) < 2:
        return None
    last_d, last_v = pts[-1]
    eps = [describe(e, last_d) for e in episodes(pts)]
    worst = min(eps, key=lambda e: e["depth"]) if eps else None
    peak_v = max(v for _, v in pts)
    longest = max(eps, key=lambda e: e["total_days"]) if eps else None
    return {"first": pts[0][0], "last": last_d, "ret": last_v / pts[0][1] - 1,
            "current_dd": last_v / peak_v - 1, "worst": worst, "longest": longest,
            "top": sorted(eps, key=lambda e: e["depth"])[:TOP_N], "count": len(eps)}


def months(days):
    return round(days / 30.4375, 1)


def ongoing(w, key, f=lambda x: x):
    """ระยะเวลาของช่วงที่ยังไม่ฟื้น แสดงเป็น "123+" (นับถึงวัน NAV ล่าสุด ยังไม่จบ)"""
    if not w:
        return 0
    v = f(w[key])
    return v if w["recovered"] else f"{v}+"


# ---------------------------------------------------------------- เขียนไฟล์
SUMMARY_HEAD = ["กองทุน", "ชื่อกอง", "บลจ.", "ข้อมูล NAV ตั้งแต่", "NAV ล่าสุด ณ", "Max Drawdown",
                "จุดสูงสุดก่อนลง", "จุดต่ำสุด", "วันที่ฟื้นตัว", "ระยะเวลาลง (วัน)", "ระยะเวลาฟื้นตัว (วัน)",
                "ระยะเวลาฟื้นตัว (เดือน)", "รวมตั้งแต่จุดสูงสุดจนฟื้น (วัน)", "Drawdown ปัจจุบัน",
                "ช่วงที่ใช้เวลาฟื้นนานที่สุด (วัน)", "หมายเหตุ"]


def summary_rows(names, series, meta):
    rows = []
    for n in names:
        if n not in series:
            continue
        a = analyse(series[n])
        if not a:
            continue
        info, w = meta[n]["info"], a["worst"]
        notes = [x for x in (meta[n].get("note"), meta[n].get("warning")) if x]
        if w and not w["recovered"]:
            notes.insert(0, f"ยังไม่ฟื้นตัว (นับถึง {a['last'].strftime('%d/%m/%Y')})")
        lg = a["longest"]
        rows.append([
            n, info.get("proj_name_th") or "", info.get("comp_name_th") or "", a["first"], a["last"],
            w["depth"] if w else 0.0, w["peak"] if w else None, w["trough"] if w else None,
            (w["recovery"] if w["recovered"] else "ยังไม่ฟื้นตัว") if w else None,
            w["down_days"] if w else 0, ongoing(w, "rec_days"), ongoing(w, "rec_days", months),
            ongoing(w, "total_days"), a["current_dd"],
            (f"{lg['total_days']} ({lg['peak'].strftime('%d/%m/%Y')} – "
             f"{lg['recovery'].strftime('%d/%m/%Y') if lg['recovered'] else 'ยังไม่ฟื้น'})") if lg else "",
            " · ".join(notes),
        ])
    return rows


def window_rows(names, series, today):
    rows = []
    for n in names:
        if n not in series:
            continue
        row = [n]
        for label, yrs in WINDOWS:
            since = None if yrs is None else today.replace(year=today.year - yrs)
            a = analyse(series[n], since)
            if not a or not a["worst"]:
                row += [None, None, None]
                continue
            w = a["worst"]
            short = since is not None and a["first"] > since + timedelta(days=10)
            rec = ongoing(w, "rec_days")
            row += [w["depth"], rec, f"เริ่มมีข้อมูล {a['first'].strftime('%d/%m/%Y')}" if short else ""]
        rows.append(row)
    return rows


def top_rows(names, series):
    rows = []
    for n in names:
        if n not in series:
            continue
        a = analyse(series[n])
        for i, e in enumerate(a["top"] if a else [], 1):
            rows.append([n, i, e["depth"], e["peak"], e["peak_nav"], e["trough"], e["trough_nav"],
                         e["recovery"] if e["recovered"] else "ยังไม่ฟื้นตัว",
                         e["down_days"], ongoing(e, "rec_days"), ongoing(e, "total_days")])
    return rows


def cell_text(v):
    if isinstance(v, date):
        return v.strftime("%d/%m/%Y")
    return "" if v is None else v


def write_outputs(names, series, meta, missing, today):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    OUT.mkdir(parents=True, exist_ok=True)
    nav_to = max((max(s) for s in series.values()), default=today)
    srows = summary_rows(names, series, meta)
    wrows = window_rows(names, series, today)
    trows = top_rows(names, series)

    with (OUT / "drawdown_summary.csv").open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(SUMMARY_HEAD)
        for r in srows:
            w.writerow([f"{v * 100:.2f}%" if i in (5, 13) else cell_text(v) for i, v in enumerate(r)])

    navy = PatternFill("solid", fgColor="1B3560")
    hfont = Font(name="BLS Bold", bold=True, color="FFFFFF", size=11)
    bfont = Font(name="BLS Text", size=11)
    thin = Side(style="thin", color="D1D5DB")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    red = Font(name="BLS Text", size=11, color="C62828")

    wb = Workbook()

    def sheet(ws, title_lines, head, rows, pct_cols=(), date_cols=(), nav_cols=(), widths=None, head2=None):
        r0 = 1
        for t in title_lines:
            ws.cell(r0, 1, t).font = Font(name="BLS Bold", bold=True, size=13 if r0 == 1 else 10,
                                          color="1B3560" if r0 == 1 else "6B7280")
            r0 += 1
        r0 += 1
        if head2:   # หัวกลุ่ม (เช่น 1Y / 3Y …) ผสานเซลล์
            for c0, span, txt in head2:
                ws.cell(r0, c0, txt)
                if span > 1:
                    ws.merge_cells(start_row=r0, start_column=c0, end_row=r0, end_column=c0 + span - 1)
                for c in range(c0, c0 + span):
                    x = ws.cell(r0, c)
                    x.fill, x.font, x.border = navy, hfont, border
                    x.alignment = Alignment(horizontal="center", vertical="center")
            r0 += 1
        for c, h in enumerate(head, 1):
            x = ws.cell(r0, c, h)
            x.fill, x.font, x.border = navy, hfont, border
            x.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        ws.row_dimensions[r0].height = 34
        hr = r0
        for row in rows:
            r0 += 1
            for c, v in enumerate(row, 1):
                x = ws.cell(r0, c, v)
                x.font, x.border = bfont, border
                x.alignment = Alignment(horizontal="left" if c == 1 or isinstance(v, str) and c > 1 and len(v) > 14
                                        else "center", vertical="center")
                if c in pct_cols and isinstance(v, (int, float)):
                    x.number_format = "0.00%"
                    if v < 0:
                        x.font = red
                elif c in date_cols and isinstance(v, date):
                    x.number_format = "dd/mm/yyyy"
                elif c in nav_cols and isinstance(v, (int, float)):
                    x.number_format = "0.0000"
                elif isinstance(v, int):
                    x.number_format = "#,##0"
        ws.freeze_panes = ws.cell(hr + 1, 2)
        ws.auto_filter.ref = f"A{hr}:{get_column_letter(len(head))}{max(r0, hr)}"
        for c, wdt in enumerate(widths or [], 1):
            ws.column_dimensions[get_column_letter(c)].width = wdt
        ws.sheet_view.showGridLines = False

    asof = f"NAV ณ {nav_to.strftime('%d/%m/%Y')} · ทำเมื่อ {today.strftime('%d/%m/%Y')} · ข้อมูล NAV จาก SEC Open API (สำนักงาน ก.ล.ต.)"
    ws = wb.active
    ws.title = "สรุป"
    sheet(ws, ["Drawdown และ Recovery Period", asof,
               f"ใช้ NAV ตั้งแต่ {START.strftime('%d/%m/%Y')} หรือวันแรกที่กองมีข้อมูล · ระยะเวลานับเป็นวันปฏิทิน · "
               "ฟื้นตัว = วันที่ NAV กลับมาเท่าหรือสูงกว่าจุดสูงสุดก่อนลง · ตัวเลขที่มี + คือยังไม่ฟื้น นับถึงวัน NAV ล่าสุด"],
          SUMMARY_HEAD, srows, pct_cols=(6, 14), date_cols=(4, 5, 7, 8, 9),
          widths=[16, 40, 26, 13, 13, 12, 13, 13, 13, 11, 12, 12, 14, 12, 34, 40])

    ws = wb.create_sheet("ตามช่วงเวลา")
    head, head2, c = ["กองทุน"], [], 2
    for label, _ in WINDOWS:
        head += ["Max Drawdown", "ระยะเวลาฟื้นตัว (วัน)", "หมายเหตุ"]
        head2.append((c, 3, label if label == "ทั้งหมด" else f"ย้อนหลัง {label}"))
        c += 3
    head2.insert(0, (1, 1, ""))
    sheet(ws, ["Max Drawdown ตามช่วงเวลา", asof,
               "แต่ละช่วงคำนวณจาก NAV ในช่วงนั้นเท่านั้น (จุดสูงสุดก่อนช่วงไม่นับ) · ระยะเวลาฟื้นตัว = จุดต่ำสุด → กลับมาเท่าจุดสูงสุดเดิม"],
          head, wrows, pct_cols=(2, 5, 8, 11), widths=[16] + [12, 13, 20] * len(WINDOWS), head2=head2)

    ws = wb.create_sheet(f"{TOP_N} ครั้งที่ลงลึกสุด")
    sheet(ws, [f"{TOP_N} ครั้งที่ลงลึกที่สุดของแต่ละกอง", asof],
          ["กองทุน", "ลำดับ", "Drawdown", "จุดสูงสุดก่อนลง", "NAV จุดสูงสุด", "จุดต่ำสุด", "NAV จุดต่ำสุด",
           "วันที่ฟื้นตัว", "ระยะเวลาลง (วัน)", "ระยะเวลาฟื้นตัว (วัน)", "รวม (วัน)"],
          trows, pct_cols=(3,), date_cols=(4, 6, 8), nav_cols=(5, 7),
          widths=[16, 7, 11, 14, 12, 13, 12, 14, 12, 13, 10])

    ws = wb.create_sheet("ต้องเช็ค")
    chk = [[n, why] for n, why in missing.items()]
    chk += [[n, m["warning"]] for n, m in meta.items() if m.get("warning")]
    chk += [[n, m["note"]] for n, m in meta.items() if m.get("note")]
    sheet(ws, ["กองที่ต้องเช็ค / หมายเหตุข้อมูล",
               "แก้ชื่อใน drawdown_funds.csv แล้วรัน workflow ใหม่ · กองจ่ายปันผล: NAV ลดทุกครั้งที่จ่าย drawdown อาจดูลึกกว่าจริง"],
          ["กองทุน", "รายละเอียด"], chk or [["–", "ไม่มี"]], widths=[18, 100])

    wb.save(OUT / "drawdown_report.xlsx")
    print(f"เขียน drawdown/drawdown_report.xlsx ({len(srows)} กอง, ต้องเช็ค {len(missing)})")


def main():
    local = "--local" in sys.argv
    today = datetime.now(timezone(timedelta(hours=7))).date()   # เวลาไทย
    names = read_list()
    print(f"กองในรายการ {len(names)} กอง (ตัดชื่อซ้ำแล้ว)")
    if local:
        series, meta, missing = load_local(names)
    else:
        if not fn.API_KEY:
            raise SystemExit("ไม่พบ SEC_API_KEY (ตั้งใน GitHub → Settings → Secrets → Actions)")
        series, meta, missing = fetch_all(names, today)
    for n, why in missing.items():
        print(f"  ! {n}: {why}")
    if not series:
        raise SystemExit("ไม่มี NAV เลยสักกอง")
    write_outputs(names, series, meta, missing, today)


if __name__ == "__main__":
    main()

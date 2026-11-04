"""
ตาราง Maximum Drawdown และ Recovering Period ของกองที่กำหนดใน drawdown_funds.csv (ไม่มีหน้าเว็บ)
ใช้ค่าที่ บลจ. รายงานใน factsheet ผ่าน SEC Open API (/v2/fund/factsheet/statistics) — ไม่ดึง NAV

แยกจากกราฟ weekly และหน้าเปรียบเทียบทั้งหมด: ไม่แตะ funds.csv, data/ หรือ docs/

อ่าน  : drawdown_funds.csv                 รายชื่อกอง (คอลัมน์ fund)
        SEC profiles (หา proj_id / class) + factsheet statistics (ฉบับล่าสุด)
เขียน : drawdown/drawdown_report.xlsx       ชีต สรุป · ข้อมูลดิบ factsheet · ต้องเช็ค
        drawdown/drawdown_summary.csv       ตารางสรุปแบบ CSV

รัน:
  python scripts/drawdown_report.py
"""

import csv
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fetch_nav as fn  # noqa: E402

ROOT = fn.ROOT
OUT = ROOT / "drawdown"
STATS_URL = f"{fn.BASE}/factsheet/statistics"

# ชื่อ field ใน factsheet statistics: หาจากชื่อ (SEC อาจตั้งชื่อต่างจากนี้เล็กน้อย)
DD_KEY = re.compile(r"draw.?down", re.I)
REC_KEY = re.compile(r"recover", re.I)
SKIP_RAW = {"proj_id", "fund_class_name", "last_upd_date"}


def read_list():
    with (ROOT / "drawdown_funds.csv").open(encoding="utf-8-sig") as f:
        names = [(r.get("fund") or "").strip() for r in csv.DictReader(f)]
    out, seen = [], set()
    for n in names:   # ตัดชื่อซ้ำ (ไม่สนช่องว่าง/ขีด/ตัวพิมพ์)
        if n and fn.norm(n) not in seen:
            seen.add(fn.norm(n))
            out.append(n)
    return out


def cls_key(c) -> str:
    c = str(c or "").strip()
    return "" if c.lower() in ("", "main", "none", "nan") else fn.norm(c)


def load_stats():
    """proj_id -> {class(norm) -> แถว factsheet ล่าสุด} ดึงทีเดียวทั้งตลาด"""
    out, cursor = {}, None
    while True:
        params = {"latest": "true", "page_size": "100"}
        if cursor:
            params["next_cursor"] = cursor
        r = fn.get(STATS_URL, params)
        if r.status_code != 200:
            raise SystemExit(f"factsheet statistics HTTP {r.status_code}: {r.text[:300]}")
        rows, cursor = fn.extract_list(r.json())
        for row in rows:
            slot = out.setdefault(row.get("proj_id"), {})
            k = cls_key(row.get("fund_class_name"))
            if str(row.get("start_date") or "") >= str(slot.get(k, {}).get("start_date") or ""):
                slot[k] = row
        if not rows or not cursor:
            break
    print(f"  factsheet statistics ได้ {len(out)} กอง")
    return out


def pick(row, pat):
    for k, v in row.items():
        if pat.search(k) and v not in (None, ""):
            return k, v
    return None, None


def num(v):
    try:
        return float(str(v).replace("%", "").replace(",", "").strip())
    except (TypeError, ValueError):
        return v


def thdate(s):
    s = str(s or "")[:10]
    try:
        return datetime.fromisoformat(s).strftime("%d/%m/%Y")
    except ValueError:
        return s


def main():
    if not fn.API_KEY:
        raise SystemExit("ไม่พบ SEC_API_KEY (ตั้งใน GitHub → Settings → Secrets → Actions)")
    today = datetime.now(timezone(timedelta(hours=7))).date()
    names = read_list()
    print(f"กองในรายการ {len(names)} กอง (ตัดชื่อซ้ำแล้ว)")

    by_class, by_abbr = fn.build_lookup(fn.load_profiles())
    stats = load_stats()

    rows, raw, missing, shown = [], [], {}, False
    for n in names:
        res = fn.resolve(n, by_class, by_abbr)
        if res is None:
            missing[n] = "ไม่พบชื่อนี้ใน SEC (เช็คชื่อย่อ/ชื่อ class)"
            continue
        if "ambiguous" in res:
            missing[n] = "ชื่อนี้มีหลาย class ให้ระบุเป็น: " + ", ".join(res["ambiguous"])
            continue
        slot = stats.get(res["proj_id"], {})
        row = slot.get(cls_key(res["fund_class_name"])) or slot.get("")
        if row is None and len(slot) == 1:
            row = next(iter(slot.values()))
        if row is None:
            missing[n] = "ไม่พบข้อมูลสถิติใน factsheet ของกองนี้"
            continue
        if not shown:   # พิมพ์แถวแรกไว้ใน log เผื่อต้องเช็คชื่อ field
            print("ตัวอย่างข้อมูล factsheet:", {k: v for k, v in row.items()})
            shown = True
        dk, dd = pick(row, DD_KEY)
        rk, rec = pick(row, REC_KEY)
        if dk is None and rk is None:
            missing[n] = "factsheet ไม่มีค่า Maximum Drawdown / Recovering Period"
        rows.append([n, res.get("proj_name_th") or "", res.get("comp_name_th") or "",
                     num(dd) if dd is not None else "", rec if rec is not None else "",
                     thdate(row.get("start_date"))])
        raw.append((n, row))
        print(f"  {n}: drawdown={dd} ({dk}) · recovering={rec} ({rk})")

    head = ["กองทุน", "ชื่อกอง", "บลจ.", "Maximum Drawdown (%)", "Recovering Period", "factsheet ณ"]
    OUT.mkdir(parents=True, exist_ok=True)
    with (OUT / "drawdown_summary.csv").open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(head)
        w.writerows(rows)

    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

    navy = PatternFill("solid", fgColor="1B3560")
    hfont = Font(name="BLS Bold", bold=True, color="FFFFFF", size=11)
    bfont = Font(name="BLS Text", size=11)
    thin = Side(style="thin", color="D1D5DB")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)

    def sheet(ws, title, sub, head, data, widths):
        ws.cell(1, 1, title).font = Font(name="BLS Bold", bold=True, size=13, color="1B3560")
        ws.cell(2, 1, sub).font = Font(name="BLS Text", size=10, color="6B7280")
        for c, h in enumerate(head, 1):
            x = ws.cell(4, c, h)
            x.fill, x.font, x.border = navy, hfont, border
            x.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        for r, vals in enumerate(data, 5):
            for c, v in enumerate(vals, 1):
                x = ws.cell(r, c, v)
                x.font, x.border = bfont, border
                x.alignment = Alignment(horizontal="left" if c <= 3 else "center", vertical="center")
                if isinstance(v, float):
                    x.number_format = "0.00"
                    if v < 0:
                        x.font = Font(name="BLS Text", size=11, color="C62828")
        for i, wd in enumerate(widths):
            ws.column_dimensions[chr(65 + i)].width = wd
        ws.freeze_panes = "B5"
        ws.sheet_view.showGridLines = False

    src = f"ข้อมูลจาก factsheet ฉบับล่าสุด (SEC Open API สำนักงาน ก.ล.ต.) · ทำเมื่อ {today.strftime('%d/%m/%Y')}"
    wb = Workbook()
    sheet(wb.active, "Maximum Drawdown และ Recovering Period", src, head, rows, [16, 44, 26, 16, 20, 13])
    wb.active.title = "สรุป"

    keys = []
    for _, r in raw:
        keys += [k for k in r if k not in keys and k not in SKIP_RAW]
    ws = wb.create_sheet("ข้อมูลดิบ factsheet")
    sheet(ws, "ข้อมูลสถิติทั้งหมดใน factsheet", src, ["กองทุน", *keys],
          [[n, *[r.get(k) for k in keys]] for n, r in raw], [16] + [18] * min(len(keys), 24))

    ws = wb.create_sheet("ต้องเช็ค")
    sheet(ws, "กองที่ต้องเช็ค", "แก้ชื่อใน drawdown_funds.csv แล้วรัน workflow ใหม่", ["กองทุน", "รายละเอียด"],
          [[n, why] for n, why in missing.items()] or [["–", "ไม่มี"]], [18, 90])
    wb.save(OUT / "drawdown_report.xlsx")
    for n, why in missing.items():
        print(f"  ! {n}: {why}")
    print(f"เขียน drawdown/drawdown_report.xlsx ({len(rows)} กอง, ต้องเช็ค {len(missing)})")


if __name__ == "__main__":
    main()

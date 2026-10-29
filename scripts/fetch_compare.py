"""
ดึง NAV ย้อนหลัง 3 ปี + ข้อมูลเบื้องต้นจาก SEC Open API (v2) สำหรับหน้าเปรียบเทียบ (docs/compare.html)
แยกจากกราฟ Top Funds Weekly ทั้งหมด: ไม่แตะ funds.csv, data/nav หรือ docs/data.json

อ่าน  : compare_funds.csv                 รายชื่อกองที่เลือกเทียบได้
เขียน : data/compare/nav/<กอง>.csv          NAV สะสม (ดึงเพิ่มเฉพาะวันใหม่)
        docs/compare/data.json            ข้อมูลที่หน้าเว็บใช้

ใช้ฟังก์ชันดึงข้อมูลจาก scripts/fetch_nav.py (import มาใช้ ไม่แก้ไฟล์นั้น)

รัน:
  python scripts/fetch_compare.py             # อัปเดตปกติ
  python scripts/fetch_compare.py --selftest  # พิมพ์ข้อมูล profile ดิบของกองแรกออกมาดู field ไม่บันทึกอะไร
"""

import csv
import json
import os
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fetch_nav as fn  # noqa: E402

ROOT = fn.ROOT
DATA = ROOT / "data" / "compare"
OUT = ROOT / "docs" / "compare"
YEARS = int(os.getenv("COMPARE_YEARS", "3"))
# เพิ่มกองความเสี่ยงระดับ 1–2 (กองตลาดเงิน) ทุกกองให้อัตโนมัติ จาก /v2/fund/factsheet/risk-spectrum
AUTO_MM = os.getenv("AUTO_MONEY_MARKET", "1") == "1"
MM_LEVELS = {int(x) for x in os.getenv("RISK_LEVELS", "1,2,3,4").split(",") if x.strip()}
LEVEL_GROUP = {
    1: "ความเสี่ยง 1 · ตลาดเงิน (ในประเทศ)",
    2: "ความเสี่ยง 2 · ตลาดเงิน (ต่างประเทศบางส่วน)",
    3: "ความเสี่ยง 3 · พันธบัตรรัฐบาล",
    4: "ความเสี่ยง 4 · ตราสารหนี้",
}
MM_GROUP = "ตลาดเงิน"  # ใช้ตอนดึงระดับความเสี่ยงไม่ได้
RISK_URL = f"{fn.BASE}/factsheet/risk-spectrum"


def load_risk():
    """proj_id -> ระดับความเสี่ยงล่าสุด (int) จาก factsheet ที่มีผลล่าสุด ดึงทีเดียวทุกกอง"""
    out, cursor = {}, None
    while True:
        params = {"latest": "true", "page_size": "100"}
        if cursor:
            params["next_cursor"] = cursor
        r = fn.get(RISK_URL, params)
        if r.status_code != 200:
            raise RuntimeError(f"risk-spectrum HTTP {r.status_code}: {r.text[:200]}")
        rows, cursor = fn.extract_list(r.json())
        for row in rows:
            m = re.search(r"(\d)", str(row.get("risk_spectrum") or ""))
            pid = row.get("proj_id")
            if m and pid:
                out[pid] = int(m.group(1))
        if not rows or not cursor:
            break
    print(f"  risk-spectrum ได้ {len(out)} กอง")
    return out


def is_money_market(p: dict) -> bool:
    txt = re.sub(r"<[^>]+>", " ", str(p.get("investment_policy_desc") or ""))
    txt = re.sub(r"\s+", "", txt)
    return "กองทุนรวมตลาดเงิน" in txt and "ไม่ใช่กองทุนรวมตลาดเงิน" not in txt and "ไม่เป็นกองทุนรวมตลาดเงิน" not in txt

# ให้ fetch_series ของ fetch_nav.py เขียนลงโฟลเดอร์ของหน้าเปรียบเทียบแทนโฟลเดอร์เดิม
fn.NAV_DIR = DATA / "nav"


def read_list():
    with (ROOT / "compare_funds.csv").open(encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    out, seen = [], set()
    for r in rows:
        name = (r.get("fund") or "").strip()
        if name and name not in seen:
            seen.add(name)
            out.append({"name": name, "group": (r.get("group") or "").strip() or "อื่น ๆ"})
    return out


def scalar_fields(row: dict) -> dict:
    """เก็บเฉพาะค่าเดี่ยวที่ไม่ว่าง จาก profile ดิบ (หน้าเว็บเลือกแสดงเอง)"""
    keep = {}
    for k, v in row.items():
        if isinstance(v, (str, int, float, bool)) and str(v).strip() not in ("", "-", "None", "null"):
            s = str(v).strip()
            keep[k] = s if len(s) <= 400 else s[:400] + "…"
    return keep


def stitch_old_class(info, start, series):
    """class ที่เพิ่งแยกออกมา (เช่น K-CASH-A): NAV ก่อนวันแรกของ class อยู่ใต้ class เดิม
    ดึงช่วงที่ขาด แยกตาม class แล้วต่อเฉพาะ class ที่ NAV ต่อเนื่องกัน (ต่างกันไม่เกิน 0.5% ที่รอยต่อ)
    คืน (series ใหม่, ข้อความหมายเหตุ หรือ None)"""
    if not series or not info["fund_class_name"]:
        return series, None
    first = min(series)
    if first <= start + timedelta(days=20):
        return series, None
    by_cls = {}
    cur = start
    while cur < first:
        to = min(cur + timedelta(days=fn.CHUNK_DAYS - 1), first - timedelta(days=1))
        try:
            rows = fn.call_nav(info["proj_id"], cur, to)
        except Exception as e:  # noqa: BLE001
            print(f"  ! ต่อข้อมูล class เดิมไม่ได้: {e}")
            return series, None
        for row in rows:
            dk, vk = fn.first_key(row, fn.DATE_KEYS), fn.first_key(row, fn.VALUE_KEYS)
            if not dk or not vk:
                continue
            ck = fn.first_key(row, fn.CLASS_KEYS)
            try:
                raw = str(row[dk])
                d = datetime.fromisoformat(raw[:10]).date() if "-" in raw else datetime.strptime(raw[:8], "%Y%m%d").date()
                v = float(row[vk])
            except (ValueError, TypeError):
                continue
            if v > 0:
                by_cls.setdefault(str(row[ck]).strip() if ck else "", {})[d] = v
        cur = to + timedelta(days=1)
    base = series[first]
    best = None
    for cls_name, ser in by_cls.items():
        if not ser or fn.norm(cls_name) == fn.norm(info["fund_class_name"]):
            continue
        last = max(ser)
        if (first - last).days > 7:
            continue
        gap = abs(ser[last] / base - 1)
        if gap <= 0.005 and (best is None or gap < best[1]):
            best = (cls_name, gap, ser)
    if not best:
        return series, None
    cls_name, _, ser = best
    merged = {**{d: v for d, v in ser.items() if d < first}, **series}
    note = f"ก่อน {first.isoformat()} ใช้ NAV ของ class {cls_name or 'หลัก'} (class นี้เริ่มวันนั้น)"
    print(f"  ต่อข้อมูล: {note}")
    return merged, note


def main():
    if not fn.API_KEY:
        raise SystemExit("ไม่พบ SEC_API_KEY (ตั้งใน GitHub → Settings → Secrets → Actions)")
    selftest = "--selftest" in sys.argv

    funds = read_list()
    profiles = fn.load_profiles()
    by_class, by_abbr = fn.build_lookup(profiles)

    # proj_id -> profile ดิบ (ทุก class) เอาไว้ดึงข้อมูลเบื้องต้น
    raw_by_proj = {}
    for p in profiles:
        raw_by_proj.setdefault(p.get("proj_id"), []).append(p)

    def raw_profile(info):
        rows = raw_by_proj.get(info["proj_id"], [])
        want = fn.norm(info["fund_class_name"])
        for r in rows:
            if fn.norm(r.get("fund_class_name")) == want:
                return r
        return rows[0] if rows else {}

    today = date.today()
    start = today - timedelta(days=round(365.25 * YEARS) + 7)

    resolved, missing = {}, {}
    try:
        risk = load_risk()
    except Exception as e:  # noqa: BLE001
        print(f"  ! ดึงระดับความเสี่ยงไม่ได้: {e} (ใช้นโยบายการลงทุนแทน)")
        risk = {}

    def low_risk(p):
        if risk:
            return risk.get(p.get("proj_id")) in MM_LEVELS
        return is_money_market(p)

    if AUTO_MM:
        listed = {fn.norm(f["name"]) for f in funds}
        added = 0
        for p in profiles:
            if str(p.get("proj_retail_type") or "R").strip().upper() != "R" or not low_risk(p):
                continue
            # ตัดกองปิดที่มีอายุโครงการ (term fund) เทียบกับพอร์ตเปิดไม่ได้
            if str(p.get("proj_term_flag") or "N").strip().upper() not in ("N", ""):
                continue
            cls = str(p.get("fund_class_name") or "").strip()
            cls = "" if cls.lower() in ("main", "none", "nan") else cls
            name = cls or str(p.get("proj_abbr_name") or "").strip()
            if not name or fn.norm(name) in listed:
                continue
            listed.add(fn.norm(name))
            lv = risk.get(p.get("proj_id"))
            funds.append({"name": name, "group": LEVEL_GROUP.get(lv, MM_GROUP), "auto": True})
            resolved[name] = {
                "proj_id": p.get("proj_id"), "proj_abbr_name": str(p.get("proj_abbr_name") or "").strip(),
                "fund_class_name": cls, "proj_name_th": p.get("proj_name_th"),
                "comp_name_th": p.get("comp_name_th"), "name": name,
            }
            added += 1
        print(f"เพิ่มกองความเสี่ยง {sorted(MM_LEVELS)} อัตโนมัติ {added} กอง")
    for f in funds:
        if f["name"] in resolved:
            continue
        res = fn.resolve(f["name"], by_class, by_abbr)
        if res is None:
            missing[f["name"]] = "ไม่พบชื่อนี้ใน SEC (เช็คชื่อย่อ/ชื่อ class)"
        elif "ambiguous" in res:
            missing[f["name"]] = "ชื่อนี้มีหลาย class ให้ระบุเป็น: " + ", ".join(res["ambiguous"])
        else:
            resolved[f["name"]] = {**res, "name": f["name"]}
    for n, why in missing.items():
        print(f"  ! {n}: {why}")

    if selftest:
        print("\n=== SELFTEST: profile ดิบ ===")
        for f in funds:
            if f["name"] in resolved:
                print(f["name"])
                print(json.dumps(raw_profile(resolved[f["name"]]), ensure_ascii=False, indent=1)[:4000])
                break
        return

    notes_path = DATA / "notes.json"
    notes = json.loads(notes_path.read_text()) if notes_path.exists() else {}
    funds.sort(key=lambda f: (f["group"], f["name"]))
    out_funds = []
    for f in funds:
        info = resolved.get(f["name"])
        if not info:
            continue
        print(f"NAV {f['name']} ({info['proj_id']} {info['fund_class_name'] or 'main'}) ...")
        warn = None
        try:
            _, warn = fn.fetch_series(info, start, today)
        except Exception as e:  # noqa: BLE001
            warn = str(e)[:300]
            print(f"  ! {e}")
        cache = fn.NAV_DIR / f"{fn.slug(info['name'])}.csv"
        series = fn.read_cache(cache)
        series, stitched = stitch_old_class(info, start, series)
        if stitched:
            fn.write_cache(cache, series)   # รอบหน้าดึงเฉพาะวันใหม่
            notes[info["name"]] = stitched
        stitched = stitched or notes.get(info["name"])
        series = {d: v for d, v in series.items() if d >= start}
        days = sorted(series)
        # กอง/class ที่เพิ่งเปิด: ช่วงก่อนเปิดไม่มี NAV ของ class นี้ ไม่นับเป็นปัญหาถ้าได้ข้อมูลมาแล้ว
        if days and warn and warn.startswith("API ตอบกลับมาแต่ไม่มี NAV"):
            warn = None
        print(f"  ok {len(days)} วัน {warn or ''}")
        out_funds.append({
            "name": f["name"],
            "group": f["group"],
            "info": info,
            "sec": scalar_fields(raw_profile(info)),
            "risk": risk.get(info["proj_id"]),
            "warning": warn,
            "note": stitched,
            "s": days[0].isoformat() if days else None,
            "dd": [(b - a).days for a, b in zip(days, days[1:])],
            "v": [round(series[d], 4) for d in days],
        })

    DATA.mkdir(parents=True, exist_ok=True)
    notes_path.write_text(json.dumps(notes, ensure_ascii=False, indent=1))
    OUT.mkdir(parents=True, exist_ok=True)
    payload = {
        "updated": today.isoformat(),
        "run_at": datetime.utcnow().isoformat() + "Z",
        "years": YEARS,
        "funds": out_funds,
        "missing": missing,
    }
    (OUT / "data.json").write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    print(f"ขนาดไฟล์ {(OUT / 'data.json').stat().st_size / 1e6:.1f} MB")
    print(f"เขียน docs/compare/data.json ({len(out_funds)} กอง)")

    if not any(x["s"] for x in out_funds):
        raise SystemExit("ดึง NAV ไม่ได้เลยสักกอง - ลองรันแบบ selftest เพื่อดู response")


if __name__ == "__main__":
    main()

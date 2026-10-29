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
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fetch_nav as fn  # noqa: E402

ROOT = fn.ROOT
DATA = ROOT / "data" / "compare"
OUT = ROOT / "docs" / "compare"
YEARS = int(os.getenv("COMPARE_YEARS", "3"))

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
    for f in funds:
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
        series = fn.read_cache(fn.NAV_DIR / f"{fn.slug(info['name'])}.csv")
        series = {d: v for d, v in series.items() if d >= start}
        days = sorted(series)
        print(f"  ok {len(days)} วัน {warn or ''}")
        out_funds.append({
            "name": f["name"],
            "group": f["group"],
            "info": info,
            "sec": scalar_fields(raw_profile(info)),
            "warning": warn,
            "d": [d.isoformat() for d in days],
            "v": [round(series[d], 4) for d in days],
        })

    OUT.mkdir(parents=True, exist_ok=True)
    payload = {
        "updated": today.isoformat(),
        "run_at": datetime.utcnow().isoformat() + "Z",
        "years": YEARS,
        "funds": out_funds,
        "missing": missing,
    }
    (OUT / "data.json").write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    print(f"เขียน docs/compare/data.json ({len(out_funds)} กอง)")

    if not any(x["d"] for x in out_funds):
        raise SystemExit("ดึง NAV ไม่ได้เลยสักกอง - ลองรันแบบ selftest เพื่อดู response")


if __name__ == "__main__":
    main()

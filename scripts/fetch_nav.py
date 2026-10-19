"""
ดึง NAV รายวันจาก SEC Open API (v2) เฉพาะกองที่อยู่ใน funds.csv

ใช้ 2 endpoint:
  GET https://api.sec.or.th/v2/fund/general-info/profiles   -> หา proj_id / class จากชื่อย่อกองทุน
  GET https://api.sec.or.th/v2/fund/daily-info/nav          -> NAV รายวันตามช่วงวันที่

ผลลัพธ์:
  data/fund_index.json      ตารางชื่อ -> proj_id ของกองที่ใช้อยู่
  data/nav/<ชื่อกอง>.csv     date,nav  (เก็บสะสม ดึงเพิ่มเฉพาะวันที่ใหม่)
  data/fetch_report.json    สรุปว่ากองไหนหาเจอ / หาไม่เจอ

รัน:
  python scripts/fetch_nav.py             # อัปเดตปกติ
  python scripts/fetch_nav.py --selftest  # พิมพ์ response จริงของกองแรกออกมาดู ไม่บันทึกอะไร
"""

import csv
import json
import os
import re
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
NAV_DIR = DATA / "nav"

BASE = "https://api.sec.or.th/v2/fund"
PROFILES_URL = f"{BASE}/general-info/profiles"
NAV_URL = f"{BASE}/daily-info/nav"

LOOKBACK_DAYS = int(os.getenv("LOOKBACK_DAYS", "365"))   # ใช้เฉพาะกองที่ไม่ได้ใส่วันแนะนำ
CHUNK_DAYS = int(os.getenv("NAV_CHUNK_DAYS", "31"))       # ดึงทีละกี่วันต่อ 1 call
REFETCH_DAYS = 10                                          # ดึงซ้ำย้อนหลังกันข้อมูลแก้ไขย้อนหลัง

API_KEY = os.getenv("SEC_API_KEY")
SESSION = requests.Session()
SESSION.headers.update({"Ocp-Apim-Subscription-Key": API_KEY or ""})

VALUE_KEYS = ["last_val", "nav", "nav_value", "nav_per_unit"]
DATE_KEYS = ["nav_date", "date"]
CLASS_KEYS = ["fund_class_name", "class_abbr_name", "class_name", "fund_class"]

# รูปแบบวันที่ที่จะลอง (จำตัวที่ใช้ได้ไว้)
DATE_FORMATS = ["%Y-%m-%d", "%Y%m%d"]
_date_fmt = None


# ---------------------------------------------------------------- helpers
def norm(s) -> str:
    """ทำชื่อให้เทียบกันได้: ตัวพิมพ์ใหญ่ ตัดช่องว่าง/ขีด/วงเล็บ"""
    return re.sub(r"[^A-Z0-9ก-๙]", "", str(s or "").upper())


def slug(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-").upper()


def get(url, params, tries=4):
    for i in range(tries):
        r = SESSION.get(url, params=params, timeout=60)
        if r.status_code == 429 or r.status_code >= 500:
            wait = int(r.headers.get("Retry-After", 0) or 0) or 5 * (i + 1)
            print(f"  HTTP {r.status_code} รอ {wait}s แล้วลองใหม่")
            time.sleep(wait)
            continue
        return r
    return r


def extract_list(payload):
    """รองรับทั้ง response ที่เป็น list ตรงๆ หรือ {items:[...]} / {data:[...]}"""
    if isinstance(payload, list):
        return payload, None
    if isinstance(payload, dict):
        for k in ("items", "data", "result", "results", "value"):
            if isinstance(payload.get(k), list):
                return payload[k], payload.get("next_cursor")
        # object เดี่ยว
        if any(k in payload for k in DATE_KEYS):
            return [payload], None
    return [], None


def first_key(row: dict, keys):
    for k in keys:
        if k in row and row[k] not in (None, ""):
            return k
    return None


# ---------------------------------------------------------------- profiles
def load_profiles():
    print("ดึงรายชื่อกองทุนจาก profiles ...")
    items, cursor, page = [], None, 1
    while True:
        params = {"page_size": "100"}
        if cursor:
            params["next_cursor"] = cursor
        r = get(PROFILES_URL, params)
        if r.status_code != 200:
            raise SystemExit(f"profiles HTTP {r.status_code}: {r.text[:300]}")
        rows, cursor = extract_list(r.json())
        if not rows:
            break
        items.extend(rows)
        if not cursor:
            break
        page += 1
    active = [
        x for x in items
        if not str(x.get("cancel_date") or "").strip()
        and str(x.get("fund_status") or "").strip() in ("Registered", "IPO")
    ]
    print(f"  ได้ {len(items)} รายการ (active {len(active)})")
    return active


def build_lookup(profiles):
    """name(normalized) -> list of candidates"""
    by_class, by_abbr = {}, {}
    for p in profiles:
        cls = str(p.get("fund_class_name") or "").strip()
        abbr = str(p.get("proj_abbr_name") or "").strip()
        info = {
            "proj_id": p.get("proj_id"),
            "proj_abbr_name": abbr,
            "fund_class_name": cls if cls.lower() not in ("", "main", "none", "nan") else "",
            "proj_name_th": p.get("proj_name_th"),
            "comp_name_th": p.get("comp_name_th"),
        }
        if info["fund_class_name"]:
            by_class.setdefault(norm(cls), []).append(info)
        if abbr:
            by_abbr.setdefault(norm(abbr), []).append(info)
    return by_class, by_abbr


def resolve(name, by_class, by_abbr):
    key = norm(name)
    if key in by_class:
        return by_class[key][0]
    if key in by_abbr:
        cands = by_abbr[key]
        mains = [c for c in cands if not c["fund_class_name"]]
        if mains:
            return mains[0]
        if len(cands) == 1:
            return cands[0]
        # ชื่อโครงการมีหลาย class -> ต้องระบุ class
        return {"ambiguous": [c["fund_class_name"] for c in cands]}
    return None


# ---------------------------------------------------------------- NAV
def call_nav(proj_id, start: date, end: date):
    """เรียก NAV ช่วงวันที่ คืน list ของ row ดิบ (รองรับ next_cursor)"""
    global _date_fmt
    fmts = [_date_fmt] if _date_fmt else DATE_FORMATS
    last = None
    for fmt in fmts:
        rows, cursor = [], None
        while True:
            params = {
                "proj_id": proj_id,
                "start_nav_date": start.strftime(fmt),
                "end_nav_date": end.strftime(fmt),
            }
            if cursor:
                params["next_cursor"] = cursor
            r = get(NAV_URL, params)
            last = r
            if r.status_code == 204:
                break
            if r.status_code != 200:
                rows = None
                break
            got, cursor = extract_list(r.json())
            rows.extend(got)
            if not cursor:
                break
        if rows is not None:
            _date_fmt = fmt
            return rows
    raise RuntimeError(f"NAV HTTP {last.status_code}: {last.text[:300]}")


def parse_rows(rows, want_class: str):
    """แปลง row ดิบ -> {date: nav} กรองเฉพาะ class ที่ต้องการ"""
    out, warn = {}, None
    want = norm(want_class)
    no_class_field = False
    for row in rows:
        dk, vk = first_key(row, DATE_KEYS), first_key(row, VALUE_KEYS)
        if not dk or not vk:
            continue
        ck = first_key(row, CLASS_KEYS)
        if not ck and want:
            no_class_field = True
        if ck:
            rc = norm(row[ck])
            is_main = rc in ("", "MAIN", "NONE")
            if want and rc != want:
                continue
            if not want and not is_main:
                continue
        try:
            d = datetime.fromisoformat(str(row[dk])[:10]).date() if "-" in str(row[dk]) \
                else datetime.strptime(str(row[dk])[:8], "%Y%m%d").date()
            v = float(row[vk])
        except (ValueError, TypeError):
            continue
        if v > 0:
            out[d] = v
    if rows and not out:
        warn = "API ตอบกลับมาแต่ไม่มี NAV ของ class นี้ (ลองรัน selftest ดู field)"
    elif no_class_field:
        warn = "response ไม่มี field class - ถ้ากองนี้มีหลาย class ค่า NAV อาจปนกัน (รัน selftest เช็ค)"
    return out, warn


def read_cache(path: Path):
    if not path.exists():
        return {}
    with path.open() as f:
        return {date.fromisoformat(r["date"]): float(r["nav"]) for r in csv.DictReader(f)}


def write_cache(path: Path, series: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "nav"])
        for d in sorted(series):
            w.writerow([d.isoformat(), f"{series[d]:.4f}"])


def fetch_series(info, start: date, end: date):
    path = NAV_DIR / f"{slug(info['name'])}.csv"
    series = read_cache(path)
    if series and min(series) <= start + timedelta(days=7):
        frm = max(start, max(series) - timedelta(days=REFETCH_DAYS))
    else:
        frm = start
    warn = None
    cur = frm
    while cur <= end:
        to = min(cur + timedelta(days=CHUNK_DAYS - 1), end)
        rows = call_nav(info["proj_id"], cur, to)
        got, w = parse_rows(rows, info["fund_class_name"])
        warn = warn or w
        series.update(got)
        cur = to + timedelta(days=1)
        time.sleep(0.15)
    # ตัดข้อมูลที่เก่าเกินช่วงที่ใช้
    series = {d: v for d, v in series.items() if d >= start - timedelta(days=40)}
    write_cache(path, series)
    return len(series), warn


# ---------------------------------------------------------------- main
def read_funds():
    with (ROOT / "funds.csv").open(encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    funds = []
    for r in rows:
        name = (r.get("fund") or "").strip()
        if not name:
            continue
        rec = (r.get("recommend_date") or "").strip()
        peers = [p.strip() for p in re.split(r"[;|]", r.get("compare_with") or "") if p.strip()]
        funds.append({"name": name, "recommend_date": rec, "peers": peers})
    return funds


def main():
    if not API_KEY:
        raise SystemExit("ไม่พบ SEC_API_KEY (ตั้งใน GitHub → Settings → Secrets → Actions)")
    selftest = "--selftest" in sys.argv

    funds = read_funds()
    names = []
    for f in funds:
        for n in [f["name"], *f["peers"]]:
            if n not in names:
                names.append(n)

    by_class, by_abbr = build_lookup(load_profiles())

    today = date.today()
    resolved, missing = {}, {}
    for n in names:
        res = resolve(n, by_class, by_abbr)
        if res is None:
            missing[n] = "ไม่พบชื่อนี้ใน SEC (เช็คชื่อย่อ/ชื่อ class)"
        elif "ambiguous" in res:
            missing[n] = "ชื่อนี้มีหลาย class ให้ระบุเป็น: " + ", ".join(res["ambiguous"])
        else:
            resolved[n] = {**res, "name": n}
    for n, why in missing.items():
        print(f"  ! {n}: {why}")

    if selftest:
        n = funds[0]["name"]
        info = resolved.get(n)
        print("\n=== SELFTEST ===")
        print("กองทดสอบ:", n, json.dumps(info, ensure_ascii=False))
        if info:
            params = {"proj_id": info["proj_id"]}
            for fmt in DATE_FORMATS:
                params.update(
                    start_nav_date=(today - timedelta(days=14)).strftime(fmt),
                    end_nav_date=today.strftime(fmt),
                )
                r = get(NAV_URL, params)
                print(f"\nรูปแบบวันที่ {fmt} -> HTTP {r.status_code}")
                print(r.text[:3000])
                if r.status_code == 200:
                    rows, _ = extract_list(r.json())
                    got, w = parse_rows(rows, info["fund_class_name"])
                    print(f"\nอ่านได้ {len(got)} วัน", w or "",
                          [(d.isoformat(), v) for d, v in sorted(got.items())[-3:]])
                    break
        return

    rec_dates = {}
    for f in funds:
        try:
            rec_dates[f["name"]] = date.fromisoformat(f["recommend_date"])
        except ValueError:
            pass

    report = {"run_at": datetime.utcnow().isoformat() + "Z", "funds": {}, "missing": missing}
    for f in funds:
        # ดึงตั้งแต่ก่อนวันแนะนำเล็กน้อย (เผื่อวันแนะนำเป็นวันหยุด)
        if f["name"] in rec_dates:
            start = rec_dates[f["name"]] - timedelta(days=14)
        else:
            start = today - timedelta(days=LOOKBACK_DAYS)
        for n in [f["name"], *f["peers"]]:
            if n not in resolved or n in report["funds"]:
                continue
            info = resolved[n]
            print(f"NAV {n} ({info['proj_id']} {info['fund_class_name'] or 'main'}) ...")
            try:
                count, warn = fetch_series(info, start, today)
                report["funds"][n] = {**info, "days": count, "warning": warn}
                print(f"  ok {count} วัน {warn or ''}")
            except Exception as e:  # noqa: BLE001
                report["funds"][n] = {**info, "days": 0, "warning": str(e)[:300]}
                print(f"  ! {e}")

    DATA.mkdir(exist_ok=True)
    (DATA / "fetch_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1))
    (DATA / "fund_index.json").write_text(json.dumps(resolved, ensure_ascii=False, indent=1))

    if not any(v["days"] for v in report["funds"].values()):
        raise SystemExit("ดึง NAV ไม่ได้เลยสักกอง - ลองรันแบบ selftest เพื่อดู response")


if __name__ == "__main__":
    main()

# Top Funds Weekly Charts

สร้างกราฟ NAV สำหรับรายงาน **Top Funds Weekly Market Recap** อัตโนมัติจาก SEC Open API
เปิดหน้าเว็บ → เลือกกอง → กด **Copy รูป** → วางใน PowerPoint

## ใช้งานทุกสัปดาห์

1. ระบบรันเองทุก **วันจันทร์ 06:15** (เวลาไทย) ดึง NAV ล่าสุดและสร้างกราฟใหม่
2. เปิดหน้าเว็บ (GitHub Pages ของ repo นี้) เลือกกอง แล้วกด **Copy รูป**
3. ใน PowerPoint: **คลิกขวาที่รูปกราฟเก่า → Change Picture → From Clipboard**
   รูปใหม่จะเข้าแทนที่ด้วยขนาดและตำแหน่งเดิม (รูปทำมาสัดส่วนเท่ากรอบเดิม 520×212 pt)

## เปลี่ยนกองที่แนะนำ

แก้ไฟล์ `funds.csv` บรรทัดเดียว แล้วกด Run workflow (หรือรอวันจันทร์)

| คอลัมน์ | ตัวอย่าง | หมายเหตุ |
|---|---|---|
| `fund` | `KKP GHC-A` | ชื่อย่อกอง หรือชื่อ class ตามที่ ก.ล.ต. ใช้ (ไม่สนช่องว่าง/ขีด/ตัวพิมพ์) |
| `recommend_date` | `2025-11-11` | วันที่แนะนำ กราฟเริ่มจากวันนี้ (0%) และใช้คำนวณผลตอบแทน "ตั้งแต่แนะนำ" |
| `compare_with` | `KT-EMEQ-A; SCBEMEQA` | กองเปรียบเทียบ คั่นด้วย `;` (ไม่ใส่ก็ได้) |

ระบบหา `proj_id` และ class ให้เองจาก `/v2/fund/general-info/profiles`
ถ้าชื่อผิดหรือชื่อมีหลาย class หน้าเว็บจะขึ้นกล่อง "ต้องเช็ค" พร้อมบอกชื่อ class ที่ถูกต้อง
กองใหม่จะถูกดึง NAV ย้อนหลังให้อัตโนมัติในรอบแรก

## รันเอง

GitHub → แท็บ **Actions** → **Weekly charts** → **Run workflow**
- `update` ดึงข้อมูล + สร้างกราฟ + บันทึก
- `selftest` พิมพ์ response จริงของ API กองแรกออกมาใน log (ไม่บันทึกอะไร) ใช้เช็คเวลา API มีปัญหา

## ตั้งค่าครั้งแรก

1. Secret `SEC_API_KEY` (Settings → Secrets and variables → Actions)
2. Settings → Pages → Source: **Deploy from a branch** → `main` / `/docs`

## โครงสร้าง

```
funds.csv                  รายชื่อกอง (ไฟล์เดียวที่ต้องแก้)
scripts/fetch_nav.py       ดึง profiles + NAV จาก SEC Open API v2
scripts/build_charts.py    สร้าง docs/charts/*.png และ docs/data.json
docs/index.html            หน้าเว็บเลือกกอง / Copy รูป / ตารางผลตอบแทน
data/nav/*.csv             NAV สะสมรายวัน (ดึงเพิ่มเฉพาะวันใหม่)
```

ข้อมูลทั้งหมดมาจาก SEC Open API (สำนักงาน ก.ล.ต.) เท่านั้น

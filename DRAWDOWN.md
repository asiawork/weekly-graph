# Maximum Drawdown & Recovering Period (ไม่มีหน้าเว็บ)

ตาราง Maximum Drawdown และ Recovering Period ของกองที่กำหนดเอง ได้เป็นไฟล์ Excel ไว้เปิดหรือ copy ไปวาง
ใช้ค่าที่ บลจ. รายงานใน **factsheet ฉบับล่าสุด** (SEC Open API `/v2/fund/factsheet/statistics`) ไม่ได้คำนวณจาก NAV
ไม่แตะกราฟ weekly และหน้าเปรียบเทียบ FIXEDSP

## รัน

GitHub → แท็บ **Actions** → **Drawdown report** → **Run workflow** (ไม่มีรอบอัตโนมัติ · factsheet อัปเดตรายเดือน)

รันเสร็จได้ไฟล์ 2 ที่ (เหมือนกัน):
- ใน repo: `drawdown/drawdown_report.xlsx` (กด Download raw file) และ `drawdown/drawdown_summary.csv`
- ท้ายหน้ารันใน Actions: **Artifacts → drawdown-report** (zip)

## แก้รายชื่อกอง

แก้ `drawdown_funds.csv` คอลัมน์ `fund` บรรทัดละกอง ชื่อย่อกองหรือชื่อ class ตาม ก.ล.ต. (ใส่ซ้ำได้ ระบบตัดให้)
ชื่อผิด ชื่อมีหลาย class หรือ factsheet ไม่มีค่า จะขึ้นในชีต **ต้องเช็ค**

## ในไฟล์ Excel

| ชีต | มีอะไร |
|---|---|
| สรุป | กองทุน · ชื่อกอง · บลจ. · Maximum Drawdown (%) · Recovering Period · ที่มา Recovering Period · factsheet ณ · ลิงก์ factsheet |
| ข้อมูลดิบ factsheet | ค่าสถิติทั้งหมดที่ factsheet ให้มา (เผื่ออยากได้ค่าอื่น เช่น Sharpe, Beta) |
| ต้องเช็ค | กองที่หาไม่เจอ / ไม่มีข้อมูล |

SEC API บางกองส่ง Recovering Period มาว่าง ทั้งที่ใน PDF factsheet มีค่า กองแบบนี้ระบบเปิด PDF factsheet ฉบับล่าสุดแล้วอ่านค่าให้ (คอลัมน์ "ที่มา" บอกว่าได้จาก API หรือ PDF) ถ้าอ่านไม่ได้จะบอกให้เปิดลิงก์เช็คเอง

ค่าแสดงตามที่ บลจ. รายงาน (ช่วงเวลาที่ใช้คำนวณขึ้นกับแต่ละ บลจ. ตามแบบ factsheet ของ ก.ล.ต.)

## ไฟล์

```
drawdown_funds.csv               รายชื่อกอง
scripts/drawdown_report.py       ดึง factsheet statistics + เขียน Excel/CSV
.github/workflows/drawdown.yml   workflow "Drawdown report" (รันเอง)
drawdown/                        ไฟล์ผลลัพธ์
```

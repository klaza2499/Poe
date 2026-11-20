# PoE Build Planner 🛠️ (คล้าย Path of Building — ให้ Claude ทำบิ้วได้เอง)

`poe_build/` คือโปรแกรมวางแผนบิ้ว **Path of Exile 1** ที่มีข้อมูลเกมครบ (ดึงจาก Path of Building Community)
และออกแบบมาให้ **Claude เข้าไปค้นข้อมูลและทำบิ้วได้เองทั้งหมด** ผ่าน MCP / command line ส่วนคุณดูบิ้วสดๆ ในเว็บ

**ข้อมูลที่มี** (ต้นไม้ 3.30): passive 3,468 node (notable / keystone / mastery / ascendancy / bloodline),
เจม 824 อัน (รวม support พร้อมกฎว่าเสริมสกิลไหนได้), unique 1,318 ชิ้น (ทุก variant), base item 1,271 แบบ,
mod ~25,000 ตัว (prefix/suffix ทุก tier, craft bench, corrupted, veiled, eldritch, jewel, flask …),
essence, enchant, cluster jewel, pantheon, minion/spectre, boss

**ทำอะไรได้**
- 🌳 เพิ่ม passive แค่บอกชื่อ — หาเส้นทางสั้นที่สุดให้อัตโนมัติ, เลือก mastery, ascendancy, bloodline
- 💎 จัดกลุ่มเจม แก้เลเวล/quality + เช็คว่า support ใช้กับสกิลได้จริง (กฎ skill type แบบเดียวกับ PoB)
- 🛡️ ใส่ unique ตามชื่อ / สร้าง rare จาก base + mod ที่ roll ได้จริง / วางข้อความไอเทมจากเกม
- ⚔️ **คำนวณ DPS แบบ PoB**: hit DPS (spell / attack / dual wield), conversion & gain as extra, crit, accuracy,
  penetration, damage over time, ignite / bleed / poison, aura / herald / curse จากกลุ่มเจมอื่น, charges
  — ใช้ตัวแยก mod ของ PoB เอง (ModCache ~19,000 บรรทัด) และสูตรจากโค้ด PoB
- 🛡️ คำนวณป้องกัน: life / mana / ES / ward, armour / evasion, resist, block, suppress, regen, mana reservation, **max hit taken**
- ⚙️ **Config ครบ 591 ตัวเลือกแบบ PoB** (ประเภทบอส, resist ศัตรู, charges, shock, full life, flask …)
- 🔬 **ลองเปลี่ยนก่อนบันทึก** (`what_if`) — ดูว่า DPS/Life เปลี่ยนเท่าไร, **แนะนำ passive ที่คุ้มที่สุดต่อแต้ม** (`suggest_passives`), เปรียบเทียบบิ้ว
- 📋 รายการปัญหาที่ต้องแก้: resist ไม่ cap, attribute ไม่พอสำหรับไอเทม/เจม, เลเวลไม่ถึง, แต้มเกิน, mana ไม่พอ
- 🔁 **Export เป็นโค้ด Path of Building** / **Import** โค้ด PoB, ลิงก์ pobb.in หรือ **ดึงตัวละครจากบัญชี pathofexile.com** (โปรไฟล์ต้องเปิด public)

- 🧟 **DPS ของ minion** (Zombie, SRS, Golem, Spectre …) รวม Minion Damage support, mod "Minions deal…" บนต้นไม้/ไอเทม และ aura ที่ช่วยมินเนี่ยน
- ⚡ **สกิลที่ trigger**: Cast On Critical Strike (ใช้อัตรา crit ของ attack ในกลุ่มเดียวกัน + ปัดคูลดาวน์ตาม server tick แบบ PoB), Cast while Channelling, Spellslinger, CwDT (แสดงดาเมจต่อครั้ง)
- 💠 **Cluster jewel**: ใส่ใน socket นอก ระบบนับ notable + small passive ทั้งหมด (และนับแต้มที่ใช้)
- 🗂️ **Loadout** หลายชุดในบิ้วเดียว (Leveling / Mapping / Bossing) แบบ tree/item/skill set ของ PoB
- ✍️ **Custom modifiers** แบบ PoB (Config) — พิมพ์ mod เพิ่มเองได้
- ✅ เทียบผลกับ **เทสต์ของ PoB เอง** (max hit, gem level/quality, crit) — ผ่านทั้งหมด (`tests/test_pob_reference.py`)

> ยังไม่ครบ: timeless jewel, unique บางชิ้น/สกิลประมาณ 120 ตัวที่ PoB เขียนสูตรพิเศษ (มีหมายเหตุบอก),
> trigger แบบซับซ้อน (Mirage Archer, Arcanist Brand ฯลฯ คิดแบบประมาณ) — หน้าสรุปจะบอกเสมอว่าอะไรคำนวณไม่ได้

## ใช้กับ Claude

**Claude Code** — เปิดโฟลเดอร์นี้ใน Claude Code จะเจอ MCP server `poe-build` จาก `.mcp.json` เอง
(และ `CLAUDE.md` สอนวิธีใช้ไว้แล้ว) แล้วสั่งได้เลย เช่น
*"ทำบิ้ว Righteous Fire Juggernaut เลเวล 95 งบน้อย ใส่ของให้ resist ครบ แล้วขอโค้ด PoB"*

**Claude Desktop** — เพิ่มใน `claude_desktop_config.json` (Settings → Developer → Edit Config):
```json
{
  "mcpServers": {
    "poe-build": {
      "command": "python",
      "args": ["-m", "poe_build", "mcp"],
      "env": { "PYTHONPATH": "C:\\path\\to\\Poe" }
    }
  }
}
```
(เปลี่ยน `C:\\path\\to\\Poe` เป็นที่อยู่โฟลเดอร์นี้ แล้วปิด-เปิด Claude Desktop ใหม่)

**Command line** (ใช้ได้ทุกที่ ผลเป็น JSON): `python -m poe_build tools` ดูคำสั่งทั้งหมด
```bash
python -m poe_build new_build --name "RF Jugg" --class_name Marauder --ascendancy Juggernaut --level 95
python -m poe_build allocate --name "RF Jugg" --nodes "Unbreakable" "Heart of Oak"
python -m poe_build show_build --name "RF Jugg"
python -m poe_build export_pob --name "RF Jugg"
```

## หน้าเว็บ (ดู/แก้บิ้วเอง)

ดับเบิลคลิก **`run_planner.bat`** (หรือ `python -m poe_build web`) → เปิด http://127.0.0.1:8765
- ต้นไม้ passive: คลิก node เพื่อเพิ่ม/ลบ (ชี้เมาส์เห็นเส้นทาง + จำนวนแต้ม), ค้นหาไฮไลต์ node
- ชี้ node จะเห็น **DPS / Life ที่เพิ่ม-ลด** ทันที (แบบ PoB)
- แท็บ สรุป (DPS + ป้องกัน) / ไอเทม (ปุ่ม U = unique, R = สร้าง rare, ✎ = วางข้อความ) / สกิล (แก้เลเวล/quality, ปุ่ม ? ดู support ที่ใช้ได้) / Passive / Config / โน้ต
- ปุ่ม Export / Import โค้ด PoB
- **อัปเดตสด** — ระหว่างที่ Claude แก้บิ้วผ่าน MCP หน้าเว็บจะเปลี่ยนตามเองทุก 2 วินาที

บิ้วเก็บเป็นไฟล์ JSON ในโฟลเดอร์ `builds/` (ตั้งที่อื่นได้ด้วย env `POE_BUILDS_DIR`) ไม่ต้องติดตั้งอะไรเพิ่ม ใช้แค่ Python 3.10+

## อัปเดตข้อมูลเมื่อมีลีกใหม่

```bash
tools/update_data.sh 3_30     # ดึงข้อมูลล่าสุดจาก PoB Community แล้วแปลงเป็น poe_build/data/
```
ตัวแปลง (`tools/build_data.py`) แค่ *อ่าน* ไฟล์ Lua ของ PoB เป็นข้อมูล ไม่ได้รันโค้ดใดๆ
(รวมถึง stat map ของสกิล, ModCache และตัวเลือก Config — แปลงจากข้อความเป็นข้อมูลด้วย `tools/lualit.py` + `tools/luaexpr.py`)

---

# PoE Act Helper ⚡

Overlay ช่วยวิ่ง Act 1–10 ใน **Path of Exile 1** ให้จบเร็วที่สุด — ลอยอยู่บนเกม บอกว่าต้องทำอะไรในแต่ละโซน
และ **เลื่อนขั้นตอนให้อัตโนมัติ** เมื่อคุณเข้าโซนใหม่ (อ่านจากไฟล์ log ของเกม ไม่ยุ่งกับตัวเกม จึงไม่ผิดกฎ)

## ฟีเจอร์

- 🗺️ **เส้นทางสปีดรันครบ Act 1–10** (ภาษาไทย) — ลำดับโซน, เควสที่ต้องส่ง, จุดที่ควร Logout, ทริคหาทางออก
- 🤖 **เลื่อนขั้นอัตโนมัติ** จาก `Client.txt` (ใช้รหัสพื้นที่ จึงแยก The Coast ของ Act 1 กับ Act 6 ได้)
  — ผ่านเมืองตอน Logout ก็ไม่กระโดดข้ามขั้น, เปิดโปรแกรมกลางทางก็ตามทันเอง
- ⭐ ไฮไลต์เควสที่ให้ **Passive** (สีทอง) และจุด **Logout** (สีส้ม) ไม่ให้พลาด
- 📈 **ตัวเตือน XP penalty** — แสดงเลเวลตัวละคร vs เลเวลพื้นที่ บอกว่าได้ XP กี่ % / เลเวลเกินควรรีบไป
- ⏱️ **จับเวลา + Split ต่อ Act** เทียบกับสถิติดีที่สุด (PB) — เริ่มเองเมื่อเข้า The Twilight Strand จบเองที่ Karui Shores
- ⚙️ ตัวเลือก: โหมดเปิดลีก (Trial/Lab/เควสเก็บของ), เควส Library, Bandits (ฆ่าหมด / ช่วย Alira)
- 🗺️ **ดูภาพ layout แมพ** ของโซนที่อยู่ — เปลี่ยนตามโซนอัตโนมัติ, แคปหน้าจอเก็บ layout เองได้ (ดูหัวข้อด้านล่าง)
- 💾 จำตำแหน่งหน้าต่าง/ขั้นตอน/เวลา — ปิดแล้วเปิดใหม่ทำต่อได้

## วิธีใช้ (Windows)

1. ติดตั้ง [Python 3.10+](https://www.python.org/downloads/) (ติ๊ก *Add Python to PATH*)
2. ดาวน์โหลดโปรเจกต์นี้ แล้วดับเบิลคลิก **`run.bat`**
3. ในเกมตั้ง **Options → Graphics → Display Mode: Windowed Fullscreen** (overlay จะได้อยู่บนเกม)
4. โปรแกรมจะหา `Client.txt` เอง ถ้าหาไม่เจอ กด ⚙ แล้วเลือกไฟล์
   (ปกติอยู่ที่ `...\Path of Exile\logs\Client.txt` ทั้ง Standalone และ Steam)
5. สร้างตัวละครใหม่แล้ววิ่งได้เลย!

หรือรันจาก terminal: `python -m poe_act_helper` (ใส่ `--log "D:\...\Client.txt"` เพื่อระบุไฟล์เอง)

## ปุ่มลัด

| ปุ่ม | ทำอะไร |
|---|---|
| ◀ / ▶ (หรือลูกศรซ้าย/ขวาเมื่อคลิกหน้าต่าง) | ย้อน / ข้ามขั้น |
| ⏯ | หยุด/เดินเวลา |
| 🗺 หรือปุ่ม `M` / `Ctrl+Alt+M` | เปิด/ปิดหน้าต่าง layout ของโซนปัจจุบัน |
| `Ctrl+Alt+S` | แคปหน้าจอเก็บเป็น layout ของโซนนี้ (กด Tab เปิดแมพในเกมก่อน) |
| ⚙ หรือคลิกขวา | ตั้งค่า, กระโดดไป Act, รีเซ็ตรัน |
| ลากแถบบน | ย้ายหน้าต่าง |
| `Ctrl+Alt+→` / `Ctrl+Alt+←` / `Ctrl+Alt+P` | ปุ่มลัดทั่วระบบ (ต้อง `pip install keyboard` ก่อน) |

## ภาพ layout แมพ

แต่ละโซนใน PoE มี layout ตายตัวไม่กี่แบบ จำได้แล้วจะวิ่งเร็วขึ้นมาก กด 🗺 เพื่อดูภาพของโซนปัจจุบัน
(หน้าต่างจะเปลี่ยนภาพเองเมื่อเข้าโซนใหม่ คลิกภาพหรือ ◀ ▶ เพื่อดูแบบอื่น)

ได้ภาพมาได้ 3 ทาง:
1. **⬇ ดาวน์โหลด layout** (แนะนำ) — กดในหน้าต่าง layout ครั้งเดียว จะได้ภาพครบทุกโซนของ Act 1–10 (~470 ภาพ, 18 MB)
   จากฟีเจอร์ Act-Decoder ของ [Exile-UI](https://github.com/Lailloken/Exile-UI) (MIT license)
   - ภาพ `แบบ N · เบาะแสบน minimap` = จุดสังเกตไว้เทียบกับ minimap ว่าโซนนี้เป็นแบบไหน
   - ภาพ `แบบ N · แผนที่` = แผนที่เต็มของแบบนั้น พร้อมจุดเควส/บอส/ทางออก (กด ❓ ดูความหมายสัญลักษณ์)
2. **แคปเอง** — ในเกมกด Tab เปิดแมพใหญ่ แล้วกด `Ctrl+Alt+S` หรือ 📷
3. **ใส่ภาพเอง** ในโฟลเดอร์ [`layouts/`](layouts/README.md) ตั้งชื่อตามรหัสโซน เช่น `1_1_3.png`

> `run.bat` ติดตั้ง Pillow (ใช้เปิดภาพ jpg/แคปหน้าจอ) และ keyboard (ปุ่มลัดทั่วระบบ) ให้อัตโนมัติตอนเปิดครั้งแรก

## ใช้ Exile-UI เป็นภาษาไทย

ถ้าใช้ [Exile-UI](https://github.com/Lailloken/Exile-UI) ร่วมด้วย มีแพ็กภาษาไทยในโฟลเดอร์ [`exile-ui-thai/`](exile-ui-thai/README.md) — ลากโฟลเดอร์ Exile-UI มาวางบน `install.bat` จบ

## ทริควิ่งให้เร็ว

- ทำ **รองเท้า +10% Movement Speed** ใน Act 1: ขาย Quicksilver Flask + Orb of Augmentation + รองเท้าธรรมดา
- ใช้ **Logout** แทนการเดินกลับเมือง (เร็วกว่า Portal) — โปรแกรมบอกจุดที่ควรทำ
- อย่าเคลียร์มอนทุกตัว ให้ดูแถบ XP — ถ้าขึ้นสีแดง "เลเวลต่ำไป" ค่อยหยุดเก็บเลเวล
- หลังฆ่า Kitava (Act 5 และ 10) จะโดนลด Resistance — เตรียมของ resist ไว้ก่อน
- โหมด "เปิดลีก" ทำ Trial ครบเพื่อขึ้น Lab ได้ทันที ถ้าวิ่งแข่งเวลาล้วนๆ ปิดโหมดนี้ได้

## แก้ไขเส้นทางเอง

เส้นทางเก็บใน [`poe_act_helper/data/route_poe1.json`](poe_act_helper/data/route_poe1.json) แต่ละขั้นคือ "การเข้าโซน 1 ครั้ง":

```json
{"act": 1, "area": "1_1_3", "zone": "The Mud Flats",
 "tasks": ["เก็บ Glyph 3 อัน", {"t": "งานเฉพาะโหมดเปิดลีก", "only": "league_start"}],
 "tips": ["คำแนะนำหาทาง"], "lvl": "8"}
```

- `area` = รหัสพื้นที่จากบรรทัด `Generating level X area "..."` ใน Client.txt
- `only` = `league_start`, `library`, `bandit:kill_all`, `bandit:alira` (ใส่ `!` ข้างหน้าเพื่อกลับเงื่อนไข)
- ทดลองเส้นทางโดยไม่ต้องเปิดเกม: `python -m poe_act_helper --simulate`
- รันเทสต์: `python -m unittest discover tests`

> ⚠️ เส้นทางอ้างอิงลำดับเควสของ PoE 1 เวอร์ชัน 3.x ถ้า GGG เปลี่ยนเควส/แผนที่ในลีกใหม่ อาจต้องแก้ไฟล์ JSON นิดหน่อย
>
> ไฟล์การตั้งค่า/สถิติอยู่ที่ `%USERPROFILE%\.poe_act_helper\state.json`
>
> ภาพ layout จาก Exile-UI © Lailloken — MIT license, ดู [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)

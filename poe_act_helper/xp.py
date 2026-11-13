"""คำนวณ XP penalty ตามสูตรของ PoE 1 (ช่วยให้รู้ว่าเลเวลเกิน/ขาดจนเสีย XP หรือไม่)."""
from __future__ import annotations


def safe_range(char_level: int) -> int:
    return 3 + char_level // 16


def effective_area_level(area_level: int) -> float:
    if area_level >= 70:  # แผนที่ระดับสูงใช้เลเวลมอนที่ถูกปรับลด
        return -0.03 * area_level**2 + 5.17 * area_level - 144.9
    return float(area_level)


def xp_multiplier(char_level: int, area_level: int) -> float:
    if char_level <= 0 or area_level <= 0:
        return 1.0
    diff = abs(char_level - effective_area_level(area_level))
    eff = max(diff - safe_range(char_level), 0.0)
    mult = ((char_level + 5) / (char_level + 5 + eff**2.5)) ** 1.5
    # หมายเหตุ: ไม่รวม penalty พิเศษช่วงเลเวล 95+ เพราะไม่เกี่ยวกับการวิ่ง Act
    return max(mult, 0.01)


def level_window(char_level: int) -> tuple[int, int]:
    """ช่วงเลเวลพื้นที่ที่ได้ XP เต็ม 100%"""
    s = safe_range(char_level)
    return char_level - s, char_level + s

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "exile-ui-thai"))
import install_thai  # noqa: E402

UI = '''﻿;## comment = "not a value"
\t\tsystem_font\t\t\t=\t"Fontin SmallCaps"\t\t;## font
\t\ttranslator\t\t\t=\t"translators:"
\t\ttranslator\t\t\t=\t"lailloken"
\t\tglobal_save\t\t\t=\t"save"\t\t;## button
\t\tlvltracker_format_kill\t\t=\t"kill"
\t\tlvltracker_recommended\t\t=\t"recommended level:"
\t\tlvltracker_importerror\t\t=\t""gems only" guides;are not supported"
\t\tmechanic_delve\t\t\t=\t"delve"
'''
CLIENT = '\t\tsystem_font\t\t\t=\t"Fontin SmallCaps"\t\t;## x\n'


class InstallThaiTests(unittest.TestCase):
    def test_install_and_undo(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            eng = root / "data" / "english"
            eng.mkdir(parents=True)
            (eng / "UI.txt").write_text(UI, encoding="utf-8")
            (eng / "client.txt").write_text(CLIENT, encoding="utf-8")
            install_thai.install(root)
            th = (root / "data" / "thai" / "UI.txt").read_text(encoding="utf-8-sig")
            self.assertIn('"บันทึก"\t\t;## button', th)
            self.assertIn('"kill"', th)                       # คำสั่งอ่านไกด์ต้องคงเดิม
            self.assertIn('"recommended level:"', th)
            self.assertIn('"delve"', th)                      # ชื่อในเกมคงเดิม
            self.assertIn('"Leelawadee UI"', th)
            self.assertIn('""gems only" guides;are not supported"', th)
            self.assertIn('"ภาษาไทย: PoE Act Helper"', th)
            self.assertIn("Leelawadee UI", (eng / "client.txt").read_text(encoding="utf-8-sig"))
            self.assertIn("language=thai", (root / "ini" / "config.ini").read_text())
            install_thai.undo(root)
            self.assertIn("Fontin", (eng / "client.txt").read_text(encoding="utf-8-sig"))
            self.assertIn("language=english", (root / "ini" / "config.ini").read_text())

    def test_no_placeholder_lost(self):
        from translations import T
        for en, th in T.items():
            for tok in ("#", ";", "<", ">"):
                self.assertEqual(en.count(tok), th.count(tok), f"{tok!r} ไม่ครบใน: {en} -> {th}")


if __name__ == "__main__":
    unittest.main()

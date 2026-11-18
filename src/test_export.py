"""엑셀 내보내기 검증. 기대 결과(expected/A_test_predictions.json)로 만들어 읽어서 값과 표시가 맞는지 본다.
실행: python -X utf8 -m unittest src.test_export -v   (프로젝트 폴더에서)
"""
import csv
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import export_xlsx as X
from openpyxl import load_workbook

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PRED = os.path.join(ROOT, "expected", "A_test_predictions.json")
with open(PRED, encoding="utf-8") as f:
    PREDS = json.load(f)


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.xlsx = os.path.join(self.tmp.name, "ledger.xlsx")
        self.csv = os.path.join(self.tmp.name, "ledger.csv")
        self.counts = X.export(PRED, self.xlsx, self.csv)
        self.wb = load_workbook(self.xlsx)

    def tearDown(self):
        self.tmp.cleanup()

    def test_row_counts(self):
        # 예측의 품목은 103줄: 정답 102줄 + 17번 손글씨가 별도 줄로 읽힌 1줄(내보내기는 예측을 그대로 옮긴다)
        n = sum(len(d["items"]) for d in PREDS)
        self.assertEqual(n, 103)
        self.assertEqual(self.counts, (n, 20))
        self.assertEqual(self.wb.sheetnames, ["장부 초안", "문서 요약", "읽는 법"])
        self.assertEqual(self.wb["장부 초안"].max_row, n + 1)
        self.assertEqual(self.wb["문서 요약"].max_row, 21)

    def test_values_are_copied_not_changed(self):
        ws = self.wb["장부 초안"]
        header = [c.value for c in ws[1]]
        rows = [dict(zip(header, [c.value for c in r])) for r in ws.iter_rows(min_row=2)]
        flat = [(d["file"], it) for d in PREDS for it in d["items"]]
        self.assertEqual(len(rows), len(flat))
        for row, (f, it) in zip(rows, flat):
            self.assertEqual(row["파일"], f)
            self.assertEqual(row["품목"] or "", it["item"])  # 엑셀은 빈 문자열을 빈 칸(None)으로 읽는다
            for src, col in (("qty", "수량"), ("unit_price", "단가"), ("amount", "금액")):
                self.assertEqual(row[col], it[src])

    def test_numbers_are_numeric_cells(self):
        ws = self.wb["장부 초안"]
        for r in ws.iter_rows(min_row=2):
            for col in (3, 4, 5):  # 수량, 단가, 금액 (None은 읽지 못한 값)
                self.assertIn(type(r[col].value).__name__, ("int", "float", "NoneType"))

    def test_review_rows_are_flagged_and_yellow(self):
        ws = self.wb["장부 초안"]
        expected = sum(1 for d in PREDS for it in d["items"] if it["review"])
        flagged = [r for r in ws.iter_rows(min_row=2) if r[6].value == "확인 필요"]
        self.assertEqual(len(flagged), expected)
        self.assertGreater(expected, 0)
        for r in flagged:
            self.assertEqual(r[0].fill.fgColor.rgb[-6:], "FFF2CC")
        plain = next(r for r in ws.iter_rows(min_row=2) if r[6].value in (None, ""))
        self.assertNotEqual(plain[0].fill.fgColor.rgb[-6:], "FFF2CC")

    def test_confirm_column_is_left_empty_for_the_person(self):
        ws = self.wb["장부 초안"]
        self.assertTrue(all(r[8].value in (None, "") for r in ws.iter_rows(min_row=2)))

    def test_doc_summary_marks_only_the_mismatch_document(self):
        ws = self.wb["문서 요약"]
        header = [c.value for c in ws[1]]
        mism = [dict(zip(header, [c.value for c in r]))["파일"] for r in ws.iter_rows(min_row=2)
                if dict(zip(header, [c.value for c in r]))["합계 불일치"] == "불일치"]
        self.assertEqual(mism, ["명세서_20.pdf"])

    def test_csv_matches_sheet(self):
        with open(self.csv, encoding="utf-8-sig", newline="") as f:
            rows = list(csv.reader(f))
        self.assertEqual(rows[0], X.ITEM_COLS)
        self.assertEqual(len(rows), self.wb["장부 초안"].max_row)

    def test_refuses_to_overwrite(self):
        with self.assertRaises(FileExistsError):
            X.export(PRED, self.xlsx)
        with self.assertRaises(FileExistsError):
            X.export(PRED, os.path.join(self.tmp.name, "other.xlsx"), self.csv)

    def test_reasons_are_read_from_the_run_log_when_present(self):
        run = os.path.join(ROOT, "outputs", "A", "20261118-000022")
        if not os.path.exists(os.path.join(run, "params.json")):
            self.skipTest("outputs/가 없음(저장소에는 포함되지 않는 실행 결과)")
        out = os.path.join(self.tmp.name, "with_reasons.xlsx")
        X.export(os.path.join(run, "predictions.json"), out)
        ws = load_workbook(out)["장부 초안"]
        reasons = {r[7].value for r in ws.iter_rows(min_row=2) if r[6].value == "확인 필요"}
        self.assertIn("글자 인식 신뢰도 낮음", reasons)
        self.assertNotIn("확인 필요", reasons)


if __name__ == "__main__":
    unittest.main()

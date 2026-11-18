"""측정 도구 검증. 정답에서 '완벽한 입력'을 만들고 일부러 망가뜨려 기대대로 세는지 본다.
실행: python -X utf8 -m unittest src.test_measure -v   (프로젝트 폴더에서)
"""
import csv
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import measure as M
from openpyxl import Workbook

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
with open(os.path.join(ROOT, "sample-invoices", "정답.json"), encoding="utf-8") as f:
    GT = json.load(f)
DOCS = ["명세서_01.pdf", "명세서_02.pdf"]


def perfect_rows(docs=DOCS, flagged=()):
    rows = []
    for g in GT:
        if g["file"] not in docs:
            continue
        for it in g["items"]:
            rows.append({"파일": g["file"], "거래처": g["supplier"], "날짜": g["date"], "품목": it["item"],
                         "수량": it["qty"], "단가": it["unit_price"], "금액": it["amount"],
                         "확인 필요": "확인 필요" if (g["file"], it["item"]) in flagged else ""})
    return rows


def timing(man=(100, 200), ass=(40, 80), who="나", docs=DOCS):
    rows = []
    for d, m, a in zip(docs, man, ass):
        rows.append({"doc": d, "method": M.MANUAL, "sec": float(m), "fixed": "", "who": who})
        rows.append({"doc": d, "method": M.ASSISTED, "sec": float(a), "fixed": "", "who": who})
    return rows


class MeasureTests(unittest.TestCase):
    def test_perfect_entries_have_no_wrong_cells(self):
        s, per = M.score_entries(GT, perfect_rows(), DOCS)
        self.assertEqual((s["wrong"], s["silent"]), (0, 0))
        n = sum(len(g["items"]) for g in GT if g["file"] in DOCS)
        self.assertEqual(s["cells"], 2 * len(DOCS) + 4 * n)

    def test_each_wrong_cell_counts_once(self):
        rows = perfect_rows()
        rows[0]["수량"] += 1
        rows[1]["금액"] += 10
        rows[2]["품목"] = rows[2]["품목"] + "X"
        s, _ = M.score_entries(GT, rows, DOCS)
        self.assertEqual(s["wrong"], 3)

    def test_wrong_supplier_and_date_count_two_cells(self):
        rows = perfect_rows()
        for r in rows:
            if r["파일"] == DOCS[0]:
                r["거래처"] = "엉뚱상사"
                r["날짜"] = "2020-01-01"
        s, _ = M.score_entries(GT, rows, DOCS)
        self.assertEqual(s["wrong"], 2)

    def test_missing_row_is_four_silent_cells(self):
        rows = perfect_rows()
        rows.pop(0)
        s, _ = M.score_entries(GT, rows, DOCS)
        self.assertEqual((s["wrong"], s["silent"]), (4, 4))

    def test_extra_row_counts_four_cells(self):
        rows = perfect_rows()
        rows.append({"파일": DOCS[0], "거래처": rows[0]["거래처"], "날짜": rows[0]["날짜"], "품목": "없는품목",
                     "수량": 1, "단가": 100, "금액": 100, "확인 필요": ""})
        s, _ = M.score_entries(GT, rows, DOCS)
        self.assertEqual((s["wrong"], s["silent"]), (4, 4))

    def test_flagged_wrong_cell_is_not_silent(self):
        first = next(g for g in GT if g["file"] == DOCS[0])["items"][0]["item"]
        rows = perfect_rows(flagged={(DOCS[0], first)})
        rows[0]["수량"] += 1
        s, _ = M.score_entries(GT, rows, DOCS)
        self.assertEqual((s["wrong"], s["silent"]), (1, 0))

    def test_unflagged_wrong_cell_is_silent(self):
        rows = perfect_rows()
        rows[0]["수량"] += 1
        s, _ = M.score_entries(GT, rows, DOCS)
        self.assertEqual((s["wrong"], s["silent"]), (1, 1))

    def test_row_order_and_whitespace_and_name_without_pdf(self):
        rows = list(reversed(perfect_rows()))
        for r in rows:
            r["품목"] = " ".join(r["품목"])
            r["파일"] = M.norm_doc(r["파일"].replace(".pdf", ""))
        s, _ = M.score_entries(GT, rows, DOCS)
        self.assertEqual(s["wrong"], 0)

    def test_missing_document_is_all_wrong(self):
        s, per = M.score_entries(GT, perfect_rows(docs=DOCS[:1]), DOCS)
        self.assertEqual(per[1]["wrong"], per[1]["cells"])

    def test_unknown_document_raises(self):
        with self.assertRaises(ValueError):
            M.score_entries(GT, perfect_rows(), ["명세서_99.pdf"])

    def test_summary_ratio_and_verdict(self):
        r = M.summarize(timing(), perfect_rows(), perfect_rows(), GT, target_ratio=0.7)
        ai = [r["ai_total"][d][0] for d in DOCS]
        self.assertAlmostEqual(r["med_manual"], 150.0)
        self.assertAlmostEqual(r["med_assisted"], sum(ai) / 2)
        self.assertAlmostEqual(r["ratio"], r["med_assisted"] / 150.0)
        self.assertTrue(r["achieved"])

    def test_verdict_fails_when_time_target_missed(self):
        r = M.summarize(timing(man=(100, 100), ass=(90, 90)), perfect_rows(), perfect_rows(), GT, target_ratio=0.7)
        self.assertFalse(r["time_ok"])
        self.assertFalse(r["achieved"])

    def test_verdict_fails_when_assisted_has_more_wrong_cells(self):
        bad = perfect_rows()
        bad[0]["수량"] += 1
        r = M.summarize(timing(), perfect_rows(), bad, GT, target_ratio=0.7)
        self.assertTrue(r["time_ok"])
        self.assertFalse(r["err_ok"])
        self.assertFalse(r["achieved"])

    def test_only_paired_documents_are_compared_and_noted(self):
        t = timing()
        t.append({"doc": "명세서_05.pdf", "method": M.MANUAL, "sec": 50.0, "fixed": "", "who": "나"})
        r = M.summarize(t, perfect_rows(), perfect_rows(), GT, target_ratio=0.7)
        self.assertEqual(r["n"], 2)
        self.assertTrue(any("명세서_05.pdf" in n for n in r["notes"]))

    def test_notes_small_sample_and_multiple_people(self):
        t = timing()
        t[0]["who"] = "다른사람"
        r = M.summarize(t, perfect_rows(), perfect_rows(), GT, target_ratio=0.7)
        text = M.render(r)
        self.assertTrue(any("8장" in n for n in r["notes"]))
        self.assertTrue(any("2명" in n for n in r["notes"]))
        self.assertIn("일반화할 수 없습니다", text)

    def test_per_document_ai_seconds_are_used_when_given(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "pred.json")
            json.dump([{"file": DOCS[0], "seconds": 30.0}, {"file": DOCS[1], "seconds": 10.0}], open(p, "w"))
            r = M.summarize(timing(), perfect_rows(), perfect_rows(), GT, 0.7, ai_pred=p)
        self.assertEqual(r["ai_total"][DOCS[0]], (70.0, 30.0))
        self.assertEqual(r["ai_total"][DOCS[1]], (90.0, 10.0))

    def test_template_and_report_roundtrip_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "m")
            M.cmd_template(type("A", (), {"out": out, "docs": ""})())
            rows = list(csv.reader(open(os.path.join(out, "timing_log.csv"), encoding="utf-8-sig")))
            self.assertEqual(rows[0], M.TIMING_COLS)
            self.assertEqual(len(rows), 1 + 2 * len(M.DEFAULT_DOCS))
            with self.assertRaises(SystemExit):
                M.cmd_template(type("A", (), {"out": out, "docs": ""})())
            # 채워 넣고 report
            t = os.path.join(tmp, "t.csv")
            with open(t, "w", encoding="utf-8-sig", newline="") as f:
                w = csv.writer(f)
                w.writerow(M.TIMING_COLS)
                for d, m, a in zip(DOCS, (100, 200), (40, 80)):
                    w.writerow([d.replace(".pdf", ""), M.MANUAL, "나", "", m, "", ""])
                    w.writerow([d.replace(".pdf", ""), M.ASSISTED, "나", "", a, 2, ""])
            xl = []
            for name in ("manual.xlsx", "assisted.xlsx"):
                wb = Workbook()
                ws = wb.active
                ws.append(M.ENTRY_COLS + ["확인 필요"])
                for r in perfect_rows():
                    ws.append([r[c] for c in M.ENTRY_COLS] + [r["확인 필요"]])
                path = os.path.join(tmp, name)
                wb.save(path)
                xl.append(path)
            res = os.path.join(tmp, "result.md")
            args = type("A", (), {"timing": t, "manual": xl[0], "assisted": xl[1],
                                  "gt": os.path.join(ROOT, "sample-invoices", "정답.json"),
                                  "target_time_ratio": 0.7, "ai_pred": "", "out": res})()
            M.cmd_report(args)
            self.assertIn("목표 달성", open(res, encoding="utf-8").read())
            with self.assertRaises(SystemExit):
                M.cmd_report(args)

    def test_read_entries_rejects_missing_columns_and_skips_blank_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "x.csv")
            open(p, "w", encoding="utf-8-sig").write("거래처,품목\nA,B\n")
            with self.assertRaises(ValueError):
                M.read_entries(p)
            q = os.path.join(tmp, "y.csv")
            open(q, "w", encoding="utf-8-sig").write("파일,품목,수량\n명세서_01,닭다리살,1\n,,\n")
            self.assertEqual(len(M.read_entries(q)), 1)

    def test_timing_rejects_unknown_method_and_skips_unmeasured_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "t.csv")
            open(p, "w", encoding="utf-8-sig").write("서류,방식,측정자,날짜,초,고친 칸 수,메모\n명세서_01,수기,나,,,,\n명세서_01,AI보조,나,,30,,\n")
            rows = M.read_timing(p)
            self.assertEqual(len(rows), 1)
            open(p, "w", encoding="utf-8-sig").write("서류,방식,측정자,날짜,초,고친 칸 수,메모\n명세서_01,직접,나,,30,,\n")
            with self.assertRaises(ValueError):
                M.read_timing(p)


if __name__ == "__main__":
    unittest.main()

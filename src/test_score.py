"""채점기 자체 검증. 정답 파일에서 '완벽한 예측'을 만들고, 일부러 망가뜨려 채점기가 기대대로 세는지 본다.
실행: python -X utf8 -m unittest src.test_score -v   (프로젝트 폴더에서)
"""
import copy
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import score as S

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
with open(os.path.join(ROOT, "sample-invoices", "정답.json"), encoding="utf-8") as f:
    GT = json.load(f)

BOX = [10, 10, 100, 40]


def perfect():
    preds = []
    for g in GT:
        preds.append({
            "file": g["file"], "seconds": 10.0, "total_mismatch": g["total_mismatch"],
            "fields": {
                "supplier": {"value": g["supplier"], "review": False, "box": BOX},
                "date": {"value": g["date"], "review": False, "box": BOX},
                "doc_total": {"value": g["doc_total"], "review": False, "box": BOX},
            },
            "items": [{"item": i["item"], "qty": i["qty"], "unit_price": i["unit_price"],
                       "amount": i["amount"], "review": False, "box": BOX} for i in g["items"]],
        })
    return preds


def run(preds):
    return S.score(GT, preds)[0]


class ScoreTests(unittest.TestCase):
    def test_gt_shape(self):
        self.assertEqual(len(GT), 20)
        self.assertEqual(sum(len(g["items"]) for g in GT), 102)
        self.assertEqual([g["file"] for g in GT if g["total_mismatch"]], ["명세서_20.pdf"])

    def test_perfect_passes_everything(self):
        s = run(perfect())
        self.assertTrue(s["전체_통과(1~6)"], s["criteria"])
        self.assertEqual(s["참고"]["품목_오류_전체"], 0)
        self.assertEqual(s["참고"]["필드_오류_전체"], 0)

    def test_wrong_qty_is_silent_error(self):
        p = perfect()
        p[0]["items"][0]["qty"] += 1
        s = run(p)
        self.assertEqual(s["criteria"]["1_품목_조용한오류"]["값"], 1)

    def test_flagged_error_is_caught_not_silent(self):
        p = perfect()
        p[0]["items"][0]["qty"] += 1
        p[0]["items"][0]["review"] = True
        s = run(p)
        self.assertEqual(s["criteria"]["1_품목_조용한오류"]["값"], 0)
        self.assertEqual(s["참고"]["품목_확인필요로_잡음"], 1)

    def test_false_alarm_counts_toward_review_ratio(self):
        p = perfect()
        p[0]["items"][0]["review"] = True
        s = run(p)
        self.assertEqual(s["참고"]["품목_불필요한_확인필요"], 1)
        self.assertEqual(s["참고"]["확인필요_슬롯"], 1)

    def test_flag_everything_fails_review_ratio(self):
        p = perfect()
        for d in p:
            for i in d["items"]:
                i["review"] = True
            for f in d["fields"].values():
                f["review"] = True
        s = run(p)
        self.assertFalse(s["criteria"]["3_확인필요_비율"]["통과"])
        self.assertEqual(s["criteria"]["3_확인필요_비율"]["값"], 1.0)

    def test_dropped_row_is_silent_missing(self):
        p = perfect()
        p[1]["items"].pop(0)
        s = run(p)
        self.assertEqual(s["criteria"]["1_품목_조용한오류"]["값"], 1)

    def test_dropped_row_does_not_shift_the_rest(self):
        p = perfect()
        p[2]["items"].pop(0)
        s = run(p)
        self.assertEqual(s["참고"]["품목_오류_전체"], 1)

    def test_extra_row_is_error(self):
        p = perfect()
        p[1]["items"].append({"item": "없는품목", "qty": 1, "unit_price": 100, "amount": 100,
                              "review": False, "box": BOX})
        s = run(p)
        self.assertEqual(s["criteria"]["1_품목_조용한오류"]["값"], 1)

    def test_row_order_does_not_matter(self):
        p = perfect()
        for d in p:
            d["items"].reverse()
        self.assertEqual(run(p)["참고"]["품목_오류_전체"], 0)

    def test_whitespace_in_name_is_ignored(self):
        p = perfect()
        p[0]["items"][0]["item"] = " ".join(p[0]["items"][0]["item"])
        self.assertEqual(run(p)["참고"]["품목_오류_전체"], 0)

    def test_changed_name_char_is_error(self):
        p = perfect()
        nm = p[0]["items"][0]["item"]
        p[0]["items"][0]["item"] = nm[:-1] + ("가" if nm[-1] != "가" else "나")
        self.assertEqual(run(p)["참고"]["품목_오류_전체"], 1)

    def test_numbers_with_commas_and_won(self):
        p = perfect()
        p[0]["items"][0]["amount"] = f"₩{p[0]['items'][0]['amount']:,}원"
        self.assertEqual(run(p)["참고"]["품목_오류_전체"], 0)

    def test_date_formats(self):
        for raw, iso in [("2026.10.04", "2026-10-04"), ("2026년 10월 4일", "2026-10-04"),
                         ("26/10/04", "2026-10-04"), ("2026-10-04", "2026-10-04")]:
            self.assertEqual(S.to_date(raw), iso, raw)
        self.assertIsNone(S.to_date("10월 4일"))

    def test_wrong_date_is_silent_field_error(self):
        p = perfect()
        p[0]["fields"]["date"]["value"] = "2026-01-01"
        s = run(p)
        self.assertEqual(s["criteria"]["2_필드_조용한오류"]["값"], 1)
        self.assertTrue(s["criteria"]["2_필드_조용한오류"]["통과"])  # 1개까지는 허용
        p[1]["fields"]["doc_total"]["value"] += 10
        self.assertFalse(run(p)["criteria"]["2_필드_조용한오류"]["통과"])

    def test_item_limit_boundary(self):
        p = perfect()
        for k in range(3):
            p[k]["items"][0]["qty"] += 1
        self.assertTrue(run(p)["criteria"]["1_품목_조용한오류"]["통과"])
        p[3]["items"][0]["qty"] += 1
        self.assertFalse(run(p)["criteria"]["1_품목_조용한오류"]["통과"])

    def test_mismatch_flag_wrong_on_doc20(self):
        p = perfect()
        p[19]["total_mismatch"] = False
        s = run(p)
        self.assertFalse(s["criteria"]["4_합계불일치_표시"]["통과"])
        self.assertEqual(s["criteria"]["4_합계불일치_표시"]["값"], "19/20장 일치")

    def test_mismatch_false_alarm_on_normal_doc(self):
        p = perfect()
        p[0]["total_mismatch"] = True
        self.assertFalse(run(p)["criteria"]["4_합계불일치_표시"]["통과"])

    def test_missing_box_fails_location(self):
        p = perfect()
        p[0]["items"][0]["box"] = None
        s = run(p)
        self.assertFalse(s["criteria"]["5_위치_추적"]["통과"])

    def test_bad_boxes_are_invalid(self):
        for b in (None, [1, 2, 3], [10, 10, 5, 40], [-1, 0, 5, 5], ["a", 1, 2, 3], [0, 0, 0, 0]):
            self.assertFalse(S.valid_box(b), b)
        self.assertTrue(S.valid_box([0, 0, 1, 1]))

    def test_missing_document_counts_all_slots(self):
        p = perfect()
        gone = p.pop(5)
        s, per_doc, _ = S.score(GT, p)
        n = len(next(g for g in GT if g["file"] == gone["file"])["items"])
        self.assertEqual(s["criteria"]["1_품목_조용한오류"]["값"], n)
        self.assertEqual(s["criteria"]["2_필드_조용한오류"]["값"], 3)
        self.assertTrue(any("예측이 없는 문서" in w for w in s["경고"]))

    def test_time_criterion(self):
        p = perfect()
        for d in p:
            d["seconds"] = 61.0
        self.assertFalse(run(p)["criteria"]["6_처리시간"]["통과"])
        p = perfect()
        del p[0]["seconds"]
        self.assertFalse(run(p)["criteria"]["6_처리시간"]["통과"])

    def test_doc17_pre_correction_values_are_errors(self):
        """17번에서 손글씨 정정 전 값(수량 2, 금액 28,000)으로 읽으면 오류여야 한다."""
        g = next(x for x in GT if x["file"] == "명세서_17.pdf")
        self.assertEqual(g["items"][2]["qty"], 5)
        self.assertEqual(g["items"][2]["amount"], 70000)
        p = perfect()
        idx = [x["file"] for x in GT].index("명세서_17.pdf")
        p[idx]["items"][2]["qty"] = 2
        p[idx]["items"][2]["amount"] = 28000
        p[idx]["fields"]["doc_total"]["value"] = 219800
        s = run(p)
        self.assertEqual(s["criteria"]["1_품목_조용한오류"]["값"], 1)
        self.assertEqual(s["criteria"]["2_필드_조용한오류"]["값"], 1)

    def test_file_extension_does_not_matter(self):
        """스캔(.pdf)과 폰 사진(.jpg)이 같은 정답을 쓴다: 확장자를 떼고 이름으로 짝짓는다."""
        p = perfect()
        for d in p:
            d["file"] = d["file"].replace(".pdf", ".jpg")
        s = run(p)
        self.assertTrue(s["전체_통과(1~6)"], s["criteria"])
        self.assertEqual(s["경고"], [])
        self.assertEqual(S.file_key("a/b/명세서_01.JPG"), "명세서_01")

    def test_gt_is_not_modified(self):
        before = copy.deepcopy(GT)
        run(perfect())
        self.assertEqual(before, GT)


if __name__ == "__main__":
    unittest.main()

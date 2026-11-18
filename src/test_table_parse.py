"""표 파서 검증(표 제목 인식 개선). 글자 조각을 합성해 정상 제목, 한 글자 오독, 붙어서 읽힘, 제목 없음을 확인한다.
실행: python -X utf8 -m unittest src.test_table_parse -v   (프로젝트 폴더에서)
"""
import copy
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import table_parse as T

P = T.Params()
W, H = 1240, 1754


def tok(text, cx, cy, w=80, h=36, score=0.99):
    return {"text": text, "score": score, "box": [int(cx - w / 2), int(cy - h / 2), int(cx + w / 2), int(cy + h / 2)]}


ROWS = [("닭모래집", "2", "kg", "6,800", "13,600"), ("닭목살", "3", "kg", "9,500", "28,500"),
        ("닭날개", "10", "kg", "6,900", "69,000")]


def top():
    return [tok("거래명세표", 620, 95, 300, 60), tok("작성일자:2026-09-18", 215, 190, 280),
            tok("공급자 상호:한울닭유통", 770, 250, 270), tok("공급받는자", 150, 250)]


def template_a(rows=ROWS, header=("번호", "품목", "수량", "단위", "단가", "금액"), total="₩ 111,100"):
    toks = top()
    y0 = 448
    for h, x in zip(header, (110, 370, 640, 730, 860, 1060)):
        if h:
            toks.append(tok(h, x, y0, 70))
    y = y0 + 54
    for i, (name, qty, unit, price, amount) in enumerate(rows, 1):
        toks += [tok(str(i), 112, y, 30), tok(name, 215, y, 120), tok(qty, 667, y, 50), tok(unit, 728, y, 40),
                 tok(price, 910, y, 80), tok(amount, 1100, y, 90)]
        y += 54
    toks += [tok("합계금액", 190, y + 20, 160), tok(total, 1050, y + 20, 180)]
    return toks


def template_b(rows=ROWS, total="111,100"):
    """납품서: 번호 칸이 없고 수량에 단위가 붙는다(5kg)."""
    toks = [tok("한울닭유통", 620, 90, 300, 60), tok("납 품 서", 620, 150, 200), tok("일자:2026.09.17", 215, 215, 260)]
    y = 340
    for name, qty, unit, price, amount in rows:
        toks += [tok(name, 200, y, 120), tok(qty + unit, 760, y, 90), tok(price, 940, y, 80), tok(amount, 1090, y, 90)]
        y += 56
    toks += [tok("합 계", 150, y + 20, 100), tok(total, 1090, y + 20, 120)]
    return toks


def template_c(with_header=True):
    """세액이 따로 있는 양식: 번호, 품목, 규격(EA), 수량, 단가, 공급가액, 세액(또는 면세)."""
    rows = [("식용유", "3", "42,000", "126,000", "12,600"), ("계란", "8", "6,500", "52,000", "면세"),
            ("소바면", "20", "1,300", "26,000", "2,600")]
    toks = top()
    y0 = 400
    if with_header:
        for h, x in zip(("No", "품목", "규격", "수량", "단가", "공급가액", "세액"), (100, 320, 600, 740, 840, 980, 1100)):
            toks.append(tok(h, x, y0, 80))
    y = y0 + 52
    for i, (name, qty, price, amt, tax) in enumerate(rows, 1):
        toks += [tok(str(i), 98, y, 30), tok(name, 230, y, 120), tok("EA", 605, y, 40), tok(qty, 755, y, 40),
                 tok(price, 870, y, 80), tok(amt, 1030, y, 90), tok(tax, 1130, y, 80)]
        y += 52
    toks += [tok("합계금액", 190, y + 70, 160), tok("₩ 219,200", 1050, y + 70, 180)]  # 126,000+52,000+26,000 + 세액 12,600+0+2,600
    return toks


def run(toks):
    return T.parse(copy.deepcopy(toks), W, H, P)


def values(items):
    return [(i["item"], i["qty"], i["unit_price"], i["amount"]) for i in items]


EXPECT = [("닭모래집", 2, 6800, 13600), ("닭목살", 3, 9500, 28500), ("닭날개", 10, 6900, 69000)]


class FuzzyRoleTests(unittest.TestCase):
    def test_exact_words(self):
        self.assertEqual(T._fuzzy_role("수량"), ("qty", False))
        self.assertEqual(T._fuzzy_role("단 가"), ("price", False))
        self.assertEqual(T._fuzzy_role("단위"), ("unit", False))
        self.assertEqual(T._fuzzy_role("공급가액"), ("amount", False))

    def test_one_character_misread(self):
        for text, role in (("단기", "price"), ("수황", "qty"), ("수 항", "qty"), ("수양", "qty"), ("금4", "amount"),
                           ("세엑", "tax")):
            self.assertEqual(T._fuzzy_role(text), (role, True), text)

    def test_ambiguous_or_unrelated_words_are_rejected(self):
        for text in ("단»", "계란", "대파", "닭목살", "합계", "인수자", "", "수"):
            self.assertEqual(T._fuzzy_role(text), (None, False), text)

    def test_shape_closer_word_wins_over_other_one_char_candidate(self):
        # '단기'는 단가·단위 모두 한 글자 차이지만 기↔가가 기↔위보다 모양이 가깝다
        self.assertEqual(T._fuzzy_role("단기")[0], "price")

    def test_merged_header_is_split_by_role(self):
        t = tok("수양단위", 685, 448, 140)
        t["cx"], t["cy"] = 685, 448
        parts = T._split_header_token(t)
        self.assertEqual([r for r, _ in parts], ["qty", "unit"])
        self.assertLess(parts[0][1]["cx"], parts[1][1]["cx"])
        self.assertAlmostEqual(parts[0][1]["cx"], 650, delta=2)
        self.assertEqual(T._split_header_token({**t, "text": "닭목살닭날개"}), [])


class HeaderRecoveryTests(unittest.TestCase):
    def test_baseline_exact_header(self):
        fields, items, flags, notes = run(template_a())
        self.assertEqual(values(items), EXPECT)
        self.assertFalse(any("비슷한" in n or "추정" in n for n in notes))
        self.assertEqual(fields["doc_total"]["value"], 111100)
        self.assertFalse(flags["total_mismatch"])

    def test_one_character_misread_in_header_is_accepted(self):
        _, items, _, notes = run(template_a(header=("번호", "품목", "수황", "단위", "단기", "금액")))
        self.assertEqual(values(items), EXPECT)
        self.assertTrue(any("비슷한" in n for n in notes), notes)

    def test_merged_header_words(self):
        toks = [t for t in template_a(header=("번호", "품목", "", "", "단가", "금액"))]
        toks.append(tok("수양단위", 685, 448, 140))
        _, items, _, notes = run(toks)
        self.assertEqual(values(items), EXPECT)

    def test_missing_header_template_a_uses_number_columns(self):
        fields, items, flags, notes = run(template_a(header=("", "", "", "", "", "")))
        self.assertEqual(values(items), EXPECT)
        self.assertTrue(any("추정" in n for n in notes), notes)
        self.assertEqual(fields["doc_total"]["value"], 111100)

    def test_unreadable_header_fragments_do_not_stop_it(self):
        _, items, _, _ = run(template_a(header=("", "", "가", "", "", "금4")))
        self.assertEqual(values(items), EXPECT)

    def test_missing_header_template_b_without_row_numbers(self):
        fields, items, _, notes = run(template_b())
        self.assertEqual(values(items), EXPECT)
        self.assertEqual(fields["doc_total"]["value"], 111100)

    def test_missing_header_template_c_keeps_tax_and_drops_spec(self):
        fields, items, flags, _ = run(template_c(with_header=False))
        self.assertEqual(values(items), [("식용유", 3, 42000, 126000), ("계란", 8, 6500, 52000), ("소바면", 20, 1300, 26000)])
        self.assertEqual(fields["doc_total"]["value"], 219200)
        self.assertFalse(flags["total_mismatch"])

    def test_template_c_with_header_still_works(self):
        _, items, _, notes = run(template_c(with_header=True))
        self.assertEqual(len(items), 3)
        self.assertFalse(any("추정" in n for n in notes))

    def test_inferred_columns_must_pass_the_arithmetic_check(self):
        """수량×단가=금액이 맞는 줄이 절반이 안 되면 열 추정을 믿지 않고 실패로 둔다."""
        bad = [(n, q, u, p, "99") for n, q, u, p, _ in ROWS]
        fields, items, _, notes = run(template_a(rows=bad, header=("", "", "", "", "", "")))
        self.assertEqual(items, [])
        self.assertTrue(any("못 찾음" in n for n in notes))
        self.assertTrue(all(f["review"] for f in fields.values()))

    def test_one_wrong_row_under_inference_is_flagged_for_review(self):
        rows = list(ROWS)
        rows[1] = ("닭목살", "3", "kg", "9,500", "28,000")  # 수량×단가와 금액이 다름
        _, items, _, _ = run(template_a(rows=rows, header=("", "", "", "", "", "")))
        self.assertEqual(len(items), 3)
        self.assertEqual([i["review"] for i in items], [False, True, False])

    def test_no_numbers_at_all_still_fails_loudly(self):
        fields, items, _, notes = run(top())
        self.assertEqual(items, [])
        self.assertTrue(all(f["review"] for f in fields.values()))

    def test_text_lines_are_never_taken_as_rows(self):
        toks = template_a(header=("", "", "", "", "", "")) + [tok("등록번호:726-86-72174", 770, 330, 260),
                                                               tok("TEL 02-414-4516", 1070, 190, 200)]
        _, items, _, _ = run(toks)
        self.assertEqual(len(items), 3)


if __name__ == "__main__":
    unittest.main()

"""측정 앱 검증: 저장 로직(Store)과 HTTP 요청. 임시 폴더만 쓰고 measurement/ 는 건드리지 않는다.
실행: python -X utf8 -m unittest src.test_measure_app -v   (프로젝트 폴더에서)
"""
import csv
import datetime
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(__file__))
import measure as M
import measure_app as A

ROOT = A.ROOT
with open(os.path.join(ROOT, "sample-invoices", "정답.json"), encoding="utf-8") as f:
    GT = {g["file"]: g for g in json.load(f)}
T0 = datetime.datetime(2026, 11, 20, 10, 0, 0)


def gt_rows(doc):
    g = GT[doc + ".pdf"]
    return g, [{"src": None, "item": it["item"], "qty": str(it["qty"]), "unit_price": str(it["unit_price"]),
                "amount": str(it["amount"])} for it in g["items"]]


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.s = A.Store(self.tmp.name)
        self.s.setup("테스터", "0.7")

    def tearDown(self):
        self.tmp.cleanup()

    def manual(self, doc, sec=100, now=T0, rows=None):
        g, r = gt_rows(doc)
        return self.s.complete(doc, M.MANUAL, sec, {"supplier": g["supplier"], "date": g["date"]}, rows or r, now)

    def assisted_payload(self, doc, edit=None):
        d = self.s.draft(doc)
        rows = [{k: r[k] for k in ("src", "item", "qty", "unit_price", "amount")} for r in d["rows"]]
        if edit:
            edit(rows, d)
        return {"supplier": d["fields"]["supplier"]["value"], "date": d["fields"]["date"]["value"]}, rows

    # ---- 구성 ----
    def test_task_plan_is_counterbalanced(self):
        ts = self.s.task_list()
        self.assertEqual(len(ts), 16)
        by = {}
        for t in ts:
            by.setdefault(t["doc"], {})[t["method"]] = t["day"]
        self.assertEqual(len(by), 8)
        for doc, m in by.items():  # 모든 서류가 두 방식으로, 서로 다른 날에
            self.assertEqual(set(m), {M.MANUAL, M.ASSISTED})
            self.assertNotEqual(m[M.MANUAL], m[M.ASSISTED])
        self.assertEqual({d for d in by} , set(M.DEFAULT_DOCS))
        self.assertEqual({t["day"] for t in ts if t["method"] == M.MANUAL and t["group"] == "X"}, {1})
        self.assertEqual({t["day"] for t in ts if t["method"] == M.ASSISTED and t["group"] == "X"}, {2})

    def test_groups_are_balanced_and_each_has_one_hard_doc(self):
        rows = lambda g: sum(len(GT[d + ".pdf"]["items"]) for d in g)
        self.assertLessEqual(abs(rows(A.SET_X) - rows(A.SET_Y)), 2)
        for g in (A.SET_X, A.SET_Y):
            self.assertEqual(sum(GT[d + ".pdf"]["difficulty"] == "어려움" for d in g), 1)

    def test_practice_doc_is_not_one_of_the_measured_docs(self):
        self.assertNotIn(A.PRACTICE, M.DEFAULT_DOCS)
        self.assertTrue(self.s.known_doc(A.PRACTICE))

    # ---- 설정과 잠금 ----
    def test_setup_validation(self):
        s = A.Store(tempfile.mkdtemp())
        for bad in ("", "abc", "0", "-1", "2"):
            with self.assertRaises(ValueError):
                s.setup("나", bad)
        with self.assertRaises(ValueError):
            s.setup("  ", "0.7")

    def test_target_is_locked_after_first_measurement(self):
        self.s.setup("테스터", "0.5")  # 잠기기 전에는 바꿀 수 있다
        self.assertEqual(self.s.load_state()["target_ratio"], 0.5)
        self.manual("명세서_01")
        with self.assertRaises(ValueError):
            self.s.setup("테스터", "0.9")
        self.assertEqual(self.s.load_state()["target_ratio"], 0.5)

    def test_cannot_complete_before_setup(self):
        s = A.Store(tempfile.mkdtemp())
        with self.assertRaises(ValueError):
            s.complete("명세서_01", M.MANUAL, 10, {}, [{"item": "a"}])

    # ---- 수기 저장 ----
    def test_manual_completion_writes_files_the_cli_can_read(self):
        self.manual("명세서_01", 123.4)
        timing = M.read_timing(self.s.p_timing)
        self.assertEqual([(t["doc"], t["method"], t["sec"]) for t in timing], [("명세서_01.pdf", M.MANUAL, 123.4)])
        rows = M.read_entries(self.s.p_manual)
        self.assertEqual(len(rows), 4)
        s, _ = M.score_entries(list(GT.values()), rows, ["명세서_01.pdf"])
        self.assertEqual(s["wrong"], 0)

    def test_manual_numbers_are_normalized_and_wrong_values_are_kept(self):
        g, r = gt_rows("명세서_01")
        r[0]["unit_price"] = "1,234 원"
        r[1]["qty"] = "열"
        self.s.complete("명세서_01", M.MANUAL, 10, {"supplier": g["supplier"], "date": g["date"]}, r, T0)
        rows = M.read_entries(self.s.p_manual)
        self.assertEqual(rows[0]["단가"], "1234")
        self.assertEqual(rows[1]["수량"], "열")

    def test_empty_manual_rows_are_ignored_and_no_rows_is_rejected(self):
        g, r = gt_rows("명세서_01")
        blank = {"src": None, "item": "", "qty": "", "unit_price": "", "amount": ""}
        self.manual("명세서_01", rows=r + [blank, blank])
        self.assertEqual(len(M.read_entries(self.s.p_manual)), 4)
        with self.assertRaises(ValueError):
            self.s.complete("명세서_02", M.MANUAL, 10, {}, [blank], T0)

    def test_redo_replaces_rows_and_is_noted(self):
        self.manual("명세서_01", 100)
        self.manual("명세서_01", 90, now=T0 + datetime.timedelta(minutes=5))
        self.assertEqual(len(M.read_entries(self.s.p_manual)), 4)
        with open(self.s.p_timing, encoding="utf-8-sig") as f:
            row = [r for r in csv.DictReader(f) if r["서류"] == "명세서_01" and r["방식"] == M.MANUAL][0]
        self.assertEqual(row["초"], "90.0")
        self.assertIn("다시 측정함", row["메모"])

    def test_rejects_bad_seconds_and_unknown_tasks(self):
        g, r = gt_rows("명세서_01")
        for bad in (0, -5, 99999, "abc", None):
            with self.assertRaises(ValueError):
                self.s.complete("명세서_01", M.MANUAL, bad, {}, r, T0)
        with self.assertRaises(ValueError):
            self.s.complete("명세서_03", M.MANUAL, 10, {}, r, T0)

    # ---- AI 보조 ----
    def test_assisted_no_edits_keeps_flags_and_counts_zero_fixes(self):
        f, rows = self.assisted_payload("명세서_01")
        res = self.s.complete("명세서_01", M.ASSISTED, 30, f, rows, T0)
        self.assertEqual(res["fixed"], 0)
        saved = M.read_entries(self.s.p_assisted)
        d = self.s.draft("명세서_01")
        self.assertEqual([bool(str(r["확인 필요"]).strip()) for r in saved], [r["flagged"] for r in d["rows"]])

    def test_assisted_fix_on_flagged_row_counts_cells(self):
        d = self.s.draft("명세서_11")
        idx = next(r["src"] for r in d["rows"] if r["flagged"])

        def edit(rows, _):
            rows[idx]["qty"] = "999"
            rows[idx]["item"] = rows[idx]["item"] + "수정"
        f, rows = self.assisted_payload("명세서_11", edit)
        self.assertEqual(self.s.complete("명세서_11", M.ASSISTED, 30, f, rows, T0)["fixed"], 2)

    def test_assisted_rejects_editing_a_locked_row(self):
        d = self.s.draft("명세서_14")
        idx = next(r["src"] for r in d["rows"] if not r["flagged"])
        f, rows = self.assisted_payload("명세서_14", lambda rs, _: rs[idx].update(qty="777"))
        with self.assertRaises(ValueError):
            self.s.complete("명세서_14", M.ASSISTED, 30, f, rows, T0)
        self.assertFalse(os.path.exists(self.s.p_assisted))

    def test_assisted_rejects_deleting_locked_row_and_adding_rows(self):
        d = self.s.draft("명세서_14")
        idx = next(r["src"] for r in d["rows"] if not r["flagged"])
        f, rows = self.assisted_payload("명세서_14", lambda rs, _: rs.pop(idx))
        with self.assertRaises(ValueError):
            self.s.complete("명세서_14", M.ASSISTED, 30, f, rows, T0)
        f, rows = self.assisted_payload("명세서_14", lambda rs, _: rs.append(
            {"src": None, "item": "새 줄", "qty": "1", "unit_price": "1", "amount": "1"}))
        with self.assertRaises(ValueError):
            self.s.complete("명세서_14", M.ASSISTED, 30, f, rows, T0)

    def test_assisted_can_delete_a_flagged_row_counts_four(self):
        d = self.s.draft("명세서_11")
        idx = next(r["src"] for r in d["rows"] if r["flagged"])
        keep = [r for r in d["rows"] if r["src"] != idx]
        rows = [{k: r[k] for k in ("src", "item", "qty", "unit_price", "amount")} for r in keep]
        f = {"supplier": d["fields"]["supplier"]["value"], "date": d["fields"]["date"]["value"]}
        self.assertEqual(self.s.complete("명세서_11", M.ASSISTED, 30, f, rows, T0)["fixed"], 4)
        self.assertEqual(len(M.read_entries(self.s.p_assisted)), len(d["rows"]) - 1)

    def test_assisted_rejects_editing_unflagged_field(self):
        d = self.s.draft("명세서_14")
        f, rows = self.assisted_payload("명세서_14")
        if not d["fields"]["supplier"]["flagged"]:
            f["supplier"] = "엉뚱상사"
            with self.assertRaises(ValueError):
                self.s.complete("명세서_14", M.ASSISTED, 30, f, rows, T0)

    def test_assisted_flagged_supplier_can_be_fixed(self):
        d = self.s.draft("명세서_18")  # 18번은 거래처가 확인 필요
        self.assertTrue(d["fields"]["supplier"]["flagged"])
        f, rows = self.assisted_payload("명세서_18")
        f["supplier"] = GT["명세서_18.pdf"]["supplier"]
        res = self.s.complete("명세서_18", M.ASSISTED, 30, f, rows, T0)
        self.assertEqual(res["fixed"], 1)

    def test_manual_task_data_has_no_draft_and_assisted_does(self):
        self.assertIsNone(self.s.task_data("명세서_01", M.MANUAL)["draft"])
        self.assertTrue(self.s.task_data("명세서_01", M.ASSISTED)["draft"]["rows"])
        with self.assertRaises(ValueError):
            self.s.task_data("명세서_99", M.MANUAL)

    # ---- 간격 ----
    def test_gap_between_methods_is_warned_and_recorded(self):
        self.manual("명세서_01", now=T0)
        later = T0 + datetime.timedelta(hours=2)
        st = self.s.state(now=later)
        t = next(x for x in st["tasks"] if x["doc"] == "명세서_01" and x["method"] == M.ASSISTED)
        self.assertTrue(t["warn"])
        f, rows = self.assisted_payload("명세서_01")
        self.s.complete("명세서_01", M.ASSISTED, 20, f, rows, later)
        with open(self.s.p_timing, encoding="utf-8-sig") as fh:
            row = [r for r in csv.DictReader(fh) if r["서류"] == "명세서_01" and r["방식"] == M.ASSISTED][0]
        self.assertIn("간격 2.0시간", row["메모"])
        t2 = next(x for x in self.s.state(now=T0 + datetime.timedelta(hours=30))["tasks"]
                  if x["doc"] == "명세서_01" and x["method"] == M.ASSISTED)
        self.assertFalse(t2["warn"])

    # ---- 결과 ----
    def test_report_needs_both_methods(self):
        with self.assertRaises(ValueError):
            self.s.report()
        self.manual("명세서_01")
        with self.assertRaises(ValueError):
            self.s.report()

    def test_full_run_produces_a_report_and_never_overwrites(self):
        for doc in M.DEFAULT_DOCS:
            self.manual(doc, 200, now=T0)
            f, rows = self.assisted_payload(doc)
            self.s.complete(doc, M.ASSISTED, 60, f, rows, T0 + datetime.timedelta(days=1))
        self.assertEqual(self.s.state()["done"], 16)
        text, path = self.s.report(now=T0)
        self.assertIn("비교한 서류: 8장", text)
        self.assertIn("재기 전에 정한 목표: 0.70", text)
        self.assertTrue(os.path.exists(path))
        with self.assertRaises(FileExistsError):
            self.s.report(now=T0)  # 같은 초에는 덮어쓰지 않는다


class HttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.srv = A.make_server(cls.tmp.name, 0)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.port}"

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        cls.tmp.cleanup()

    def req(self, path, body=None, headers=None, method=None):
        h = dict(headers or {})
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            h.setdefault("Content-Type", "application/json")
        r = urllib.request.Request(self.base + path, data=data, headers=h, method=method)
        try:
            with urllib.request.urlopen(r) as resp:
                return resp.status, resp.read(), resp.headers
        except urllib.error.HTTPError as e:
            return e.code, e.read(), e.headers

    def test_binds_to_loopback_only(self):
        self.assertEqual(self.srv.server_address[0], "127.0.0.1")

    def test_index_state_and_png(self):
        code, body, _ = self.req("/")
        self.assertEqual(code, 200)
        self.assertIn("수기 입력 vs AI 보조 측정".encode("utf-8"), body)
        code, body, _ = self.req("/api/state")
        self.assertEqual((code, json.loads(body)["total"]), (200, 16))
        code, body, h = self.req("/api/doc/" + urllib.request.quote("명세서_01") + ".png")
        self.assertEqual((code, body[:8], h["Content-Type"]), (200, b"\x89PNG\r\n\x1a\n", "image/png"))

    def test_unknown_doc_and_unknown_path(self):
        code, _, _ = self.req("/api/doc/" + urllib.request.quote("명세서_99") + ".png")
        self.assertEqual(code, 404)
        self.assertEqual(self.req("/nope")[0], 404)
        self.assertEqual(self.req("/api/doc/..%2f..%2fsecret.png")[0], 404)

    def test_wrong_host_is_rejected(self):
        code, _, _ = self.req("/api/state", headers={"Host": "evil.example.com"})
        self.assertEqual(code, 403)

    def test_post_requires_json_and_same_origin(self):
        self.assertEqual(self.req("/api/setup", body={"person": "a", "target_ratio": "0.7"},
                                  headers={"Content-Type": "text/plain"})[0], 415)
        code, _, _ = self.req("/api/setup", body={"person": "a", "target_ratio": "0.7"},
                              headers={"Origin": "http://evil.example.com"})
        self.assertEqual(code, 403)

    def test_setup_error_is_reported_in_korean(self):
        code, body, _ = self.req("/api/setup", body={"person": "a", "target_ratio": "abc"})
        self.assertEqual(code, 400)
        self.assertIn("숫자", json.loads(body)["error"])


if __name__ == "__main__":
    unittest.main()

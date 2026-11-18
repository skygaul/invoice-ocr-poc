"""수기 입력 vs AI 보조 검토를 브라우저에서 클릭으로 재는 로컬 앱 (문제 정의서 기준 7).

실행: python -X utf8 -W ignore src/measure_app.py
- 이 컴퓨터(127.0.0.1)에서만 열리고 새로 설치할 패키지가 없다. 자료는 밖으로 나가지 않는다.
- 결과는 measurement/ 폴더에 쌓인다: timing_log.csv, manual_entries.csv, assisted_final.csv, state.json, result_*.md
  (src/measure.py report 와 같은 형식이라 명령줄로도 다시 계산할 수 있다.)

재는 방식(docs/07_수기입력_측정절차.md): 서류 8장을 X조·Y조로 나눠
  1일차 X조 수기 + Y조 AI 보조, 2일차 X조 AI 보조 + Y조 수기.
모든 서류를 두 방식으로 재면서도 같은 서류의 두 측정 사이에 간격이 생기고 순서 효과가 줄어든다.
- AI 보조: 노란색(확인 필요) 행과 칸만 고칠 수 있고 나머지는 잠긴다(서버도 검사한다).
- 목표 시간 비율은 측정 전에 정해 저장하고, 첫 측정을 마친 뒤에는 바꿀 수 없다.
"""
import argparse
import csv
import datetime
import io
import json
import os
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common
import measure as M
import score as S

ROOT = M.ROOT
SET_X = ["명세서_01", "명세서_02", "명세서_05", "명세서_19"]
SET_Y = ["명세서_08", "명세서_11", "명세서_14", "명세서_18"]
PRACTICE = "명세서_04"
PLAN = [
    (1, [("X", M.MANUAL), ("Y", M.ASSISTED)]),
    (2, [("X", M.ASSISTED), ("Y", M.MANUAL)]),
]
GROUPS = {"X": SET_X, "Y": SET_Y}
MAX_SECONDS = 4 * 3600
WARN_HOURS = 12  # 같은 서류의 두 측정이 이보다 가까우면 화면에서 알린다


def _num_str(v):
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return str(v)


def _same(a, b):
    """칸 값이 같은가: 둘 다 숫자로 읽히면 숫자로, 아니면 공백 없이 글자로 비교."""
    na, nb = S.to_num(a), S.to_num(b)
    if na is not None and nb is not None and str(a).strip() and str(b).strip():
        return abs(na - nb) < 1e-6
    return S.norm_text(a or "") == S.norm_text(b or "")


class Store:
    """측정 상태와 결과 파일을 다룬다. 화면과 무관한 로직이라 단위 테스트가 쉽다."""

    def __init__(self, data_dir, project_root=ROOT):
        self.dir = data_dir
        os.makedirs(self.dir, exist_ok=True)
        self.project_root = project_root
        self.pdf_dir = os.path.join(project_root, "sample-invoices")
        with open(os.path.join(project_root, "expected", "A_test_predictions.json"), encoding="utf-8") as f:
            self._drafts = {d["file"]: d for d in json.load(f)}
        self._png = {}
        self._lock = threading.Lock()
        self.p_state = os.path.join(self.dir, "state.json")
        self.p_timing = os.path.join(self.dir, "timing_log.csv")
        self.p_manual = os.path.join(self.dir, "manual_entries.csv")
        self.p_assisted = os.path.join(self.dir, "assisted_final.csv")

    # ---- 상태 ----
    def load_state(self):
        if os.path.exists(self.p_state):
            with open(self.p_state, encoding="utf-8") as f:
                return json.load(f)
        return {"person": "", "target_ratio": None, "locked": False, "done_at": {}}

    def _save_state(self, st):
        with open(self.p_state, "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False, indent=1)

    def task_list(self):
        out = []
        for day, plan in PLAN:
            for group, method in plan:
                for doc in GROUPS[group]:
                    out.append({"id": f"{doc}|{method}", "doc": doc, "method": method, "day": day, "group": group})
        return out

    def _task(self, doc, method):
        for t in self.task_list():
            if t["doc"] == doc and t["method"] == method:
                return t
        raise ValueError(f"측정 대상이 아닌 서류입니다: {doc} / {method}")

    def _timing_rows(self):
        rows = []
        if os.path.exists(self.p_timing):
            with open(self.p_timing, encoding="utf-8-sig", newline="") as f:
                rows = list(csv.DictReader(f))
        have = {(r["서류"], r["방식"]) for r in rows}
        for t in self.task_list():  # 빠진 행은 채워 둔다
            if (t["doc"], t["method"]) not in have:
                rows.append({"서류": t["doc"], "방식": t["method"], "측정자": "", "날짜": "", "초": "",
                             "고친 칸 수": "", "메모": ""})
        return rows

    def _write_timing(self, rows):
        order = {(t["doc"], t["method"]): i for i, t in enumerate(self.task_list())}
        rows = sorted(rows, key=lambda r: order.get((r["서류"], r["방식"]), 999))
        with open(self.p_timing, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(M.TIMING_COLS)
            for r in rows:
                w.writerow([r.get(c, "") for c in M.TIMING_COLS])

    def state(self, now=None):
        now = now or datetime.datetime.now()
        st = self.load_state()
        timing = {(r["서류"], r["방식"]): r for r in self._timing_rows()}
        tasks = []
        for t in self.task_list():
            r = timing[(t["doc"], t["method"])]
            other = M.ASSISTED if t["method"] == M.MANUAL else M.MANUAL
            od = st["done_at"].get(f"{t['doc']}|{other}")
            hours = None
            if od:
                hours = abs((now - datetime.datetime.fromisoformat(od)).total_seconds()) / 3600
            tasks.append({**t, "done": bool((r.get("초") or "").strip()), "seconds": r.get("초") or "",
                          "other_done_hours": hours, "warn": hours is not None and hours < WARN_HOURS})
        return {"person": st["person"], "target_ratio": st["target_ratio"], "locked": st["locked"],
                "tasks": tasks, "practice": PRACTICE, "warn_hours": WARN_HOURS,
                "done": sum(t["done"] for t in tasks), "total": len(tasks)}

    def setup(self, person, target_ratio):
        st = self.load_state()
        if st["locked"]:
            raise ValueError("첫 측정을 마친 뒤에는 측정자와 목표를 바꿀 수 없습니다.")
        person = str(person or "").strip()
        if not person:
            raise ValueError("측정자 이름(별명도 됩니다)을 적어 주세요.")
        try:
            target = float(target_ratio)
        except (TypeError, ValueError):
            raise ValueError("목표 비율은 숫자로 적어 주세요(예: 0.7).")
        if not (0 < target <= 1.5):
            raise ValueError("목표 비율은 0보다 크고 1.5 이하여야 합니다.")
        st["person"], st["target_ratio"] = person, target
        self._save_state(st)
        return st

    # ---- 서류 ----
    def known_doc(self, doc):
        return doc == PRACTICE or doc in SET_X or doc in SET_Y

    def doc_png(self, doc):
        if not self.known_doc(doc):
            raise KeyError(doc)
        with self._lock:
            if doc not in self._png:
                from PIL import Image
                img = Image.fromarray(common.render_pdf(os.path.join(self.pdf_dir, doc + ".pdf")))
                img = img.resize((1000, int(img.height * 1000 / img.width)))
                buf = io.BytesIO()
                img.save(buf, "PNG")
                self._png[doc] = buf.getvalue()
            return self._png[doc]

    def draft(self, doc):
        """AI가 만든 초안. 화면에 보여 줄 행·칸과 노란색(확인 필요) 여부."""
        d = self._drafts[doc + ".pdf"]
        f = d["fields"]
        rows = [{"src": i, "item": it.get("item") or "", "qty": _num_str(it.get("qty")),
                 "unit_price": _num_str(it.get("unit_price")), "amount": _num_str(it.get("amount")),
                 "flagged": bool(it.get("review"))} for i, it in enumerate(d["items"])]
        return {"fields": {"supplier": {"value": f["supplier"]["value"] or "", "flagged": bool(f["supplier"]["review"])},
                           "date": {"value": f["date"]["value"] or "", "flagged": bool(f["date"]["review"])},
                           "doc_total": {"value": _num_str(f["doc_total"]["value"]),
                                         "flagged": bool(f["doc_total"]["review"])}},
                "rows": rows}

    def task_data(self, doc, method):
        if not self.known_doc(doc):
            raise ValueError("알 수 없는 서류입니다.")
        if method == M.ASSISTED:
            return {"doc": doc, "method": method, "draft": self.draft(doc)}
        return {"doc": doc, "method": method, "draft": None}

    # ---- 완료 저장 ----
    def _clean_rows(self, rows):
        out = []
        for r in rows:
            cells = {k: ("" if r.get(k) is None else str(r.get(k)).strip()) for k in ("item", "qty", "unit_price", "amount")}
            if not any(cells.values()):
                continue
            for k in ("qty", "unit_price", "amount"):  # 숫자로 읽히면 쉼표 등을 정리해서 저장
                n = S.to_num(cells[k]) if cells[k] else None
                if n is not None:
                    cells[k] = _num_str(int(n) if float(n).is_integer() else n)
            out.append({"src": r.get("src"), **cells})
        return out

    def complete(self, doc, method, seconds, fields, rows, now=None):
        now = now or datetime.datetime.now()
        self._task(doc, method)
        try:
            seconds = float(seconds)
        except (TypeError, ValueError):
            raise ValueError("시간 값이 올바르지 않습니다.")
        if not (1 <= seconds <= MAX_SECONDS):
            raise ValueError("시간은 1초 이상 4시간 이하여야 합니다.")
        st = self.load_state()
        if not st["person"] or st["target_ratio"] is None:
            raise ValueError("먼저 측정자와 목표 비율을 저장해 주세요.")
        rows = self._clean_rows(rows or [])
        fields = {"supplier": str((fields or {}).get("supplier") or "").strip(),
                  "date": str((fields or {}).get("date") or "").strip()}
        fixed = ""
        flags = [""] * len(rows)
        if method == M.ASSISTED:
            fixed, flags = self._check_assisted(doc, fields, rows)
        else:
            if not rows:
                raise ValueError("입력한 행이 없습니다.")

        with self._lock:
            self._rewrite_entries(self.p_manual if method == M.MANUAL else self.p_assisted, doc, fields, rows, flags)
            memo = []
            other = M.ASSISTED if method == M.MANUAL else M.MANUAL
            od = st["done_at"].get(f"{doc}|{other}")
            if od:
                memo.append(f"다른 방식과 간격 {abs((now - datetime.datetime.fromisoformat(od)).total_seconds()) / 3600:.1f}시간")
            if f"{doc}|{method}" in st["done_at"]:
                memo.append("다시 측정함")
            trs = self._timing_rows()
            for r in trs:
                if r["서류"] == doc and r["방식"] == method:
                    r.update({"측정자": st["person"], "날짜": now.strftime("%Y-%m-%d"), "초": f"{seconds:.1f}",
                              "고친 칸 수": str(fixed), "메모": "; ".join(memo)})
            self._write_timing(trs)
            st["done_at"][f"{doc}|{method}"] = now.isoformat(timespec="seconds")
            st["locked"] = True
            self._save_state(st)
        return {"seconds": seconds, "fixed": fixed}

    def _check_assisted(self, doc, fields, rows):
        """AI 보조 규칙: 노란색이 아닌 행·칸은 고칠 수 없고, 행을 더하거나 뺄 수 없다(노란 행만 지울 수 있다)."""
        dr = self.draft(doc)
        by_src = {}
        for r in rows:
            src = r.get("src")
            if not isinstance(src, int) or not (0 <= src < len(dr["rows"])) or src in by_src:
                raise ValueError("AI 보조에서는 행을 새로 추가할 수 없습니다.")
            by_src[src] = r
        fixed = 0
        flags = [""] * len(rows)
        order = {id(r): i for i, r in enumerate(rows)}
        for i, orig in enumerate(dr["rows"]):
            mine = by_src.get(i)
            cells = ("item", "qty", "unit_price", "amount")
            if mine is None:
                if not orig["flagged"]:
                    raise ValueError("노란색이 아닌 행은 지울 수 없습니다.")
                fixed += 4
                continue
            diff = sum(not _same(mine[k], orig[k]) for k in cells)
            if diff and not orig["flagged"]:
                raise ValueError("노란색이 아닌 행은 고칠 수 없습니다. 화면에서 잠긴 행입니다.")
            fixed += diff
            flags[order[id(mine)]] = "확인 필요" if orig["flagged"] else ""
        for k in ("supplier", "date"):
            o = dr["fields"][k]
            if not _same(fields[k], o["value"]):
                if not o["flagged"]:
                    raise ValueError("노란색이 아닌 칸은 고칠 수 없습니다.")
                fixed += 1
        return fixed, flags

    def _rewrite_entries(self, path, doc, fields, rows, flags):
        keep = []
        if os.path.exists(path):
            with open(path, encoding="utf-8-sig", newline="") as f:
                keep = [r for r in csv.reader(f)][1:]
        keep = [r for r in keep if r and r[0] != doc + ".pdf"]
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(M.ENTRY_COLS + ["확인 필요"])
            w.writerows(keep)
            for r, fl in zip(rows, flags):
                w.writerow([doc + ".pdf", fields["supplier"], fields["date"], r["item"], r["qty"], r["unit_price"],
                            r["amount"], fl])

    # ---- 결과 ----
    def report(self, now=None):
        now = now or datetime.datetime.now()
        st = self.load_state()
        if st["target_ratio"] is None:
            raise ValueError("목표 비율이 저장되지 않았습니다.")
        if not (os.path.exists(self.p_manual) and os.path.exists(self.p_assisted)):
            raise ValueError("수기와 AI 보조를 한 서류 이상씩 마친 뒤에 계산할 수 있습니다.")
        with open(os.path.join(self.project_root, "sample-invoices", "정답.json"), encoding="utf-8") as f:
            gt = json.load(f)
        r = M.summarize(M.read_timing(self.p_timing), M.read_entries(self.p_manual), M.read_entries(self.p_assisted),
                        gt, st["target_ratio"])
        text = M.render(r)
        path = os.path.join(self.dir, f"result_{now.strftime('%Y%m%d-%H%M%S')}.md")
        with open(path, "x", encoding="utf-8") as f:
            f.write(text)
        return text, path


class Handler(BaseHTTPRequestHandler):
    store = None
    allowed_hosts = set()
    index_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "measure_app", "index.html")

    def log_message(self, *a):  # 콘솔을 어지럽히지 않는다
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _host_ok(self):
        # DNS 리바인딩 방지: 이 컴퓨터 주소로 들어온 요청만 받는다
        return (self.headers.get("Host") or "") in self.allowed_hosts

    def do_GET(self):
        if not self._host_ok():
            return self._send(403, {"error": "허용되지 않은 주소입니다."})
        u = urlparse(self.path)
        try:
            if u.path in ("/", "/index.html"):
                with open(self.index_path, "rb") as f:
                    return self._send(200, f.read(), "text/html; charset=utf-8")
            if u.path == "/api/state":
                return self._send(200, self.store.state())
            if u.path == "/api/task":
                q = parse_qs(u.query)
                return self._send(200, self.store.task_data(q.get("doc", [""])[0], q.get("method", [""])[0]))
            if u.path.startswith("/api/doc/") and u.path.endswith(".png"):
                doc = unquote(u.path[len("/api/doc/"):-4])
                return self._send(200, self.store.doc_png(doc), "image/png")
        except KeyError:
            return self._send(404, {"error": "서류를 찾을 수 없습니다."})
        except ValueError as e:
            return self._send(400, {"error": str(e)})
        self._send(404, {"error": "없는 주소입니다."})

    def do_POST(self):
        if not self._host_ok():
            return self._send(403, {"error": "허용되지 않은 주소입니다."})
        origin = self.headers.get("Origin")
        if origin and urlparse(origin).netloc not in self.allowed_hosts:
            return self._send(403, {"error": "다른 사이트에서 보낸 요청입니다."})
        if not (self.headers.get("Content-Type") or "").startswith("application/json"):
            return self._send(415, {"error": "JSON만 받습니다."})
        try:
            n = int(self.headers.get("Content-Length") or 0)
            if n > 2_000_000:
                return self._send(413, {"error": "요청이 너무 큽니다."})
            data = json.loads(self.rfile.read(n) or b"{}")
        except (ValueError, json.JSONDecodeError):
            return self._send(400, {"error": "요청을 읽을 수 없습니다."})
        try:
            if self.path == "/api/setup":
                self.store.setup(data.get("person"), data.get("target_ratio"))
                return self._send(200, self.store.state())
            if self.path == "/api/complete":
                res = self.store.complete(data.get("doc"), data.get("method"), data.get("seconds"),
                                          data.get("fields"), data.get("rows"))
                return self._send(200, {**res, "state": self.store.state()})
            if self.path == "/api/report":
                text, path = self.store.report()
                return self._send(200, {"text": text, "path": os.path.relpath(path, self.store.project_root)
                                        if path.startswith(self.store.project_root) else path})
        except ValueError as e:
            return self._send(400, {"error": str(e)})
        self._send(404, {"error": "없는 주소입니다."})


def make_server(data_dir, port=8765, project_root=ROOT):
    Handler.store = Store(data_dir, project_root)
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    p = srv.server_address[1]
    Handler.allowed_hosts = {f"127.0.0.1:{p}", f"localhost:{p}"}
    return srv


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--data", default=os.path.join(ROOT, "measurement"), help="결과를 쌓을 폴더")
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args()
    srv = make_server(a.data, a.port)
    url = f"http://127.0.0.1:{srv.server_address[1]}/"
    print(f"측정 앱: {url}  (끝내려면 Ctrl+C)\n결과 폴더: {a.data}")
    if not a.no_browser:
        webbrowser.open(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n종료합니다. 결과는 위 폴더에 저장되어 있습니다.")


if __name__ == "__main__":
    main()

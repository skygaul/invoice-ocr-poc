"""후보 B: PaddleOCR-VL 1.6 (0.9B 문서 전용 VLM) + 표(HTML) 읽기. 노트북 CPU에서 실행, 외부 전송 없음.

B의 한계(결과 해석 때 반드시 같이 쓸 것):
- 신뢰도를 주지 않는다. '확인 필요'는 숫자 교차검산(수량×단가=금액, 세액, 합계)으로만 만든다.
  품목명 오타는 잡을 수 없다.
- 줄 단위 위치를 주지 않는다. 모든 품목의 box는 '표 전체' 박스다(box_granularity=table).

사용 예: python -X utf8 -W ignore src/run_b.py --pdf-dir dev-invoices --only 명세서_02,명세서_17
이미 읽어 둔 결과로 파싱만 다시: --reuse-raw outputs/B/<실행>
"""
import argparse
import json
import os
import re
import sys
import time
from html.parser import HTMLParser

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common
from table_parse import _norm, _num, _to_date


class _Tables(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows, self._row, self._cell, self._in = [], None, None, False

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self._row = []
        elif tag in ("td", "th"):
            self._cell, self._in = [], True

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._row is not None:
            self._row.append("".join(self._cell or []))
            self._cell, self._in = None, False
        elif tag == "tr" and self._row is not None:
            self.rows.append(self._row)
            self._row = None

    def handle_data(self, data):
        if self._in:
            self._cell.append(data)


def html_rows(content):
    p = _Tables()
    p.feed(str(content).replace("\\n", " "))
    return [[re.sub(r"\s+", " ", c).strip() for c in r] for r in p.rows]


def parse_blocks(blocks, tolerance=0, tax_rate=0.10):
    """parsing_res_list -> (fields, items, mismatch, notes). 기준은 table_parse와 같은 교차검산."""
    notes = []
    all_text = []
    tables = []
    for b in blocks:
        c = str(b.get("block_content") or "")
        if b.get("block_label") == "table":
            tables.append(b)
        all_text.append((b, c.replace("\\n", "\n")))
    joined = "\n".join(t for _, t in all_text)

    def block_box(b):
        return [int(v) for v in b["block_bbox"]]

    # 거래처
    sup_val, sup_box = None, None
    for b, t in all_text:
        m = re.search(r"(?:상호|공급자)\s*[:：]\s*([^\n<]+?)(?:\s*(?:대표|등록번호|사업자번호)|$|\n)", re.sub(r"<[^>]+>", "\n", t))
        if m:
            sup_val, sup_box = _norm(m.group(1)), block_box(b)
    if sup_val is None:
        skip = ("거래명세표", "거래명세서", "납품서", "공급받는자", "보관용")
        for b, t in sorted(all_text, key=lambda x: x[0]["block_bbox"][1]):
            tt = _norm(t)
            if b.get("block_label") != "table" and re.search(r"[가-힣]", tt) and not any(k in tt for k in skip):
                sup_val, sup_box = tt, block_box(b)
                break
    # 날짜
    date_val, date_box = None, None
    for b, t in all_text:
        d = _to_date(re.sub(r"<[^>]+>", " ", t))
        if d:
            date_val, date_box = d, block_box(b)
            break

    # 표 읽기
    items, hdr, tbox = [], None, None
    total_val, total_box = None, None
    sum_tax_cells = []
    seq_roles = None  # 제목줄이 한 칸에 합쳐졌을 때, 글자 순서로 정한 칸 역할
    for tb in tables:
        rows = html_rows(tb.get("block_content"))
        for ri, r in enumerate(rows):
            norm = [_norm(c) for c in r]
            if hdr is None and "수량" in norm and "단가" in norm and any(k in norm for k in ("금액", "공급가액")):
                hdr = {k: norm.index(k) for k in ("품목", "품명", "수량", "단가", "금액", "공급가액", "세액") if k in norm}
                tbox = block_box(tb)
                hdr_len = len(norm)
                hdr_row_idx = ri
                continue
            if hdr is None:
                # 대체 규칙: 칸이 합쳐진 제목줄(예: '품목 수량 단위 단가 금액')을 글자 순서대로 푼다
                joined_hdr = _norm("".join(r))
                keys = [k for k in ("번호", "품목", "품명", "수량", "단위", "규격", "단가", "공급가액", "금액", "세액")
                        if k in joined_hdr]
                if all(k in joined_hdr for k in ("수량", "단가")) and ("금액" in joined_hdr or "공급가액" in joined_hdr) \
                        and ("품목" in joined_hdr or "품명" in joined_hdr):
                    seq_roles = sorted(keys, key=lambda k: joined_hdr.index(k))
                    # '공급가액'에 '금액'이 따로 겹쳐 잡히지 않게 한다
                    if "공급가액" in seq_roles and "금액" in seq_roles:
                        seq_roles.remove("금액")
                    hdr = {k: i for i, k in enumerate(seq_roles)}
                    tbox = block_box(tb)
                    hdr_len = None
                    notes.append("제목줄이 합쳐져 있어 글자 순서로 칸을 해석함")
                    continue
                continue
            text = _norm("".join(r))
            if "합계" in text:
                nums = [_num(c) for c in r if re.search(r"\d", c) and _num(c) is not None]
                if nums:
                    total_val, total_box = nums[-1], block_box(tb)
                continue
            if text.startswith(("공급가액계", "세액계", "소계")):
                continue
            name_i = hdr.get("품목", hdr.get("품명"))
            amt_i = hdr.get("금액", hdr.get("공급가액"))
            tax_i = hdr.get("세액")
            if seq_roles is not None:
                # 합쳐진 제목줄: 칸 수가 역할 수와 같거나, 맨 끝(금액·세액) 칸이 빠진 줄만 읽는다
                if len(r) == len(seq_roles):
                    cell = dict(zip(seq_roles, r))
                elif len(r) == len(seq_roles) - 1 and seq_roles[-1] in ("금액", "공급가액", "세액"):
                    cell = dict(zip(seq_roles[:-1], r))
                else:
                    continue
                if not re.fullmatch(r"\d+", _norm(cell.get("번호", "0"))):
                    continue  # 번호 칸이 숫자가 아니면 품목 줄이 아니다
                gname = cell.get("품목", cell.get("품명"))
                qty = _num(cell.get("수량", ""))
                price = _num(cell.get("단가", ""))
                amt = _num(cell.get("금액", cell.get("공급가액", "")))
                tax = None
                if "세액" in cell:
                    tax = 0 if _norm(cell["세액"]) == "면세" else _num(cell["세액"])
                if gname is None:
                    continue
                r_name = gname
                tax_present = "세액" in seq_roles
            else:
                if len(r) != hdr_len:
                    continue  # 칸 수가 다른 줄은 품목 줄로 보지 않는다
                qty, price, amt = _num(r[hdr["수량"]]), _num(r[hdr["단가"]]), _num(r[amt_i])
                tax = None
                if tax_i is not None:
                    tax = 0 if _norm(r[tax_i]) == "면세" else _num(r[tax_i])
                if name_i is None:
                    continue
                r_name = r[name_i]
                tax_present = tax_i is not None
            if qty is None and price is None and amt is None:
                continue
            review, num_ok, why = False, True, []
            if None in (qty, price, amt):
                review, num_ok = True, False
                why.append("숫자 누락")
            elif abs(round(qty * price) - amt) > tolerance:
                review, num_ok = True, False
                why.append("수량×단가≠금액")
            if tax_present and amt is not None and tax is not None and tax not in (0, round(amt * tax_rate)):
                review = True
                why.append("세액 이상")
            name = _norm(r_name)
            if not name:
                review = True
                why.append("품목명 없음")
            items.append({"item": name, "qty": qty, "unit_price": price, "amount": amt, "review": review,
                          "box": block_box(tb), "_tax": tax, "_ok": num_ok, "_why": why})
    if hdr is None:
        notes.append("표 제목줄을 못 찾음")
    # 합계가 표 밖 글자에 있을 때
    if total_val is None:
        for b, t in all_text:
            m = re.search(r"합\s*계(?:\s*금\s*액)?\s*[:：]?\s*[₩W]?\s*([\d,]+)", t)
            if m:
                total_val, total_box = _num(m.group(1)), block_box(b)

    # '합계' 글자 없이 숫자만 표 바로 아래 따로 떨어진 블록으로 나온 경우
    if total_val is None and tbox is not None:
        below = [(b["block_bbox"][1], b) for b, t in all_text
                 if b.get("block_label") != "table" and b["block_bbox"][1] >= tbox[3] - 5
                 and re.fullmatch(r"[₩W\s]*\d[\d,]*", t.strip())]
        if below:
            b = min(below, key=lambda x: x[0])[1]
            total_val = _num(b.get("block_content"))
            total_box = block_box(b)
            notes.append("합계 글자 없이 숫자만 있는 블록을 합계로 봄")

    sum_amount = sum(i["amount"] for i in items if i["amount"] is not None)
    sum_tax = sum(i["_tax"] or 0 for i in items) if hdr and "세액" in hdr else 0
    differs = total_val is not None and bool(items) and sum_amount + sum_tax != total_val
    reliable = bool(items) and all(i["_ok"] for i in items)
    mismatch = differs and reliable
    if differs:
        notes.append(f"품목 합({sum_amount}+세액{sum_tax}) != 합계({total_val})" + ("" if reliable else " — 품목 숫자를 못 믿어 불일치 표시는 보류"))

    def fld(v, box, extra=False):
        return {"value": v, "review": bool(v is None or extra), "box": box}

    fields = {"supplier": fld(sup_val, sup_box), "date": fld(date_val, date_box),
              "doc_total": fld(total_val, total_box, extra=bool(differs))}
    for i in items:
        i.pop("_tax", None)
        i.pop("_ok", None)
    return fields, items, bool(mismatch), notes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf-dir", required=True)
    ap.add_argument("--pipeline-version", default="v1.6")
    ap.add_argument("--cpu-threads", type=int, default=os.cpu_count())
    ap.add_argument("--tolerance", type=int, default=0)
    ap.add_argument("--tax-rate", type=float, default=0.10)
    ap.add_argument("--only", default="", help="쉼표로 구분한 파일 이름(확장자 제외)만 실행")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--reuse-raw", default="")
    ap.add_argument("--out-root", default="outputs")
    a = ap.parse_args()

    common.block_network()
    pdfs = common.list_pdfs(a.pdf_dir)
    if a.only:
        want = set(a.only.split(","))
        pdfs = [p for p in pdfs if os.path.splitext(os.path.basename(p))[0] in want]
    if a.limit:
        pdfs = pdfs[:a.limit]
    run_dir = common.new_run_dir("B", a.out_root)
    os.makedirs(os.path.join(run_dir, "raw"))

    pipe, load_s = None, None
    if not a.reuse_raw:
        from paddleocr import PaddleOCRVL
        t0 = time.perf_counter()
        pipe = PaddleOCRVL(pipeline_version=a.pipeline_version, device="cpu", use_doc_orientation_classify=False,
                           use_doc_unwarping=False, enable_mkldnn=False, cpu_threads=a.cpu_threads)
        load_s = time.perf_counter() - t0

    preds, log = [], {"model_load_seconds": load_s, "box_granularity": "table", "docs": {}}
    for path in pdfs:
        fname = os.path.basename(path)
        img = common.render_pdf(path)
        if a.reuse_raw:
            with open(os.path.join(a.reuse_raw, "raw", fname + ".json"), encoding="utf-8") as f:
                raw = json.load(f)
            vl_s, blocks = raw["vl_seconds"], raw["blocks"]
        else:
            t0 = time.perf_counter()
            r = list(pipe.predict(img))[0]
            vl_s = time.perf_counter() - t0
            j = r.json["res"] if "res" in r.json else r.json
            blocks = [{"block_label": b.get("block_label"), "block_bbox": [int(v) for v in b.get("block_bbox")],
                       "block_content": b.get("block_content")} for b in j.get("parsing_res_list", [])]
        with open(os.path.join(run_dir, "raw", fname + ".json"), "w", encoding="utf-8") as f:
            json.dump({"file": fname, "vl_seconds": vl_s, "blocks": blocks}, f, ensure_ascii=False)
        t1 = time.perf_counter()
        fields, items, mismatch, notes = parse_blocks(blocks, a.tolerance, a.tax_rate)
        parse_s = time.perf_counter() - t1
        pred = {"file": fname, "seconds": vl_s + parse_s, "total_mismatch": mismatch, "fields": fields,
                "items": [{k: v for k, v in it.items() if not k.startswith("_")} for it in items]}
        preds.append(pred)
        log["docs"][fname] = {"vl_seconds": vl_s, "parse_seconds": parse_s, "notes": notes,
                              "why": [it.get("_why") for it in items]}
        common.draw_overlay(img, pred, os.path.join(run_dir, "overlay", fname.replace(".pdf", ".png")))
        print(f"{fname}: {vl_s + parse_s:.1f}s items={len(items)} notes={notes}", flush=True)
        # 오래 걸리는 실행이라 문서마다 중간 저장(마지막에 덮어쓰지 않고 새 파일로 한 번 더 저장)
        with open(os.path.join(run_dir, "predictions.partial.json"), "w", encoding="utf-8") as f:
            json.dump(preds, f, ensure_ascii=False)

    common.save_run(run_dir, preds, vars(a), log)
    print("저장:", run_dir)


if __name__ == "__main__":
    main()

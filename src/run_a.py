"""후보 A: PP-OCRv5 한국어 + 규칙 기반 표 파싱. 노트북 CPU에서 실행, 외부 전송 없음.

사용 예(개발용 샘플):
  python -X utf8 -W ignore src/run_a.py --pdf-dir dev-invoices
시험용 20장:
  python -X utf8 -W ignore src/run_a.py --pdf-dir sample-invoices
이미 읽어 둔 글자로 파싱만 다시(시간은 기록된 OCR 시간을 그대로 씀):
  python -X utf8 -W ignore src/run_a.py --pdf-dir dev-invoices --reuse-raw outputs/A/<실행>
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common
from rectify import RectifyParams, rectify as rectify_page
from table_parse import Params, parse


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf-dir", "--input-dir", dest="pdf_dir", required=True,
                    help="PDF 또는 사진(jpg, png)이 들어 있는 폴더")
    ap.add_argument("--rectify", action="store_true", help="폰 사진에서 종이를 찾아 반듯하게 편 뒤 읽는다")
    ap.add_argument("--flatfield", action="store_true", help="(--rectify와 함께) 조명 얼룩을 평평하게 보정")
    ap.add_argument("--paper-min-area", type=float, default=RectifyParams.min_area_ratio)
    ap.add_argument("--rect-work-side", type=int, default=RectifyParams.work_side)
    ap.add_argument("--det-model", default="PP-OCRv5_server_det")
    ap.add_argument("--rec-model", default="korean_PP-OCRv5_mobile_rec")
    ap.add_argument("--det-side", type=int, default=960, help="검출 입력의 긴 변 상한(px)")
    ap.add_argument("--cpu-threads", type=int, default=os.cpu_count())
    ap.add_argument("--conf-thresh", type=float, default=Params.conf_thresh)
    ap.add_argument("--line-gap-ratio", type=float, default=Params.line_gap_ratio)
    ap.add_argument("--tax-rate", type=float, default=Params.tax_rate)
    ap.add_argument("--tolerance", type=int, default=Params.tolerance)
    ap.add_argument("--limit", type=int, default=0, help="앞에서 N장만(0이면 전부)")
    ap.add_argument("--reuse-raw", default="", help="이전 실행 폴더의 raw/ 글자를 다시 파싱")
    ap.add_argument("--out-root", default="outputs")
    ap.add_argument("--export", action="store_true",
                    help="결과를 엑셀(ledger.xlsx)과 CSV(ledger.csv)로도 저장(openpyxl 필요)")
    a = ap.parse_args()

    common.block_network()
    p = Params(a.conf_thresh, a.line_gap_ratio, a.tax_rate, a.tolerance)
    rp = RectifyParams(work_side=a.rect_work_side, min_area_ratio=a.paper_min_area)
    pdfs = common.list_inputs(a.pdf_dir)
    if a.limit:
        pdfs = pdfs[:a.limit]
    run_dir = common.new_run_dir("A", a.out_root)
    os.makedirs(os.path.join(run_dir, "raw"))

    ocr, load_s = None, None
    if not a.reuse_raw:
        from paddleocr import PaddleOCR
        t0 = time.perf_counter()
        ocr = PaddleOCR(text_detection_model_name=a.det_model, text_recognition_model_name=a.rec_model,
                        use_doc_orientation_classify=False, use_doc_unwarping=False,
                        use_textline_orientation=False, device="cpu", enable_mkldnn=False,
                        text_det_limit_side_len=a.det_side, text_det_limit_type="max",
                        cpu_threads=a.cpu_threads)
        load_s = time.perf_counter() - t0

    preds, log = [], {"model_load_seconds": load_s, "docs": {}}
    for path in pdfs:
        fname = os.path.basename(path)
        img = common.render_input(path)
        rect_info, rect_s = None, 0.0
        if a.rectify:  # 폰 사진: 종이를 찾아 반듯하게 편다. 박스 좌표는 편 페이지 기준이다.
            t0 = time.perf_counter()
            img, rect_info = rectify_page(img, rp, flatfield=a.flatfield)
            rect_s = time.perf_counter() - t0
        h, w = img.shape[:2]
        raw_path = os.path.join(run_dir, "raw", fname + ".json")
        if a.reuse_raw:
            with open(os.path.join(a.reuse_raw, "raw", fname + ".json"), encoding="utf-8") as f:
                raw = json.load(f)
            ocr_s = raw["ocr_seconds"]
            rect_s = raw.get("rectify_seconds", rect_s)
            tokens = raw["tokens"]
        else:
            t0 = time.perf_counter()
            d = ocr.predict(img)[0].json["res"]
            ocr_s = time.perf_counter() - t0
            tokens = [{"text": tx, "score": float(sc), "box": [int(v) for v in bx],
                       "poly": [[int(x), int(y)] for x, y in pl]}
                      for tx, sc, bx, pl in zip(d["rec_texts"], d["rec_scores"], d["rec_boxes"], d["rec_polys"])]
        with open(raw_path, "w", encoding="utf-8") as f:
            json.dump({"file": fname, "ocr_seconds": ocr_s, "rectify_seconds": rect_s, "image": [w, h],
                       "tokens": tokens}, f, ensure_ascii=False)
        t1 = time.perf_counter()
        fields, items, flags, notes = parse([dict(t) for t in tokens], w, h, p)
        parse_s = time.perf_counter() - t1
        pred = {"file": fname, "seconds": rect_s + ocr_s + parse_s, "total_mismatch": flags["total_mismatch"],
                "fields": fields, "items": [{k: v for k, v in it.items() if not k.startswith("_")} for it in items]}
        preds.append(pred)
        log["docs"][fname] = {"ocr_seconds": ocr_s, "parse_seconds": parse_s, "rectify_seconds": rect_s,
                              "rectify": rect_info, "notes": notes, "why": [it.get("_why") for it in items]}
        common.draw_overlay(img, pred, os.path.join(run_dir, "overlay", os.path.splitext(fname)[0] + ".png"))
        rect_note = "" if rect_info is None else (" 종이 찾음" if rect_info.get("찾음") else f" 종이 못 찾음({rect_info.get('사유')})")
        print(f"{fname}: {rect_s + ocr_s + parse_s:.1f}s items={len(items)}{rect_note} notes={notes}", flush=True)

    common.save_run(run_dir, preds, vars(a), log)
    if a.export:
        from export_xlsx import export
        n_items, n_docs = export(os.path.join(run_dir, "predictions.json"),
                                 os.path.join(run_dir, "ledger.xlsx"), os.path.join(run_dir, "ledger.csv"))
        print(f"엑셀: {os.path.join(run_dir, 'ledger.xlsx')} (품목 {n_items}행, 문서 {n_docs}장)")
    print("저장:", run_dir)


if __name__ == "__main__":
    main()

"""추출 결과(predictions.json)를 사람이 확인할 수 있는 엑셀(.xlsx)과 CSV로 내보낸다.

PoC의 마지막 단계: 입력(PDF) → AI(글자 인식 + 규칙) → **엑셀 행 초안**.
- 시트 "장부 초안": 품목 한 줄이 한 행. 확인 필요 행은 노란색, 사유와 원본 위치(박스)가 함께 적힌다.
  마지막 "확인 완료" 열은 사람이 확인한 뒤 표시하는 칸(비어 있음). AI는 초안만 만들고 반영은 사람이 한다.
- 시트 "문서 요약": 문서당 한 행. 품목 금액의 합과 문서 합계를 나란히 놓아 불일치를 눈으로도 볼 수 있다.
- 시트 "읽는 법": 색과 열의 뜻.
확인 사유는 predictions.json과 같은 폴더의 params.json(실행 기록)에 있으면 읽어서 넣는다.

사용: python -X utf8 src/export_xlsx.py --pred outputs/A/<실행>/predictions.json --out ledger.xlsx [--csv ledger.csv]
이미 있는 파일은 덮어쓰지 않는다.
"""
import argparse
import csv
import json
import os
import re
import sys

ITEM_COLS = ["거래처", "날짜", "품목", "수량", "단가", "금액", "확인 필요", "확인 사유", "확인 완료", "파일", "원본 위치(x1,y1,x2,y2)"]
DOC_COLS = ["파일", "거래처", "날짜", "품목 수", "품목 금액 합", "문서 합계", "합계 불일치", "확인 필요 항목",
            "확인 필요 품목 수", "처리 시간(초)"]
FIELD_NAMES = {"supplier": "거래처", "date": "날짜", "doc_total": "합계"}
REASON_TEXT = {"낮은 신뢰도": "글자 인식 신뢰도 낮음", "수량×단가≠금액": "수량×단가가 금액과 다름",
               "품목명 없음": "품목명을 못 읽음", "세액 이상": "세액이 맞지 않음"}


ROLE_KO = {"qty": "수량", "price": "단가", "amount": "금액"}


def _reasons(why):
    out = []
    for w in why or []:
        if w in REASON_TEXT:
            out.append(REASON_TEXT[w])
            continue
        m = re.fullmatch(r"(qty|price|amount) 칸에 조각 (\d+)개", w)
        if m:  # 예: 단가 칸에서 읽은 글자가 0개(없음)나 2개 이상(겹침)
            out.append(f"{ROLE_KO[m.group(1)]} 칸에서 읽은 글자가 {m.group(2)}개")
        else:
            out.append(w)
    return "; ".join(out)


def build(preds, why_by_file=None, notes_by_file=None):
    """predictions -> (품목 행 목록, 문서 요약 행 목록). 값은 가공하지 않고 그대로 옮긴다."""
    why_by_file = why_by_file or {}
    notes_by_file = notes_by_file or {}
    item_rows, doc_rows = [], []
    for d in preds:
        f = d["file"]
        fields = d.get("fields") or {}
        sup = (fields.get("supplier") or {}).get("value")
        date = (fields.get("date") or {}).get("value")
        why_list = why_by_file.get(f) or []
        n_flag = 0
        amount_sum = 0
        for i, it in enumerate(d.get("items") or []):
            review = bool(it.get("review"))
            n_flag += review
            if isinstance(it.get("amount"), (int, float)):
                amount_sum += it["amount"]
            box = it.get("box")
            reason = _reasons(why_list[i]) if i < len(why_list) else ""
            if review and not reason:
                reason = "확인 필요"
            item_rows.append({
                "거래처": sup, "날짜": date, "품목": it.get("item"), "수량": it.get("qty"),
                "단가": it.get("unit_price"), "금액": it.get("amount"),
                "확인 필요": "확인 필요" if review else "", "확인 사유": reason, "확인 완료": "",
                "파일": f, "원본 위치(x1,y1,x2,y2)": ",".join(str(int(v)) for v in box) if box else "",
                "_review": review,
            })
        flagged_fields = [FIELD_NAMES[k] for k in FIELD_NAMES if (fields.get(k) or {}).get("review")]
        total = (fields.get("doc_total") or {}).get("value")
        doc_rows.append({
            "파일": f, "거래처": sup, "날짜": date, "품목 수": len(d.get("items") or []),
            "품목 금액 합": amount_sum, "문서 합계": total,
            "합계 불일치": "불일치" if d.get("total_mismatch") else "",
            "확인 필요 항목": ", ".join(flagged_fields), "확인 필요 품목 수": n_flag,
            "처리 시간(초)": round(d["seconds"], 1) if isinstance(d.get("seconds"), (int, float)) else None,
            "_attention": bool(d.get("total_mismatch") or flagged_fields),
        })
    return item_rows, doc_rows


def load_run_log(pred_path):
    """predictions.json 옆의 params.json에서 확인 사유와 메모를 읽는다(없으면 빈 값)."""
    p = os.path.join(os.path.dirname(os.path.abspath(pred_path)), "params.json")
    if not os.path.exists(p):
        return {}, {}
    with open(p, encoding="utf-8") as f:
        docs = (json.load(f).get("log") or {}).get("docs") or {}
    return ({k: v.get("why") for k, v in docs.items()}, {k: v.get("notes") for k, v in docs.items()})


def export(pred_path, xlsx_path, csv_path=None):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    for p in (xlsx_path, csv_path):
        if p and os.path.exists(p):
            raise FileExistsError(f"이미 있는 파일이라 덮어쓰지 않습니다: {p}")
    with open(pred_path, encoding="utf-8") as f:
        preds = json.load(f)
    why, notes = load_run_log(pred_path)
    item_rows, doc_rows = build(preds, why, notes)

    yellow = PatternFill("solid", fgColor="FFF2CC")
    head_fill = PatternFill("solid", fgColor="DDE3EA")
    bold = Font(bold=True)

    def sheet(ws, cols, rows, flag_key, widths, money):
        ws.append(cols)
        for c in range(1, len(cols) + 1):
            cell = ws.cell(row=1, column=c)
            cell.font, cell.fill = bold, head_fill
            cell.alignment = Alignment(vertical="center", wrap_text=True)
        for r in rows:
            ws.append([r[c] for c in cols])
            if r[flag_key]:
                for c in range(1, len(cols) + 1):
                    ws.cell(row=ws.max_row, column=c).fill = yellow
            for name in money:
                ws.cell(row=ws.max_row, column=cols.index(name) + 1).number_format = "#,##0"
        for i, w in enumerate(widths, 1):
            ws.column_dimensions[get_column_letter(i)].width = w
        ws.freeze_panes = "A2"
        ws.auto_filter.ref = ws.dimensions

    wb = Workbook()
    ws1 = wb.active
    ws1.title = "장부 초안"
    sheet(ws1, ITEM_COLS, item_rows, "_review", [14, 12, 32, 8, 10, 12, 10, 30, 10, 16, 24], ["단가", "금액"])
    ws2 = wb.create_sheet("문서 요약")
    sheet(ws2, DOC_COLS, doc_rows, "_attention", [16, 14, 12, 8, 14, 12, 12, 18, 12, 12], ["품목 금액 합", "문서 합계"])
    ws3 = wb.create_sheet("읽는 법")
    for line in [
        "이 파일은 AI가 만든 초안입니다. 장부에 반영하기 전에 사람이 확인합니다.",
        "노란색 행 = '확인 필요'. 글자 인식 신뢰도가 낮거나 계산이 맞지 않는 값이 들어 있으니 원본과 대조하세요.",
        "노란색이 아닌 행도 틀릴 수 있습니다(신뢰도가 높아도 오독할 수 있음). 합성 샘플 20장에서는 틀린 값이 모두 확인 필요로 넘어왔지만, 실제 서류에서는 확인하지 못했습니다.",
        "'원본 위치'는 원본 PDF를 150dpi로 렌더링한 이미지의 픽셀 좌표(왼쪽 위 x1,y1 → 오른쪽 아래 x2,y2)입니다.",
        "'문서 요약'의 '합계 불일치'는 품목 금액의 합(세액 포함)이 문서에 적힌 합계와 다를 때 붙습니다. 원본 서류의 오류일 수 있습니다.",
        "세액이 따로 적힌 양식은 '품목 금액 합'과 '문서 합계'가 달라 보이는데, 그 차이가 세액이면 정상이라 '합계 불일치'가 붙지 않습니다(예: 명세서_19).",
        "품목 숫자를 제대로 읽지 못한 문서는 불일치로 단정하지 않고 '확인 필요 항목'에 '합계'만 표시합니다(예: 명세서_17).",
        "'확인 완료' 열은 확인한 뒤 사람이 표시하는 칸입니다.",
    ]:
        ws3.append([line])
    ws3.column_dimensions["A"].width = 120
    for row in ws3.iter_rows():
        row[0].alignment = Alignment(wrap_text=True, vertical="top")
    wb.save(xlsx_path)

    if csv_path:
        with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(ITEM_COLS)
            for r in item_rows:
                w.writerow([r[c] for c in ITEM_COLS])
    return len(item_rows), len(doc_rows)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pred", required=True)
    ap.add_argument("--out", required=True, help="만들 .xlsx 경로")
    ap.add_argument("--csv", default="", help="CSV도 만들 경로(선택)")
    a = ap.parse_args()
    try:
        n_items, n_docs = export(a.pred, a.out, a.csv or None)
    except FileExistsError as e:
        sys.exit(str(e))
    print(f"저장: {a.out} (품목 {n_items}행, 문서 {n_docs}장)" + (f", {a.csv}" if a.csv else ""))


if __name__ == "__main__":
    main()

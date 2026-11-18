"""수기 입력 vs AI 보조 검토를 같은 서류로 비교하는 측정 도구(문제 정의서 기준 7).

두 명령:
  template  기록 시트(timing_log.csv)와 수기 입력용 빈 엑셀(manual_entries.xlsx)을 만든다.
  report    기록과 입력 결과를 정답과 비교해 시간·틀린 칸 수를 계산하고 목표와 견준다.

사용:
  python -X utf8 src/measure.py template --out measurement
  python -X utf8 src/measure.py report --timing measurement/timing_log.csv \
      --manual measurement/manual_entries.xlsx --assisted measurement/assisted_final.xlsx \
      --target-time-ratio 0.7 --out measurement/result.md

규칙(재기 전에 고정):
- '틀린 칸' = 정답과 다른 칸. 서류마다 거래처·날짜 2칸 + 품목마다 품목·수량·단가·금액 4칸.
  정답 줄이 없으면(빠뜨림) 그 줄의 4칸이 모두 틀린 것, 정답에 없는 줄을 넣으면 그 줄의 4칸이 틀린 것이다.
- AI 보조의 '조용한 오류' = 틀린 칸이 있는데 그 행의 '확인 필요' 열이 비어 있는 경우(노란색이 아니었는데 틀림).
  빠뜨린 줄은 표시할 행이 없으므로 조용한 오류로 센다.
- AI 보조 시간 = 사람이 검토한 초 + AI 처리 초(기록된 값. 문서별 값이 없으면 평균). 수기는 입력한 초.
- 판정은 사용자가 재기 전에 정한 --target-time-ratio(AI 보조 중앙값 ÷ 수기 중앙값)와
  "AI 보조의 틀린 칸이 수기보다 늘지 않았다"를 함께 만족할 때만 '목표 달성'이다.
- 표본이 작으면 일반화할 수 없다는 한계를 리포트에 함께 적는다.
"""
import argparse
import csv
import datetime
import json
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import score as S

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DOCS = ["명세서_01", "명세서_02", "명세서_05", "명세서_08", "명세서_11", "명세서_14", "명세서_18", "명세서_19"]
ENTRY_COLS = ["파일", "거래처", "날짜", "품목", "수량", "단가", "금액"]
TIMING_COLS = ["서류", "방식", "측정자", "날짜", "초", "고친 칸 수", "메모"]
MANUAL, ASSISTED = "수기", "AI보조"


# ---------- 읽기 ----------
def norm_doc(name):
    n = str(name).strip()
    return n if n.lower().endswith(".pdf") else n + ".pdf"


def _cell(v):
    if hasattr(v, "strftime"):
        return v.strftime("%Y-%m-%d")
    return v


def read_entries(path):
    """입력 결과(.xlsx 또는 .csv)를 행 목록으로 읽는다. '장부 초안' 시트가 있으면 그것을, 없으면 첫 시트를 쓴다."""
    if path.lower().endswith(".csv"):
        with open(path, encoding="utf-8-sig", newline="") as f:
            raw = list(csv.reader(f))
    else:
        from openpyxl import load_workbook
        wb = load_workbook(path, data_only=True)
        ws = wb["장부 초안"] if "장부 초안" in wb.sheetnames else wb.worksheets[0]
        raw = [[_cell(c) for c in r] for r in ws.iter_rows(values_only=True)]
    if not raw:
        return []
    header = [str(h).strip() if h is not None else "" for h in raw[0]]
    if "파일" not in header or "품목" not in header:
        raise ValueError(f"{path}: 첫 줄에 '파일'과 '품목' 열이 있어야 합니다. 찾은 열: {header}")
    rows = []
    for r in raw[1:]:
        d = {h: (r[i] if i < len(r) else None) for i, h in enumerate(header) if h}
        if all(v in (None, "") for v in d.values()):
            continue
        if d.get("파일") in (None, ""):
            continue
        d["파일"] = norm_doc(d["파일"])
        rows.append(d)
    return rows


def read_timing(path):
    with open(path, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    out = []
    for r in rows:
        sec = (r.get("초") or "").strip()
        if not sec:
            continue  # 아직 재지 않은 행
        method = (r.get("방식") or "").strip()
        if method not in (MANUAL, ASSISTED):
            raise ValueError(f"방식은 '{MANUAL}' 또는 '{ASSISTED}'여야 합니다: {method!r}")
        out.append({"doc": norm_doc(r["서류"]), "method": method, "sec": float(sec),
                    "fixed": (r.get("고친 칸 수") or "").strip(), "who": (r.get("측정자") or "").strip()})
    return out


# ---------- 채점 ----------
def score_entries(gt_docs, rows, docs):
    """docs(문서 파일 이름 목록)에 대해 틀린 칸을 센다. 반환: 요약과 문서별 목록."""
    by_doc = {}
    for r in rows:
        by_doc.setdefault(r["파일"], []).append(r)
    gt = {g["file"]: g for g in gt_docs}
    per_doc, total_cells, wrong, silent = [], 0, 0, 0
    for f in docs:
        g = gt.get(f)
        if g is None:
            raise ValueError(f"정답에 없는 서류입니다: {f}")
        mine = by_doc.get(f, [])
        w = s = 0
        cells = 2 + 4 * len(g["items"])
        # 거래처·날짜: 비어 있지 않은 첫 값
        sup = next((r.get("거래처") for r in mine if r.get("거래처") not in (None, "")), None)
        dat = next((r.get("날짜") for r in mine if r.get("날짜") not in (None, "")), None)
        w += int(S.norm_text(sup) != S.norm_text(g["supplier"]))
        w += int(S.to_date(dat) != g["date"])
        # 이 두 칸은 행에 속하지 않아 조용한 오류 계산에서 제외한다
        items = [{"item": r.get("품목"), "qty": r.get("수량"), "unit_price": r.get("단가"), "amount": r.get("금액")}
                 for r in mine]
        matched, extra = S.match_items(g["items"], items)
        for i, gi in enumerate(g["items"]):
            if i not in matched:
                w += 4
                s += 4  # 빠뜨린 줄은 표시할 행이 없다
                continue
            j = matched[i]
            pi = items[j]
            bad = int(S.norm_text(gi["item"]) != S.norm_text(pi["item"]))
            bad += sum(not S.same_num(gi[k], pi[k]) for k in ("qty", "unit_price", "amount"))
            w += bad
            flagged = str(mine[j].get("확인 필요") or "").strip() != ""
            if bad and not flagged:
                s += bad
        for j in extra:
            w += 4
            if str(mine[j].get("확인 필요") or "").strip() == "":
                s += 4
        total_cells += cells
        wrong += w
        silent += s
        per_doc.append({"doc": f, "cells": cells, "wrong": w, "silent": s, "rows": len(mine)})
    return {"cells": total_cells, "wrong": wrong, "silent": silent, "docs": len(docs)}, per_doc


def ai_seconds_by_doc(pred_path=None):
    """AI 처리 시간(초). 문서별 값이 있는 predictions.json을 주면 그 값, 없으면 기대 결과의 평균."""
    if pred_path and os.path.exists(pred_path):
        with open(pred_path, encoding="utf-8") as f:
            return {d["file"]: float(d["seconds"]) for d in json.load(f) if d.get("seconds") is not None}, None
    with open(os.path.join(ROOT, "expected", "A_test_summary.json"), encoding="utf-8") as f:
        mean = float(json.load(f)["criteria"]["6_처리시간"]["값"])
    return {}, mean


def summarize(timing, manual_rows, assisted_rows, gt_docs, target_ratio, ai_pred=None):
    ai_map, ai_mean = ai_seconds_by_doc(ai_pred)
    man = {t["doc"]: t for t in timing if t["method"] == MANUAL}
    ass = {t["doc"]: t for t in timing if t["method"] == ASSISTED}
    paired = sorted(set(man) & set(ass))
    notes = []
    if not paired:
        raise ValueError("수기와 AI보조 시간이 모두 기록된 서류가 없습니다.")
    only = sorted((set(man) | set(ass)) - set(paired))
    if only:
        notes.append(f"한쪽 방식만 잰 서류는 비교에서 뺐습니다: {', '.join(only)}")

    def ai_total(doc):
        proc = ai_map.get(doc, ai_mean)
        return ass[doc]["sec"] + proc, proc

    man_secs = [man[d]["sec"] for d in paired]
    ass_secs = [ai_total(d)[0] for d in paired]
    med_man, med_ass = statistics.median(man_secs), statistics.median(ass_secs)
    ratio = med_ass / med_man if med_man else float("inf")

    m_sum, m_docs = score_entries(gt_docs, manual_rows, paired)
    a_sum, a_docs = score_entries(gt_docs, assisted_rows, paired)
    time_ok = ratio <= target_ratio
    err_ok = a_sum["wrong"] <= m_sum["wrong"]
    who = sorted({t["who"] for t in timing if t["who"]})
    if len(paired) < 8:
        notes.append(f"비교한 서류가 {len(paired)}장이라 8장 권장 기준에 못 미칩니다.")
    if len(who) != 1:
        notes.append(f"측정자가 {len(who)}명으로 기록되었습니다({', '.join(who) or '기록 없음'}). 한 사람이 재는 것이 비교에 맞습니다.")
    return {
        "paired": paired, "n": len(paired), "med_manual": med_man, "med_assisted": med_ass, "ratio": ratio,
        "target_ratio": target_ratio, "time_ok": time_ok, "err_ok": err_ok, "achieved": time_ok and err_ok,
        "manual": m_sum, "assisted": a_sum, "manual_docs": m_docs, "assisted_docs": a_docs,
        "man": man, "ass": ass, "ai_total": {d: ai_total(d) for d in paired}, "notes": notes, "who": who,
    }


def render(r):
    p = lambda x: f"{x:.0f}"
    lines = [
        "# 수기 입력 vs AI 보조 검토 결과", "",
        f"- 비교한 서류: {r['n']}장 ({', '.join(d.replace('.pdf', '') for d in r['paired'])})",
        f"- 측정자: {', '.join(r['who']) or '기록 없음'}", "",
        "## 시간 (장당 초, 중앙값)", "",
        "| 방식 | 중앙값 |", "|---|---|",
        f"| 수기 입력 | {p(r['med_manual'])}초 |",
        f"| AI 보조 (검토 + AI 처리) | {p(r['med_assisted'])}초 |", "",
        f"- AI 보조 ÷ 수기 = **{r['ratio']:.2f}** (재기 전에 정한 목표: {r['target_ratio']:.2f} 이하) → "
        f"{'달성' if r['time_ok'] else '미달'}", "",
        "## 틀린 칸 (정답과 다른 칸)", "",
        "| 방식 | 틀린 칸 / 전체 칸 | 그중 조용한 오류 |", "|---|---|---|",
        f"| 수기 입력 | {r['manual']['wrong']} / {r['manual']['cells']} | - |",
        f"| AI 보조 | {r['assisted']['wrong']} / {r['assisted']['cells']} | {r['assisted']['silent']} |", "",
        f"- AI 보조의 틀린 칸이 수기보다 늘지 않았다: {'예' if r['err_ok'] else '아니오'}", "",
        "## 판정", "",
        f"**{'목표 달성' if r['achieved'] else '목표 미달'}**: 시간 목표 {'충족' if r['time_ok'] else '미충족'}, "
        f"오류 조건 {'충족' if r['err_ok'] else '미충족'}.", "",
        "## 서류별", "",
        "| 서류 | 수기 초 | AI 보조 초(검토+처리) | 수기 틀린 칸 | AI 보조 틀린 칸(조용한) |", "|---|---|---|---|---|",
    ]
    md = {x["doc"]: x for x in r["manual_docs"]}
    ad = {x["doc"]: x for x in r["assisted_docs"]}
    for d in r["paired"]:
        tot, proc = r["ai_total"][d]
        lines.append(f"| {d.replace('.pdf', '')} | {p(r['man'][d]['sec'])} | {p(tot)} ({p(r['ass'][d]['sec'])}+{p(proc)}) | "
                     f"{md[d]['wrong']} | {ad[d]['wrong']} ({ad[d]['silent']}) |")
    lines += ["", "## 한계", "",
              f"- 표본 {r['n']}장, 측정자 {len(r['who'])}명. 이 사람이 이 서류들에서 잰 값이라 **일반화할 수 없습니다**.",
              "- 수기와 AI 보조를 같은 서류로 쟀다면 두 번째 작업이 서류를 기억해 빨라졌을 수 있습니다(학습 효과).",
              "- 합성 서류라 실제 서류의 번짐과 거래처별 양식 차이가 없습니다.",
              "- AI 처리 시간은 기록된 값(또는 평균)을 더한 것이고 실제 대기 시간과 다를 수 있습니다."]
    for n in r["notes"]:
        lines.append(f"- {n}")
    return "\n".join(lines) + "\n"


# ---------- 명령 ----------
def cmd_template(a):
    if os.path.exists(a.out) and os.listdir(a.out):
        sys.exit(f"비어 있지 않은 폴더라 덮어쓰지 않습니다: {a.out}")
    os.makedirs(a.out, exist_ok=True)
    docs = [norm_doc(d) for d in (a.docs.split(",") if a.docs else DEFAULT_DOCS)]
    with open(os.path.join(a.out, "timing_log.csv"), "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(TIMING_COLS)
        for d in docs:
            for m in (MANUAL, ASSISTED):
                w.writerow([d.replace(".pdf", ""), m, "", "", "", "", ""])
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "장부 초안"
    ws.append(ENTRY_COLS)
    ws2 = wb.create_sheet("입력 방법")
    for line in ["서류 한 줄을 한 행으로 입력합니다. '파일' 열에 서류 이름(예: 명세서_01)을 매 행마다 적습니다.",
                 "거래처와 날짜도 매 행에 같이 적습니다(서류마다 같은 값).",
                 "복사·붙여넣기로 OCR 결과를 쓰지 마세요. 서류를 보면서 직접 입력합니다.",
                 "정답 파일(sample-invoices/정답*)은 재는 동안 열어 보지 마세요."]:
        ws2.append([line])
    ws2.column_dimensions["A"].width = 100
    wb.save(os.path.join(a.out, "manual_entries.xlsx"))
    print(f"만들었습니다: {a.out}/timing_log.csv (서류 {len(docs)}장 × 2방식), {a.out}/manual_entries.xlsx")
    print("AI 보조용 파일은 examples/ledger_A_test20.xlsx를 복사해 assisted_final.xlsx로 저장한 뒤 노란색 행만 고치세요.")


def cmd_report(a):
    if os.path.exists(a.out):
        sys.exit(f"이미 있는 파일이라 덮어쓰지 않습니다: {a.out}")
    with open(a.gt, encoding="utf-8") as f:
        gt_docs = json.load(f)
    r = summarize(read_timing(a.timing), read_entries(a.manual), read_entries(a.assisted), gt_docs,
                  a.target_time_ratio, a.ai_pred)
    text = render(r)
    with open(a.out, "w", encoding="utf-8") as f:
        f.write(text)
    print(text)
    print(f"저장: {a.out}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("template")
    t.add_argument("--out", default="measurement")
    t.add_argument("--docs", default="", help="쉼표로 구분한 서류 이름(기본 8장)")
    r = sub.add_parser("report")
    r.add_argument("--timing", required=True)
    r.add_argument("--manual", required=True)
    r.add_argument("--assisted", required=True)
    r.add_argument("--gt", default="sample-invoices/정답.json")
    r.add_argument("--target-time-ratio", type=float, required=True,
                   help="재기 전에 정한 목표: AI 보조 중앙값 ÷ 수기 중앙값이 이 값 이하(예: 0.7)")
    r.add_argument("--ai-pred", default="", help="문서별 AI 처리 시간을 읽을 predictions.json(선택)")
    r.add_argument("--out", required=True)
    a = ap.parse_args()
    {"template": cmd_template, "report": cmd_report}[a.cmd](a)


if __name__ == "__main__":
    main()

"""거래명세서 추출 결과 채점기.

모델을 돌리기 전에 고정한다. 기준은 docs/02_성공기준.md, 채점 단위와 정규화는
sample-invoices/채점규칙.md를 따른다. 결과를 본 뒤 이 파일의 규칙을 바꾸지 않는다.

사용:
  python -X utf8 src/score.py --pred outputs/A/<실행>/predictions.json \
      --gt sample-invoices/정답.json --out results/A_<실행>

예측 파일(predictions.json) 형식: 문서 하나당 객체 하나인 배열
[
  {
    "file": "명세서_01.pdf",
    "seconds": 12.3,                      # 이 문서 처리에 걸린 시간(초)
    "total_mismatch": false,              # 품목 합과 합계가 다르다고 표시했는가
    "fields": {                           # 문서 필드 3개
      "supplier":  {"value": "한울닭유통", "review": false, "box": [x1, y1, x2, y2]},
      "date":      {"value": "2026-10-04", "review": false, "box": [...]},
      "doc_total": {"value": 219800,       "review": false, "box": [...]}
    },
    "items": [                            # 품목 한 줄당 객체 하나
      {"item": "닭염통", "qty": 2, "unit_price": 7200, "amount": 14400,
       "review": false, "box": [x1, y1, x2, y2]}
    ]
  }
]
- review가 true면 "사람이 확인해야 함"으로 넘긴 것이다.
- box는 원본 페이지에서 그 값(품목은 그 줄)의 위치다.
"""
import argparse
import csv
import json
import os
import re
import sys
import unicodedata
from difflib import SequenceMatcher

FIELDS = ("supplier", "date", "doc_total")
ITEM_NUMS = ("qty", "unit_price", "amount")

# 성공 기준 (docs/02_성공기준.md)
LIMITS = {
    "item_silent_errors_max": 3,
    "field_silent_errors_max": 1,
    "review_ratio_max": 0.20,
    "seconds_per_doc_max": 60.0,
}
MATCH_THRESHOLD = 0.4


# ---------- 정규화 ----------
def norm_text(s):
    if s is None:
        return None
    s = unicodedata.normalize("NFC", str(s))
    return re.sub(r"\s+", "", s).lower()


def file_key(name):
    """파일 이름에서 확장자를 뗀 것. 스캔(.pdf)과 폰 사진(.jpg)이 같은 정답을 쓰도록 이름으로만 짝짓는다."""
    return os.path.splitext(os.path.basename(str(name or "")))[0]


def to_num(x):
    if x is None or isinstance(x, bool):
        return None
    if isinstance(x, (int, float)):
        return float(x)
    s = re.sub(r"[,\s₩원]", "", str(x))
    try:
        return float(s)
    except ValueError:
        return None


def to_date(x):
    if x is None:
        return None
    s = str(x).strip()
    m = re.fullmatch(r"(\d{4})[-./](\d{1,2})[-./](\d{1,2})", s)
    if not m:
        m = re.fullmatch(r"(\d{4})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일", s)
    if not m:
        m2 = re.fullmatch(r"(\d{2})[-./](\d{1,2})[-./](\d{1,2})", s)
        if m2:
            y, mo, d = m2.groups()
            return f"{2000 + int(y):04d}-{int(mo):02d}-{int(d):02d}"
        return None
    y, mo, d = m.groups()
    return f"{int(y):04d}-{int(mo):02d}-{int(d):02d}"


def same_num(a, b):
    a, b = to_num(a), to_num(b)
    return a is not None and b is not None and abs(a - b) < 1e-6


def valid_box(b):
    if not isinstance(b, (list, tuple)) or len(b) != 4:
        return False
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in b):
        return False
    x1, y1, x2, y2 = b
    return x1 >= 0 and y1 >= 0 and x2 > x1 and y2 > y1


def field_parts(f):
    """예측 필드를 (값, review, box)로 푼다. 값만 있어도 허용."""
    if isinstance(f, dict):
        return f.get("value"), bool(f.get("review", False)), f.get("box")
    return f, False, None


def field_ok(name, expected, got):
    if got is None:
        return False
    if name == "supplier":
        return norm_text(expected) == norm_text(got)
    if name == "date":
        return to_date(got) == expected
    return same_num(expected, got)


# ---------- 품목 맞추기 ----------
def item_score(gt, pr):
    ratio = SequenceMatcher(None, norm_text(gt["item"]), norm_text(pr.get("item")) or "").ratio()
    eq = sum(1 for k in ITEM_NUMS if same_num(gt[k], pr.get(k)))
    return ratio + 0.5 * eq / 3


def match_items(gt_items, pr_items):
    """점수가 높은 쌍부터 짝짓는다. 순서가 바뀌어도, 한 줄이 빠져도 뒤가 밀리지 않는다."""
    pairs = []
    for i, g in enumerate(gt_items):
        for j, p in enumerate(pr_items):
            pairs.append((item_score(g, p), i, j))
    pairs.sort(key=lambda t: (-t[0], t[1], t[2]))
    used_g, used_p, matched = set(), set(), {}
    for sc, i, j in pairs:
        if sc < MATCH_THRESHOLD:
            break
        if i in used_g or j in used_p:
            continue
        used_g.add(i)
        used_p.add(j)
        matched[i] = j
    extra = [j for j in range(len(pr_items)) if j not in used_p]
    return matched, extra


# ---------- 채점 ----------
def score(gt_docs, pred_docs):
    preds = {}
    warnings = []
    for p in pred_docs:
        key = file_key(p.get("file"))
        if key in preds:
            warnings.append(f"예측에 같은 파일이 두 번 있음: {p.get('file')} (앞의 것만 사용)")
            continue
        preds[key] = p
    gt_files = {file_key(g["file"]) for g in gt_docs}
    for f in preds:
        if f not in gt_files:
            warnings.append(f"정답에 없는 파일이 예측에 있음(무시): {f}")

    errors, per_doc = [], []
    tot = {
        "item_slots": 0, "field_slots": 0,
        "item_errors": 0, "item_silent": 0, "item_caught": 0, "item_false_alarm": 0,
        "field_errors": 0, "field_silent": 0, "field_caught": 0, "field_false_alarm": 0,
        "flagged_slots": 0, "location_ok": 0, "location_slots": 0,
        "mismatch_ok": 0, "docs": 0, "seconds": [], "missing_seconds": 0,
    }
    by_diff = {}

    for g in gt_docs:
        tot["docs"] += 1
        p = preds.get(file_key(g["file"]))
        d = by_diff.setdefault(g["difficulty"], {"item_silent": 0, "item_errors": 0,
                                                 "field_silent": 0, "field_errors": 0, "docs": 0})
        d["docs"] += 1
        row = {"file": g["file"], "difficulty": g["difficulty"], "item_errors": 0, "item_silent": 0,
               "field_errors": 0, "field_silent": 0, "predicted": p is not None}
        if p is None:
            warnings.append(f"예측이 없는 문서: {g['file']} (전 항목을 누락으로 셈)")
        p = p or {}

        # 문서 필드
        pf = p.get("fields") or {}
        expected_fields = {"supplier": g["supplier"], "date": g["date"], "doc_total": g["doc_total"]}
        for name in FIELDS:
            tot["field_slots"] += 1
            tot["location_slots"] += 1
            val, review, box = field_parts(pf.get(name))
            ok = field_ok(name, expected_fields[name], val)
            if review:
                tot["flagged_slots"] += 1
            if valid_box(box):
                tot["location_ok"] += 1
            if not ok:
                tot["field_errors"] += 1
                d["field_errors"] += 1
                row["field_errors"] += 1
                if review:
                    tot["field_caught"] += 1
                else:
                    tot["field_silent"] += 1
                    d["field_silent"] += 1
                    row["field_silent"] += 1
                errors.append([g["file"], g["difficulty"], "필드", name, expected_fields[name], val,
                               "확인필요로 넘김" if review else "조용한 오류"])
            elif review:
                tot["field_false_alarm"] += 1

        # 품목
        gt_items = g["items"]
        pr_items = p.get("items") or []
        matched, extra = match_items(gt_items, pr_items)
        for i, gi in enumerate(gt_items):
            tot["item_slots"] += 1
            tot["location_slots"] += 1
            if i not in matched:
                tot["item_errors"] += 1
                tot["item_silent"] += 1
                d["item_errors"] += 1
                d["item_silent"] += 1
                row["item_errors"] += 1
                row["item_silent"] += 1
                errors.append([g["file"], g["difficulty"], "품목", gi["item"], json.dumps(gi, ensure_ascii=False),
                               None, "누락(조용한 오류)"])
                continue
            pi = pr_items[matched[i]]
            review = bool(pi.get("review", False))
            if review:
                tot["flagged_slots"] += 1
            if valid_box(pi.get("box")):
                tot["location_ok"] += 1
            diffs = []
            if norm_text(gi["item"]) != norm_text(pi.get("item")):
                diffs.append(("item", gi["item"], pi.get("item")))
            for k in ITEM_NUMS:
                if not same_num(gi[k], pi.get(k)):
                    diffs.append((k, gi[k], pi.get(k)))
            if diffs:
                tot["item_errors"] += 1
                d["item_errors"] += 1
                row["item_errors"] += 1
                if review:
                    tot["item_caught"] += 1
                else:
                    tot["item_silent"] += 1
                    d["item_silent"] += 1
                    row["item_silent"] += 1
                for k, e, got in diffs:
                    errors.append([g["file"], g["difficulty"], "품목", f"{gi['item']}.{k}", e, got,
                                   "확인필요로 넘김" if review else "조용한 오류"])
            elif review:
                tot["item_false_alarm"] += 1
        for j in extra:  # 정답에 없는 줄을 만들어 냄
            pi = pr_items[j]
            review = bool(pi.get("review", False))
            if review:
                tot["flagged_slots"] += 1
            tot["item_errors"] += 1
            d["item_errors"] += 1
            row["item_errors"] += 1
            if review:
                tot["item_caught"] += 1
            else:
                tot["item_silent"] += 1
                d["item_silent"] += 1
                row["item_silent"] += 1
            errors.append([g["file"], g["difficulty"], "품목", "(정답에 없는 줄)", None,
                           json.dumps({k: pi.get(k) for k in ("item",) + ITEM_NUMS}, ensure_ascii=False),
                           "확인필요로 넘김" if review else "조용한 오류"])

        # 합계 불일치 표시
        flag_ok = bool(p.get("total_mismatch", False)) == bool(g["total_mismatch"])
        if flag_ok:
            tot["mismatch_ok"] += 1
        else:
            errors.append([g["file"], g["difficulty"], "합계불일치표시", "total_mismatch",
                           g["total_mismatch"], p.get("total_mismatch"), "표시 틀림"])
        row["mismatch_flag_ok"] = flag_ok

        sec = p.get("seconds")
        if isinstance(sec, (int, float)) and not isinstance(sec, bool):
            tot["seconds"].append(float(sec))
            row["seconds"] = float(sec)
        else:
            tot["missing_seconds"] += 1
            row["seconds"] = None
        per_doc.append(row)

    slots = tot["item_slots"] + tot["field_slots"]
    review_ratio = tot["flagged_slots"] / slots if slots else 0.0
    loc_cov = tot["location_ok"] / tot["location_slots"] if tot["location_slots"] else 0.0
    mean_sec = sum(tot["seconds"]) / len(tot["seconds"]) if tot["seconds"] else None
    max_sec = max(tot["seconds"]) if tot["seconds"] else None

    criteria = {
        "1_품목_조용한오류": {"값": tot["item_silent"], "목표": f"<= {LIMITS['item_silent_errors_max']} (항목 {tot['item_slots']}개)",
                           "통과": tot["item_silent"] <= LIMITS["item_silent_errors_max"]},
        "2_필드_조용한오류": {"값": tot["field_silent"], "목표": f"<= {LIMITS['field_silent_errors_max']} (필드 {tot['field_slots']}개)",
                           "통과": tot["field_silent"] <= LIMITS["field_silent_errors_max"]},
        "3_확인필요_비율": {"값": round(review_ratio, 4), "목표": f"<= {LIMITS['review_ratio_max']:.0%} (슬롯 {slots}개)",
                         "통과": review_ratio <= LIMITS["review_ratio_max"]},
        "4_합계불일치_표시": {"값": f"{tot['mismatch_ok']}/{tot['docs']}장 일치", "목표": "전 문서 일치 (불일치 문서만 표시)",
                          "통과": tot["mismatch_ok"] == tot["docs"]},
        "5_위치_추적": {"값": round(loc_cov, 4), "목표": "100%", "통과": tot["location_ok"] == tot["location_slots"]},
        "6_처리시간": {"값": None if mean_sec is None else round(mean_sec, 2),
                    "최댓값": None if max_sec is None else round(max_sec, 2),
                    "목표": f"장당 평균 <= {LIMITS['seconds_per_doc_max']:.0f}초 (시간 누락 없음)",
                    "통과": mean_sec is not None and tot["missing_seconds"] == 0
                          and mean_sec <= LIMITS["seconds_per_doc_max"]},
    }
    summary = {
        "criteria": criteria,
        "기준1~5_모두_통과": all(criteria[k]["통과"] for k in list(criteria)[:5]),
        "전체_통과(1~6)": all(c["통과"] for c in criteria.values()),
        "참고": {
            "품목_오류_전체": tot["item_errors"], "품목_확인필요로_잡음": tot["item_caught"],
            "품목_불필요한_확인필요": tot["item_false_alarm"],
            "필드_오류_전체": tot["field_errors"], "필드_확인필요로_잡음": tot["field_caught"],
            "필드_불필요한_확인필요": tot["field_false_alarm"],
            "확인필요_슬롯": tot["flagged_slots"], "전체_슬롯": slots,
        },
        "난이도별": by_diff,
        "경고": warnings,
    }
    return summary, per_doc, errors


def render_md(summary):
    lines = ["| 기준 | 값 | 목표 | 통과 |", "|---|---|---|---|"]
    for k, c in summary["criteria"].items():
        v = c["값"] if "최댓값" not in c else f"{c['값']} (최대 {c['최댓값']})"
        lines.append(f"| {k} | {v} | {c['목표']} | {'통과' if c['통과'] else '**미달**'} |")
    lines.append("")
    lines.append(f"기준 1~5 모두 통과: {summary['기준1~5_모두_통과']} / 1~6 모두 통과: {summary['전체_통과(1~6)']}")
    for diff, d in summary["난이도별"].items():
        lines.append(f"- {diff} ({d['docs']}장): 품목 오류 {d['item_errors']}(조용한 {d['item_silent']}), "
                     f"필드 오류 {d['field_errors']}(조용한 {d['field_silent']})")
    r = summary["참고"]
    lines.append(f"- 확인필요로 잡은 오류: 품목 {r['품목_확인필요로_잡음']}, 필드 {r['필드_확인필요로_잡음']} / "
                 f"불필요한 확인필요: 품목 {r['품목_불필요한_확인필요']}, 필드 {r['필드_불필요한_확인필요']}")
    for w in summary["경고"]:
        lines.append(f"- 경고: {w}")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pred", required=True, help="predictions.json 경로")
    ap.add_argument("--gt", default="sample-invoices/정답.json", help="정답.json 경로")
    ap.add_argument("--out", required=True, help="결과를 저장할 새 폴더(이미 있으면 거부)")
    a = ap.parse_args()
    if os.path.exists(a.out):
        sys.exit(f"이미 있는 폴더라 덮어쓰지 않습니다: {a.out}")
    with open(a.gt, encoding="utf-8") as f:
        gt = json.load(f)
    with open(a.pred, encoding="utf-8") as f:
        pred = json.load(f)
    summary, per_doc, errors = score(gt, pred)
    os.makedirs(a.out)
    with open(os.path.join(a.out, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    with open(os.path.join(a.out, "errors.csv"), "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["파일", "난이도", "구분", "항목", "정답", "예측", "분류"])
        w.writerows(errors)
    with open(os.path.join(a.out, "per_doc.csv"), "w", encoding="utf-8-sig", newline="") as f:
        cols = ["file", "difficulty", "predicted", "item_errors", "item_silent", "field_errors",
                "field_silent", "mismatch_flag_ok", "seconds"]
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(per_doc)
    md = render_md(summary)
    with open(os.path.join(a.out, "summary.md"), "w", encoding="utf-8") as f:
        f.write(md + "\n")
    print(md)


if __name__ == "__main__":
    main()

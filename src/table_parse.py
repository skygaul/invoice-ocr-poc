"""OCR 글자 조각(텍스트, 점수, 박스)을 거래명세서 필드와 품목 줄로 조립하는 규칙 코드. 후보 A 전용.

양식 3종(표 제목줄에 품목·수량·단가·금액 또는 공급가액·세액이 있는 형태)을 겨냥한다.
개발은 dev-invoices(다른 시드)로만 했고 시험용 20장은 보지 않았다. 양식 3종의 모양은 개발 중에 봤다는 점을
결론에 적는다. 거래처가 다른 양식을 보내면 이 코드는 다시 손봐야 한다.

모든 임계값은 인자(Params)로 받는다.
"""
import math
import re
import statistics
from dataclasses import dataclass


@dataclass
class Params:
    conf_thresh: float = 0.90      # 이 점수보다 낮은 글자 조각이 값에 들어 있으면 '확인 필요'
    line_gap_ratio: float = 0.55   # 같은 줄로 묶는 y 간격(글자 높이 중앙값 대비)
    tax_rate: float = 0.10         # 세액 교차검산에 쓰는 세율
    tolerance: int = 0             # 수량×단가와 금액의 허용 차이(원)


def _norm(s):
    return re.sub(r"\s+", "", str(s)).replace("−", "-").replace("–", "-").replace("—", "-")


def _num(s):
    t = _norm(s).replace(",", "")
    m = re.search(r"-?\d+(?:\.\d+)?", t)
    if not m:
        return None
    v = float(m.group())
    return int(v) if v.is_integer() else v


def _is_numeric_token(s):
    t = _norm(s).replace(",", "")
    return re.fullmatch(r"-?\d+(\.\d+)?[A-Za-z가-힣]{0,2}", t) is not None


def _to_date(s):
    t = str(s)
    m = re.search(r"(\d{4})\s*[-./]\s*(\d{1,2})\s*[-./]\s*(\d{1,2})", t)
    if not m:
        m = re.search(r"(\d{4})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일", t)
    if m:
        y, mo, d = m.groups()
        return f"{int(y):04d}-{int(mo):02d}-{int(d):02d}"
    m = re.search(r"(?<!\d)(\d{2})\s*[-./]\s*(\d{1,2})\s*[-./]\s*(\d{1,2})(?!\d)", t)
    if m:
        y, mo, d = m.groups()
        return f"{2000 + int(y):04d}-{int(mo):02d}-{int(d):02d}"
    return None


def _union(boxes):
    return [int(min(b[0] for b in boxes)), int(min(b[1] for b in boxes)),
            int(max(b[2] for b in boxes)), int(max(b[3] for b in boxes))]


def estimate_angle(tokens):
    """글자 조각 윗변 기울기의 중앙값(도). 기울어진 스캔에서 줄을 제대로 묶으려고 쓴다."""
    angs = []
    for t in tokens:
        p = t.get("poly")
        if p is None:
            continue
        dx, dy = p[1][0] - p[0][0], p[1][1] - p[0][1]
        if dx > 80:
            angs.append(math.degrees(math.atan2(dy, dx)))
    return statistics.median(angs) if angs else 0.0


def _rotate_center(cx, cy, ang_deg, ox, oy):
    a = math.radians(-ang_deg)
    x, y = cx - ox, cy - oy
    return x * math.cos(a) - y * math.sin(a) + ox, x * math.sin(a) + y * math.cos(a) + oy


HEADER_KEYS = {
    "name": ("품목", "품명"), "qty": ("수량",), "price": ("단가",),
    "amount": ("금액", "공급가액"), "tax": ("세액",), "unit": ("단위",), "spec": ("규격",), "no": ("번호", "no"),
}


def _header_role(text):
    t = _norm(text).lower()
    for role, keys in HEADER_KEYS.items():
        if t in keys:
            return role
    return None


UNIT_WORDS = {"ea", "kg", "g", "l", "ml", "box", "박스", "봉", "개", "통", "단", "팩", "속", "판", "병", "캔", "마리", "식"}


def _jamo(ch):
    c = ord(ch) - 0xAC00
    return (c // 588, (c % 588) // 28, c % 28) if 0 <= c < 11172 else None


def _syl_dist(a, b):
    """두 글자의 모양 차이(초성·중성·종성 중 다른 개수). 한글이 아니면 최대(3)."""
    ja, jb = _jamo(a), _jamo(b)
    if ja is None or jb is None:
        return 3
    return sum(x != y for x, y in zip(ja, jb))


def _fuzzy_role(text):
    """표 제목 글자의 역할. 정확히 같으면 (역할, False), 한 글자만 다르면 (역할, True), 아니면 (None, False).
    '단기'는 단가·단위 둘 다 한 글자 차이라서, 자음·모음이 더 비슷한 쪽을 고르고 동점이면 포기한다."""
    t = _norm(text).lower()
    r = _header_role(t)
    if r:
        return r, False
    if not 2 <= len(t) <= 4:
        return None, False
    cands = []
    for role, keys in HEADER_KEYS.items():
        if role == "no":
            continue
        for k in keys:
            if len(k) != len(t):
                continue
            diff = [(a, b) for a, b in zip(k, t) if a != b]
            if len(diff) == 1:
                cands.append((_syl_dist(*diff[0]), role))
    if not cands:
        return None, False
    cands.sort()
    if len(cands) > 1 and cands[0][0] == cands[1][0] and cands[0][1] != cands[1][1]:
        return None, False
    return cands[0][1], True


def _split_header_token(t):
    """'수양단위'처럼 붙어서 읽힌 제목을 역할별 조각으로 쪼갠다(조각 위치는 글자 수 비례). 못 쪼개면 []."""
    s = _norm(t["text"])
    n = len(s)
    if not 4 <= n <= 12:
        return []
    parts, i = [], 0
    while i < n:
        for L in (4, 2):
            chunk = s[i:i + L]
            if len(chunk) == L and _fuzzy_role(chunk)[0]:
                parts.append((_fuzzy_role(chunk)[0], i, L))
                i += L
                break
        else:
            return []
    if len(parts) < 2:
        return []
    width = t["box"][2] - t["box"][0]
    left = t["cx"] - width / 2
    out = []
    for role, i, L in parts:
        v = dict(t)
        v["text"] = s[i:i + L]
        v["cx"] = left + (i + L / 2) / n * width
        out.append((role, v))
    return out


def _line_roles(ln):
    roles, fuzzy = {}, False
    for t in ln:
        r, fz = _fuzzy_role(t["text"])
        if r:
            roles[r] = t
            fuzzy = fuzzy or fz
            continue
        for role, v in _split_header_token(t):
            roles[role] = v
            fuzzy = True
    return roles, fuzzy


def _is_num_like(t):
    s = _norm(t["text"])
    return _is_numeric_token(s) or s == "면세"


def _infer_columns(lines, img_w):
    """제목줄을 못 읽었을 때: 숫자 조각이 3개 이상인 줄을 품목 줄로 보고 숫자 칸의 위치로 열을 추정한다.
    번호 칸(왼쪽의 1, 2, 3…)을 빼고 남은 숫자가 3개면 수량·단가·금액, 4개면 세액까지.
    추정이 맞는지는 수량×단가=금액이 맞는 줄이 절반 이상일 때만 인정한다. 반환: (열, 첫 줄, 끝 줄+1) 또는 None."""
    cand = [i for i, ln in enumerate(lines) if sum(map(_is_num_like, ln)) >= 3]
    if len(cand) < 2:
        return None
    rows = []
    for i in cand:
        ln = lines[i]
        nums = sorted([t for t in ln if _is_num_like(t)], key=lambda t: t["cx"])
        ids = {id(t) for t in nums}
        names = [t for t in ln if id(t) not in ids]
        no_tok = None
        if len(nums) >= 4 and re.fullmatch(r"\d+", _norm(nums[0]["text"])):
            left_limit = min((t["cx"] for t in names), default=0.15 * img_w)
            if nums[0]["cx"] < left_limit:
                no_tok, nums = nums[0], nums[1:]
        rows.append((i, no_tok, nums))
    mode = statistics.mode(len(r[2]) for r in rows)
    if mode not in (3, 4):
        return None
    good = [r for r in rows if len(r[2]) == mode]
    cols = {}
    for k, role in enumerate(("qty", "price", "amount", "tax")[:mode]):
        cols[role] = {"cx": statistics.median(r[2][k]["cx"] for r in good), "score": 1.0, "box": None}
    nos = [r[1]["cx"] for r in good if r[1] is not None]
    if nos:
        cols["no"] = {"cx": statistics.median(nos), "score": 1.0, "box": None}
    ok = 0
    for r in good:
        q, pr, am = (_num(r[2][k]["text"]) for k in range(3))
        if None not in (q, pr, am) and round(q * pr) == am:
            ok += 1
    if ok < max(2, 0.5 * len(good)):
        return None
    first, last = cand[0], cand[-1]
    end = last + 1
    pitch = statistics.median(lines_cy(lines, cand[k + 1]) - lines_cy(lines, cand[k]) for k in range(len(cand) - 1))
    if end < len(lines):  # 마지막 품목명이 줄바꿈됐으면 바로 아래 글자만 있는 줄을 붙인다
        nxt = lines[end]
        txt = _norm("".join(t["text"] for t in nxt))
        gap = lines_cy(lines, end) - lines_cy(lines, last)
        if not any(map(_is_num_like, nxt)) and gap <= 1.6 * pitch and not re.search(r"합계|계|비고|※", txt):
            end += 1
    return cols, first, end


def lines_cy(lines, i):
    return statistics.mean(t["cy"] for t in lines[i])


def parse(tokens, img_w, img_h, p: Params):
    """tokens: [{'text','score','box':[x1,y1,x2,y2],'poly':[[x,y]*4] (선택)}]
    반환: (fields, items, doc_flags, notes)"""
    notes = []
    ang = estimate_angle(tokens)
    for t in tokens:
        b = t["box"]
        t["cx"], t["cy"] = _rotate_center((b[0] + b[2]) / 2, (b[1] + b[3]) / 2, ang, img_w / 2, img_h / 2)
        t["h"] = b[3] - b[1]
    if not tokens:
        return ({k: {"value": None, "review": True, "box": None} for k in ("supplier", "date", "doc_total")},
                [], {"total_mismatch": False}, ["글자를 하나도 못 읽음"])
    med_h = statistics.median(t["h"] for t in tokens)

    # 줄 묶기
    toks = sorted(tokens, key=lambda t: t["cy"])
    lines, cur = [], [toks[0]]
    for t in toks[1:]:
        if abs(t["cy"] - statistics.mean(x["cy"] for x in cur)) <= p.line_gap_ratio * med_h:
            cur.append(t)
        else:
            lines.append(cur)
            cur = [t]
    lines.append(cur)
    for ln in lines:
        ln.sort(key=lambda t: t["cx"])
    line_y = [statistics.mean(t["cy"] for t in ln) for ln in lines]

    # 표 제목줄: 수량·단가·금액을 함께 가진 줄. 한 글자 오독과 붙어서 읽힌 제목도 인정한다.
    hdr_idx, cols, fallback = None, {}, None
    for i, ln in enumerate(lines):
        roles, fuzzy = _line_roles(ln)
        if "qty" in roles and "price" in roles and "amount" in roles:
            hdr_idx, cols = i, roles
            if fuzzy:
                notes.append("표 제목 글자를 비슷한 글자로 인정함")
            break
    if hdr_idx is None:  # 제목줄을 못 읽음: 숫자 칸의 위치로 열을 추정하고, 수량×단가=금액으로 검증한다
        inferred = _infer_columns(lines, img_w)
        if inferred is None:
            notes.append("표 제목줄을 못 찾음")
            return ({k: {"value": None, "review": True, "box": None} for k in ("supplier", "date", "doc_total")},
                    [], {"total_mismatch": False}, notes)
        cols, first, end_idx = inferred
        hdr_idx = first - 1
        fallback = {"end": end_idx}
        notes.append("표 제목줄을 못 찾아 숫자 칸의 위치로 열을 추정함")
    has_tax = "tax" in cols
    num_cols = [r for r in ("qty", "price", "amount", "tax") if r in cols]
    anchors = {r: cols[r]["cx"] for r in num_cols}
    name_anchor = cols["name"]["cx"] if "name" in cols else None

    def nearest_num_col(t):
        return min(num_cols, key=lambda r: abs(anchors[r] - t["cx"]))

    # 합계줄: 제목줄 아래에서 '합계'가 들어간 마지막 줄. 품목 영역은 합계류 글자가 처음 나오는 줄에서 끝난다.
    total_idx, end = None, None
    for i in range(hdr_idx + 1, len(lines)):
        joined = _norm("".join(t["text"] for t in lines[i]))
        if "합계" in joined:
            total_idx = i
        if end is None and ("합계" in joined or joined.startswith(("공급가액계", "세액계", "소계"))):
            end = i
    if end is None:
        end = len(lines)
    if fallback is not None:  # 제목 없이 추정한 경우 품목 영역은 숫자 줄이 있는 범위로 한정한다
        end = fallback["end"]
    skip_x = [cols[r]["cx"] for r in ("spec", "unit") if r in cols]

    # 품목 줄
    items, row_lines = [], []
    for i in range(hdr_idx + 1, end):
        ln = lines[i]
        by_col = {r: [] for r in num_cols}
        name_toks = []
        for t in ln:
            txt = _norm(t["text"])
            if _is_numeric_token(txt) or (has_tax and txt == "면세"):
                if cols.get("no") and abs(cols["no"]["cx"] - t["cx"]) < 45 and \
                        abs(cols["no"]["cx"] - t["cx"]) < min(abs(a - t["cx"]) for a in anchors.values()) * 0.4:
                    continue  # 번호 칸
                by_col[nearest_num_col(t)].append(t)
            elif fallback is not None and txt.lower() in UNIT_WORDS:
                continue  # 규격·단위 칸 위치를 모를 때는 단위 글자(EA, kg, 박스…)로 걸러낸다
            elif any(abs(sx - t["cx"]) < 55 for sx in skip_x):
                continue  # 규격·단위 칸의 글자(EA, kg)는 품목명이 아님
            elif _header_role(txt) is None and t["cx"] < (anchors["qty"] - 30) and \
                    (name_anchor is None or t["cx"] > name_anchor - 260):
                name_toks.append(t)
        # 품목명만 있는 이어지는 줄(줄바꿈된 이름): 앞 줄에 붙인다
        if not any(by_col[r] for r in ("qty", "price", "amount")):
            if name_toks and items:
                items[-1]["_name_toks"] += name_toks
                items[-1]["_toks"] += name_toks
                items[-1]["_cont"] = True
            continue
        toks_here = [t for r in num_cols for t in by_col[r]] + name_toks
        items.append({"_by_col": by_col, "_name_toks": name_toks, "_toks": toks_here, "_cont": False})
        row_lines.append(i)

    out_items = []
    for it in items:
        by_col = it["_by_col"]
        review, why = False, []
        num_ok = True  # 숫자 칸을 믿을 수 있는가(합계 불일치 판단에 씀)
        vals = {}
        for r in ("qty", "price", "amount"):
            ts = by_col[r]
            if len(ts) != 1:
                review = True
                num_ok = False
                why.append(f"{r} 칸에 조각 {len(ts)}개")
            vals[r] = _num(ts[-1]["text"]) if ts else None
            if vals[r] is None:
                review = True
                num_ok = False
        tax_tok = by_col["tax"] if has_tax else []
        tax_val = None
        if has_tax:
            if len(tax_tok) != 1:
                review = True
                why.append("세액 칸 이상")
            elif _norm(tax_tok[0]["text"]) == "면세":
                tax_val = 0
            else:
                tax_val = _num(tax_tok[0]["text"])
        name = "".join(_norm(t["text"]) for t in sorted(it["_name_toks"], key=lambda t: (round(t["cy"] / max(med_h, 1)), t["cx"])))
        if not name:
            review = True
            why.append("품목명 없음")
        if any(t["score"] < p.conf_thresh for t in it["_toks"]):
            review = True
            why.append("낮은 신뢰도")
        if None not in (vals["qty"], vals["price"], vals["amount"]):
            if abs(round(vals["qty"] * vals["price"]) - vals["amount"]) > p.tolerance:
                review = True
                num_ok = False
                why.append("수량×단가≠금액")
        if has_tax and vals["amount"] is not None and tax_val is not None:
            exp = round(vals["amount"] * p.tax_rate)
            if tax_val not in (0, exp):
                review = True
                why.append("세액 이상")
        out_items.append({"item": name, "qty": vals["qty"], "unit_price": vals["price"], "amount": vals["amount"],
                          "review": review, "box": _union([t["box"] for t in it["_toks"]]),
                          "_tax": tax_val, "_why": why, "_num_ok": num_ok})

    # 문서 필드
    def field(value, toks, extra_review=False):
        if not toks:
            return {"value": value, "review": True, "box": None}
        low = any(t["score"] < p.conf_thresh for t in toks)
        return {"value": value, "review": bool(low or value is None or extra_review),
                "box": _union([t["box"] for t in toks])}

    head_lines = lines[:max(hdr_idx, 0)]
    # 거래처
    sup_val, sup_toks = None, []
    for ln in head_lines:
        for t in ln:
            m = re.search(r"(?:상호|공급자)\s*[:：]\s*(.+)$", t["text"])
            if m:
                sup_val, sup_toks = _norm(m.group(1)), [t]
    if sup_val is None:
        skip = ("거래명세표", "거래명세서", "납품서", "납품서", "공급받는자", "보관용")
        for ln in head_lines:
            for t in ln:
                if not any(k in _norm(t["text"]) for k in skip) and re.search(r"[가-힣]", t["text"]):
                    sup_val, sup_toks = _norm(t["text"]), [t]
                    break
            if sup_val:
                break
    # 날짜
    date_val, date_toks = None, []
    for ln in head_lines:
        for t in ln:
            d = _to_date(t["text"])
            if d and ("일자" in _norm(t["text"]) or date_val is None):
                date_val, date_toks = d, [t]
    # 합계
    tot_val, tot_toks = None, []
    if total_idx is not None:
        cand = [t for t in lines[total_idx] if _num(t["text"]) is not None and re.search(r"\d", t["text"])]
        if cand:
            tot_toks = [cand[-1]]
            tot_val = _num(cand[-1]["text"])

    sum_amount = sum(i["amount"] for i in out_items if i["amount"] is not None)
    sum_tax = sum(i["_tax"] or 0 for i in out_items) if has_tax else 0
    differs = tot_val is not None and bool(out_items) and (sum_amount + sum_tax != tot_val)
    reliable = bool(out_items) and all(i["_num_ok"] for i in out_items)
    # 품목 숫자를 못 믿으면 '공급자 오류'라고 단정하지 않고, 합계만 사람이 확인하게 한다.
    mismatch = differs and reliable
    fields = {"supplier": field(sup_val, sup_toks), "date": field(date_val, date_toks),
              "doc_total": field(tot_val, tot_toks, extra_review=bool(differs))}
    if differs:
        notes.append(f"품목 합({sum_amount}+세액{sum_tax}) != 합계({tot_val})" +
                     ("" if reliable else " — 품목 숫자를 못 믿어 불일치 표시는 보류"))
    for i in out_items:
        i.pop("_tax", None)
        i.pop("_num_ok", None)
    return fields, out_items, {"total_mismatch": bool(mismatch)}, notes

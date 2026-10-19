"""
금정구 새마을금고 창구 금리 수집기
- 새마을금고중앙회 '금고위치안내'에서 금고 목록과 금리표를 가져와 data.json 으로 저장해요.
- 비교 지역을 바꾸려면 아래 REGIONS 만 고치면 돼요. 예) [("부산", "금정구"), ("부산", "동래구")]
"""
import json
import re
import sys
import time
from datetime import datetime, timedelta, timezone

import requests
from bs4 import BeautifulSoup

REGIONS = [("부산", "금정구")]
PRODUCTS = {"dep": ("정기예탁금", 13), "sav": ("정기적금", 14)}  # 키: (상품명, gubuncode)

BASE = "https://www.kfcc.co.kr/map"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
}
KST = timezone(timedelta(hours=9))


def get(url, params, tries=3):
    for i in range(tries):
        try:
            r = requests.get(url, params=params, headers=HEADERS, timeout=20)
            r.raise_for_status()
            r.encoding = r.apparent_encoding or "utf-8"
            return r.text
        except Exception as e:  # 가끔 실패해서 재시도
            print(f"  재시도 {i + 1}/{tries}: {e}", file=sys.stderr)
            time.sleep(3)
    raise RuntimeError(f"요청 실패: {url} {params}")


def parse_bank_list(html):
    """list.do 페이지 → [{cd, nm, type, addr, tel}] (본점 기준, 금고코드 중복 제거)"""
    soup = BeautifulSoup(html, "html.parser")
    banks = {}
    for tr in soup.select("tr[id]"):
        info = {s.get("title"): s.get_text(strip=True) for s in tr.select("span[title]")}
        cd = info.get("gmgoCd")
        if not cd:
            continue
        if cd in banks and info.get("divNm") != "본점":
            continue
        banks[cd] = {
            "cd": cd,
            "nm": info.get("gmgoNm") or info.get("name", ""),
            "type": info.get("gmgoType", ""),
            "addr": info.get("addr", ""),
            "tel": info.get("telephone", ""),
        }
    return list(banks.values())


PERIOD_RE = re.compile(r"(\d+)\s*개?월\s*이상")
RATE_RE = re.compile(r"연\s*([\d.]+)\s*%")
DATE_RE = re.compile(r"\d{4}/\d{2}/\d{2}")


def parse_rates(html, product_name):
    """goods_19.do 페이지 → ({개월: 금리}, 기준일)
    기본이율 표(#divTmp1)에서 상품명이 정확히 일치하는 표만 읽어요.
    한 줄에 금리가 여러 개(월지급식/만기지급식)면 마지막 값 = 만기지급식을 써요."""
    soup = BeautifulSoup(html, "html.parser")
    bd = soup.select_one(".base-date")
    m = DATE_RE.search(bd.get_text()) if bd else None
    base_date = m.group(0) if m else ""

    for box in soup.select(".tblWrap"):
        tmp1 = box.select_one("#divTmp1") or box
        tit = tmp1.select_one(".tbl-tit")
        title = tit.get_text(strip=True) if tit else ""
        title = re.split(r"[<(]", title)[0].strip()
        if title != product_name:
            continue
        tiers = {}
        for tr in tmp1.select("tbody tr"):
            text = " ".join(td.get_text(" ", strip=True) for td in tr.find_all("td"))
            pm, rates = PERIOD_RE.search(text), RATE_RE.findall(text)
            if pm and rates:
                tiers[int(pm.group(1))] = float(rates[-1])
        if tiers:
            return tiers, base_date
    return {}, base_date


def main():
    try:
        with open("data.json", encoding="utf-8") as f:
            old = json.load(f)
    except Exception:
        old = {}
    old_rates = {b["cd"]: b for b in old.get("banks", [])}

    banks = []
    for r1, r2 in REGIONS:
        print(f"[목록] {r1} {r2}")
        lst = parse_bank_list(get(f"{BASE}/list.do", {"r1": r1, "r2": r2}))
        for b in lst:
            b["region"] = r2
        banks += lst

    base_date = ""
    for b in banks:
        for key, (name, gubun) in PRODUCTS.items():
            html = get(f"{BASE}/goods_19.do", {"OPEN_TRMID": b["cd"], "gubuncode": gubun})
            tiers, d = parse_rates(html, name)
            b[key] = {str(k): v for k, v in sorted(tiers.items())}
            base_date = base_date or d
            time.sleep(0.7)  # 서버에 부담 주지 않게 천천히
        print(f"  {b['nm']}: 예탁금 {len(b['dep'])}구간, 적금 {len(b['sav'])}구간")
        # 직전 기준일 금리 보관 → 페이지에서 변동 표시
        prev = old_rates.get(b["cd"])
        if prev and old.get("baseDate") != base_date:
            b["prev"] = {k: prev.get(k, {}) for k in PRODUCTS}
            b["prevDate"] = old.get("baseDate", "")
        elif prev and "prev" in prev:
            b["prev"], b["prevDate"] = prev["prev"], prev.get("prevDate", "")

    if not any(b["dep"] or b["sav"] for b in banks):
        sys.exit("금리를 하나도 못 읽었어요. 사이트 구조가 바뀌었을 수 있어요.")

    out = {
        "baseDate": base_date,
        "updatedAt": datetime.now(KST).strftime("%Y-%m-%d %H:%M"),
        "regions": [f"{a} {b}" for a, b in REGIONS],
        "banks": banks,
    }
    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"저장 완료: {len(banks)}곳, 기준일 {base_date}")


if __name__ == "__main__":
    main()

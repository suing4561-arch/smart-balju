"""
나이스오케이포스 ASP 매출 자동 수집 스크래퍼 v9
- 환경변수 기반 (GitHub Actions / 로컬 .env 모두 지원)
- Firebase Realtime DB 자동 저장
- 대화형 input() 제거 (CI/CD 호환)
- exit code 반환 (실패 시 GitHub Actions 알림)
"""

import os
import sys
import json
import time
import re
import datetime
import requests
from typing import Optional

# ─────────────────────────────────────────────────────────────
# CONFIG — 모두 환경변수에서 로드
# ─────────────────────────────────────────────────────────────
CONFIG = {
    "id":       os.environ.get("OKPOS_ID", ""),
    "pw":       os.environ.get("OKPOS_PW", ""),
    "base_url": os.environ.get("OKPOS_BASE_URL", "https://nice.okpos.co.kr"),
}

FIREBASE_DB_URL          = os.environ.get("FIREBASE_DB_URL", "")
FIREBASE_CREDENTIAL_PATH = os.environ.get("FIREBASE_CREDENTIAL_PATH", "firebase-credentials.json")
FIREBASE_ENABLED         = bool(FIREBASE_DB_URL) and os.path.exists(FIREBASE_CREDENTIAL_PATH)



def _init_firebase():
    """firebase_admin 초기화 (중복 호출 안전)."""
    import firebase_admin
    from firebase_admin import credentials, db
    if not firebase_admin._apps:
        cred = credentials.Certificate(FIREBASE_CREDENTIAL_PATH)
        firebase_admin.initialize_app(cred, {"databaseURL": FIREBASE_DB_URL})
    return db


def load_hq_accounts_from_firebase() -> list:
    """users 에서 role='hq' 이고 okposId·okposPw 가 모두 있는 본사 목록 반환.
    Firebase 꺼짐·읽기 실패 시 빈 리스트."""
    if not FIREBASE_ENABLED:
        return []
    try:
        db = _init_firebase()
        snapshot = db.reference("users").get()
        if not snapshot:
            return []
        accounts = []
        for uid, u in snapshot.items():
            if not isinstance(u, dict):
                continue
            if u.get("role") != "hq":
                continue
            okpos_id = str(u.get("okposId", "")).strip()
            okpos_pw = str(u.get("okposPw", "")).strip()
            if not okpos_id or not okpos_pw:
                continue
            accounts.append({
                "uid":      uid,
                "label":    u.get("businessName") or u.get("name") or uid,
                "brand_id": str(u.get("brandId", "")).strip(),
                "okpos_id": okpos_id,
                "okpos_pw": okpos_pw,
            })
        print(f"[HQ 계정 로드] {len(accounts)}개: "
              + ", ".join(a["label"] for a in accounts))
        return accounts
    except Exception as e:
        print(f"❌ HQ 계정 로드 실패: {e}")
        return []


def load_menu_shops_from_firebase(brand_id: str = "") -> list:
    """hq_franchises 에서 okposShopCd 가 있는 가맹점만 추려 반환.
    brand_id 지정 시 해당 브랜드 소속 매장만, 빈 문자열이면 전체.
    Firebase 연동이 꺼져있거나 읽기 실패 시 빈 리스트."""
    try:
        db = _init_firebase()
        snapshot = db.reference("hq_franchises").get()
        if not snapshot:
            print("[메뉴 매장 로드] hq_franchises 데이터 없음")
            return []

        shops = []
        for fid, fdata in snapshot.items():
            if not isinstance(fdata, dict):
                continue
            shop_cd = str(fdata.get("okposShopCd", "")).strip()
            if not shop_cd:
                continue
            if brand_id and str(fdata.get("brandId", "")).strip() != brand_id:
                continue
            shops.append({
                "shop_cd": shop_cd,
                "shop_nm": str(fdata.get("name", fid)).strip(),
            })

        label = f"브랜드={brand_id}" if brand_id else "전체 브랜드"
        if not shops:
            print(f"[메뉴 매장 로드] okposShopCd 있는 가맹점 없음 ({label})")
        else:
            names = ", ".join(f"{s['shop_nm']}({s['shop_cd']})" for s in shops)
            print(f"[메뉴 매장 로드] {len(shops)}개 ({label}): {names}")
        return shops
    except Exception as e:
        print(f"❌ hq_franchises 로드 실패: {e}")
        return []


def get_hidden_fields(html: str) -> dict:
    fields = {}
    for p in [
        r'<input[^>]*type=["\']hidden["\'][^>]*name=["\']([^"\']+)["\'][^>]*value=["\']([^"\']*)["\']',
        r'<input[^>]*name=["\']([^"\']+)["\'][^>]*type=["\']hidden["\'][^>]*value=["\']([^"\']*)["\']',
        r'<input[^>]*name=["\']([^"\']+)["\'][^>]*value=["\']([^"\']*)["\'][^>]*type=["\']hidden["\']',
    ]:
        for name, val in re.findall(p, html, re.IGNORECASE):
            fields[name] = val
    return fields


def get_form_action(html: str, base_url: str) -> Optional[str]:
    m = re.search(r'<form[^>]*action=["\']([^"\']+)["\']', html, re.IGNORECASE)
    if not m:
        return None
    action = m.group(1)
    if action.startswith("http"):   return action
    if action.startswith("/"):      return base_url + action
    return base_url + "/login/" + action


def login(session: requests.Session) -> bool:
    base = CONFIG["base_url"]
    uid  = CONFIG["id"]
    upw  = CONFIG["pw"]

    if not uid or not upw:
        print("❌ OKPOS_ID / OKPOS_PW 환경변수가 비어있습니다")
        return False

    hdr  = lambda ref: {
        "Referer": ref, "Origin": base,
        "Content-Type": "application/x-www-form-urlencoded",
    }

    r1 = session.get(f"{base}/login/login_form.jsp", timeout=15)
    h1 = get_hidden_fields(r1.text)
    print(f"[S1] {r1.status_code}")

    r2 = session.post(f"{base}/login/login_check.jsp",
                      data={"user_id": uid, "user_pwd": upw,
                            "id_chk": "", "auto_login_chk": "", **h1},
                      headers=hdr(f"{base}/login/login_form.jsp"),
                      timeout=15, allow_redirects=True)
    h2 = get_hidden_fields(r2.text)
    a3 = get_form_action(r2.text, base) or f"{base}/login/login_check_action.jsp"
    print(f"[S2] {r2.status_code} → {a3.split('/')[-1]}")

    r3 = session.post(a3,
                      data={"user_id": uid, "user_pwd": upw, **h1, **h2},
                      headers=hdr(r2.url),
                      timeout=15, allow_redirects=True)
    print(f"[S3] {r3.status_code}")

    if "error.jsp" in r3.text:
        print("❌ 로그인 실패 — 아이디/비밀번호 확인")
        return False

    a4 = get_form_action(r3.text, base)
    h3 = get_hidden_fields(r3.text)
    if a4 and a4 != a3 and "error" not in a4:
        r4 = session.post(a4, data={"user_id": uid, "user_pwd": upw, **h3},
                          headers=hdr(r3.url), timeout=15, allow_redirects=True)
        print(f"[S4] {r4.status_code}")

    time.sleep(1)
    chk = session.get(f"{base}/login/top_frame.jsp", timeout=15)
    if "로그아웃" in chk.text or "divTopFrameHead" in chk.text:
        print("✅ 로그인 성공!")
        return True
    print("⚠️  로그인 불명확 — 계속 진행")
    return True


def fetch_sales(session: requests.Session, date_from: str,
                date_to: str, shop_cd: str = "") -> Optional[list]:
    base = CONFIG["base_url"]

    r_page = session.get(f"{base}/sale/day/day_jump010.jsp", timeout=15)
    print(f"[매출페이지] {r_page.status_code}, {len(r_page.content):,} bytes")

    token_key, token_val = "", ""
    hidden = get_hidden_fields(r_page.text)
    for k, v in hidden.items():
        if re.match(r'^[0-9a-f]{8}-[0-9a-f]{4}-', v, re.IGNORECASE):
            token_key, token_val = k, v
            print(f"[토큰] {k[:16]}... = {v[:16]}...")
            break

    time.sleep(0.5)

    payload = {
        "S_CONTROLLER": "sale.day.day_total010",
        "S_METHOD":     "search",
        "SHEETSEQ":     "1",
        "S_SAVENAME": (
            "SALE_DATE|SALE_YOIL|SHOP_CD|SHOP_NM|"
            "TOT_SALE_AMT|TOT_DC_AMT|DCM_SALE_AMT|"
            "NO_TAX_SALE_AMT|VAT_AMT|TOT_SALE_CNT|"
            "DCM_TOT_RATE|FD_GST_CNT_T|SALE_PER_GST|"
            "FD_GST_CNT_1|FD_GST_CNT_2|FD_GST_CNT_3|FD_GST_CNT_4|"
            "TABLE_CNT|SALE_PER_TABLE|GST_PER_TABLE|"
            "SVC_TIP_AMT|TOT_ETC_AMT|TOT_PAY_AMT|"
            "CASH_AMT2|CASH_BILL_AMT|CRD_CARD_AMT|"
            "WES_AMT|TK_GFT_AMT|TK_FOD_AMT|CST_POINT_AMT|"
            "JCD_CARD_AMT|KP_AMT|"
            "P01_AMT|P02_AMT|P03_AMT|P04_AMT|P05_AMT|"
            "P06_AMT|P07_AMT|P08_AMT|P09_AMT|P10_AMT|"
            "P11_AMT|P12_AMT|P13_AMT|P14_AMT|P15_AMT|"
            "P16_AMT|P17_AMT|P18_AMT|P19_AMT|P20_AMT|"
            "P21_AMT|P22_AMT|P23_AMT|P24_AMT|P25_AMT|"
            "P26_AMT|P27_AMT|P28_AMT|P29_AMT|P30_AMT|"
            "P99_AMT|RFC_AMT|MCP_AMT|PCD_CARD_AMT|EGIFT_AMT|"
            "O2O_AMT|ETC_PAY_AMT|"
            "GEN_DCM_SALE_AMT|GEN_DCM_SALE_RATE|"
            "PKG_DCM_SALE_AMT|PKG_DCM_SALE_RATE|"
            "DLV_DCM_SALE_AMT|DLV_DCM_SALE_RATE|"
            "DC_GEN_AMT|DC_SVC_AMT|DC_PCD_AMT|DC_CPN_AMT|"
            "DC_CST_AMT|DC_TFD_AMT|DC_PACK_AMT|DC_YAP_AMT|"
            "A_TAX_RFND_AMT|D_TAX_RFND_AMT|D_TAX_RFND_FEE"
        ),
        "S_ORDERBY":    "",
        "date1_1":      date_from,
        "date1_2":      date_to,
        "date_period1": "366",
        "ss_SHOP_CD":   shop_cd,
        "ss_SHOP_NM":   "" if shop_cd else "전체",
        "ss_SHOP_INFO": "[]",
    }
    if token_key:
        payload[token_key] = token_val

    resp = session.post(
        f"{base}/sale/day/ddd.htmlSheetAction",
        data=payload,
        headers={
            "Referer":      f"{base}/sale/day/day_jump010.jsp",
            "Origin":       base,
            "Content-Type": "application/x-www-form-urlencoded",
        },
        timeout=30
    )
    print(f"[매출 조회] {resp.status_code}, {len(resp.content):,} bytes")

    if resp.status_code != 200:
        return None

    return parse_json(resp.text, date_from)


def parse_json(raw: str, date: str) -> list:
    """JSON 형식 응답 파싱"""
    try:
        data = json.loads(raw)
    except Exception as e:
        print(f"[파싱 오류] JSON 파싱 실패: {e}")
        print(f"[원문] {raw[:300]}")
        return []

    rows = data.get("Data", [])
    if not rows:
        print(f"[파싱] Data 없음. 전체 키: {list(data.keys())}")
        print(f"[원문] {raw[:300]}")
        return []

    results = []
    for row in rows:
        shop_cd = row.get("SHOP_CD", "")
        if not shop_cd:
            continue

        def to_int(v):
            try:
                return int(float(str(v).replace(",", "")))
            except:
                return 0

        results.append({
            "SALE_DATE":    row.get("SALE_DATE", date),
            "SALE_YOIL":    row.get("SALE_YOIL", ""),
            "SHOP_CD":      shop_cd,
            "SHOP_NM":      row.get("SHOP_NM", ""),
            "TOT_SALE_AMT": to_int(row.get("TOT_SALE_AMT", 0)),
            "TOT_DC_AMT":   to_int(row.get("TOT_DC_AMT", 0)),
            "DCM_SALE_AMT": to_int(row.get("DCM_SALE_AMT", 0)),
            "TOT_SALE_CNT": to_int(row.get("TOT_SALE_CNT", 0)),
            "FD_GST_CNT_T": to_int(row.get("FD_GST_CNT_T", 0)),
            "SALE_PER_GST": to_int(row.get("SALE_PER_GST", 0)),
            "CRD_CARD_AMT": to_int(row.get("CRD_CARD_AMT", 0)),
            "CASH_AMT2":    to_int(row.get("CASH_AMT2", 0)),
            "CASH_BILL_AMT":to_int(row.get("CASH_BILL_AMT", 0)),
            "VAT_AMT":      to_int(row.get("VAT_AMT", 0)),
            "TABLE_CNT":    to_int(row.get("TABLE_CNT", 0)),
        })

    print(f"[파싱 완료] {len(results)}개 가맹점")
    return results


def build_shop_data(row: dict, date: str) -> dict:
    """공통 데이터 구조 (JSON 저장 / Firebase 저장에서 공유)"""
    return {
        "shop_nm":      row.get("SHOP_NM", ""),
        "date":         date,
        "tot_sale_amt": row.get("TOT_SALE_AMT", 0),
        "dcm_sale_amt": row.get("DCM_SALE_AMT", 0),
        "tot_sale_cnt": row.get("TOT_SALE_CNT", 0),
        "gst_cnt":      row.get("FD_GST_CNT_T", 0),
        "sale_per_gst": row.get("SALE_PER_GST", 0),
        "card_amt":     row.get("CRD_CARD_AMT", 0),
        "cash_amt":     row.get("CASH_AMT2", 0),
        "updated_at":   datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
    }


def save_to_json(sales_data: list, date: str):
    filename = f"okpos_sales_{date.replace('-','')}.json"
    output = {}
    for row in sales_data:
        shop_cd = row.get("SHOP_CD", "")
        if shop_cd:
            output[shop_cd] = build_shop_data(row, date)
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"✅ JSON 저장: {filename} ({len(output)}개 가맹점)")
    return filename


def save_to_firebase(sales_data: list, date: str) -> bool:
    try:
        import firebase_admin
        from firebase_admin import credentials, db

        if not firebase_admin._apps:
            cred = credentials.Certificate(FIREBASE_CREDENTIAL_PATH)
            firebase_admin.initialize_app(cred, {"databaseURL": FIREBASE_DB_URL})

        date_key = date.replace("-", "")
        ref = db.reference(f"okpos_sales/{date_key}")

        output = {}
        for row in sales_data:
            shop_cd = row.get("SHOP_CD", "")
            if shop_cd:
                output[shop_cd] = build_shop_data(row, date)

        # set() 대신 update() 사용 — 같은 날짜 재실행해도 다른 데이터 보존
        ref.update(output)
        print(f"✅ Firebase 저장: okpos_sales/{date_key} ({len(output)}개 가맹점)")
        return True
    except Exception as e:
        print(f"❌ Firebase 오류: {e}")
        return False


def fetch_menu_sales(session: requests.Session, date_from: str,
                     date_to: str, shop_cd: str = "", shop_nm: str = "") -> Optional[list]:
    base = CONFIG["base_url"]

    # 상품별 매출 페이지 GET → CSRF 토큰 수집
    # 후보 경로를 순서대로 시도; 500 bytes 이상 응답이 오면 사용
    token_page_candidates = [
        f"{base}/sale/sale/prod_jump011.jsp",   # 상품별 매출 화면 (추정)
        f"{base}/sale/prod/prod_jump011.jsp",   # 대안 경로
        f"{base}/sale/day/day_jump010.jsp",     # fallback — 일별 화면 재사용
    ]
    r_page = None
    page_url = token_page_candidates[-1]
    for url in token_page_candidates:
        try:
            r = session.get(url, timeout=15)
            if r.status_code == 200 and len(r.content) > 500:
                r_page = r
                page_url = url
                print(f"[상품매출 토큰 페이지] {r.status_code} ← {url}")
                break
        except Exception:
            continue
    if not r_page:
        print("[상품매출 토큰 페이지] 모든 후보 실패 — 토큰 없이 진행")

    hidden = get_hidden_fields(r_page.text) if r_page else {}
    token_key, token_val = "", ""
    for k, v in hidden.items():
        if re.match(r'^[0-9a-f]{8}-[0-9a-f]{4}-', v, re.IGNORECASE):
            token_key, token_val = k, v
            print(f"[토큰] {k[:16]}... = {v[:16]}...")
            break

    time.sleep(0.5)

    payload = {
        "S_CONTROLLER": "sale.sale.prod011",
        "S_METHOD":     "search",
        "SHEETSEQ":     "1",
        "S_SAVENAME": (
            "sSeq|LCLS_NM|MCLS_NM|SCLS_NM|SALE_DATE|PROD_CD|BAR_CD|MAP_PROD_CD|"
            "PROD_NM|VENDORS_NM|COLOR_CD|SIZE_STR_CD|SALE_QTY|PROD_WEIGHT|"
            "TOT_SALE_AMT|TOT_DC_AMT|DCM_SALE_AMT|DC_AMT_GEN|DC_AMT_SVC|"
            "DC_AMT_JCD|DC_AMT_CPN|DC_AMT_CST|DC_AMT_FOD|DC_AMT_PACK|DC_AMT_YAP|SHOP_CD"
        ),
        "S_ORDERBY":     "",
        "ss_PROD_FG":    "N",
        "date1_1":       date_from,
        "date1_2":       date_to,
        "date_period1":  "366",
        "ss_CLS_TEXT":   "전체",
        "ss_SHOP_NM":    "전체",
        "ss_SHOP_INFO":  "[]",
        "ss_VENDOR_NM":  "전체",
        "ss_VENDOR_INFO":"[]",
        "ss_PAGE_SIZE":  "100",
        "ss_PAGE_NO1":   "1",
        # 나머지 ss_* 필드 — 빈 값으로 전송
        "ss_LCLS_CD":     "",
        "ss_MCLS_CD":     "",
        "ss_SCLS_CD":     "",
        "ss_SIZE_CLS_CD": "",
        "ss_PROD_CD":     "",
        "ss_PROD_NM":     "",
        "ss_BAR_CD":      "",
        "ss_SHOP_CD":     shop_cd,
        "ss_VENDOR_CD":   "",
    }
    if token_key:
        payload[token_key] = token_val

    debug_payload = {
        k: (v[:8] + "..." if k == token_key and len(v) > 8 else v)
        for k, v in payload.items()
    }
    print(f"[디버그] payload 전송:\n{json.dumps(debug_payload, ensure_ascii=False, indent=2)}")

    resp = session.post(
        f"{base}/sale/day/ddd.htmlSheetAction",
        data=payload,
        headers={
            "Referer":      page_url,
            "Origin":       base,
            "Content-Type": "application/x-www-form-urlencoded",
        },
        timeout=30
    )
    print(f"[상품별 매출 조회] {resp.status_code}, {len(resp.content):,} bytes")
    print(f"[응답 미리보기] {resp.text[:300]}")

    if resp.status_code != 200:
        return None

    # 구조 확인용 raw 저장
    raw_filename = f"menu_sales_raw_{date_from.replace('-','')}.json"
    with open(raw_filename, "w", encoding="utf-8") as f:
        f.write(resp.text)
    print(f"[디버그] 원본 응답 저장: {raw_filename}")

    return parse_menu_json(resp.text, date_from, shop_cd=shop_cd, shop_nm=shop_nm)


def parse_menu_json(raw: str, date: str, shop_cd: str = "", shop_nm: str = "") -> list:
    """상품별 매출 JSON 파싱 — 1차 실행은 구조 확인용"""
    try:
        data = json.loads(raw)
    except Exception as e:
        print(f"[상품 파싱 오류] JSON 파싱 실패: {e}")
        print(f"[원문] {raw[:300]}")
        return []

    print(f"[상품 파싱] 최상위 키: {list(data.keys())}")
    # OKPos 응답은 보통 "Data" 키 사용; 실제 응답 후 키 이름 확인 필요
    rows = data.get("Data", data.get("data", data.get("rows", [])))
    if not rows:
        print(f"[상품 파싱] 데이터 없음. 전체 구조 미리보기:\n{json.dumps(data, ensure_ascii=False)[:500]}")
        return []

    print(f"[상품 파싱] {len(rows)}행 수신, 첫 행 키: {list(rows[0].keys())}")

    def to_num(v, cast=int):
        try:
            return cast(float(str(v).replace(",", "")))
        except Exception:
            return cast(0)

    results = []
    for row in rows:
        prod_cd = str(row.get("PROD_CD", row.get("prod_cd", ""))).strip()
        if not prod_cd:
            continue
        results.append({
            "SHOP_CD":      (str(row.get("SHOP_CD", row.get("shop_cd", ""))).strip() or shop_cd),
            "SHOP_NM":      (str(row.get("SHOP_NM", row.get("shop_nm", ""))).strip() or shop_nm),
            "PROD_CD":      prod_cd,
            "PROD_NM":      str(row.get("PROD_NM", row.get("prod_nm", ""))).strip(),
            "SALE_QTY":     to_num(row.get("SALE_QTY", 0), float),
            "SALE_DATE":    str(row.get("SALE_DATE", date)).strip(),
            "TOT_SALE_AMT": to_num(row.get("TOT_SALE_AMT", 0)),
            "DCM_SALE_AMT": to_num(row.get("DCM_SALE_AMT", 0)),
        })

    print(f"[상품 파싱 완료] {len(results)}개 행")
    return results


def save_menu_sales_to_firebase(menu_data: list, date: str) -> bool:
    """okpos_menu_sales/{date_key}/{SHOP_CD}/{PROD_CD} 에 저장.
    기존 okpos_sales/ 경로는 절대 건드리지 않는다."""
    try:
        import firebase_admin
        from firebase_admin import credentials, db

        if not firebase_admin._apps:
            cred = credentials.Certificate(FIREBASE_CREDENTIAL_PATH)
            firebase_admin.initialize_app(cred, {"databaseURL": FIREBASE_DB_URL})

        date_key = date.replace("-", "")
        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

        # {shop_cd: {prod_cd: {...}}} 구조로 모으기
        shop_map: dict = {}
        skipped = 0
        for row in menu_data:
            s_cd = row.get("SHOP_CD", "")
            prod_cd = row.get("PROD_CD", "")
            if not s_cd:
                skipped += 1
                continue
            if not prod_cd:
                skipped += 1
                continue
            shop_map.setdefault(s_cd, {})[prod_cd] = {
                "prod_nm":    row.get("PROD_NM", ""),
                "sale_qty":   row.get("SALE_QTY", 0),
                "sale_amt":   row.get("DCM_SALE_AMT", 0),
                "shop_nm":    row.get("SHOP_NM", ""),
                "date":       date,
                "updated_at": now_str,
            }

        if skipped:
            print(f"⚠️  저장 제외 행: {skipped}건 (SHOP_CD 또는 PROD_CD 없음)")

        total_prods = 0
        for s_cd, prods in shop_map.items():
            shop_label = f"{prods[next(iter(prods))].get('shop_nm', '')}({s_cd})" if prods else s_cd
            print(f"  저장 대상: {shop_label} — {len(prods)}개 상품")
            ref = db.reference(f"okpos_menu_sales/{date_key}/{s_cd}")
            ref.update(prods)
            total_prods += len(prods)
            print(f"  ✅ 저장 완료: {len(prods)}건")

        print(f"✅ Firebase 저장: okpos_menu_sales/{date_key} "
              f"({len(shop_map)}개 가맹점, {total_prods}개 상품)")
        return True
    except Exception as e:
        print(f"❌ Firebase 오류 (menu): {e}")
        return False


def _make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "ko-KR,ko;q=0.9",
    })
    return s


def _collect_account(account: dict, date_from: str, date_to: str,
                     run_menu: bool) -> int:
    """단일 OKPos 계정으로 일별 매출 + 상품별 매출 수집.
    account = {label, okpos_id, okpos_pw, brand_id}
    반환: 0=성공, 양수=오류코드"""
    label    = account["label"]
    brand_id = account.get("brand_id", "")

    CONFIG["id"] = account["okpos_id"]
    CONFIG["pw"] = account["okpos_pw"]

    print(f"\n{'='*55}")
    print(f"  본사: {label}" + (f" (brandId={brand_id})" if brand_id else ""))
    print(f"  조회: {date_from} ~ {date_to}")
    print(f"{'='*55}\n")

    session = _make_session()
    if not login(session):
        print(f"❌ [{label}] 로그인 실패")
        return 1

    time.sleep(1)
    sales = fetch_sales(session, date_from, date_to)

    if not sales:
        print(f"⚠️  [{label}] 매출 데이터 없음")
        return 0   # 영업 안 한 날일 수 있으므로 오류 아님

    print(f"\n{'매장명':<20} {'총매출':>12} {'실매출':>12} {'주문':>6} {'고객':>6}")
    print("-" * 60)
    for row in sales:
        print(
            f"{row.get('SHOP_NM',''):<20} "
            f"{row.get('TOT_SALE_AMT',0):>12,} "
            f"{row.get('DCM_SALE_AMT',0):>12,} "
            f"{row.get('TOT_SALE_CNT',0):>6,} "
            f"{row.get('FD_GST_CNT_T',0):>6,}"
        )
    total = sum(r.get("TOT_SALE_AMT", 0) for r in sales)
    print("-" * 60)
    print(f"{'합계':<20} {total:>12,}")

    if FIREBASE_ENABLED:
        if not save_to_firebase(sales, date_from):
            return 3
    else:
        save_to_json(sales, date_from)

    print(f"✅ [{label}] 일별 매출 {len(sales)}개 가맹점 수집 완료")

    if not run_menu:
        return 0

    # ── 상품별(메뉴별) 판매수량 수집 ─────────────────────────
    if FIREBASE_ENABLED:
        menu_shops = load_menu_shops_from_firebase(brand_id)
    else:
        _env = os.environ.get("MENU_SHOPS", "")
        menu_shops = json.loads(_env) if _env else []
        if not menu_shops:
            print("⚠️  Firebase 꺼짐: MENU_SHOPS 환경변수(JSON)를 지정하면 수동 수집 가능")

    if not menu_shops:
        print(f"⚠️  [{label}] 수집 대상 매장 없음 — 상품별 수집 건너뜀")
        return 0

    print(f"\n  상품별 매출 수집 시작 (대상: {len(menu_shops)}개 매장)\n")
    total_menu_rows = 0
    for shop in menu_shops:
        s_cd = shop["shop_cd"]
        s_nm = shop["shop_nm"]
        print(f"\n--- {s_nm}({s_cd}) 수집 중 ---")
        time.sleep(1)
        menu_sales = fetch_menu_sales(session, date_from, date_to,
                                      shop_cd=s_cd, shop_nm=s_nm)
        if not menu_sales:
            print(f"⚠️  {s_nm}: 상품별 매출 데이터 없음")
            continue
        print(f"  파싱 결과: {len(menu_sales)}개 행")
        total_menu_rows += len(menu_sales)

        if FIREBASE_ENABLED:
            if not save_menu_sales_to_firebase(menu_sales, date_from):
                return 5
        else:
            import collections
            out_file = f"menu_sales_{s_cd}_{date_from.replace('-','')}.json"
            shop_map_out: dict = collections.defaultdict(dict)
            for row in menu_sales:
                shop_map_out[row["SHOP_CD"]][row["PROD_CD"]] = {
                    "prod_nm":  row["PROD_NM"],
                    "sale_qty": row["SALE_QTY"],
                    "sale_amt": row["DCM_SALE_AMT"],
                    "shop_nm":  row["SHOP_NM"],
                    "date":     date_from,
                }
            with open(out_file, "w", encoding="utf-8") as f:
                json.dump(dict(shop_map_out), f, ensure_ascii=False, indent=2)
            print(f"  JSON 저장: {out_file}")

    print(f"\n✅ [{label}] 상품별 수집 완료 — 총 {total_menu_rows}개 행 "
          f"({len(menu_shops)}개 매장)")
    return 0


def main(date_from=None, date_to=None, run_menu=False) -> int:
    if not date_from:
        yesterday = datetime.date.today() - datetime.timedelta(days=1)
        date_from = date_to = yesterday.strftime("%Y-%m-%d")

    print(f"\n{'='*50}")
    print(f"  나이스오케이포스 매출 수집 v10 (멀티 본사)")
    print(f"  조회: {date_from} ~ {date_to}")
    print(f"  Firebase 연동: {'ON' if FIREBASE_ENABLED else 'OFF (JSON 저장)'}")
    print(f"{'='*50}\n")

    # ── OKPos 계정 목록 결정 ─────────────────────────────────
    # Firebase 우선: users 에서 role=hq + okposId/okposPw 읽기
    # Secrets fallback: Firebase 계정 없을 때 OKPOS_ID/PW 환경변수 사용
    accounts = load_hq_accounts_from_firebase() if FIREBASE_ENABLED else []

    if not accounts:
        fallback_id = os.environ.get("OKPOS_ID", "")
        fallback_pw = os.environ.get("OKPOS_PW", "")
        if fallback_id and fallback_pw:
            print("[계정] Firebase HQ 없음 — Secrets fallback 사용")
            accounts = [{"label": "Secrets계정", "okpos_id": fallback_id,
                         "okpos_pw": fallback_pw, "brand_id": ""}]
        else:
            print("❌ OKPos 계정 없음 (Firebase HQ 미등록 + Secrets 미설정)")
            return 1

    print(f"[수집 대상] {len(accounts)}개 본사\n")

    # ── 본사별 순회 수집 ─────────────────────────────────────
    any_error = 0
    for account in accounts:
        try:
            rc = _collect_account(account, date_from, date_to, run_menu)
            if rc != 0:
                any_error = rc
        except Exception as e:
            print(f"❌ [{account['label']}] 예외 발생 — 다음 본사로 계속: {e}")
            any_error = 1
        time.sleep(2)   # 본사 간 간격

    if any_error:
        print(f"\n⚠️  일부 본사 수집 실패 (exit={any_error})")
    else:
        print(f"\n✅ 전체 {len(accounts)}개 본사 수집 완료!")
    return any_error


if __name__ == "__main__":
    # python okpos_scraper.py                              → 어제 일별 집계 (멀티 본사)
    # python okpos_scraper.py 2026-05-19 2026-05-19        → 특정 날짜 일별 집계
    # python okpos_scraper.py --menu                       → 어제 + 상품별 수집
    # python okpos_scraper.py 2026-05-19 2026-05-19 --menu → 특정 날짜 + 상품별 수집
    # python okpos_scraper.py schedule                     → 로컬 스케줄러 (일별만)

    args = sys.argv[1:]
    run_menu = "--menu" in args
    args = [a for a in args if a != "--menu"]

    if args and args[0] == "schedule":
        try:
            import schedule
        except ImportError:
            print("❌ schedule 라이브러리가 필요합니다: pip install schedule")
            sys.exit(1)
        def job():
            d = (datetime.date.today() - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
            main(d, d)
        schedule.every().day.at("02:00").do(job)
        print("📅 매일 02:00 자동 수집 (KST)")
        while True:
            schedule.run_pending()
            time.sleep(60)
    elif len(args) == 2:
        sys.exit(main(args[0], args[1], run_menu=run_menu))
    else:
        sys.exit(main(run_menu=run_menu))

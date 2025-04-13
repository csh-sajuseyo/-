import re

def extract_items_with_amount(ocr_text):
    def accurate_parser_with_filter(line):
        # 테이블 헤더 필터링
        if re.search(r"품의.?제목|품의.?자|품의.?금액", line):
            return None

        # 캡처 접두사 제거
        line = re.sub(r"\[?캡처\d+\]?", "", line)

        # 특수기호 제거 (기존 기호 + . : ; ! 추가)
        line = re.sub(r"[|ㅣ:;.!\"'\[\]\(\)\{\}·•▶▷✔️✓★☆●○]", "", line)

        # 숫자 콤마 병합
        line = re.sub(r"(\d{1,3}),\s*(\d{3})", r"\1\2", line)

        # 금액 추출
        money_candidates = re.findall(r"\d[\d,\.』’'`]{3,}", line)
        if money_candidates:
            raw_price = money_candidates[-1]
            clean_price = re.sub(r"[^\d]", "", raw_price)
            price_str = f"{int(clean_price):,} 원"
            line = line.replace(raw_price, "").strip()
        else:
            price_str = "금액없음"
            clean_price = ""

        # 이름 제거
        if clean_price:
            parts = line.split(clean_price)
            if len(parts) >= 2:
                before_price = parts[0].strip()
                after_price = parts[1].strip()
                before_price = re.sub(r"[가-힣]{2,3}$", "", before_price).strip()
                after_price = re.sub(r"^[가-힣]{2,3}", "", after_price).strip()
                item_text = f"{before_price} {after_price}".strip()
            else:
                item_text = re.sub(r"\s[가-힣]{2,3}$", "", line).strip()
        else:
            item_text = re.sub(r"\s[가-힣]{2,3}$", "", line).strip()

        return item_text, price_str

    results = []
    for line in ocr_text.strip().splitlines():
        parsed = accurate_parser_with_filter(line)
        if parsed:
            results.append(parsed)

    return results


import re

# OCR 결과에서 품목 제목 + 금액만 추출
def parse_ocr_text(ocr_text):
    results = []
    lines = ocr_text.splitlines()
    for line in lines:
        if not line.strip():
            continue

        match = re.findall(r"(\d[\d,\.]{2,})", line)
        if match:
            raw_price = match[-1]
            price = raw_price.replace(",", "").replace(".", "")
            price_str = f"{int(price):,} 원"
            item = line.replace(raw_price, "").strip()
        else:
            price_str = "금액없음"
            item = line.strip()

        # ✨ 캡처1, 캡처2 등 제거
        item = re.sub(r"\[?캡처\d+\]?", "", item)

        # ✨ 앞쪽 특수기호 제거 보강
        item = re.sub(r"^[\|\)\:\;\.ㅣ\'\"]+", "", item)
        item = re.sub(r"^\s+", "", item)

        # ✨ 마지막 사람 이름 제거 (2~3글자 한글)
        item = re.sub(r"\s[가-힣]{2,3}$", "", item)

        results.append((item, price_str))
    return results

# ✅ 사줄게요.py 내부 연동용 별칭
extract_items_with_amount = parse_ocr_text

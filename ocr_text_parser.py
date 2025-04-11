import re

def extract_items_with_amount(ocr_text: str):
    """
    OCR 추출 텍스트에서 품목명 + 금액 항목을 정제하여 추출합니다.
    """
    lines = ocr_text.split("\n")
    lines = [line.strip() for line in lines if line.strip()]

    item_lines = [line for line in lines if re.search(r'\d{3,}', line)]

    parsed_items = []
    for line in item_lines:
        if re.search(r'000$', line):  # 너무 짧은 금액(000)은 제외
            continue
        match = re.search(r'(.*?)(\d{3,}(?:[,\.]?\d{0,3})?)\s*(원)?$', line)
        if match:
            name = match.group(1).strip(" .:-~••")
            price = match.group(2).replace(",", "").replace(".", "")
            if len(price) < 3 or not price.isdigit():
                continue
            parsed_items.append((name, price))
    return parsed_items
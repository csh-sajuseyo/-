import re

def extract_items_with_amount(ocr_text: str):
    """
    OCR 추출 텍스트에서 품목명 + 금액 항목을 추출합니다.
    :param ocr_text: Tesseract로 추출한 전체 텍스트
    :return: [(품목명, 금액)] 형태의 리스트
    """
    lines = ocr_text.split("\n")
    lines = [line.strip() for line in lines if line.strip()]

    item_lines = [line for line in lines if re.search(r'\d{3,}', line)]

    parsed_items = []
    for line in item_lines:
        match = re.search(r'(.*?)(\d{3,}[,\.]?\d*)$', line)
        if match:
            name = match.group(1).strip()
            price = match.group(2).replace(",", "").replace(".", "")
            parsed_items.append((name, price))
    return parsed_items
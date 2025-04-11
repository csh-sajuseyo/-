
import re

def extract_items_with_amount(ocr_text: str):
    lines = ocr_text.splitlines()
    items = []
    buffer = ""
    
    for i, line in enumerate(lines):
        line = line.strip()
        if not line:
            continue

        # 금액 패턴이 있는 줄이면
        if re.search(r"[\d,]{3,}\s*원|\d{3,}", line):
            # 금액 추출
            amount_match = re.search(r"([\d,]{3,})\s*원?|\d{3,}", line)
            amount = amount_match.group(1).replace(",", "") if amount_match else None
            
            # 앞줄 또는 앞앞줄에서 품목 추정
            candidate_lines = lines[max(0, i - 2):i]
            candidate_lines = [l.strip() for l in candidate_lines if l.strip()]
            item_name = candidate_lines[-1] if candidate_lines else "품목없음"

            # 전처리
            item_name = re.sub(r"[^가-힣A-Za-z0-9\s]", "", item_name)
            amount = amount.strip() + " 원" if amount else "금액없음"

            items.append((item_name, amount))

    return items

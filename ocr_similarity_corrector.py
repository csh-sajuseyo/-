from difflib import get_close_matches

CORRECTION_DICTIONARY = ['현장', '지출', '학습', '체험', '관련', '운영', '진로', '물품', '구입', '학생', '간식', '협의회', '교원', '프로그램', '검사', '행사', '추모', '도서관', '세탁기', '평가', '회계', '보고', '사용', '비용', '지급']

def correct_words_by_similarity(line):
    words = line.split()
    corrected = []

    for word in words:
        match = get_close_matches(word, CORRECTION_DICTIONARY, n=1, cutoff=0.8)
        if match:
            corrected.append(match[0])
        else:
            corrected.append(word)
    return " ".join(corrected)

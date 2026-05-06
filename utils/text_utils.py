"""
文本处理工具模块
提供相似度计算、标点符号标准化等功能
"""


def normalize_punctuation(text):
    """
    标准化标点符号，将中文标点转换为英文标点
    
    Args:
        text: 原始文本
    
    Returns:
        str: 标准化后的文本
    """
    punctuation_map = {
        '？': '?',  # 中文问号 -> 英文问号
        '，': ',',  # 中文逗号 -> 英文逗号
        '。': '.',  # 中文句号 -> 英文句号
        '！': '!',  # 中文感叹号 -> 英文感叹号
        '；': ';',  # 中文分号 -> 英文分号
        '：': ':',  # 中文冒号 -> 英文冒号
        '“': '"',  # 中文左双引号 -> 英文双引号
        '”': '"',  # 中文右双引号 -> 英文双引号
        '‘': "'",  # 中文左单引号 -> 英文单引号
        '’': "'",  # 中文右单引号 -> 英文单引号
        '（': '(',  # 中文左括号 -> 英文左括号
        '）': ')',  # 中文右括号 -> 英文右括号
        '【': '[',  # 中文左方括号 -> 英文左方括号
        '】': ']',  # 中文右方括号 -> 英文右方括号
        '《': '<',  # 中文左书名号 -> 英文小于号
        '》': '>',  # 中文右书名号 -> 英文大于号
    }
    
    normalized_text = text
    for chinese_punct, english_punct in punctuation_map.items():
        normalized_text = normalized_text.replace(chinese_punct, english_punct)
    
    return normalized_text


def similarity_score(text1, text2):
    """
    计算两个文本的相似度（字符集Jaccard相似度）
    
    Args:
        text1: 文本1
        text2: 文本2
    
    Returns:
        float: 相似度 (0-1)
    """
    # 标准化标点符号
    text1_normalized = normalize_punctuation(text1)
    text2_normalized = normalize_punctuation(text2)
    
    # 简单的字符集交集/并集比例
    set1 = set(text1_normalized)
    set2 = set(text2_normalized)
    
    if not set1 or not set2:
        return 0.0
    
    intersection = set1 & set2
    union = set1 | set2
    
    return len(intersection) / len(union)

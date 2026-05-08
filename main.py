import pyautogui
import time
import os
import threading
import json
from datetime import datetime
from PIL import Image, ImageTk, ImageDraw
import tkinter as tk
from tkinter import ttk, messagebox
import keyboard
import win32gui
import win32con
import cv2
import numpy as np
import requests

# 全局字体配置
DEFAULT_FONT = ("Microsoft YaHei", 9)  # 微软雅黑
TITLE_FONT = ("Microsoft YaHei", 10, "bold")
LARGE_FONT = ("Microsoft YaHei", 14, "bold")

# OCR相关导入
try:
    from paddleocr import PaddleOCR

    OCR_AVAILABLE = True
except ImportError:
    OCR_AVAILABLE = False
    print("警告: PaddleOCR未安装，OCR功能将不可用")


class ScreenshotTool:
    """截图工具类 - 支持全屏和窗口截图，通过快捷键控制"""

    def __init__(self):
        self.is_capturing = False
        self.screenshot_thread = None
        self.current_screenshot_path = None
        self.last_capture_time = 0
        self.log_messages = []  # 日志消息列表
        self.last_ocr_time = 0  # 上次OCR识别时间
        self.ocr_interval = 2.0  # OCR识别间隔（秒），避免频繁调用
        self.last_question_image = None  # 保存上一次的题干图片，用于对比
        self.question_bank = []  # 题库数据

        # 加载配置
        self.config_file = "config.json"
        self.config = self.load_config()

        # 从配置中读取参数
        self.output_dir = self.config.get('screenshot', {}).get('output_dir', 'screenshots')
        self.capture_interval = self.config.get('screenshot', {}).get('capture_interval', 1.0)
        self.max_log_messages = self.config.get('screenshot', {}).get('max_log_messages', 200)  # 增加到200条
        self.template_path = self.config.get('title_matching', {}).get('template_path', 'img/title.png')
        self.match_threshold = self.config.get('title_matching', {}).get('match_threshold', 0.8)

        # 目标区域偏移量（相对于红色边框左上角）- 优先从配置文件读取
        default_config = self.get_default_config()
        offset_config = self.config.get('question_area_offset', default_config['question_area_offset'])
        self.offset_top_left_x = offset_config.get('top_left_x', default_config['question_area_offset']['top_left_x'])
        self.offset_top_left_y = offset_config.get('top_left_y', default_config['question_area_offset']['top_left_y'])
        self.offset_bottom_right_x = offset_config.get('bottom_right_x',
                                                       default_config['question_area_offset']['bottom_right_x'])
        self.offset_bottom_right_y = offset_config.get('bottom_right_y',
                                                       default_config['question_area_offset']['bottom_right_y'])

        # 答案区域偏移量（A、B、C、D四个选项）- 优先从配置文件读取
        self.answer_areas = {}
        for option in ['a', 'b', 'c', 'd']:
            config_key = f'answer_area_offset_{option}'
            answer_config = self.config.get(config_key, default_config[config_key])
            self.answer_areas[option] = {
                'top_left_x': answer_config.get('top_left_x', default_config[config_key]['top_left_x']),
                'top_left_y': answer_config.get('top_left_y', default_config[config_key]['top_left_y']),
                'bottom_right_x': answer_config.get('bottom_right_x', default_config[config_key]['bottom_right_x']),
                'bottom_right_y': answer_config.get('bottom_right_y', default_config[config_key]['bottom_right_y'])
            }

        # 创建输出目录
        if not os.path.exists(self.output_dir):
            os.makedirs(self.output_dir)

        # 初始化GUI
        self.setup_gui()

        # 初始化OCR引擎（在GUI初始化之后，只初始化一次）
        self.ocr_engine = None
        if OCR_AVAILABLE:
            try:
                self.add_log("正在初始化PaddleOCR引擎...")
                # 初始化PaddleOCR，优先保证准确度
                self.ocr_engine = PaddleOCR(
                    use_angle_cls=True,  # ✅ 启用方向分类器（提高准确度）
                    lang='ch',  # 中文识别
                    use_gpu=False,  # 使用CPU
                    show_log=False,  # 关闭日志
                    det_model_dir=None,  # 使用默认模型
                    rec_model_dir=None,  # 使用默认模型
                    cls_model_dir=None,  # 使用默认模型
                )
                self.add_log("✓ PaddleOCR引擎初始化成功（高精度模式）")
            except Exception as e:
                self.add_log(f"✗ PaddleOCR引擎初始化失败: {str(e)}")

        # 加载题库
        self.load_question_bank()

        # 注册全局快捷键
        self.setup_hotkeys()

    def load_config(self):
        """加载配置文件"""
        try:
            if os.path.exists(self.config_file):
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            else:
                # 如果配置文件不存在，返回默认配置
                return self.get_default_config()
        except Exception as e:
            print(f"加载配置文件失败: {str(e)}")
            return self.get_default_config()

    def load_question_bank(self):
        """加载题库文件"""
        try:
            question_bank_file = '题库.json'
            if os.path.exists(question_bank_file):
                with open(question_bank_file, 'r', encoding='utf-8') as f:
                    self.question_bank = json.load(f)
                self.add_log(f"✓ 题库加载成功，共 {len(self.question_bank)} 道题目")
            else:
                self.add_log("⚠ 题库文件不存在，将跳过答案匹配")
                self.question_bank = []
        except Exception as e:
            self.add_log(f"✗ 题库加载失败: {str(e)}")
            self.question_bank = []

    def query_online_api(self, question_text, options):
        """
        调用网络题库API查询
        
        Args:
            question_text: OCR识别的题干文字
            options: 四个选项的字典 {'a': '选项A内容', 'b': '选项B内容', ...}
        
        Returns:
            tuple: (正确选项, 选项相似度字典) 或 (None, {})
        """
        try:
            api_url = "https://api.qqsgtk.cn/qqsgtkApi/findByQuestion"

            # 构建请求参数
            answer_list = [options.get('a', ''), options.get('b', ''),
                           options.get('c', ''), options.get('d', '')]
            answer_str = ','.join([ans for ans in answer_list if ans])  # 过滤空值

            payload = {
                "question": question_text,
                "answer": answer_str
            }

            headers = {
                "Content-Type": "application/json"
            }

            self.add_log(f"🌐 正在查询网络题库...")

            # 发送POST请求
            response = requests.post(api_url, json=payload, headers=headers, timeout=5)

            if response.status_code == 200:
                result = response.json()

                # 检查响应状态
                if result.get('success') and result.get('code') == 200:
                    data = result.get('data', [])

                    if data and len(data) > 0:
                        # 取第一个匹配结果
                        matched = data[0]
                        online_answer = matched.get('answer', '')

                        self.add_log(f"✅ 网络题库找到答案: {online_answer}")

                        # 自动录入到本地题库
                        self.add_to_local_bank(question_text, online_answer)

                        # 计算选项相似度
                        option_similarities = self.calculate_option_similarities(online_answer, options)
                        correct_option = self.match_answer(online_answer, options)

                        return correct_option, option_similarities
                    else:
                        self.add_log("⚠️ 网络题库中未找到匹配的题目")
                        return None, {}
                else:
                    self.add_log(f"❌ 网络API返回错误: {result.get('message', '未知错误')}")
                    return None, {}
            else:
                self.add_log(f"❌ 网络请求失败: HTTP {response.status_code}")
                return None, {}

        except requests.exceptions.Timeout:
            self.add_log("⚠ 网络请求超时")
            return None, {}
        except requests.exceptions.RequestException as e:
            self.add_log(f"❌ 网络请求异常: {str(e)}")
            return None, {}
        except Exception as e:
            self.add_log(f"❌ API查询失败: {str(e)}")
            return None, {}

    def add_to_local_bank(self, question, answer):
        """
        将题目添加到本地题库
        
        Args:
            question: 题干
            answer: 正确答案
        """
        try:
            # 检查是否已存在
            for q in self.question_bank:
                if q['question'].strip() == question.strip():
                    self.add_log(f"ℹ️ 题目已存在于本地题库，跳过添加")
                    return

            # 构建新题目
            new_question = {
                "question": question,
                "answer": answer
            }

            # 添加到内存
            self.question_bank.append(new_question)

            # 保存到文件
            question_bank_file = '题库.json'
            with open(question_bank_file, 'w', encoding='utf-8') as f:
                json.dump(self.question_bank, f, ensure_ascii=False, indent=2)

            self.add_log(f"✅ 已自动录入本地题库: {question[:30]}...")

        except Exception as e:
            self.add_log(f"❌ 录入本地题库失败: {str(e)}")

    def find_correct_answer(self, question_text, options):
        """
        从题库中查找正确答案
        
        Args:
            question_text: OCR识别的题干文字
            options: 四个选项的字典 {'a': '选项A内容', 'b': '选项B内容', ...}
        
        Returns:
            tuple: (正确选项, 相似度, 选项相似度字典, 题库答案文本) 或 (None, 0.0, {}, None)
                - 正确选项: 'a', 'b', 'c', 'd' 或 None
                - 相似度: 0.0-1.0（题干匹配度）
                - 选项相似度字典: {'a': 0.85, 'b': 0.3, 'c': 0.2, 'd': 0.1}
                - 题库答案文本: 题库中的正确答案文本
        """
        if not self.question_bank or not question_text:
            return None, 0.0, {}, None

        # 清理题干文字（去除空格、标点等）
        clean_question = question_text.strip()

        best_match = None  # 最佳匹配
        best_similarity = 0.0  # 最高相似度

        # 在题库中查找匹配的题目
        for q in self.question_bank:
            bank_question = q['question'].strip()
            bank_answer = q['answer'].strip()

            # 计算相似度
            similarity = 0.0

            # 判断是否完全包含（优先级最高）
            if clean_question in bank_question or bank_question in clean_question:
                # 找到完全匹配，立即返回
                option_similarities = self.calculate_option_similarities(bank_answer, options)
                correct_option = self.match_answer(bank_answer, options)
                return correct_option, 1.0, option_similarities, bank_answer
            else:
                # 否则计算字符集相似度
                similarity = self.similarity_score(clean_question, bank_question)

            # 更新最佳匹配
            if similarity > best_similarity:
                best_similarity = similarity
                best_match = {
                    'question': bank_question,
                    'answer': bank_answer,
                    'similarity': similarity
                }

        # 如果找到最佳匹配，对比答案
        if best_match and best_similarity > 0.6:  # 提高阈值到0.6，确保题干匹配更准确
            option_similarities = self.calculate_option_similarities(best_match['answer'], options)
            correct_option = self.match_answer(best_match['answer'], options)
            return correct_option, best_similarity, option_similarities, best_match['answer']

        # 本地题库未匹配或匹配度不高，返回空结果（由调用方决定是否查询网络）
        return None, 0.0, {}, None

    def calculate_option_similarities(self, correct_answer, options):
        """
        计算每个选项与题库答案的相似度（使用序列匹配）
        
        Args:
            correct_answer: 题库中的正确答案
            options: 四个选项的字典
        
        Returns:
            dict: 每个选项的相对相似度 {'a': 0.85, 'b': 1.0, 'c': 0.2, 'd': 0.1}
                  最佳匹配的选项为1.0，其他选项相对于最佳匹配的比例
        """
        if not correct_answer:
            return {option: 0.0 for option in ['a', 'b', 'c', 'd']}

        clean_answer = correct_answer.strip()

        # 第一步：计算每个选项的原始相似度
        raw_similarities = {}
        for option_key, option_text in options.items():
            if not option_text:
                raw_similarities[option_key] = 0.0
                continue

            clean_option = option_text.strip()

            # 计算原始相似度
            if clean_answer == clean_option:
                # 完全相等，最高分
                raw_similarities[option_key] = 1.0
            elif clean_answer in clean_option or clean_option in clean_answer:
                # 包含关系，高分但不是满分
                # 根据长度比例调整：越接近完整匹配，分数越高
                shorter_len = min(len(clean_answer), len(clean_option))
                longer_len = max(len(clean_answer), len(clean_option))
                length_ratio = shorter_len / longer_len if longer_len > 0 else 0
                raw_similarities[option_key] = 0.7 + (length_ratio * 0.25)  # 0.7-0.95之间
            else:
                # 否则计算序列相似度（考虑字符顺序）
                raw_similarities[option_key] = self.sequence_similarity(clean_answer, clean_option)

        # 第二步：找到最高相似度
        max_similarity = max(raw_similarities.values()) if raw_similarities else 0.0

        # 第三步：计算相对相似度（相对于最佳匹配）
        # 最佳匹配的选项为1.0，其他选项按比例缩放
        option_similarities = {}
        for option_key, raw_sim in raw_similarities.items():
            if max_similarity > 0:
                # 相对相似度 = 原始相似度 / 最大相似度
                option_similarities[option_key] = raw_sim / max_similarity
            else:
                option_similarities[option_key] = 0.0

        return option_similarities

    def sequence_similarity(self, text1, text2):
        """
        计算两个文本的序列相似度（考虑字符顺序）
        使用最长公共子序列（LCS）算法
        
        Args:
            text1: 文本1
            text2: 文本2
        
        Returns:
            float: 相似度 (0-1)
        """
        if not text1 or not text2:
            return 0.0
        
        # 标准化标点符号
        text1_normalized = self.normalize_punctuation(text1)
        text2_normalized = self.normalize_punctuation(text2)
        
        # 计算最长公共子序列长度
        lcs_length = self._lcs_length(text1_normalized, text2_normalized)
        
        # 相似度 = LCS长度 / 较长文本的长度
        max_len = max(len(text1_normalized), len(text2_normalized))
        
        if max_len == 0:
            return 0.0
        
        return lcs_length / max_len
    
    def _lcs_length(self, text1, text2):
        """
        计算两个字符串的最长公共子序列长度
        使用动态规划算法
        
        Args:
            text1: 字符串1
            text2: 字符串2
        
        Returns:
            int: LCS长度
        """
        m = len(text1)
        n = len(text2)
        
        # 创建DP表
        dp = [[0] * (n + 1) for _ in range(m + 1)]
        
        # 填充DP表
        for i in range(1, m + 1):
            for j in range(1, n + 1):
                if text1[i - 1] == text2[j - 1]:
                    dp[i][j] = dp[i - 1][j - 1] + 1
                else:
                    dp[i][j] = max(dp[i - 1][j], dp[i][j - 1])
        
        return dp[m][n]

    def similarity_score(self, text1, text2):
        """
        计算两个文本的相似度（简单实现）
        
        Args:
            text1: 文本1
            text2: 文本2
        
        Returns:
            float: 相似度 (0-1)
        """
        # 标准化标点符号：将中文标点转换为英文标点
        text1_normalized = self.normalize_punctuation(text1)
        text2_normalized = self.normalize_punctuation(text2)

        # 简单的字符集交集/并集比例
        set1 = set(text1_normalized)
        set2 = set(text2_normalized)

        if not set1 or not set2:
            return 0.0

        intersection = set1 & set2
        union = set1 | set2

        return len(intersection) / len(union)

    def normalize_punctuation(self, text):
        """
        标准化标点符号，将中文标点转换为英文标点
        
        Args:
            text: 原始文本
        
        Returns:
            str: 标准化后的文本
        """
        # 标点符号映射表
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

        # 逐个替换标点符号
        normalized_text = text
        for chinese_punct, english_punct in punctuation_map.items():
            normalized_text = normalized_text.replace(chinese_punct, english_punct)

        return normalized_text

    def match_answer(self, correct_answer, options):
        """
        根据正确答案匹配选项
        
        Args:
            correct_answer: 题库中的正确答案
            options: 四个选项的字典
        
        Returns:
            str: 匹配的选项 ('a', 'b', 'c', 'd') 或 None
        """
        if not correct_answer:
            return None

        best_option = None
        best_score = 0.0

        # 遍历四个选项，查找与答案最匹配的
        for option_key, option_text in options.items():
            if not option_text:
                continue

            # 清理文本
            clean_answer = correct_answer.strip()
            clean_option = option_text.strip()

            # 计算匹配分数
            score = 0.0

            # 完全相等（最高优先级）
            if clean_answer == clean_option:
                score = 1.0
            # 包含关系（次高优先级）
            elif clean_answer in clean_option or clean_option in clean_answer:
                score = 0.9
            else:
                # 计算字符集相似度
                score = self.similarity_score(clean_answer, clean_option)

            # 更新最佳匹配
            if score > best_score:
                best_score = score
                best_option = option_key

        # 如果最高分数超过阈值，返回最佳选项
        if best_score > 0.5:
            return best_option

        return None

    def update_result_display(self, question_text, answer_texts, correct_option=None, match_similarity=0.0,
                              option_similarities=None, bank_answer_text=None):
        """
        更新UI显示区域的题干、选项和匹配置信度
        
        Args:
            question_text: OCR识别的题干文字
            answer_texts: 四个选项的字典 {'a': '选项A内容', ...}
            correct_option: 正确选项 ('a', 'b', 'c', 'd') 或 None
            match_similarity: 题库匹配相似度 (0.0-1.0)
            option_similarities: 每个选项与题库答案的相似度字典 {'a': 0.85, 'b': 0.3, ...}
            bank_answer_text: 题库中的正确答案文本
        """
        if option_similarities is None:
            option_similarities = {}

        try:
            # 更新题干
            if question_text:
                self.question_text_var.set(question_text)
            else:
                self.question_text_var.set("等待识别...")

            # 更新题库答案显示
            if bank_answer_text:
                self.bank_answer_var.set(bank_answer_text)
            else:
                self.bank_answer_var.set("--")

            # 更新四个选项
            for option in ['a', 'b', 'c', 'd']:
                if option in answer_texts and answer_texts[option]:
                    # 设置选项内容
                    self.option_vars[option].set(answer_texts[option])

                    # 计算匹配置信度（使用选项相似度）
                    confidence = self.calculate_match_confidence(option, answer_texts, correct_option, match_similarity,
                                                                 option_similarities)
                    self.option_vars[f'{option}_confidence'].set(f"{confidence:.0f}%")

                    # 设置颜色和字体（正确答案高亮加粗）
                    if correct_option and option == correct_option:
                        self.option_labels[option]['content'].configure(
                            foreground="#2E7D32",
                            font=("Arial", 9, "bold")  # 加粗
                        )
                        self.option_labels[option]['confidence'].configure(
                            foreground="#2E7D32",
                            font=("Arial", 8, "bold")
                        )
                    else:
                        self.option_labels[option]['content'].configure(
                            foreground="#333333",
                            font=("Arial", 9)  # 正常字体
                        )
                        self.option_labels[option]['confidence'].configure(
                            foreground="#999999",
                            font=("Arial", 8)
                        )
                else:
                    self.option_vars[option].set("等待识别...")
                    self.option_vars[f'{option}_confidence'].set("--")
                    self.option_labels[option]['content'].configure(
                        foreground="#333333",
                        font=("Arial", 9)
                    )
                    self.option_labels[option]['confidence'].configure(
                        foreground="#999999",
                        font=("Arial", 8)
                    )

            # 更新最佳答案显示
            if correct_option and correct_option in answer_texts:
                # 使用选项相似度作为置信度
                option_confidence = option_similarities.get(correct_option, match_similarity)
                best_text = f"{correct_option.upper()} {answer_texts[correct_option]} {option_confidence:.0%}"
                self.best_answer_var.set(best_text)
                # 隐藏录入按钮
                if hasattr(self, 'add_question_btn'):
                    self.add_question_btn.pack_forget()
            else:
                # 未找到匹配
                if question_text and answer_texts and any(answer_texts.values()):
                    # 有识别结果但未匹配，显示录入按钮
                    self.best_answer_var.set("未查询到该题，点击录入题库")
                    if hasattr(self, 'add_question_btn'):
                        self.add_question_btn.pack(fill=tk.X, pady=(8, 0))
                else:
                    self.best_answer_var.set("等待识别...")
                    # 隐藏录入按钮
                    if hasattr(self, 'add_question_btn'):
                        self.add_question_btn.pack_forget()

        except Exception as e:
            print(f"更新显示失败: {str(e)}")

    def calculate_match_confidence(self, option, answer_texts, correct_option, match_similarity=0.0,
                                   option_similarities=None):
        """
        计算选项与题库答案的匹配置信度
        
        Args:
            option: 选项键 ('a', 'b', 'c', 'd')
            answer_texts: 四个选项的字典
            correct_option: 正确选项
            match_similarity: 题库匹配相似度 (0.0-1.0)
            option_similarities: 每个选项与题库答案的相似度字典
        
        Returns:
            float: 置信度 (0-100)
        """
        if option_similarities is None:
            option_similarities = {}

        if option not in answer_texts:
            return 0.0

        # 如果有选项相似度，直接使用
        if option in option_similarities:
            return option_similarities[option] * 100.0

        # 否则使用旧逻辑（兼容）
        if not correct_option:
            return 0.0

        # 如果是正确选项，返回匹配相似度 * 100
        if option == correct_option:
            return match_similarity * 100.0

        # 其他选项返回0%
        return 0.0

    def get_bank_answer_by_option(self, correct_option, answer_texts):
        """
        根据正确选项获取题库答案文本
        
        Args:
            correct_option: 正确选项 ('a', 'b', 'c', 'd')
            answer_texts: 四个选项的字典
        
        Returns:
            str: 题库答案文本，如果找不到则返回None
        """
        if not correct_option or correct_option not in answer_texts:
            return None

        # 直接返回该选项的内容作为题库答案
        return answer_texts[correct_option]

    def save_config(self):
        """保存配置文件"""
        try:
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(self.config, f, indent=2, ensure_ascii=False)
            return True
        except Exception as e:
            messagebox.showerror("错误", f"保存配置失败: {str(e)}")
            return False

    def get_default_config(self):
        """获取默认配置"""
        # 答案左右两侧的x坐标 因为答案是对齐的 所以始终保持一致
        answer_area_offset_x1 = 40  # 答案最左侧的x坐标
        answer_area_offset_x2 = 300  # 答案最右侧的x坐标
        return {
            "app": {
                "version": "v1.0.0",
                "name": "推举孝廉小助手"
            },
            "title_matching": {
                "template_path": "img/title.png",
                "match_threshold": 0.8
            },
            "question_area_offset": {
                "top_left_x": 10,
                "top_left_y": 61,
                "bottom_right_x": 300,
                "bottom_right_y": 149
            },
            "answer_area_offset_a": {
                "top_left_x": answer_area_offset_x1,  # 与config.json保持一致
                "top_left_y": 154,
                "bottom_right_x": answer_area_offset_x2,
                "bottom_right_y": 184
            },
            "answer_area_offset_b": {
                "top_left_x": answer_area_offset_x1,  # 与config.json保持一致
                "top_left_y": 184,
                "bottom_right_x": answer_area_offset_x2,
                "bottom_right_y": 214
            },
            "answer_area_offset_c": {
                "top_left_x": answer_area_offset_x1,  # 与config.json保持一致
                "top_left_y": 214,
                "bottom_right_x": answer_area_offset_x2,
                "bottom_right_y": 244
            },
            "answer_area_offset_d": {
                "top_left_x": answer_area_offset_x1,  # 与config.json保持一致
                "top_left_y": 244,
                "bottom_right_x": answer_area_offset_x2,
                "bottom_right_y": 274
            },
            "screenshot": {
                "capture_interval": 1.0,
                "output_dir": "screenshots",
                "max_log_messages": 200,  # 增加到200条
                "default_mode": "fullscreen"  # 默认截图模式
            }
        }

    def center_window(self, window, width, height):
        """将窗口居中显示在父窗口中央"""
        # 获取父窗口位置
        parent_x = self.root.winfo_x()
        parent_y = self.root.winfo_y()
        parent_width = self.root.winfo_width()
        parent_height = self.root.winfo_height()

        # 计算子窗口位置
        x = parent_x + (parent_width - width) // 2
        y = parent_y + (parent_height - height) // 2

        # 设置窗口位置
        window.geometry(f"{width}x{height}+{x}+{y}")

    def setup_gui(self):
        """设置GUI界面"""
        self.root = tk.Tk()
        self.root.title("推举孝廉小助手")
        self.root.geometry("1150x750")  # 初始大小
        self.root.resizable(True, True)  # 允许调整大小

        # 设置全局字体为微软雅黑
        style = ttk.Style()
        style.configure('.', font=DEFAULT_FONT)
        style.configure('TLabelframe.Label', font=TITLE_FONT)

        # 初始化变量（必须在root创建之后）
        # 从配置中读取默认截图模式
        default_mode = self.config.get('screenshot', {}).get('default_mode', 'fullscreen')
        self.capture_mode = tk.StringVar(value=default_mode)

        # 设置窗口始终在最前
        self.root.attributes('-topmost', True)

        # 创建菜单栏
        menubar = tk.Menu(self.root, bg="#f0f0f0", fg="#333333")
        self.root.config(menu=menubar)

        # 设置菜单
        settings_menu = tk.Menu(menubar, tearoff=0, bg="#ffffff", fg="#333333")
        menubar.add_cascade(label="设置", menu=settings_menu)
        settings_menu.add_command(label="参数配置", command=self.open_settings_dialog)

        # 工具菜单
        tools_menu = tk.Menu(menubar, tearoff=0, bg="#ffffff", fg="#333333")
        menubar.add_cascade(label="工具", menu=tools_menu)
        tools_menu.add_command(label="手动检索", command=self.open_search_dialog)
        tools_menu.add_command(label="手动录入", command=lambda: self.open_add_question_dialog(show_options=False))

        # 关于菜单
        help_menu = tk.Menu(menubar, tearoff=0, bg="#ffffff", fg="#333333")
        menubar.add_cascade(label="帮助", menu=help_menu)
        help_menu.add_command(label="关于", command=self.show_about_dialog)

        # 主框架 - 左右布局
        main_container = ttk.Frame(self.root, padding="10")
        main_container.pack(fill=tk.BOTH, expand=True)

        # 左侧功能区
        left_frame = ttk.Frame(main_container)
        left_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 5))

        # 右侧日志区
        right_frame = ttk.Frame(main_container)
        right_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=False, padx=(5, 0))

        # === 左侧功能区 ===
        # 截图模式选择（横排）
        mode_frame = ttk.LabelFrame(left_frame, text="截图模式", padding="8")
        mode_frame.pack(fill=tk.X, pady=(0, 8))

        self.mode_radio_fullscreen = ttk.Radiobutton(
            mode_frame,
            text="全屏截图",
            variable=self.capture_mode,
            value="fullscreen"
        )
        self.mode_radio_fullscreen.pack(side=tk.LEFT, padx=(5, 15))

        self.mode_radio_window = ttk.Radiobutton(
            mode_frame,
            text="当前窗口截图",
            variable=self.capture_mode,
            value="window"
        )
        self.mode_radio_window.pack(side=tk.LEFT, padx=5)

        # 识别结果显示区域（题干 + 四个选项）
        result_frame = ttk.LabelFrame(left_frame, text="当前识别结果", padding="8")
        result_frame.pack(fill=tk.X, pady=(0, 8))

        # 题干显示
        question_label_frame = ttk.Frame(result_frame)
        question_label_frame.pack(fill=tk.X, pady=(0, 5))

        ttk.Label(question_label_frame, text="题干:", font=TITLE_FONT, width=6).pack(side=tk.LEFT)
        self.question_text_var = tk.StringVar(value="等待识别...")
        self.question_text_label = ttk.Label(
            question_label_frame,
            textvariable=self.question_text_var,
            font=DEFAULT_FONT,
            foreground="#333333",
            wraplength=450
        )
        self.question_text_label.pack(side=tk.LEFT, fill=tk.X, expand=True)

        # 题库答案显示（在题干和选项之间）
        bank_answer_frame = ttk.Frame(result_frame)
        bank_answer_frame.pack(fill=tk.X, pady=(5, 5))

        ttk.Label(bank_answer_frame, text="题库答案:", font=TITLE_FONT, width=8, foreground="#1976D2").pack(
            side=tk.LEFT)
        self.bank_answer_var = tk.StringVar(value="--")
        self.bank_answer_label = ttk.Label(
            bank_answer_frame,
            textvariable=self.bank_answer_var,
            font=("Microsoft YaHei", 9, "bold"),
            foreground="#1976D2",
            wraplength=400
        )
        self.bank_answer_label.pack(side=tk.LEFT, fill=tk.X, expand=True)

        # 四个选项显示（带匹配置信度）
        self.option_vars = {}
        self.option_labels = {}

        for option in ['a', 'b', 'c', 'd']:
            option_frame = ttk.Frame(result_frame)
            option_frame.pack(fill=tk.X, pady=2)

            # 选项标签（A、B、C、D）
            option_color = "#2196F3" if option == 'a' else "#4CAF50" if option == 'b' else "#FF9800" if option == 'c' else "#f44336"
            ttk.Label(
                option_frame,
                text=f"{option.upper()}:",
                font=TITLE_FONT,
                width=4,
                foreground=option_color
            ).pack(side=tk.LEFT)

            # 选项内容
            var = tk.StringVar(value="等待识别...")
            self.option_vars[option] = var
            label = ttk.Label(
                option_frame,
                textvariable=var,
                font=DEFAULT_FONT,
                foreground="#333333",
                wraplength=350
            )
            label.pack(side=tk.LEFT, fill=tk.X, expand=True)

            # 匹配置信度
            confidence_var = tk.StringVar(value="--")
            self.option_vars[f'{option}_confidence'] = confidence_var
            confidence_label = ttk.Label(
                option_frame,
                textvariable=confidence_var,
                font=("Microsoft YaHei", 8),
                foreground="#999999",
                width=8
            )
            confidence_label.pack(side=tk.RIGHT, padx=(5, 0))

            self.option_labels[option] = {
                'content': label,
                'confidence': confidence_label
            }

        # 最佳答案显示区域（大字体）
        best_answer_frame = ttk.Frame(result_frame)
        best_answer_frame.pack(fill=tk.X, pady=(8, 0))

        ttk.Label(best_answer_frame, text="推荐答案:", font=("Microsoft YaHei", 10, "bold"), width=10).pack(
            side=tk.LEFT)
        self.best_answer_var = tk.StringVar(value="等待识别...")
        self.best_answer_label = ttk.Label(
            best_answer_frame,
            textvariable=self.best_answer_var,
            font=LARGE_FONT,
            foreground="#2E7D32",
            wraplength=400
        )
        self.best_answer_label.pack(side=tk.LEFT, fill=tk.X, expand=True)

        # 录入该题按钮（默认隐藏）
        self.add_question_btn = ttk.Button(
            result_frame,
            text="📝 录入该题",
            command=self.open_add_question_dialog,
            style="Accent.TButton"
        )
        # 不立即pack，需要时再显示

        # 截图预览区域
        preview_frame = ttk.LabelFrame(left_frame, text="实时截图预览", padding="5")
        preview_frame.pack(fill=tk.BOTH, expand=True, pady=(5, 8))

        # 创建画布用于显示截图，设置最小高度
        self.preview_canvas = tk.Canvas(preview_frame, bg="#f0f0f0", height=300)
        self.preview_canvas.pack(fill=tk.BOTH, expand=True)
        self.preview_image = None  # 保存PIL Image对象
        self.preview_photo = None  # 保存PhotoImage对象
        self.last_screenshot = None  # 保存最后一张截图，用于resize时重绘

        # 在画布上显示提示文字
        self.preview_canvas.create_text(
            280, 150,
            text="截图预览将在此处显示",
            fill="#999999",
            font=("Microsoft YaHei", 12)
        )

        # 绑定窗口大小改变事件
        self.root.bind('<Configure>', self.on_window_resize)

        # === 右侧日志区 ===
        # 控制按钮区域（放在日志上方）
        btn_frame = ttk.Frame(right_frame)
        btn_frame.pack(fill=tk.X, pady=(0, 5))

        self.test_btn = ttk.Button(btn_frame, text="🧪 测试功能", command=self.test_ocr)
        self.test_btn.pack(side=tk.LEFT, padx=2, expand=True, fill=tk.X)

        self.start_btn = ttk.Button(btn_frame, text="开始截图 (Home)", command=self.start_capturing)
        self.start_btn.pack(side=tk.LEFT, padx=2, expand=True, fill=tk.X)

        self.stop_btn = ttk.Button(btn_frame, text="停止截图 (End)", command=self.stop_capturing, state=tk.DISABLED)
        self.stop_btn.pack(side=tk.LEFT, padx=2, expand=True, fill=tk.X)

        # 状态显示
        self.status_var = tk.StringVar(value="⏸️ 待机中 - 按 Home 开始截图")
        status_label = ttk.Label(
            right_frame,
            textvariable=self.status_var,
            font=("Microsoft YaHei", 9),
            foreground="#666666"
        )
        status_label.pack(pady=(0, 5))

        # 日志区域
        log_frame = ttk.LabelFrame(right_frame, text="最近日志", padding="8")
        log_frame.pack(fill=tk.BOTH, expand=True)

        # 创建滚动条（必须先pack滚动条）
        log_scrollbar = ttk.Scrollbar(log_frame, orient=tk.VERTICAL)
        log_scrollbar.pack(side=tk.RIGHT, fill=tk.Y, padx=(5, 5), pady=5)

        # 使用Text控件代替Label，可以显示更多行
        self.log_text = tk.Text(
            log_frame,
            font=("Microsoft YaHei UI", 9),
            foreground="#333333",
            height=40,  # 增加行数，填满右侧空间
            wrap=tk.WORD,
            state=tk.NORMAL,  # 允许编辑（但通过程序控制）
            bg="#ffffff",
            relief=tk.SUNKEN,
            bd=1,
            padx=5,
            pady=5,
            yscrollcommand=log_scrollbar.set  # 关联滚动条
        )
        self.log_text.pack(fill=tk.BOTH, expand=True, padx=(5, 0), pady=5)  # 使用BOTH并expand

        # 配置滚动条命令
        log_scrollbar.config(command=self.log_text.yview)

        # 初始化用户滚动状态标记
        self.user_scrolling = False
        self.was_at_bottom = True  # 标记是否在底部

        # 绑定滚轮事件
        self.log_text.bind('<MouseWheel>', self.on_log_mouse_wheel)
        self.log_text.bind('<Button-4>', self.on_log_mouse_wheel)  # Linux
        self.log_text.bind('<Button-5>', self.on_log_mouse_wheel)  # Linux

        # 初始化显示
        self.log_text.insert(tk.END, "暂无日志\n")
        self.log_text.config(state=tk.DISABLED)  # 初始状态禁止编辑

        # 绑定关闭事件
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

    def add_log(self, message):
        """添加日志消息"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        log_entry = f"[{timestamp}] {message}"
        self.log_messages.append(log_entry)

        # 保持日志数量在限制内（默认200条）
        if len(self.log_messages) > self.max_log_messages:
            self.log_messages = self.log_messages[-self.max_log_messages:]

        # 更新显示 - 使用Text控件
        log_text = "\n".join(self.log_messages)
        self.log_text.config(state=tk.NORMAL)  # 临时启用编辑

        # 保存当前滚动位置
        current_view = self.log_text.yview()

        # 清空并重新插入内容
        self.log_text.delete(1.0, tk.END)
        self.log_text.insert(tk.END, log_text)

        # 智能滚动：只有当用户在底部或刚添加日志时才自动滚动
        if self.was_at_bottom:
            self.log_text.see(tk.END)  # 自动滚动到最后一行
        else:
            # 如果用户不在底部，恢复之前的滚动位置
            self.log_text.yview_moveto(current_view[0])

        self.log_text.config(state=tk.DISABLED)  # 恢复禁止编辑

    def on_log_mouse_wheel(self, event):
        """处理日志区域的滚轮事件"""
        # 检测用户是否手动滚动
        current_pos = self.log_text.yview()[1]  # 获取当前视图底部位置（0-1之间）

        # 判断是否在底部（允许小误差）
        at_bottom = current_pos >= 0.99

        # 如果用户正在向上滚动且不在底部，标记为非底部状态
        if not at_bottom:
            self.was_at_bottom = False
        else:
            # 如果滚动到底部，恢复自动滚动
            self.was_at_bottom = True

    def setup_hotkeys(self):
        """设置全局快捷键"""
        try:
            # 注册Home键 - 开始/更新截图
            keyboard.add_hotkey('home', self.on_home_pressed, suppress=False)
            # 注册End键 - 停止截图
            keyboard.add_hotkey('end', self.on_end_pressed, suppress=False)
        except Exception as e:
            messagebox.showerror("错误", f"快捷键注册失败: {str(e)}")

    def on_home_pressed(self):
        """Home键按下事件"""
        if not self.is_capturing:
            self.start_capturing()
        else:
            # 如果已经在截图，立即捕获一帧
            self.capture_once()

    def on_end_pressed(self):
        """End键按下事件"""
        self.stop_capturing()

    def start_capturing(self):
        """开始截图"""
        if self.is_capturing:
            return

        self.is_capturing = True
        self.status_var.set("🔴 正在截图...")
        self.start_btn.config(state=tk.DISABLED)
        self.stop_btn.config(state=tk.NORMAL)

        # 禁用模式选择
        self.mode_radio_window.config(state=tk.DISABLED)
        self.mode_radio_fullscreen.config(state=tk.DISABLED)

        mode_name = "窗口" if self.capture_mode.get() == "window" else "全屏"
        self.add_log(f"开始{mode_name}截图")

        # 启动截图线程
        self.screenshot_thread = threading.Thread(target=self.capture_loop, daemon=True)
        self.screenshot_thread.start()

    def stop_capturing(self):
        """停止截图"""
        self.is_capturing = False
        self.status_var.set("⏸️ 已停止 - 按 Home 开始截图")
        self.start_btn.config(state=tk.NORMAL)
        self.stop_btn.config(state=tk.DISABLED)

        # 启用模式选择
        self.mode_radio_window.config(state=tk.NORMAL)
        self.mode_radio_fullscreen.config(state=tk.NORMAL)

        self.add_log("停止截图")

    def capture_loop(self):
        """截图循环 - 在后台线程运行"""
        while self.is_capturing:
            current_time = time.time()
            # 控制截图频率，避免过度占用资源
            if current_time - self.last_capture_time >= self.capture_interval:
                self.capture_once()
                self.last_capture_time = current_time
            else:
                time.sleep(0.1)  # 短暂休眠，降低CPU占用

    def capture_once(self):
        """执行一次截图"""
        try:
            mode = self.capture_mode.get()

            if mode == "window":
                screenshot = self.capture_active_window()
            else:
                screenshot = self.capture_fullscreen()

            if screenshot:
                # 查找并标记模板
                marked_screenshot = self.find_and_mark_template(screenshot)

                # 始终保存为固定文件名，覆盖旧文件，保证是最新的
                filepath = os.path.join(self.output_dir, "latest_screenshot.png")
                marked_screenshot.save(filepath, "PNG", optimize=True)

                self.current_screenshot_path = filepath

                # 更新预览图（在主线程中）
                self.root.after(0, lambda: self.update_preview(marked_screenshot))

                # 不再每次截图都记录日志，减少冗余

        except Exception as e:
            error_msg = f"截图错误: {str(e)}"
            print(error_msg)
            self.root.after(0, lambda: self.add_log(error_msg))

    def test_ocr(self):
        """使用测试图片进行OCR功能测试"""
        test_image_path = "img/test.png"

        # 检查测试图片是否存在
        if not os.path.exists(test_image_path):
            messagebox.showerror("错误", f"测试图片不存在: {test_image_path}")
            return

        try:
            self.add_log("🧪 开始OCR功能测试...")

            # 加载测试图片
            test_image = Image.open(test_image_path)

            # 更新预览
            self.update_preview(test_image)
            self.add_log(f"✓ 已加载测试图片: {test_image.size[0]}x{test_image.size[1]}")

            # 执行模板匹配和标记
            marked_image = self.find_and_mark_template(test_image)

            # 更新预览为标记后的图片
            self.update_preview(marked_image)

            self.add_log("✓ OCR测试完成！请查看日志中的识别结果")

        except Exception as e:
            error_msg = f"测试失败: {str(e)}"
            print(error_msg)
            self.add_log(f"✗ {error_msg}")
            messagebox.showerror("错误", error_msg)

    def find_and_mark_template(self, screenshot):
        """在截图中查找模板并绘制边框"""
        try:
            template_path = "img/title.png"

            # 检查模板文件是否存在
            if not os.path.exists(template_path):
                self.add_log(f"模板文件不存在: {template_path}")
                return screenshot

            # 转换为OpenCV格式
            screenshot_cv = cv2.cvtColor(np.array(screenshot), cv2.COLOR_RGB2BGR)
            template_cv = cv2.imread(template_path)

            if template_cv is None:
                self.add_log("无法读取模板文件")
                return screenshot

            # 获取模板尺寸
            template_height, template_width = template_cv.shape[:2]

            # 执行模板匹配
            result = cv2.matchTemplate(screenshot_cv, template_cv, cv2.TM_CCOEFF_NORMED)

            # 设置阈值
            threshold = 0.8
            locations = np.where(result >= threshold)

            # 转换为PIL图像以便绘制
            marked_image = screenshot.copy()
            draw = ImageDraw.Draw(marked_image)

            found_count = 0
            matched_positions = []  # 存储所有匹配的坐标

            # 遍历所有匹配位置
            for pt in zip(*locations[::-1]):  # 反转坐标顺序
                top_left = pt
                bottom_right = (top_left[0] + template_width, top_left[1] + template_height)
                center_x = top_left[0] + template_width // 2
                center_y = top_left[1] + template_height // 2

                # 绘制title边框（标记找到的模板）
                draw.rectangle(
                    [top_left, bottom_right],
                    outline="red",
                    width=1
                )

                # 绘制蓝色边框（标记要操作的区域 - 题干）
                target_top_left = (top_left[0] + self.offset_top_left_x, top_left[1] + self.offset_top_left_y)
                target_bottom_right = (
                    top_left[0] + self.offset_bottom_right_x, top_left[1] + self.offset_bottom_right_y)
                draw.rectangle(
                    [target_top_left, target_bottom_right],
                    outline="blue",
                    width=1
                )

                # 绘制绿色边框（标记A、B、C、D四个答案区域）
                answer_areas_info = {}
                for option in ['a', 'b', 'c', 'd']:
                    area_config = self.answer_areas[option]
                    ans_top_left = (top_left[0] + area_config['top_left_x'], top_left[1] + area_config['top_left_y'])
                    ans_bottom_right = (
                        top_left[0] + area_config['bottom_right_x'], top_left[1] + area_config['bottom_right_y'])
                    draw.rectangle(
                        [ans_top_left, ans_bottom_right],
                        outline="green",
                        width=1
                    )
                    answer_areas_info[option] = {
                        'top_left': ans_top_left,
                        'bottom_right': ans_bottom_right
                    }

                # 记录坐标信息
                matched_positions.append({
                    'top_left': top_left,
                    'bottom_right': bottom_right,
                    'center': (center_x, center_y),
                    'width': template_width,
                    'height': template_height,
                    'target_area': {
                        'top_left': target_top_left,
                        'bottom_right': target_bottom_right
                    },
                    'answer_areas': answer_areas_info  # 包含A、B、C、D四个选项区域
                })

                found_count += 1

            # 如果找到匹配，执行自定义操作
            if found_count > 0:
                # 只在首次找到或数量变化时记录日志
                self.add_log(f"✓ 找到{found_count}个目标")
                # 在这里调用你的自定义操作，传递原始截图
                self.on_template_found(screenshot, matched_positions)
            else:
                # 不记录未找到的日志，减少噪音
                pass

            return marked_image

        except Exception as e:
            error_msg = f"模板匹配错误: {str(e)}"
            print(error_msg)
            self.add_log(error_msg)
            return screenshot

    def on_template_found(self, screenshot, positions):
        """
        当找到模板时执行的自定义操作
        
        Args:
            screenshot: 原始截图（PIL Image）
            positions: 匹配位置列表，每个元素包含:
                - top_left: 左上角坐标 (x, y)
                - bottom_right: 右下角坐标 (x, y)
                - center: 中心点坐标 (x, y)
                - width: 模板宽度
                - height: 模板高度
                - target_area: 目标区域 {'top_left': (x,y), 'bottom_right': (x,y)}
        """
        # ========================================
        # 在这里编写你对坐标的操作代码
        # ========================================

        # 简化日志：只显示关键信息
        if positions:
            pos = positions[0]  # 只处理第一个目标
            base_x, base_y = pos['top_left']
            self.add_log(f"📍 目标@({base_x},{base_y})")

            # 如果需要更详细的信息，可以取消下面的注释
            # target_area = pos['target_area']
            # target_top_left = target_area['top_left']
            # target_bottom_right = target_area['bottom_right']
            # self.add_log(f"  框选: ({target_top_left[0]},{target_top_left[1]})-({target_bottom_right[0]},{target_bottom_right[1]})")

            # 执行OCR识别
            self.perform_ocr_on_areas(screenshot, pos)

    def perform_ocr_on_areas(self, screenshot, position_info):
        """
        对题干和答案区域进行OCR识别（PaddleOCR）
        
        Args:
            screenshot: PIL Image对象（原始截图）
            position_info: 位置信息字典
        """
        if not self.ocr_engine:
            return

        # 检查OCR间隔，避免频繁调用导致卡顿
        current_time = time.time()
        if current_time - self.last_ocr_time < self.ocr_interval:
            return  # 距离上次OCR时间太短，跳过

        try:
            # 获取基准坐标
            base_x, base_y = position_info['top_left']

            # 提取题干区域（直接使用目标区域坐标）
            question_area = position_info['target_area']
            q_left = question_area['top_left'][0]
            q_top = question_area['top_left'][1]
            q_right = question_area['bottom_right'][0]
            q_bottom = question_area['bottom_right'][1]

            # 裁剪题干区域
            question_image = screenshot.crop((q_left, q_top, q_right, q_bottom))

            # 对比题干图片是否与上一次相同
            if self.last_question_image is not None:
                # 比较两张图片是否完全相同
                if list(question_image.getdata()) == list(self.last_question_image.getdata()):
                    # 题干相同，跳过OCR
                    return

            # 保存当前题干图片，用于下次对比
            self.last_question_image = question_image.copy()

            # 转换为OpenCV格式（保持原始分辨率，不缩放以保证准确度）
            question_cv = cv2.cvtColor(np.array(question_image), cv2.COLOR_RGB2BGR)

            # OCR识别题干（PaddleOCR API）
            question_result = self.ocr_engine.ocr(question_cv, cls=True)

            # 提取题干文字
            question_text = ""
            if question_result and question_result[0]:
                for line in question_result[0]:
                    text = line[1][0]  # 获取识别的文字
                    confidence = line[1][1]  # 置信度
                    if confidence > 0.5:  # 只保留置信度大于0.5的结果
                        question_text += text

            # 提取并识别四个答案区域（A、B、C、D）
            answer_texts = {}
            for option in ['a', 'b', 'c', 'd']:
                answer_area = position_info['answer_areas'][option]
                # 直接使用答案区域坐标
                a_left = answer_area['top_left'][0]
                a_top = answer_area['top_left'][1]
                a_right = answer_area['bottom_right'][0]
                a_bottom = answer_area['bottom_right'][1]

                # 裁剪答案区域
                answer_image = screenshot.crop((a_left, a_top, a_right, a_bottom))

                # 转换为OpenCV格式（保持原始分辨率，不缩放以保证准确度）
                answer_cv = cv2.cvtColor(np.array(answer_image), cv2.COLOR_RGB2BGR)

                # OCR识别答案（PaddleOCR API）
                answer_result = self.ocr_engine.ocr(answer_cv, cls=True)

                # 提取答案文字
                answer_text = ""
                if answer_result and answer_result[0]:
                    for line in answer_result[0]:
                        text = line[1][0]
                        confidence = line[1][1]
                        if confidence > 0.5:
                            answer_text += text

                answer_texts[option] = answer_text

            # 记录识别结果
            if question_text:
                self.add_log(f"📝 题干: {question_text}")

            # 显示四个选项的识别结果
            for option in ['a', 'b', 'c', 'd']:
                if answer_texts[option]:
                    self.add_log(f"✅ 选项{option.upper()}: {answer_texts[option]}")

            # 从题库中查找正确答案
            correct_option = None
            match_similarity = 0.0
            option_similarities = {}
            bank_answer_text = None  # 题库答案文本
            if question_text and answer_texts:
                # 第一步：先在本地题库中查找
                self.add_log("🔍 正在本地题库中查找...")
                correct_option, match_similarity, option_similarities, bank_answer_text = self.find_correct_answer(
                    question_text, answer_texts)

                if correct_option:
                    # 本地找到了，检查答案是否可信（选项相似度是否合理）
                    best_option_similarity = max(option_similarities.values()) if option_similarities else 0.0

                    if best_option_similarity >= 0.7:  # 选项相似度也较高，认为匹配可靠
                        if match_similarity >= 1.0:
                            self.add_log(
                                f"🎯 本地题库完全匹配: 选项{correct_option.upper()} ({answer_texts[correct_option]}) [置信度: {best_option_similarity:.0%}]")
                        else:
                            self.add_log(
                                f"🎯 本地题库匹配: 选项{correct_option.upper()} ({answer_texts[correct_option]}) [题干相似度: {match_similarity:.0%}, 选项置信度: {best_option_similarity:.0%}]")
                    else:
                        # 本地找到的题干匹配，但答案不匹配，说明可能是误匹配，继续查询网络
                        self.add_log(
                            f"⚠️  本地题库题干匹配但答案不匹配（选项置信度: {best_option_similarity:.0%}），继续查询网络题库...")
                        correct_option = None  # 重置，准备查询网络
                        match_similarity = 0.0
                        option_similarities = {}
                        bank_answer_text = None

                # 第二步：如果本地未找到或不可信，查询网络API
                if not correct_option:
                    self.add_log("━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
                    self.add_log("🌐 本地未找到可靠答案，开始查询网络题库...")
                    online_correct_option, online_option_similarities = self.query_online_api(question_text,
                                                                                              answer_texts)

                    if online_correct_option:
                        # 网络API找到答案
                        correct_option = online_correct_option
                        match_similarity = 1.0  # 来自网络，设为1.0
                        option_similarities = online_option_similarities
                        best_option_similarity = max(option_similarities.values()) if option_similarities else 0.0
                        # 从题库中获取答案文本
                        bank_answer_text = self.get_bank_answer_by_option(correct_option, answer_texts)
                        self.add_log(
                            f"✅ 网络题库找到答案: 选项{correct_option.upper()} ({answer_texts[correct_option]}) [置信度: {best_option_similarity:.0%}]")
                        self.add_log("━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
                    else:
                        # 网络也未找到
                        self.add_log("❌ 网络题库也未找到该题目")
                        self.add_log("💡 您可以点击'录入该题'按钮手动添加")
                        self.add_log("━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

            # 更新UI显示区域
            self.update_result_display(question_text, answer_texts, correct_option, match_similarity,
                                       option_similarities, bank_answer_text)

            # 更新最后OCR时间
            self.last_ocr_time = time.time()

        except Exception as e:
            error_msg = f"OCR识别错误: {str(e)}"
            print(error_msg)
            # 不记录到日志，避免刷屏

    def update_preview(self, pil_image):
        """更新截图预览"""
        try:
            # 保存最后一张截图，用于resize时重绘
            self.last_screenshot = pil_image

            # 获取画布尺寸
            canvas_width = self.preview_canvas.winfo_width()
            canvas_height = self.preview_canvas.winfo_height()

            if canvas_width <= 1 or canvas_height <= 1:
                # 如果画布还没有正确尺寸，使用默认值
                canvas_width = 480
                canvas_height = 250

            # 计算缩放比例，保持纵横比
            img_width, img_height = pil_image.size
            scale = min(canvas_width / img_width, canvas_height / img_height)
            new_width = int(img_width * scale)
            new_height = int(img_height * scale)

            # 缩放图像
            resized_image = pil_image.resize((new_width, new_height), Image.LANCZOS)

            # 转换为PhotoImage
            photo = ImageTk.PhotoImage(resized_image)

            # 清除画布
            self.preview_canvas.delete("all")

            # 在画布中央显示图像
            x = (canvas_width - new_width) // 2
            y = (canvas_height - new_height) // 2
            self.preview_canvas.create_image(x, y, anchor=tk.NW, image=photo)

            # 保存引用，防止被垃圾回收
            self.preview_image = resized_image
            self.preview_photo = photo

        except Exception as e:
            print(f"更新预览失败: {str(e)}")

    def on_window_resize(self, event):
        """窗口大小改变事件处理"""
        # 只处理主窗口的resize事件，忽略子窗口
        if event.widget == self.root and self.last_screenshot is not None:
            # 延迟重绘，避免频繁调用
            if hasattr(self, '_resize_timer'):
                self.root.after_cancel(self._resize_timer)
            self._resize_timer = self.root.after(100, lambda: self.update_preview(self.last_screenshot))

    def capture_fullscreen(self):
        """捕获全屏截图 - 优化性能"""
        try:
            # 使用pyautogui截图，返回PIL Image对象
            screenshot = pyautogui.screenshot()
            return screenshot
        except Exception as e:
            print(f"全屏截图失败: {str(e)}")
            return None

    def capture_active_window(self):
        """捕获当前活动窗口截图 - 优化性能"""
        try:
            # 获取活动窗口句柄
            hwnd = win32gui.GetForegroundWindow()

            if hwnd == 0:
                print("无法获取活动窗口")
                return None

            # 获取窗口位置信息
            left, top, right, bottom = win32gui.GetWindowRect(hwnd)
            width = right - left
            height = bottom - top

            # 确保窗口尺寸合理
            if width <= 0 or height <= 0:
                print(f"窗口尺寸异常: {width}x{height}")
                return None

            # 截取指定区域（窗口区域）
            screenshot = pyautogui.screenshot(region=(left, top, width, height))
            return screenshot

        except Exception as e:
            print(f"窗口截图失败: {str(e)}")
            # 如果窗口截图失败，降级为全屏截图
            return self.capture_fullscreen()

    def open_settings_dialog(self):
        """打开设置对话框"""
        dialog = tk.Toplevel(self.root)
        dialog.title("参数配置")
        dialog.resizable(True, True)  # 允许调整大小

        # 设置对话框位置在主窗口中央（使用默认尺寸）
        default_width = 850
        default_height = 770
        self.center_window(dialog, default_width, default_height)

        dialog.transient(self.root)  # 设置为子窗口
        dialog.grab_set()  # 模态窗口

        # 创建滚动框架
        canvas = tk.Canvas(dialog)
        scrollbar = ttk.Scrollbar(dialog, orient="vertical", command=canvas.yview)
        scrollable_frame = ttk.Frame(canvas)

        scrollable_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )

        canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        # 主框架
        main_frame = ttk.Frame(scrollable_frame, padding="15")
        main_frame.pack(fill=tk.BOTH, expand=True)

        # 标题
        title_label = ttk.Label(main_frame, text="区域坐标偏移量配置", font=("Microsoft YaHei", 12, "bold"))
        title_label.pack(pady=(0, 10))

        # 说明文字
        info_label = ttk.Label(
            main_frame,
            text="以下坐标是相对于红色边框左上角的偏移量",
            font=DEFAULT_FONT,
            foreground="#666666"
        )
        info_label.pack(pady=(0, 15))

        # === 题干区域（蓝色框）===
        question_frame = ttk.LabelFrame(main_frame, text="题干区域（蓝色框）", padding="10")
        question_frame.pack(fill=tk.X, pady=(0, 10))

        q_input_frame = ttk.Frame(question_frame)
        q_input_frame.pack(fill=tk.X, pady=5)

        # 一行显示所有4个坐标
        ttk.Label(q_input_frame, text="左上角X:", width=8).grid(row=0, column=0, sticky=tk.W, padx=2)
        entry_q_tl_x = ttk.Entry(q_input_frame, width=8)
        entry_q_tl_x.insert(0, str(self.offset_top_left_x))
        entry_q_tl_x.grid(row=0, column=1, padx=2)

        ttk.Label(q_input_frame, text="左上角Y:", width=8).grid(row=0, column=2, sticky=tk.W, padx=2)
        entry_q_tl_y = ttk.Entry(q_input_frame, width=8)
        entry_q_tl_y.insert(0, str(self.offset_top_left_y))
        entry_q_tl_y.grid(row=0, column=3, padx=2)

        ttk.Label(q_input_frame, text="右下角X:", width=8).grid(row=0, column=4, sticky=tk.W, padx=2)
        entry_q_br_x = ttk.Entry(q_input_frame, width=8)
        entry_q_br_x.insert(0, str(self.offset_bottom_right_x))
        entry_q_br_x.grid(row=0, column=5, padx=2)

        ttk.Label(q_input_frame, text="右下角Y:", width=8).grid(row=0, column=6, sticky=tk.W, padx=2)
        entry_q_br_y = ttk.Entry(q_input_frame, width=8)
        entry_q_br_y.insert(0, str(self.offset_bottom_right_y))
        entry_q_br_y.grid(row=0, column=7, padx=2)

        # 题干区域恢复默认按钮
        def reset_question():
            """恢复题干区域默认值"""
            default_config = self.get_default_config()
            default_q = default_config['question_area_offset']
            entry_q_tl_x.delete(0, tk.END)
            entry_q_tl_x.insert(0, str(default_q['top_left_x']))
            entry_q_tl_y.delete(0, tk.END)
            entry_q_tl_y.insert(0, str(default_q['top_left_y']))
            entry_q_br_x.delete(0, tk.END)
            entry_q_br_x.insert(0, str(default_q['bottom_right_x']))
            entry_q_br_y.delete(0, tk.END)
            entry_q_br_y.insert(0, str(default_q['bottom_right_y']))

        q_btn_frame = ttk.Frame(question_frame)
        q_btn_frame.pack(pady=(10, 0))
        reset_q_btn = ttk.Button(q_btn_frame, text="恢复默认", command=reset_question)
        reset_q_btn.pack()

        # === 答案区域（A、B、C、D四个选项）===
        answer_frame = ttk.LabelFrame(main_frame, text="答案区域（A、B、C、D四个选项）", padding="10")
        answer_frame.pack(fill=tk.X, pady=(0, 10))

        # 为每个选项创建输入框
        entries = {}

        for option in ['a', 'b', 'c', 'd']:
            option_frame = ttk.LabelFrame(answer_frame, text=f"选项 {option.upper()}", padding="8")
            option_frame.pack(fill=tk.X, pady=5)

            input_frame = ttk.Frame(option_frame)
            input_frame.pack(fill=tk.X, pady=2)

            # 一行显示所有4个坐标
            ttk.Label(input_frame, text="左上角X:", width=8).grid(row=0, column=0, sticky=tk.W, padx=2)
            entry_tl_x = ttk.Entry(input_frame, width=8)
            entry_tl_x.insert(0, str(self.answer_areas[option]['top_left_x']))
            entry_tl_x.grid(row=0, column=1, padx=2)

            ttk.Label(input_frame, text="左上角Y:", width=8).grid(row=0, column=2, sticky=tk.W, padx=2)
            entry_tl_y = ttk.Entry(input_frame, width=8)
            entry_tl_y.insert(0, str(self.answer_areas[option]['top_left_y']))
            entry_tl_y.grid(row=0, column=3, padx=2)

            ttk.Label(input_frame, text="右下角X:", width=8).grid(row=0, column=4, sticky=tk.W, padx=2)
            entry_br_x = ttk.Entry(input_frame, width=8)
            entry_br_x.insert(0, str(self.answer_areas[option]['bottom_right_x']))
            entry_br_x.grid(row=0, column=5, padx=2)

            ttk.Label(input_frame, text="右下角Y:", width=8).grid(row=0, column=6, sticky=tk.W, padx=2)
            entry_br_y = ttk.Entry(input_frame, width=8)
            entry_br_y.insert(0, str(self.answer_areas[option]['bottom_right_y']))
            entry_br_y.grid(row=0, column=7, padx=2)

            entries[option] = {
                'tl_x': entry_tl_x,
                'tl_y': entry_tl_y,
                'br_x': entry_br_x,
                'br_y': entry_br_y
            }

        # 恢复默认按钮
        def reset_answer():
            """恢复答案区域默认值"""
            default_config = self.get_default_config()
            for option in ['a', 'b', 'c', 'd']:
                config_key = f'answer_area_offset_{option}'
                default_a = default_config[config_key]
                entries[option]['tl_x'].delete(0, tk.END)
                entries[option]['tl_x'].insert(0, str(default_a['top_left_x']))
                entries[option]['tl_y'].delete(0, tk.END)
                entries[option]['tl_y'].insert(0, str(default_a['top_left_y']))
                entries[option]['br_x'].delete(0, tk.END)
                entries[option]['br_x'].insert(0, str(default_a['bottom_right_x']))
                entries[option]['br_y'].delete(0, tk.END)
                entries[option]['br_y'].insert(0, str(default_a['bottom_right_y']))

        a_btn_frame = ttk.Frame(answer_frame)
        a_btn_frame.pack(pady=(10, 0))
        reset_a_btn = ttk.Button(a_btn_frame, text="恢复默认", command=reset_answer)
        reset_a_btn.pack()

        # 按钮框架
        btn_frame = ttk.Frame(main_frame)
        btn_frame.pack(pady=15)

        def save_settings():
            """保存设置"""
            try:
                # 获取题干区域输入值
                new_q_tl_x = int(entry_q_tl_x.get())
                new_q_tl_y = int(entry_q_tl_y.get())
                new_q_br_x = int(entry_q_br_x.get())
                new_q_br_y = int(entry_q_br_y.get())

                # 更新题干区域配置
                self.offset_top_left_x = new_q_tl_x
                self.offset_top_left_y = new_q_tl_y
                self.offset_bottom_right_x = new_q_br_x
                self.offset_bottom_right_y = new_q_br_y

                # 获取并更新四个答案区域配置
                for option in ['a', 'b', 'c', 'd']:
                    config_key = f'answer_area_offset_{option}'
                    new_tl_x = int(entries[option]['tl_x'].get())
                    new_tl_y = int(entries[option]['tl_y'].get())
                    new_br_x = int(entries[option]['br_x'].get())
                    new_br_y = int(entries[option]['br_y'].get())

                    # 更新实例变量
                    self.answer_areas[option] = {
                        'top_left_x': new_tl_x,
                        'top_left_y': new_tl_y,
                        'bottom_right_x': new_br_x,
                        'bottom_right_y': new_br_y
                    }

                    # 更新配置文件
                    self.config[config_key] = {
                        'top_left_x': new_tl_x,
                        'top_left_y': new_tl_y,
                        'bottom_right_x': new_br_x,
                        'bottom_right_y': new_br_y
                    }

                # 更新题干区域配置
                self.config['question_area_offset'] = {
                    'top_left_x': new_q_tl_x,
                    'top_left_y': new_q_tl_y,
                    'bottom_right_x': new_q_br_x,
                    'bottom_right_y': new_q_br_y
                }

                # 保存配置（不显示提示弹窗）
                self.save_config()
                self.add_log(f"✓ 配置已保存")
                dialog.destroy()
            except ValueError:
                messagebox.showerror("错误", "请输入有效的整数！")

        # 保存按钮
        save_btn = ttk.Button(btn_frame, text="保存", command=save_settings)
        save_btn.pack(side=tk.LEFT, padx=10)

        # 取消按钮
        cancel_btn = ttk.Button(btn_frame, text="取消", command=dialog.destroy)
        cancel_btn.pack(side=tk.LEFT, padx=10)

    def open_add_question_dialog(self, prefill_question=None, show_options=True):
        """打开录入题目对话框
            
        Args:
            prefill_question: 预填充的题干内容，如果为None则使用当前识别结果
            show_options: 是否显示选项选择区域（True=从识别结果录入，False=手动录入）
        """
        dialog = tk.Toplevel(self.root)
        dialog.title("录入新题目")

        # 根据是否显示选项来决定窗口大小
        if show_options:
            dialog.geometry("550x480")
            self.center_window(dialog, 550, 480)
        else:
            dialog.geometry("550x350")
            self.center_window(dialog, 550, 350)

        dialog.resizable(True, True)
        dialog.transient(self.root)
        dialog.grab_set()

        # 主框架
        main_frame = ttk.Frame(dialog, padding="15")
        main_frame.pack(fill=tk.BOTH, expand=True)

        # 标题
        title_label = ttk.Label(
            main_frame,
            text="请确认或修改题目信息",
            font=("Microsoft YaHei", 12, "bold")
        )
        title_label.pack(pady=(0, 10))

        # ===== 模块A：题干输入 =====
        ttk.Label(main_frame, text="题干:", font=TITLE_FONT).pack(anchor=tk.W, pady=(5, 2))
        if prefill_question is not None:
            default_question = prefill_question
        else:
            current_question = self.question_text_var.get()
            if current_question in ["等待识别...", ""]:
                default_question = ""
            else:
                default_question = current_question
        question_text_var = tk.StringVar(value=default_question)
        question_entry = ttk.Entry(
            main_frame,
            textvariable=question_text_var,
            font=DEFAULT_FONT,
            width=60
        )
        question_entry.pack(fill=tk.X, pady=(0, 10))

        # 答案输入框变量（提前声明）
        answer_text_var = tk.StringVar(value="")

        # ===== 模块B：选项选择（仅当show_options=True时显示）=====
        if show_options:
            # 获取当前识别的四个选项
            current_options = {}
            for option in ['a', 'b', 'c', 'd']:
                opt_text = self.option_vars[option].get() if hasattr(self, 'option_vars') else ""
                if opt_text and opt_text not in ["等待识别...", ""]:
                    current_options[option] = opt_text

            # 如果有识别到的选项，显示单选按钮
            if current_options:
                ttk.Label(
                    main_frame,
                    text="从识别结果中选择：",
                    font=DEFAULT_FONT,
                    foreground="#666666"
                ).pack(anchor=tk.W, pady=(0, 5))

                answer_choice_var = tk.StringVar(value="")

                for option_key, option_text in current_options.items():
                    radio_frame = ttk.Frame(main_frame)
                    radio_frame.pack(fill=tk.X, pady=2)

                    ttk.Radiobutton(
                        radio_frame,
                        text=f"{option_key.upper()}. {option_text}",
                        variable=answer_choice_var,
                        value=option_text,
                        command=lambda: answer_text_var.set(answer_choice_var.get())
                    ).pack(side=tk.LEFT, anchor=tk.W)

        # ===== 模块C：答案输入 =====
        answer_label_text = "或手动输入：" if show_options else "正确答案:"
        ttk.Label(
            main_frame,
            text=answer_label_text,
            font=DEFAULT_FONT,
            foreground="#666666"
        ).pack(anchor=tk.W, pady=(10, 2))

        answer_entry = ttk.Entry(
            main_frame,
            textvariable=answer_text_var,
            font=DEFAULT_FONT,
            width=60
        )
        answer_entry.pack(fill=tk.X, pady=(0, 10))

        # 按钮区域
        btn_frame = ttk.Frame(main_frame)
        btn_frame.pack(pady=(15, 0))

        def save_question():
            """保存题目到题库"""
            question = question_text_var.get().strip()
            answer = answer_text_var.get().strip()

            # 验证必填项
            if not question:
                messagebox.showwarning("警告", "题干不能为空！")
                return

            if not answer:
                messagebox.showwarning("警告", "正确答案不能为空！")
                return

            # 构建题目数据
            new_question = {
                "question": question,
                "answer": answer
            }

            try:
                # 加载现有题库
                question_bank_file = '题库.json'
                question_bank = []
                if os.path.exists(question_bank_file):
                    with open(question_bank_file, 'r', encoding='utf-8') as f:
                        question_bank = json.load(f)

                # 添加新题目
                question_bank.append(new_question)

                # 保存到文件
                with open(question_bank_file, 'w', encoding='utf-8') as f:
                    json.dump(question_bank, f, ensure_ascii=False, indent=2)

                # 更新内存中的题库
                self.question_bank = question_bank

                messagebox.showinfo("成功", f"题目已录入！\n\n当前题库共有 {len(question_bank)} 道题目")
                self.add_log(f"✅ 录入新题目: {question[:30]}...")

                # 关闭对话框
                dialog.destroy()

            except Exception as e:
                messagebox.showerror("错误", f"保存失败: {str(e)}")
                self.add_log(f"❌ 录入题目失败: {str(e)}")

        # 保存按钮
        save_btn = ttk.Button(btn_frame, text="💾 保存", command=save_question)
        save_btn.pack(side=tk.LEFT, padx=10)

        # 取消按钮
        cancel_btn = ttk.Button(btn_frame, text="❌ 取消", command=dialog.destroy)
        cancel_btn.pack(side=tk.LEFT, padx=10)

    def open_search_dialog(self):
        """打开手动检索对话框"""
        dialog = tk.Toplevel(self.root)
        dialog.title("🔍 手动检索题目")
        dialog.geometry("700x600")
        dialog.resizable(True, True)

        self.center_window(dialog, 700, 600)
        dialog.transient(self.root)
        dialog.grab_set()

        # 主框架
        main_frame = ttk.Frame(dialog, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)

        # 搜索框区域
        search_frame = ttk.Frame(main_frame)
        search_frame.pack(fill=tk.X, pady=(0, 10))

        ttk.Label(search_frame, text="搜索关键词:", font=TITLE_FONT).pack(side=tk.LEFT, padx=(0, 5))

        search_var = tk.StringVar()
        search_entry = ttk.Entry(
            search_frame,
            textvariable=search_var,
            font=DEFAULT_FONT,
            width=40
        )
        search_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))

        # 搜索按钮（先不绑定命令）
        search_btn = ttk.Button(search_frame, text="🔍 检索")
        search_btn.pack(side=tk.LEFT)

        # 分隔线
        ttk.Separator(main_frame, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=5)

        # 本地题库结果区域
        local_label = ttk.Label(
            main_frame,
            text="📚 本地题库检索结果（按相似度排序）",
            font=("Microsoft YaHei", 11, "bold"),
            foreground="#2196F3"
        )
        local_label.pack(anchor=tk.W, pady=(5, 5))

        local_canvas = tk.Canvas(main_frame, height=180, bg="#f9f9f9")
        local_scrollbar = ttk.Scrollbar(main_frame, orient=tk.VERTICAL, command=local_canvas.yview)
        local_results_frame = ttk.Frame(local_canvas)

        local_results_frame.bind(
            "<Configure>",
            lambda e: local_canvas.configure(scrollregion=local_canvas.bbox("all"))
        )

        local_canvas.create_window((0, 0), window=local_results_frame, anchor="nw", width=650)
        local_canvas.configure(yscrollcommand=local_scrollbar.set)

        # Canvas和Scrollbar直接pack到main_frame
        local_canvas.pack(fill=tk.BOTH, expand=True)
        local_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # 分隔线
        ttk.Separator(main_frame, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=5)

        # 网络API结果区域
        online_label = ttk.Label(
            main_frame,
            text="🌐 网络题库检索结果（按相似度排序）",
            font=("Microsoft YaHei", 11, "bold"),
            foreground="#4CAF50"
        )
        online_label.pack(anchor=tk.W, pady=(5, 5))

        online_canvas = tk.Canvas(main_frame, height=180, bg="#f9f9f9")
        online_scrollbar = ttk.Scrollbar(main_frame, orient=tk.VERTICAL, command=online_canvas.yview)
        online_results_frame = ttk.Frame(online_canvas)

        online_results_frame.bind(
            "<Configure>",
            lambda e: online_canvas.configure(scrollregion=online_canvas.bbox("all"))
        )

        online_canvas.create_window((0, 0), window=online_results_frame, anchor="nw", width=650)
        online_canvas.configure(yscrollcommand=online_scrollbar.set)

        # Canvas和Scrollbar直接pack到main_frame
        online_canvas.pack(fill=tk.BOTH, expand=True)
        online_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # 定义搜索函数（在所有Frame创建之后）
        def do_search():
            """执行搜索"""
            keyword = search_var.get().strip()
            if not keyword:
                messagebox.showwarning("警告", "请输入搜索关键词！")
                return

            # 清空之前的结果
            for widget in local_results_frame.winfo_children():
                widget.destroy()
            for widget in online_results_frame.winfo_children():
                widget.destroy()

            # 搜索本地题库
            self.search_local_questions(keyword, local_results_frame, dialog)

            # 搜索网络API
            self.search_online_questions(keyword, online_results_frame, dialog)

        # 绑定搜索命令
        search_btn.config(command=do_search)

        # 关闭按钮
        close_btn = ttk.Button(main_frame, text="❌ 关闭", command=dialog.destroy)
        close_btn.pack(pady=(10, 0))

    def search_local_questions(self, keyword, results_frame, dialog):
        """搜索本地题库"""
        if not self.question_bank:
            ttk.Label(results_frame, text="本地题库为空", font=DEFAULT_FONT, foreground="#999999").pack(pady=10)
            return

        # 计算所有题目的相似度（改进版）
        scored_questions = []
        for q in self.question_bank:
            question = q['question']

            # 如果关键词完全包含在题目中，给予高分数
            if keyword in question:
                # 根据包含位置和部分长度计算分数
                score = 0.8 + (len(keyword) / len(question)) * 0.2  # 0.8-1.0
            else:
                # 否则使用字符集相似度
                score = self.similarity_score(keyword, question) * 0.5  # 降低权重

            scored_questions.append((q, score))

        # 按相似度降序排序
        scored_questions.sort(key=lambda x: x[1], reverse=True)

        # 取前3条
        top3 = scored_questions[:3]

        if not top3 or top3[0][1] < 0.3:
            ttk.Label(results_frame, text="未找到匹配的题目", font=DEFAULT_FONT, foreground="#999999").pack(pady=10)
            return

        # 显示结果
        for idx, (q, similarity) in enumerate(top3):
            result_frame = ttk.Frame(results_frame)
            result_frame.pack(fill=tk.X, padx=5, pady=3)

            # 题干和答案
            info_frame = ttk.Frame(result_frame)
            info_frame.pack(side=tk.LEFT, fill=tk.X, expand=True)

            ttk.Label(
                info_frame,
                text=f"{idx + 1}. {q['question']}",
                font=DEFAULT_FONT,
                wraplength=400
            ).pack(anchor=tk.W)

            ttk.Label(
                info_frame,
                text=f"   答案: {q['answer']}  (相似度: {similarity:.0%})",
                font=DEFAULT_FONT,
                foreground="#666666"
            ).pack(anchor=tk.W)

            # 修改按钮
            def make_modify_callback(question_data):
                def modify_question():
                    dialog.destroy()  # 关闭搜索窗口
                    self.open_edit_question_dialog(question_data)

                return modify_question

            modify_btn = ttk.Button(
                result_frame,
                text="✏️ 修改该题",
                command=make_modify_callback(q)
            )
            modify_btn.pack(side=tk.RIGHT, padx=5)

        # 强制更新Canvas的滚动区域
        results_frame.update_idletasks()
        canvas = results_frame.master
        if hasattr(canvas, 'configure'):
            canvas.configure(scrollregion=canvas.bbox("all"))

    def search_online_questions(self, keyword, results_frame, dialog):
        """搜索网络题库"""
        try:
            api_url = "https://api.qqsgtk.cn/qqsgtkApi/findByQuestion"

            # 构建请求参数（不传answer字段，否则返回值为空）
            payload = {
                "question": keyword
            }

            headers = {
                "Content-Type": "application/json",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            }

            response = requests.post(api_url, json=payload, headers=headers, timeout=5)

            if len(response.text) == 0:
                ttk.Label(results_frame, text="网络API返回空响应", font=DEFAULT_FONT, foreground="#999999").pack(
                    pady=10)
                return

            if response.status_code == 200:
                result = response.json()

                if result.get('success') and result.get('code') == 200:
                    data = result.get('data', [])

                    if not data:
                        ttk.Label(results_frame, text="网络题库未找到匹配", font=DEFAULT_FONT,
                                  foreground="#999999").pack(pady=10)
                        return

                    # 计算相似度并排序（改进版）
                    scored_questions = []
                    for q in data:
                        question = q.get('question', '')

                        # 如果关键词完全包含在题目中，给予高分数
                        if keyword in question:
                            score = 0.8 + (len(keyword) / len(question)) * 0.2  # 0.8-1.0
                        else:
                            # 否则使用字符集相似度
                            score = self.similarity_score(keyword, question) * 0.5  # 降低权重

                        scored_questions.append((q, score))

                    scored_questions.sort(key=lambda x: x[1], reverse=True)
                    top3 = scored_questions[:3]

                    # 显示结果
                    for idx, (q, similarity) in enumerate(top3):
                        result_frame_item = ttk.Frame(results_frame)
                        result_frame_item.pack(fill=tk.X, padx=5, pady=3)

                        info_frame = ttk.Frame(result_frame_item)
                        info_frame.pack(side=tk.LEFT, fill=tk.X, expand=True)

                        ttk.Label(
                            info_frame,
                            text=f"{idx + 1}. {q.get('question', '')}",
                            font=DEFAULT_FONT,
                            wraplength=400
                        ).pack(anchor=tk.W)

                        ttk.Label(
                            info_frame,
                            text=f"   答案: {q.get('answer', '')}  (相似度: {similarity:.0%})",
                            font=DEFAULT_FONT,
                            foreground="#666666"
                        ).pack(anchor=tk.W)

                        # 录入按钮
                        def make_add_callback(question_data):
                            def add_question():
                                dialog.destroy()  # 关闭搜索窗口
                                self.open_add_question_from_search(question_data)

                            return add_question

                        add_btn = ttk.Button(
                            result_frame_item,
                            text="➕ 录入该题",
                            command=make_add_callback(q)
                        )
                        add_btn.pack(side=tk.RIGHT, padx=5)
                else:
                    ttk.Label(results_frame, text=f"API返回错误: {result.get('message', '未知错误')}",
                              font=DEFAULT_FONT, foreground="#f44336").pack(pady=10)
            else:
                ttk.Label(results_frame, text=f"请求失败: HTTP {response.status_code}", font=DEFAULT_FONT,
                          foreground="#f44336").pack(pady=10)

        except requests.exceptions.Timeout:
            ttk.Label(results_frame, text="网络请求超时", font=DEFAULT_FONT, foreground="#f44336").pack(pady=10)
        except requests.exceptions.JSONDecodeError as e:
            ttk.Label(results_frame, text=f"JSON解析失败: API返回格式错误", font=DEFAULT_FONT,
                      foreground="#f44336").pack(pady=10)
        except Exception as e:
            ttk.Label(results_frame, text=f"查询失败: {str(e)}", font=DEFAULT_FONT, foreground="#f44336").pack(pady=10)

    def open_edit_question_dialog(self, question_data):
        """打开编辑题目对话框"""
        dialog = tk.Toplevel(self.root)
        dialog.title("✏️ 修改题目")
        dialog.geometry("500x300")
        dialog.resizable(True, True)

        self.center_window(dialog, 500, 300)
        dialog.transient(self.root)
        dialog.grab_set()

        main_frame = ttk.Frame(dialog, padding="15")
        main_frame.pack(fill=tk.BOTH, expand=True)

        title_label = ttk.Label(
            main_frame,
            text="请修改题目信息",
            font=("Microsoft YaHei", 12, "bold")
        )
        title_label.pack(pady=(0, 10))

        # 题干输入
        ttk.Label(main_frame, text="题干:", font=TITLE_FONT).pack(anchor=tk.W, pady=(5, 2))
        question_text_var = tk.StringVar(value=question_data['question'])
        question_entry = ttk.Entry(
            main_frame,
            textvariable=question_text_var,
            font=DEFAULT_FONT,
            width=60
        )
        question_entry.pack(fill=tk.X, pady=(0, 10))

        # 正确答案输入
        ttk.Label(main_frame, text="正确答案:", font=TITLE_FONT).pack(anchor=tk.W, pady=(5, 2))
        answer_text_var = tk.StringVar(value=question_data['answer'])
        answer_entry = ttk.Entry(
            main_frame,
            textvariable=answer_text_var,
            font=DEFAULT_FONT,
            width=60
        )
        answer_entry.pack(fill=tk.X, pady=(0, 10))

        btn_frame = ttk.Frame(main_frame)
        btn_frame.pack(pady=(15, 0))

        def save_edited_question():
            """保存修改后的题目"""
            new_question = question_text_var.get().strip()
            new_answer = answer_text_var.get().strip()

            if not new_question:
                messagebox.showwarning("警告", "题干不能为空！")
                return

            if not new_answer:
                messagebox.showwarning("警告", "正确答案不能为空！")
                return

            try:
                # 查找原题目在题库中的位置
                original_question = question_data['question']
                for i, q in enumerate(self.question_bank):
                    if q['question'] == original_question:
                        # 更新题目
                        self.question_bank[i]['question'] = new_question
                        self.question_bank[i]['answer'] = new_answer
                        break

                # 保存到文件
                question_bank_file = '题库.json'
                with open(question_bank_file, 'w', encoding='utf-8') as f:
                    json.dump(self.question_bank, f, ensure_ascii=False, indent=2)

                messagebox.showinfo("成功", "题目已修改！")
                self.add_log(f"✅ 修改题目: {new_question[:30]}...")
                dialog.destroy()

            except Exception as e:
                messagebox.showerror("错误", f"保存失败: {str(e)}")
                self.add_log(f"❌ 修改题目失败: {str(e)}")

        save_btn = ttk.Button(btn_frame, text="💾 保存", command=save_edited_question)
        save_btn.pack(side=tk.LEFT, padx=10)

        cancel_btn = ttk.Button(btn_frame, text="❌ 取消", command=dialog.destroy)
        cancel_btn.pack(side=tk.LEFT, padx=10)

    def open_add_question_from_search(self, question_data):
        """从搜索结果打开录入题目对话框"""
        dialog = tk.Toplevel(self.root)
        dialog.title("➕ 录入新题目")
        dialog.geometry("500x300")
        dialog.resizable(True, True)

        self.center_window(dialog, 500, 300)
        dialog.transient(self.root)
        dialog.grab_set()

        main_frame = ttk.Frame(dialog, padding="15")
        main_frame.pack(fill=tk.BOTH, expand=True)

        title_label = ttk.Label(
            main_frame,
            text="请确认或修改题目信息",
            font=("Microsoft YaHei", 12, "bold")
        )
        title_label.pack(pady=(0, 10))

        # 题干输入
        ttk.Label(main_frame, text="题干:", font=TITLE_FONT).pack(anchor=tk.W, pady=(5, 2))
        question_text_var = tk.StringVar(value=question_data.get('question', ''))
        question_entry = ttk.Entry(
            main_frame,
            textvariable=question_text_var,
            font=DEFAULT_FONT,
            width=60
        )
        question_entry.pack(fill=tk.X, pady=(0, 10))

        # 正确答案输入
        ttk.Label(main_frame, text="正确答案:", font=TITLE_FONT).pack(anchor=tk.W, pady=(5, 2))
        answer_text_var = tk.StringVar(value=question_data.get('answer', ''))
        answer_entry = ttk.Entry(
            main_frame,
            textvariable=answer_text_var,
            font=DEFAULT_FONT,
            width=60
        )
        answer_entry.pack(fill=tk.X, pady=(0, 10))

        hint_label = ttk.Label(
            main_frame,
            text="提示：直接输入正确答案内容（如：诸葛亮、207年等）",
            font=DEFAULT_FONT,
            foreground="#666666"
        )
        hint_label.pack(pady=(0, 10))

        btn_frame = ttk.Frame(main_frame)
        btn_frame.pack(pady=(15, 0))

        def save_question():
            """保存题目到题库"""
            question = question_text_var.get().strip()
            answer = answer_text_var.get().strip()

            if not question:
                messagebox.showwarning("警告", "题干不能为空！")
                return

            if not answer:
                messagebox.showwarning("警告", "正确答案不能为空！")
                return

            new_question = {
                "question": question,
                "answer": answer
            }

            try:
                question_bank_file = '题库.json'
                question_bank = []
                if os.path.exists(question_bank_file):
                    with open(question_bank_file, 'r', encoding='utf-8') as f:
                        question_bank = json.load(f)

                question_bank.append(new_question)

                with open(question_bank_file, 'w', encoding='utf-8') as f:
                    json.dump(question_bank, f, ensure_ascii=False, indent=2)

                self.question_bank = question_bank

                messagebox.showinfo("成功", f"题目已录入！\n\n当前题库共有 {len(question_bank)} 道题目")
                self.add_log(f"✅ 录入新题目: {question[:30]}...")
                dialog.destroy()

            except Exception as e:
                messagebox.showerror("错误", f"保存失败: {str(e)}")
                self.add_log(f"❌ 录入题目失败: {str(e)}")

        save_btn = ttk.Button(btn_frame, text="💾 保存", command=save_question)
        save_btn.pack(side=tk.LEFT, padx=10)

        cancel_btn = ttk.Button(btn_frame, text="❌ 取消", command=dialog.destroy)
        cancel_btn.pack(side=tk.LEFT, padx=10)

    def show_about_dialog(self):
        """显示关于对话框"""
        dialog = tk.Toplevel(self.root)
        dialog.title("关于")
        dialog.resizable(False, False)

        # 设置窗口大小和位置
        width = 400
        height = 400
        self.center_window(dialog, width, height)

        dialog.transient(self.root)
        dialog.grab_set()

        # 主框架
        main_frame = ttk.Frame(dialog, padding="20")
        main_frame.pack(fill=tk.BOTH, expand=True)

        # 从配置文件读取应用信息
        app_config = self.config.get('app', {})
        app_name = app_config.get('name', '推举孝廉小助手')
        app_version = app_config.get('version', 'v1.0.0')

        # 标题
        title_label = ttk.Label(
            main_frame,
            text=app_name,
            font=("Microsoft YaHei", 16, "bold"),
            foreground="#1976D2"
        )
        title_label.pack(pady=(0, 10))

        # 版本信息
        version_label = ttk.Label(
            main_frame,
            text=f"版本: {app_version}",
            font=("Microsoft YaHei", 10),
            foreground="#666666"
        )
        version_label.pack(pady=5)

        # 作者信息
        author_frame = ttk.Frame(main_frame)
        author_frame.pack(pady=10)

        ttk.Label(
            author_frame,
            text="仓库地址:",
            font=("Microsoft YaHei", 10),
            foreground="#333333"
        ).pack(side=tk.LEFT)

        # GitHub链接
        github_link = ttk.Label(
            author_frame,
            text="GitHub",
            font=("Microsoft YaHei", 10, "underline"),
            foreground="#1976D2",
            cursor="hand2"
        )
        github_link.pack(side=tk.LEFT, padx=5)
        github_link.bind("<Button-1>", lambda e: self.open_url("https://github.com/tokikirinanaya/qqsg_question"))

        # 项目描述
        desc_label = ttk.Label(
            main_frame,
            text="一款智能截图识别工具，\n自动识别题目并从题库中查找答案",
            font=("Microsoft YaHei", 9),
            foreground="#666666",
            justify=tk.CENTER
        )
        desc_label.pack(pady=15)

        # 关闭按钮
        close_btn = ttk.Button(main_frame, text="关闭", command=dialog.destroy)
        close_btn.pack(pady=10)

    def open_url(self, url):
        """打开URL链接"""
        import webbrowser
        webbrowser.open(url)

    def on_closing(self):
        """关闭窗口时的处理"""
        # 保存当前截图模式到配置
        self.config['screenshot']['default_mode'] = self.capture_mode.get()
        self.save_config()

        self.stop_capturing()
        self.root.destroy()

    def run(self):
        """运行程序"""
        print("=" * 50)
        print("推举孝廉小助手已启动")
        print("快捷键说明:")
        print("  Home键 - 开始/更新截图")
        print("  End键  - 停止截图")
        print("=" * 50)
        print("程序正在运行，可以使用快捷键控制...")

        # 添加初始日志
        self.add_log("程序已启动")
        self.add_log("按 Home 键开始截图")

        # 运行GUI主循环
        self.root.mainloop()


# 主程序入口
if __name__ == '__main__':
    tool = ScreenshotTool()
    tool.run()

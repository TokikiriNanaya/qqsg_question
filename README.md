# 推举孝廉小助手

一个基于PaddleOCR的QQ三国"推举孝廉"答题辅助工具，通过截图识别题目并自动从题库中匹配答案。

## ✨ 功能特性

- 📸 **智能截图**：支持全屏截图和窗口截图两种模式
- 🔍 **OCR识别**：使用PaddleOCR高精度识别题干和选项
- 📚 **题库匹配**：本地2000+道题目，支持相似度智能匹配
- 🌐 **网络查询**：自动查询在线题库并补充本地题库
- 💡 **置信度显示**：每个选项显示匹配置信度百分比
- 🔧 **手动检索**：支持关键词搜索本地和网络题库
- ➕ **题目录入**：未匹配题目可手动录入到本地题库
- ⌨️ **快捷键控制**：Home开始/End停止，操作简单

## 🚀 快速开始

### 环境要求

- Python 3.8+
- Windows 10/11

### 安装步骤

1. **克隆或下载项目**
```bash
cd qqsg_question
```

2. **安装依赖**
```bash
pip install -r requirements.txt
```

3. **运行程序**
```bash
python main.py
```

### 使用说明

1. **启动程序**后，界面会保持在最前端
2. **按 Home 键**开始截图识别
3. **按 End 键**停止截图
4. 识别结果会实时显示在界面上

## 📋 功能详解

### 截图模式

- **全屏截图**：截取整个屏幕，适合游戏全屏模式
- **窗口截图**：只截取当前活动窗口

### 题库管理

#### 本地题库
- 位置：`题库.json`
- 格式：JSON数组
- 结构：`[{"question": "题目", "answer": "答案"}, ...]`

#### 网络题库
- API地址：`https://api.qqsgtk.cn/qqsgtkApi/findByQuestion`
- 自动查询：本地未匹配时自动查询
- 自动录入：查询到的题目自动保存到本地

### 手动检索

1. 点击菜单：**工具 → 🔍 手动检索**
2. 点击"🔍 检索"按钮
3. 查看本地和网络检索结果
4. 可修改本地题目或录入网络题目

### 题目录入

当识别的题目未在题库中找到时：
1. 界面显示"未查询到该题，点击录入题库"
2. 点击按钮弹出录入窗口
3. 确认或修改题干和答案
4. 点击保存即可录入

## 🛠️ 配置说明

配置文件：`config.json`

```json
{
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
  "answer_area_offset_a": { ... },
  "answer_area_offset_b": { ... },
  "answer_area_offset_c": { ... },
  "answer_area_offset_d": { ... },
  "screenshot": {
    "capture_interval": 1.0,
    "output_dir": "screenshots",
    "max_log_messages": 200,
    "default_mode": "fullscreen"
  }
}
```

## 📁 项目结构

```
qqsg_question/
├── main.py                 # 主程序入口
├── config.json             # 配置文件
├── 题库.json               # 本地题库
├── requirements.txt        # Python依赖
├── img/                    # 图片资源
│   └── title.png          # 标题模板图片
├── screenshots/            # 截图保存目录
├── utils/                  # 工具模块
│   └── text_utils.py      # 文本处理工具
└── README.md              # 项目说明
```

## 🔧 开发说明

### 代码结构

- **main.py**：主程序文件（约2200行）
  - ScreenshotTool类：核心逻辑
  - OCR识别、题库匹配、UI显示
  - 网络API调用、题目录入等

- **utils/text_utils.py**：文本处理工具
  - 相似度计算
  - 标点符号标准化

### 扩展建议

如需进一步模块化，可考虑拆分：
- `core/ocr_engine.py` - OCR引擎封装
- `core/question_matcher.py` - 题库匹配逻辑
- `api/qqsg_api.py` - 网络API封装
- `ui/main_window.py` - UI界面
- `ui/dialogs.py` - 对话框组件
- `utils/config_manager.py` - 配置管理
- `utils/question_bank.py` - 题库管理

## ⚠️ 注意事项

1. **首次运行**会自动下载PaddleOCR模型（约100MB）
2. **题库文件**请勿手动编辑，使用程序提供的录入功能
3. **坐标配置**针对不同分辨率可能需要调整（难用 后续优化）
4. **网络查询**需要联网，超时时间为5秒

## 📝 更新日志

### v1.0.0
- ✅ 基础截图识别功能
- ✅ 本地题库匹配
- ✅ 网络API集成
- ✅ 手动检索功能
- ✅ 题目录入功能
- ✅ 置信度显示

## 🤝 贡献

欢迎提交Issue和Pull Request！

## 📄 许可证

本项目仅供学习交流使用。

---

**提示**：如遇问题，请检查日志输出或提交Issue。

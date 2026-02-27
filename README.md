# AI驱动的探索性UI测试框架

基于 Airtest + Poco + qwen3-vl-plus 视觉语言模型的智能自动化探索测试工具。

## 简介

本框架通过AI视觉模型自动分析应用界面截图和UI层级结构，智能决策下一步操作，实现对移动应用/桌面应用的全自动探索性测试。相比传统的Monkey测试，本框架具备：

- **智能决策**：AI分析截图+UI树，理解界面语义，优先探索未覆盖的功能
- **自动去重**：界面指纹识别，避免在已探索界面上重复操作
- **弹窗处理**：自动识别并关闭权限弹窗、广告弹窗等干扰
- **覆盖追踪**：实时跟踪元素探索覆盖率，达标自动停止
- **完整报告**：生成HTML可视化报告，包含截图、步骤、导航图、问题列表

## 系统流程

```
开始
 ↓
应用启动·初始化环境
 ↓
捕获当前界面截图 + UI结构  ←───────────┐
 ↓                                      │
POCO UI树分析                           │
 ↓                                      │
AI决策优化模块                           │
 ├── 提取功能点·构建探索矩阵             │
 └── 智能路径规划·优先级排序             │
 ↓                                      │
发送至AI助手分析识别                     │
 ↓                                      │
AI返回控件信息（坐标/类型/优先级）        │
 ↓                                      │
执行点击操作（Airtest/Poco）             │
 ↓                                      │
验证结果·记录日志                        │
 ↓                                      │
是否达到测试目标？ ── 否 ───────────────┘
 │
 是
 ↓
生成报告·结束流程
```

## 项目结构

```
E:\airtest\
├── ai_explorer\                     # AI探索框架核心包
│   ├── __init__.py                  # 包初始化
│   ├── models.py                    # 数据结构定义（UIElement, AIDecision, AIResponse等）
│   ├── config.py                    # 配置管理（AI配置, 探索配置, 应用配置）
│   ├── prompts.py                   # AI提示词模板（系统提示词+用户提示词）
│   ├── ai_client.py                 # AI客户端（发送截图+UI树，解析JSON响应）
│   ├── ui_analyzer.py               # UI分析器（截图捕获, Poco UI树提取）
│   ├── screen_state.py              # 界面状态管理（指纹生成, 去重, 探索进度）
│   ├── action_executor.py           # 操作执行器（AI决策 → Airtest/Poco操作）
│   ├── exploration_engine.py        # 探索引擎（核心主循环）
│   ├── device_driver_ext.py         # AI增强驱动器（包装DeviceDriver）
│   ├── logger.py                    # 结构化日志记录
│   └── report_generator.py          # HTML/JSON报告生成
├── run_explorer.py                  # 命令行入口
├── requirements_explorer.txt        # 依赖包
└── plugins/                         # 现有的手动测试用例（不影响）
```

## 环境要求

- Python 3.8+
- Airtest + Poco（已安装在现有项目中）
- OpenAI Python SDK

## 安装

```bash
pip install -r requirements_explorer.txt
```

## 使用方式

### 方式一：命令行运行

```bash
# 基本用法：指定应用包名和设备
python run_explorer.py --package tv.danmaku.bili --uuid YOUR_DEVICE_ID

# 指定最大步数和时长
python run_explorer.py --package com.example.app --uuid DEVICE_ID --max-steps 50 --max-time 600

# 使用配置文件
python run_explorer.py --config explorer_config.json

# iOS设备
python run_explorer.py --package com.example.app --platform IOS --uri http://localhost:8100

# Windows桌面应用
python run_explorer.py --platform Windows --window "应用窗口名称"
```

### 方式二：代码集成

```python
from airtest.core.api import using
using(r"E:\airtest-workspace\common.air")
from common import DeviceDriver

from ai_explorer.config import Config
from ai_explorer.device_driver_ext import AIDeviceDriver

# 1. 初始化设备驱动（复用现有的DeviceDriver）
device_info = {
    "platform": "Android",
    "uuid": "YOUR_DEVICE_ID",
    "uri": "",
    "poco_type": "",
}
dd = DeviceDriver(device_info, r"E:\tmp\explore\logs")

# 2. 创建AI增强驱动
config = Config()
config.logdir = r"E:\tmp\explore\logs"
ai_dd = AIDeviceDriver(dd, config)

# 3. 运行探索测试
result = ai_dd.explore("tv.danmaku.bili")

# 4. 生成报告
report_path = ai_dd.generate_report(result)
print(f"报告路径: {report_path}")
```

### 方式三：AI辅助单步操作

```python
# AI辅助点击：用自然语言描述目标
ai_dd.ai_click("登录按钮")
ai_dd.ai_click("底部的设置标签")
ai_dd.ai_click("搜索框")

# AI辅助断言：用自然语言描述期望状态
assert ai_dd.ai_assert("已进入首页")
assert ai_dd.ai_assert("显示了登录错误提示")
```

## 配置说明

### JSON配置文件示例

```json
{
  "ai": {
    "api_base_url": "https://apis.iflow.cn/v1",
    "api_key": "your-api-key",
    "model": "qwen3-vl-plus",
    "temperature": 0.3,
    "max_tokens": 4096
  },
  "exploration": {
    "max_steps": 200,
    "max_duration_seconds": 1800,
    "coverage_target": 0.8,
    "strategy": "priority_bfs",
    "action_delay": 2.0,
    "max_consecutive_duplicates": 5
  },
  "app": {
    "package_name": "tv.danmaku.bili",
    "platform": "Android",
    "device_uuid": "YOUR_DEVICE_ID"
  },
  "logdir": "E:\\tmp\\explore\\bilibili"
}
```

### 主要配置项

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `max_steps` | 200 | 最大探索步数 |
| `max_duration_seconds` | 1800 | 最大探索时长（秒） |
| `coverage_target` | 0.8 | 目标覆盖率（0-1） |
| `strategy` | priority_bfs | 探索策略（priority_bfs/bfs/dfs/random） |
| `action_delay` | 2.0 | 每步操作后等待时间（秒） |
| `max_consecutive_duplicates` | 5 | 连续重复界面停止阈值 |
| `max_errors` | 10 | 连续错误停止阈值 |
| `auto_dismiss_keywords` | [允许,确定,...] | 自动关闭弹窗的关键词 |

## 停止条件

探索会在满足以下任一条件时自动停止：

1. 达到最大步数（默认200步）
2. 达到最大时长（默认30分钟）
3. 探索覆盖率达到目标（默认80%）
4. 连续访问重复界面（默认5次）
5. 连续操作出错（默认10次）

## 输出文件

探索完成后，日志目录中会生成以下文件：

| 文件 | 说明 |
|------|------|
| `exploration_report.html` | HTML可视化报告（含截图、步骤表、导航图） |
| `exploration_report.json` | JSON格式摘要报告 |
| `exploration_log.jsonl` | 每步记录的结构化日志（JSON行格式） |
| `exploration.log` | 人类可读的详细运行日志 |
| `console.log` | 控制台输出日志 |
| `explore-step*.jpg` | 每步的界面截图 |

## 操作执行策略

操作执行采用多级降级策略，确保最大兼容性：

1. **Poco文本匹配** → 通过元素文本定位并点击（最可靠）
2. **Poco名称匹配** → 通过元素name/resource-id定位
3. **Poco坐标点击** → 使用归一化坐标通过Poco点击
4. **Airtest绝对坐标** → 转换为屏幕绝对坐标点击（最终兜底）

## 支持的平台

| 平台 | Poco支持 | 说明 |
|------|----------|------|
| Android | 支持 | 完整的截图+UI树+AI分析 |
| iOS | 支持 | 完整的截图+UI树+AI分析 |
| Windows | 不支持 | 仅截图+AI视觉分析（无UI树） |

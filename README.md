# AI驱动的L1/L2导航测试框架

基于 Airtest + Poco + qwen3-vl-plus 视觉语言模型，自动遍历应用的底部导航（L1）和顶部标签栏（L2），验证每个页面的阻断/功能状态。

## 核心功能

- **结构化导航遍历**：状态机驱动，按 L1(底部导航) → L2(顶部Tab) 的层级逐个点击测试
- **双模式测试**：mode=0 阻断测试（验证页面是否被成功阻断），mode=1 功能测试（验证页面是否正常加载）
- **AI视觉判断**：截图 + Poco UI树同时送入AI模型，判断页面状态（阻断成功/失败/加载中）
- **智能弹窗处理**：自动识别并关闭遮挡导航控件的弹窗，忽略不影响操作的小广告
- **页面跳转自动返回**：点击L2后如果跳转到新页面，自动检测并返回原页面继续测试
- **多级点击降级**：Poco文本 → 描述 → 名称（唯一性校验）→ Poco坐标 → Airtest绝对坐标
- **自动登录**（可选）：遇到登录界面时，AI识别输入框和按钮位置，自动填写凭据完成登录
- **HTML/JSON报告**：自包含HTML报告，内嵌截图，支持点击放大查看

## 状态机流程

```
DISCOVER_L1  ──→  DISCOVER_L2  ──→  TEST_L2  ──→  CHECK_BLOCK
  识别底部导航        识别顶部Tab       点击L2         检查页面状态
       │                  │               │               │
       │                  │               │         ┌─────┴─────┐
       │                  │               │    阻断成功     阻断失败
       │                  │               │    记录结果     记录结果
       │                  │               │         └─────┬─────┘
       │                  │               │               │
       │                  │          还有下一个L2?  ←──────┘
       │                  │          是 → 回到TEST_L2
       │                  │          否 → SWITCH_L1
       │                  │                    │
       │                  │               还有下一个L1?
       │                  │               是 → 回到DISCOVER_L2
       │                  │               否 → COMPLETE
       │                  │
       ↓                  ↓
  HANDLE_POPUP ←── 任何步骤检测到遮挡弹窗时进入
  HANDLE_LOGIN ←── 检测到登录弹窗且login_required=true时进入
```

## 项目结构

```
ai_explorer/
├── exploration_engine.py   # 核心状态机引擎（L1→L2遍历、弹窗处理、页面跳转检测）
├── ai_client.py            # AI视觉模型客户端（OpenAI兼容API，截图+UI树→JSON响应）
├── action_executor.py      # 操作执行器（多级降级点击、滑动、文本输入、返回）
├── ui_analyzer.py          # UI分析器（Poco UI树提取、截图捕获、元素格式化）
├── prompts.py              # AI提示词模板（L1发现、L2发现、阻断检查、功能检查、登录分析）
├── models.py               # 数据结构（EngineState、MenuStructure、UIElement、AIDecision等）
├── config.py               # 配置管理（AI、设备、探索行为、路由器、登录）
├── screen_state.py         # 界面指纹与去重
├── device_driver_ext.py    # AIDeviceDriver（封装DeviceDriver，提供explore/ai_click/ai_assert）
├── report_generator.py     # HTML/JSON测试报告生成
└── logger.py               # 结构化JSONL日志

run_explorer.py             # 入口脚本
config.yaml.example         # 配置模板
requirements.txt            # Python依赖
```

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 创建配置文件

```bash
cp config.yaml.example config.yaml
# 编辑 config.yaml，填入实际的设备UUID、AI API Key等
```

### 3. 运行测试

```bash
python run_explorer.py
```

程序会自动读取项目根目录的 `config.yaml`，连接设备，启动应用，执行L1→L2遍历测试。

## 配置说明

配置文件为 YAML 格式，完整示例见 `config.yaml.example`。

### 必填项

```yaml
package_name: "tv.danmaku.bili"       # 应用包名
l_class: "23921"                       # 小类ID（用于阻断规则索引和文件命名）
mode: 0                                # 0=阻断测试, 1=功能测试

device:
  platform: "Android"                  # Android / IOS / Windows
  device_uuid: "your-device-uuid"

ai:
  api_base_url: "https://apis.iflow.cn/v1"
  api_key: "your-api-key"
  model: "qwen3-vl-plus"
```

### 可选项

```yaml
# 登录配置（遇到登录界面时自动登录）
login:
  required: false
  phone: ""
  password: ""
  method: "password"                   # password / sms

# 路由器阻断规则（仅 mode=0 时使用）
router:
  router_host: "192.168.254.122"
  router_port: 22
  router_user: "admin"
  router_pwd: "your-password"
  router_enable_pwd: "your-enable-password"

# 探索行为
exploration:
  max_steps: 200                       # 最大步数
  max_duration_seconds: 1800           # 最大时长（秒）
  action_delay: 2.0                    # 每步操作后等待（秒）
  max_errors: 10                       # 连续错误上限

# 输出
output_dir: "E:\\tmp\\explore"         # 日志和报告输出目录
```

## 操作执行策略

点击操作采用5级降级策略，逐级尝试直到成功：

| 优先级 | 方式 | 说明 |
|--------|------|------|
| 1 | Poco文本匹配 | `poco(text="xxx").click()` 最可靠 |
| 2 | Poco描述匹配 | `poco(desc="xxx").click()` content-description |
| 3 | Poco名称匹配 | `poco(name="xxx").click()` 跳过通用类名，要求唯一匹配 |
| 4 | Poco坐标点击 | `poco.click((x, y))` 归一化坐标 |
| 5 | Airtest绝对坐标 | `touch((abs_x, abs_y))` 最终兜底 |

名称匹配会自动过滤 `android.widget.*`、`android.view.*` 等通用类名，且匹配到多个元素时跳过。

## 弹窗处理规则

只处理**遮挡了导航控件**的弹窗：

| 处理 | 不处理 |
|------|--------|
| 隐私政策/用户协议（点同意） | 底部小广告横幅 |
| 系统权限弹窗（点允许） | 角落悬浮广告图标 |
| 大面积广告/引导（点关闭） | 页面内嵌推荐卡片 |
| 登录弹窗/浮层（点关闭或自动登录） | 不挡Tab的通知条 |
| 青少年模式弹窗（点我知道了） | |

## 输出文件

测试完成后在 `output_dir/l_class/` 目录下生成：

| 文件 | 说明 |
|------|------|
| `{l_class}.html` | 自包含HTML报告（内嵌截图，可点击放大） |
| `{l_class}.json` | JSON格式摘要（步骤、结果、统计） |
| `{l_class}.jsonl` | 每步结构化日志（JSON行格式） |
| `{l_class}.log` | 控制台运行日志 |
| `{l_class}_detail.log` | 详细模块日志 |
| `{l_class}-*.jpg` | 每步界面截图 |

## 代码集成

```python
from ai_explorer.config import Config
from ai_explorer.device_driver_ext import AIDeviceDriver

config = Config.load()  # 自动读取 config.yaml

# dd = 已初始化的 DeviceDriver 实例
ai_dd = AIDeviceDriver(dd, config)
result = ai_dd.explore(config.package_name)
ai_dd.generate_report(result)
```

### AI辅助单步操作

```python
# 自然语言点击
ai_dd.ai_click("登录按钮")
ai_dd.ai_click("底部的设置标签")

# 自然语言断言
assert ai_dd.ai_assert("已进入首页")
assert ai_dd.ai_assert("显示了登录错误提示")
```

## 支持平台

| 平台 | Poco UI树 | 说明 |
|------|-----------|------|
| Android | 支持 | 截图 + UI树 + AI分析 |
| iOS | 支持 | 截图 + UI树 + AI分析 |
| Windows | 不支持 | 仅截图 + AI视觉分析 |

## 依赖

- Python 3.8+
- openai >= 1.0.0
- Pillow >= 9.0.0
- PyYAML >= 6.0
- airtest >= 1.3.0
- pocoui >= 1.0.90

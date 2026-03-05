# AI驱动的L1/L2导航测试框架

基于 Airtest + Poco + qwen3-vl-plus 视觉语言模型，自动遍历应用的底部导航（L1）和顶部标签栏（L2），验证每个页面的阻断/功能状态。

## 核心功能

- **结构化导航遍历**：三阶段统一AI循环（发现菜单 → 逐项测试 → 完成），按 L1(底部导航) → L2(顶部Tab) 的层级稳定执行
- **双模式测试**：mode=0 阻断测试（验证页面是否被成功阻断），mode=1 功能测试（验证页面是否正常加载）
- **AI视觉判断**：截图 + Poco UI树同时送入AI模型，判断页面状态（阻断成功/失败/加载中）
- **智能弹窗处理**：自动识别并关闭遮挡导航控件的弹窗，忽略不影响操作的小广告
- **页面跳转自动返回**：点击L2后如果跳转到新页面，自动检测并返回原页面继续测试
- **多级点击降级**：Poco文本 → 描述 → 名称（唯一性校验）→ Poco坐标 → Airtest绝对坐标
- **自动登录**（可选）：遇到登录界面时，AI识别输入框和按钮位置，自动填写凭据完成登录
- **HTML/JSON报告**：自包含HTML报告，内嵌截图，支持点击放大查看

## 执行流程（Unified AI Loop）

当前代码在 `exploration_engine.py` 中采用“模式选择 + 三阶段循环”：

~~~text
run()
  ├─ replay_mode=auto: 有 playbook -> replay；否则 -> record
  ├─ replay_mode=replay: _run_replay()
  └─ replay_mode=record: _run_record()

_run_record()
  1) DISCOVER 阶段（_discover_phase）
     - AI discover_call 在循环中二选一返回：
       - action: 处理弹窗/登录/引导/导航动作（执行后继续循环）
       - menu_found: 返回当前 L1 + L2 结构
     - 若仍有 L1 未发现 L2：自动切换到该 L1 继续 discover
  2) TEST 阶段（_test_phase）
     - 按发现结果确定性遍历：L1 -> L2（无 L2 则直接测 L1）
     - 每个目标进入 _check_page_loop：
       - AI test_call 返回 action：先执行（弹窗、登录、返回等）再重试判断
       - 返回 page_status=blocked/loaded/loading：
         - loading 连续重试，超限后按 mode 记为失败
         - blocked/loaded 立即记结果
  3) COMPLETE
     - 保存 menu_structure 到 playbook
     - 输出 ExplorationResult，后续生成 HTML/JSON 报告

_run_replay()
  - 加载 playbook 步骤回放（click/check/back 等）
  - check 步骤仍调用 AI 复判页面状态
  - 回放异常时自动降级到 record 路径继续完成
~~~

全局终止条件（任一满足即停止）：
- `max_steps`
- `max_duration_seconds`
- `max_errors`（连续错误上限）

## 项目结构

```
ai_explorer/
├── exploration_engine.py   # 统一AI循环引擎（discover/test/replay 主流程、菜单遍历、弹窗与登录处理）
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
  max_l2_per_l1: 0                     # 每个L1最多测几个L2，0=不限制，1=只测1个
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

# 阻断测试设计文档

## 一、概述

阻断测试（mode=0）的目标是验证：通过路由器下发阻断规则后，App的各个页面是否被成功阻断（无法加载业务数据）。

根据App是否需要登录以及是否有可用账号，分为两种阻断策略：

| 策略 | 场景 | 说明 |
|------|------|------|
| `login_first` | 有可用账号，需要登录后才能测试 | 先正常登录 → 再下发阻断 → 测试各页面 |
| `block_first` | 无可用账号或无需登录 | 先下发阻断 → 启动App → 测试可见页面 |

---

## 二、配置设计

```yaml
# config.yaml

mode: 0  # 0=阻断测试, 1=功能测试

# 阻断策略（仅 mode=0 时生效）
block_strategy: "login_first"   # login_first / block_first

login:
  required: true                # 是否需要登录
  phone: "17358607087"          # 登录手机号（block_first时留空则随机生成）
  password: "Test1735860"       # 登录密码（block_first时留空则随机生成）
  method: "password"            # password=密码登录, sms=验证码登录

router:
  router_host: "192.168.254.122"
  router_port: 22
  router_user: "admin"
  router_pwd: "zaq1,lp-"
  router_enable_pwd: "zaq1,lp-"
  extend_device: "t1"
```

### 配置字段说明

- `block_strategy`：仅在 `mode=0` 时生效
  - `login_first`：先登录再阻断（默认值）
  - `block_first`：先阻断再测试
- `login.required`：两种策略下含义不同
  - `login_first`：必须为 `true`，需要提供真实账号密码
  - `block_first`：如果为 `true`，遇到登录页会随机输入账号密码测试登录功能
- `login.phone` / `login.password`：
  - `login_first`：必须填写真实账号密码
  - `block_first`：留空时自动随机生成（用于测试登录功能是否被阻断）

---

## 三、策略一：login_first（先登录再阻断）

### 适用场景

- App核心功能页面需要登录后才能访问（如小红书、淘宝、微信等）
- 有可用的测试账号密码
- 不登录的话看不到真实业务页面，阻断测试没有意义

### 完整流程

```
┌─────────────────────────────────────────┐
│           Phase 1: 登录阶段              │
│         （不下发阻断规则）                │
├─────────────────────────────────────────┤
│ 1. 启动App                              │
│ 2. 处理系统弹窗（权限、协议等）           │
│ 3. 检测登录页面                          │
│ 4. AI逐步引导登录（输入账号→密码→点登录） │
│ 5. 验证登录是否成功                      │
│    - 成功 → 进入Phase 2                  │
│    - 失败 → 重试（最多3次）→ 放弃        │
├─────────────────────────────────────────┤
│           Phase 2: 阻断阶段              │
│         （下发阻断规则）                  │
├─────────────────────────────────────────┤
│ 6. SSH连接路由器，下发阻断规则            │
│ 7. 等待规则生效（约3-5秒）               │
├─────────────────────────────────────────┤
│           Phase 3: 测试阶段              │
│         （遍历L1→L2检查阻断）            │
├─────────────────────────────────────────┤
│ 8. 发现L1菜单（底部导航栏）              │
│ 9. 确保从第一个L1开始                    │
│ 10. 对每个L1：                           │
│     a. 发现L2标签（顶部Tab）             │
│     b. 逐个点击L2，检查阻断状态          │
│        - 阻断成功：页面显示错误/空数据    │
│        - 阻断失败：页面加载了真实数据     │
│        - 加载中：等待后重新检查           │
│     c. 切换到下一个L1                    │
│ 11. 所有L1/L2测试完成                    │
├─────────────────────────────────────────┤
│           Phase 4: 清理阶段              │
├─────────────────────────────────────────┤
│ 12. 移除路由器阻断规则                   │
│ 13. 生成测试报告                         │
└─────────────────────────────────────────┘
```

### 引擎状态机流程

```
INIT → LOGIN_PHASE → APPLY_BLOCK → DISCOVER_L1 → DISCOVER_L2 → TEST_L2 → CHECK_BLOCK
                                       ↑                                      │
                                       └──── SWITCH_L1 ←──────────────────────┘
```

### 关键点

1. **登录阶段不下发阻断**：确保网络正常，登录能成功
2. **登录成功的判断**：AI分析登录后的页面，不再是登录界面即为成功
3. **阻断规则下发时机**：登录成功后、开始L1发现之前
4. **弹窗处理**：登录阶段和测试阶段都可能遇到弹窗，统一处理

---

## 四、策略二：block_first（先阻断再测试）

### 适用场景

- App必须登录但**没有可用账号**（无法注册、无测试账号）
- 只能验证"登录功能在阻断状态下的表现"
- 或App不需要登录，可直接测试

### 完整流程

```
┌─────────────────────────────────────────┐
│           Phase 1: 阻断阶段              │
├─────────────────────────────────────────┤
│ 1. SSH连接路由器，下发阻断规则            │
│ 2. 等待规则生效（约3-5秒）               │
├─────────────────────────────────────────┤
│           Phase 2: 启动与检测            │
├─────────────────────────────────────────┤
│ 3. 启动App                              │
│ 4. 处理系统弹窗（权限、协议等）           │
├─────────────────────────────────────────┤
│         Phase 3: 登录功能测试             │
│     （仅 login.required=true 时）        │
├─────────────────────────────────────────┤
│ 5. 检测到登录页面                        │
│ 6. 随机生成/使用配置的手机号密码          │
│ 7. AI逐步操作登录流程：                   │
│    a. 切换到密码登录Tab（如需要）         │
│    b. 勾选协议（如需要）                  │
│    c. 输入手机号                          │
│    d. 输入密码                            │
│    e. 点击登录按钮                        │
│ 8. 快速连续截图（捕获Toast/错误提示）     │
│    - 点击登录后立即截图（0.5秒间隔）      │
│    - 连续截3张，每张都送AI分析            │
│    - 尝试捕获"网络错误"等Toast提示        │
│ 9. 记录登录测试结果：                     │
│    - 截到网络错误 → 阻断成功              │
│    - 截到账号密码错误 → 阻断失败          │
│    - 什么都没截到 → 结果未知              │
│    - 页面无变化仍在登录页 → 阻断成功      │
│      （请求未发出/被拦截）                │
├─────────────────────────────────────────┤
│         Phase 4: 可见页面测试             │
│     （如果能跳过登录访问其他页面）        │
├─────────────────────────────────────────┤
│ 10. 如果登录页有"跳过"/"游客"按钮：       │
│     → 点击跳过，继续L1→L2测试            │
│ 11. 如果无法跳过登录：                    │
│     → 登录功能测试完毕，流程结束          │
│ 12. 能跳过时：正常L1→L2阻断测试          │
├─────────────────────────────────────────┤
│           Phase 5: 清理阶段              │
├─────────────────────────────────────────┤
│ 13. 移除路由器阻断规则                   │
│ 14. 生成测试报告                         │
└─────────────────────────────────────────┘
```

### 引擎状态机流程

```
INIT → APPLY_BLOCK → DISCOVER_L1 → HANDLE_POPUP → LOGIN_TEST → (尝试跳过登录)
                                                                      │
                                                    能跳过 ←─────────┤
                                                      │              │
                                               DISCOVER_L2 ...   不能跳过
                                                                      │
                                                                  COMPLETE
```

### 登录功能测试的详细设计

#### 随机账号密码生成规则

```python
import random
import string

def generate_random_phone():
    """生成随机11位手机号（1开头）"""
    prefixes = ['13', '14', '15', '16', '17', '18', '19']
    prefix = random.choice(prefixes)
    suffix = ''.join(random.choices(string.digits, k=9))
    return prefix + suffix

def generate_random_password():
    """生成随机密码（8-12位，含字母数字）"""
    length = random.randint(8, 12)
    chars = string.ascii_letters + string.digits
    return ''.join(random.choices(chars, k=length))
```

#### 快速截图捕获Toast

点击登录按钮后，需要快速连续截图以捕获可能一闪而过的Toast提示：

```python
def _capture_login_result(self, step_number):
    """点击登录后快速连续截图，尝试捕获Toast/错误提示"""
    screenshots = []
    for i in range(3):
        time.sleep(0.5)  # 间隔0.5秒
        path = self._take_screenshot(step_number, suffix=f"_toast{i+1}")
        if path:
            screenshots.append(path)
    return screenshots
```

#### 登录结果判断

| 截图内容 | 判断 | 说明 |
|---------|------|------|
| 页面显示"网络错误"/"连接超时"/"网络不可用" | 阻断成功 | 请求被路由器拦截 |
| 页面显示"账号或密码错误"/"用户不存在" | 阻断失败 | 请求成功到达了服务器 |
| 页面无变化，仍在登录页，无任何提示 | 阻断成功（推测） | 请求可能超时未返回 |
| 页面跳转到了主页/其他页面 | 阻断失败 | 不太可能发生（随机账号） |
| Toast未捕获到 | 结果未知 | 记录为"未捕获到登录结果" |

#### AI分析提示词（登录结果检查）

需要新增一个专门的提示词，用于分析登录点击后的截图：

```
你是一个移动应用QA测试员，正在执行阻断测试。
刚才在登录页面输入了账号密码并点击了登录按钮。

请分析当前截图，判断登录请求的结果：
1. 是否有Toast/弹窗/提示信息？
2. 提示内容是什么？
3. 是网络相关的错误（网络错误、连接超时、网络不可用）还是业务错误（账号密码错误、用户不存在）？
4. 页面是否发生了变化（跳转到其他页面）？

响应格式（JSON）：
{
  "has_toast": true,
  "toast_text": "网络错误",
  "error_type": "network",  // network=网络错误(阻断成功), business=业务错误(阻断失败), none=无提示
  "page_changed": false,
  "description": "页面显示网络错误Toast"
}
```

---

## 五、两种策略的对比

| 维度 | login_first | block_first |
|------|-------------|-------------|
| 阻断规则下发时机 | 登录成功后 | App启动前 |
| 登录行为 | 真实登录（真实账号密码） | 测试登录功能（随机账号密码） |
| 可测试的页面范围 | 所有页面（已登录） | 仅不需要登录的页面 |
| 登录结果 | 必须登录成功才继续 | 登录必然失败，测的是失败表现 |
| 测试覆盖面 | 高（所有L1/L2） | 低（可能卡在登录页） |
| 适用场景 | 有测试账号 | 无测试账号 |
| 报告内容 | 各页面阻断状态 | 登录功能阻断状态 + 可见页面阻断状态 |

---

## 六、引擎改造要点

### 6.1 新增状态

```python
class EngineState(Enum):
    # ... 已有状态 ...
    APPLY_BLOCK = "apply_block"        # 下发阻断规则
    REMOVE_BLOCK = "remove_block"      # 移除阻断规则
    LOGIN_PHASE = "login_phase"        # 登录阶段（login_first专用）
    LOGIN_TEST = "login_test"          # 登录功能测试（block_first专用）
```

### 6.2 主循环改造

```python
def run(self):
    if self.config.mode == 0:  # 阻断测试
        if self.config.block_strategy == "login_first":
            self._run_login_first()
        else:
            self._run_block_first()
    elif self.config.mode == 1:  # 功能测试
        self._run_function_test()
```

### 6.3 login_first 流程

```python
def _run_login_first(self):
    # Phase 1: 登录（不阻断）
    self._handle_popups_and_login()

    # Phase 2: 下发阻断规则
    self._apply_block_rules()
    time.sleep(5)  # 等待规则生效

    # Phase 3: L1→L2 阻断测试
    self._run_l1_l2_block_test()

    # Phase 4: 清理
    self._remove_block_rules()
    self._generate_report()
```

### 6.4 block_first 流程

```python
def _run_block_first(self):
    # Phase 1: 下发阻断规则
    self._apply_block_rules()
    time.sleep(5)

    # Phase 2: 启动App，处理弹窗
    self._handle_system_popups()

    # Phase 3: 登录功能测试（如果遇到登录页）
    if self._is_login_page():
        self._test_login_under_block()

        # 尝试跳过登录
        if not self._try_skip_login():
            # 无法跳过，测试结束
            self._remove_block_rules()
            self._generate_report()
            return

    # Phase 4: L1→L2 阻断测试
    self._run_l1_l2_block_test()

    # Phase 5: 清理
    self._remove_block_rules()
    self._generate_report()
```

### 6.5 登录功能测试（block_first专用）

```python
def _test_login_under_block(self):
    """在阻断状态下测试登录功能"""
    # 1. 生成随机账号密码（或使用配置的）
    phone = self.config.login_phone or generate_random_phone()
    password = self.config.login_password or generate_random_password()

    # 2. AI逐步操作登录流程
    #    - 和正常登录流程一样，每步截图分析
    #    - 但使用的是随机账号密码

    # 3. 点击登录后快速连续截图
    screenshots = self._capture_login_result(step_number)

    # 4. AI分析每张截图，尝试识别Toast
    for screenshot in screenshots:
        result = self.ai_client.analyze_login_result(screenshot, ui_tree_text)
        if result.get("has_toast"):
            error_type = result.get("error_type")
            if error_type == "network":
                # 阻断成功
                self._record_block_result("login", True, result["toast_text"])
            elif error_type == "business":
                # 阻断失败（请求到达了服务器）
                self._record_block_result("login", False, result["toast_text"])
            break
    else:
        # 未捕获到Toast
        # 检查页面是否有变化
        if self._is_still_login_page():
            # 仍在登录页，推测阻断成功（请求超时）
            self._record_block_result("login", True, "登录页面无响应，推测请求被阻断")
        else:
            self._record_block_result("login", None, "未捕获到登录结果")
```

---

## 七、报告展示

### login_first 报告

和当前功能测试报告类似，展示每个L1/L2的阻断状态：

```
阻断测试报告
├── 首页
│   ├── 推荐 → 阻断成功 ✓（页面显示网络错误）
│   ├── 直播 → 阻断成功 ✓（图片全是灰色占位符）
│   └── 热门 → 阻断失败 ✗（页面加载了真实数据）
├── 动态
│   └── （无L2标签）→ 阻断成功 ✓
└── 我的
    ├── 收藏 → 阻断成功 ✓
    └── 历史 → 阻断成功 ✓
```

### block_first 报告

增加登录功能测试结果：

```
阻断测试报告
├── 登录功能测试
│   ├── 操作：输入随机手机号 138****5678，输入随机密码
│   ├── 点击登录按钮
│   ├── 截图1 (0.5s后)：[截图]
│   ├── 截图2 (1.0s后)：[截图]
│   ├── 截图3 (1.5s后)：[截图]
│   └── 结果：阻断成功 ✓（Toast显示"网络连接失败"）
│         / 结果未知 ？（未捕获到Toast）
│         / 阻断失败 ✗（Toast显示"账号不存在"）
├── 可见页面测试（如能跳过登录）
│   ├── 首页 → ...
│   └── ...
└── 总结：登录功能阻断成功，N个页面阻断成功，M个页面阻断失败
```

---

## 八、边界情况处理

### 8.1 login_first 边界情况

| 情况 | 处理 |
|------|------|
| 登录失败（密码错误） | 重试3次后放弃，报告"登录失败，无法执行阻断测试" |
| 登录需要验证码 | 当前不支持自动获取验证码，报告"需要验证码，无法自动登录" |
| 登录成功后遇到新手引导 | 正常处理onboarding弹窗，完成后再下发阻断 |
| 阻断规则下发失败 | SSH连接失败时报告错误，不继续测试 |
| 阻断后App崩溃 | 记录崩溃，尝试重启App继续测试 |

### 8.2 block_first 边界情况

| 情况 | 处理 |
|------|------|
| App启动就崩溃（网络不通） | 记录崩溃现象，报告"App在阻断状态下无法启动" |
| 登录页无法输入（网络不通导致页面异常） | 记录异常现象，报告阻断成功 |
| 有"游客模式"/"跳过"按钮 | 点击跳过，继续测试可见页面 |
| Toast出现时间极短（<0.3秒） | 3次快速截图仍可能漏掉，记录为"结果未知" |
| 登录页面直接显示网络错误（不是Toast） | AI能识别页面上的错误文字，正常判断阻断成功 |
| 点击登录后页面转圈/loading很久 | 等待超时后判断为阻断成功（请求无法到达服务器） |

---

## 九、实现优先级

1. **P0 - 基础框架**：`block_strategy` 配置项、引擎分流逻辑
2. **P0 - login_first 流程**：登录阶段 → 下发阻断 → 测试阶段的完整串联
3. **P1 - block_first 流程**：阻断 → 登录功能测试 → 快速截图捕获Toast
4. **P1 - 登录结果分析提示词**：专门分析登录点击后截图的AI提示词
5. **P2 - 报告展示**：区分两种策略的报告格式
6. **P2 - 随机账号密码生成**：合理的随机号码生成逻辑

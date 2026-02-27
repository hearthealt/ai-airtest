# 应用导航缓存方案设计

## 背景

当前阻断测试流程中，每次运行都需要AI调用来发现L1菜单和L2标签（每次~30秒），即使同一个应用的导航结构基本不变。

**目标**：将AI发现的导航结构缓存到数据库，后续运行直接复用，跳过AI发现阶段。仅在缓存失效时（点击失败/界面变化）重新调用AI更新。

## 核心流程

```
运行阻断测试(app_package)
  │
  ├─ 数据库有该应用的缓存？
  │    │
  │    ├─ 有 → 加载缓存的MenuStructure，跳过DISCOVER_L1/L2
  │    │      → 直接进入 SWITCH_L1 → TEST_L2 → CHECK_BLOCK 循环
  │    │      → 某个L2点击失败？→ 对该L1重新AI发现L2，更新数据库
  │    │
  │    └─ 没有 → AI发现L1 → AI发现每个L1的L2
  │              → 测试完成后，将MenuStructure写入数据库
  │
  └─ 测试结果（block_success/block_failure）也记录到数据库
```

## 数据库设计（SQLite）

使用SQLite，单文件 `E:\airtest\data\nav_cache.db`，零依赖。

### 表结构

```sql
-- 应用导航缓存表
CREATE TABLE IF NOT EXISTS app_nav_cache (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    app_package TEXT NOT NULL,          -- 应用包名，如 tv.danmaku.bili
    item_name TEXT NOT NULL,            -- 菜单项名称，如 "首页"、"直播"
    item_level INTEGER NOT NULL,        -- 1=L1底部导航, 2=L2顶部Tab
    parent_name TEXT DEFAULT '',        -- L2的父级L1名称（L1时为空）
    element_text TEXT DEFAULT '',       -- Poco text属性
    element_name TEXT DEFAULT '',       -- Poco name属性
    coord_x REAL NOT NULL,             -- 归一化x坐标
    coord_y REAL NOT NULL,             -- 归一化y坐标
    sort_order INTEGER DEFAULT 0,      -- 同级排序（从左到右）
    created_at TEXT DEFAULT (datetime('now','localtime')),
    updated_at TEXT DEFAULT (datetime('now','localtime')),
    UNIQUE(app_package, item_name, item_level, parent_name)
);

-- 测试结果记录表
CREATE TABLE IF NOT EXISTS block_test_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    app_package TEXT NOT NULL,
    l_class TEXT NOT NULL,              -- 阻断规则小类ID
    item_name TEXT NOT NULL,            -- 被测菜单项名称
    item_level INTEGER NOT NULL,
    parent_name TEXT DEFAULT '',
    result TEXT NOT NULL,               -- block_success / block_failure
    description TEXT DEFAULT '',        -- 结果描述
    screenshot_path TEXT DEFAULT '',
    tested_at TEXT DEFAULT (datetime('now','localtime'))
);
```

### 索引

```sql
CREATE INDEX IF NOT EXISTS idx_nav_app ON app_nav_cache(app_package);
CREATE INDEX IF NOT EXISTS idx_result_app_class ON block_test_results(app_package, l_class);
```

## 新增文件

### `ai_explorer/nav_cache.py` — 导航缓存管理器

```python
class NavCacheDB:
    """应用导航结构的SQLite缓存"""

    def __init__(self, db_path: str):
        """连接数据库，自动建表"""

    def has_cache(self, app_package: str) -> bool:
        """该应用是否有缓存的导航数据"""

    def load_menu_structure(self, app_package: str) -> MenuStructure:
        """从数据库加载MenuStructure（L1列表 + 每个L1的L2列表）"""

    def save_menu_structure(self, app_package: str, menu: MenuStructure):
        """保存/更新整个MenuStructure到数据库（REPLACE方式）"""

    def update_l2_for_l1(self, app_package: str, l1_name: str, l2_items: List[MenuItemInfo]):
        """更新某个L1下的L2列表（AI重新发现后调用）"""

    def delete_cache(self, app_package: str):
        """删除某应用的全部缓存（强制下次重新发现）"""

    def save_test_result(self, app_package: str, l_class: str,
                         item_name: str, item_level: int, parent_name: str,
                         result: str, description: str, screenshot_path: str):
        """记录一条测试结果"""

    def get_last_results(self, app_package: str, l_class: str) -> List[dict]:
        """获取某应用某规则的最近测试结果"""
```

## 修改文件

### `exploration_engine.py` — 引擎集成缓存

**`__init__`** 新增：
```python
from .nav_cache import NavCacheDB

db_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "nav_cache.db")
os.makedirs(os.path.dirname(db_path), exist_ok=True)
self.nav_cache = NavCacheDB(db_path)
```

**`run()` 方法修改** — 启动时检查缓存：
```python
def run(self, app_package):
    # ... 启动应用 ...

    # 尝试加载缓存
    if self.nav_cache.has_cache(app_package):
        self.menu_structure = self.nav_cache.load_menu_structure(app_package)
        logger.info(f"从缓存加载导航: {len(self.menu_structure.l1_items)}个L1")
        self.state = EngineState.SWITCH_L1  # 跳过发现，直接开始测试
    else:
        self.state = EngineState.DISCOVER_L1  # 首次运行，AI发现

    # ... 主循环 ...
```

**`_step_discover_l1()` 修改** — 发现后存缓存：
```python
# 发现完L1后
self.nav_cache.save_menu_structure(app_package, self.menu_structure)
```

**`_step_discover_l2()` 修改** — 发现后更新缓存：
```python
# 发现完某L1的L2后
self.nav_cache.update_l2_for_l1(app_package, l1.name, l2_items)
```

**`_step_test_l2()` / `_step_switch_l1()` 修改** — 点击失败时重新发现：
```python
action_result = self.action_executor.execute(action)
if action_result != "success":
    # 缓存的坐标可能过期，重新AI发现
    logger.warning(f"缓存坐标点击失败，重新AI发现L2")
    self.state = EngineState.DISCOVER_L2  # 回到发现阶段
    return ...
```

**`_step_check_block()` 修改** — 记录结果到数据库：
```python
self.nav_cache.save_test_result(
    app_package, self.config.l_class,
    item_name, level, parent_name,
    result_type, desc, screenshot_path
)
```

### `config.py` — 新增缓存配置

```python
@dataclass
class Config:
    # ... 现有字段 ...
    use_nav_cache: bool = True   # 是否启用导航缓存
```

## 效果对比

| 场景 | 无缓存 | 有缓存 |
|------|--------|--------|
| L1发现 | 1次AI调用(~30s) | 0（读数据库） |
| L2发现（5个L1） | 5次AI调用(~150s) | 0（读数据库） |
| 阻断检查 | ~20次AI调用 | ~20次（不变） |
| **总AI调用** | **~26次** | **~20次** |
| **节省时间** | - | **~180秒** |

## 缓存失效策略

| 场景 | 处理方式 |
|------|---------|
| 应用更新（UI变了） | 点击缓存坐标失败 → 自动重新AI发现 → 更新数据库 |
| 手动清缓存 | `NavCacheDB.delete_cache(app_package)` |
| 首次测试新应用 | 无缓存 → AI发现 → 自动存入数据库 |
| 同应用不同设备/分辨率 | 坐标是归一化的(0-1)，设备无关，缓存通用 |

## 验证

1. 首次运行：日志应显示 "AI识别L1..." → 测试完成 → "导航缓存已保存"
2. 第二次运行同一应用：日志应显示 "从缓存加载导航: N个L1" → 直接开始测试
3. 检查 `E:\airtest\data\nav_cache.db` 文件存在且有数据
4. 模拟缓存失效：手动修改数据库中的坐标为错误值 → 运行 → 应自动重新发现并更新

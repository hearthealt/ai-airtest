# -*- encoding=utf8 -*-
"""AI探索框架的数据结构定义模块。"""

from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
from enum import Enum


class ActionType(Enum):
    """操作类型枚举"""
    CLICK = "click"              # 点击
    LONG_PRESS = "long_press"    # 长按
    SWIPE = "swipe"              # 滑动
    TEXT_INPUT = "text_input"    # 文本输入
    BACK = "back"                # 返回
    SCROLL_DOWN = "scroll_down"  # 向下滚动
    SCROLL_UP = "scroll_up"     # 向上滚动
    SCROLL_LEFT = "scroll_left"  # 向左滚动
    SCROLL_RIGHT = "scroll_right"  # 向右滚动
    HOME = "home"                # 主页键
    WAIT = "wait"                # 等待


class ControlType(Enum):
    """控件类型枚举"""
    BUTTON = "button"            # 按钮
    TEXT_FIELD = "text_field"    # 文本输入框
    IMAGE = "image"              # 图片
    LIST_ITEM = "list_item"     # 列表项
    TAB = "tab"                  # 标签页
    MENU = "menu"                # 菜单
    CHECKBOX = "checkbox"        # 复选框
    SWITCH = "switch"            # 开关
    LINK = "link"                # 链接/文本
    DIALOG = "dialog"            # 弹窗
    UNKNOWN = "unknown"          # 未知


class Priority(Enum):
    """优先级枚举"""
    CRITICAL = 1  # 关键
    HIGH = 2      # 高
    MEDIUM = 3    # 中
    LOW = 4       # 低
    SKIP = 5      # 跳过


@dataclass
class UIElement:
    """
    单个UI元素，从Poco树或AI分析中提取。

    :param name: 元素标识名称
    :param text: 元素上显示的文字
    :param desc: 无障碍描述（content-description）
    :param type: 原始控件类型（如 android.widget.Button）
    :param control_type: 归一化后的控件类型
    :param bounds: 边界框 {"x", "y", "width", "height"}（归一化0-1）
    :param center: 中心坐标 (x, y)（归一化0-1）
    :param clickable: 是否可点击
    :param enabled: 是否启用
    :param visible: 是否可见
    :param selected: 是否选中
    :param poco_path: Poco选择器路径（如有）
    :param attributes: 其他属性
    :param element_id: 用于去重的唯一标识
    """
    name: str
    text: str
    desc: str
    type: str
    control_type: ControlType
    bounds: Dict[str, float]
    center: tuple
    clickable: bool
    enabled: bool
    visible: bool
    selected: bool = False
    poco_path: Optional[str] = None
    attributes: Dict[str, Any] = field(default_factory=dict)
    element_id: str = ""


@dataclass
class AIDecision:
    """
    AI返回的一条操作决策。

    :param action: 操作类型
    :param target_element: 目标UI元素
    :param coordinates: 目标坐标 (x, y)（归一化0-1）
    :param text_input: 文本输入内容（用于TEXT_INPUT操作）
    :param swipe_direction: 滑动方向（用于SWIPE操作）
    :param priority: 优先级
    :param reasoning: AI的决策理由
    :param confidence: 置信度（0-1）
    """
    action: ActionType
    target_element: Optional[UIElement] = None
    coordinates: Optional[tuple] = None
    text_input: Optional[str] = None
    swipe_direction: Optional[str] = None
    priority: Priority = Priority.MEDIUM
    reasoning: str = ""
    confidence: float = 0.0
    is_popup: bool = False    # AI判断：该操作是否为关闭弹窗


@dataclass
class AIResponse:
    """
    AI模型的完整解析响应。

    :param screen_description: 当前界面描述
    :param detected_elements: 检测到的UI元素列表
    :param recommended_actions: 推荐的操作列表
    :param is_error_screen: 是否为错误/崩溃界面
    :param error_description: 错误描述
    :param is_duplicate_screen: 是否与已访问界面重复
    :param raw_response: AI的原始响应文本
    """
    screen_description: str
    detected_elements: List[UIElement]
    recommended_actions: List[AIDecision]
    is_error_screen: bool = False
    is_loading: bool = False
    error_description: str = ""
    is_duplicate_screen: bool = False
    raw_response: str = ""


@dataclass
class ExplorationStep:
    """
    一次探索步骤的记录。

    :param step_number: 步骤编号
    :param timestamp: 时间戳
    :param screenshot_path: 截图文件路径
    :param screen_description: 界面描述
    :param ui_tree_summary: UI树摘要
    :param action_taken: 执行的操作
    :param action_result: 操作结果（"success"/"failed"/"error"）
    :param post_screenshot_path: 操作后截图路径
    :param screen_fingerprint: 界面指纹
    :param duration_ms: 耗时（毫秒）
    """
    step_number: int
    timestamp: float
    screenshot_path: str
    screen_description: str
    ui_tree_summary: str
    action_taken: AIDecision
    action_result: str
    post_screenshot_path: str = ""
    screen_fingerprint: str = ""
    duration_ms: int = 0


@dataclass
class ScreenState:
    """
    应用中一个唯一界面/页面的状态。

    :param fingerprint: 界面指纹哈希
    :param description: 界面描述
    :param screenshot_path: 首次截图路径
    :param elements: 界面上的UI元素列表
    :param visit_count: 访问次数
    :param explored_elements: 已探索的元素ID集合
    :param first_seen_step: 首次发现的步骤编号
    :param last_seen_step: 最后一次看到的步骤编号
    """
    fingerprint: str
    description: str
    screenshot_path: str
    elements: List[UIElement]
    visit_count: int = 0
    explored_elements: set = field(default_factory=set)
    first_seen_step: int = 0
    last_seen_step: int = 0


@dataclass
class ExplorationResult:
    """
    一次探索会话的最终结果。

    :param app_package: 应用包名
    :param platform: 平台（Android/IOS/Windows）
    :param start_time: 开始时间戳
    :param end_time: 结束时间戳
    :param total_steps: 总步骤数
    :param unique_screens: 发现的唯一界面数
    :param total_elements_found: 发现的总元素数
    :param elements_interacted: 已交互的元素数
    :param coverage_percentage: 探索覆盖率百分比
    :param steps: 所有步骤记录
    :param screens: 所有界面状态 {指纹: ScreenState}
    :param issues_found: 发现的问题列表
    :param exploration_graph: 界面导航图 {源界面指纹: [目标界面指纹]}
    """
    app_package: str
    platform: str
    start_time: float
    end_time: float
    total_steps: int
    unique_screens: int
    total_elements_found: int
    elements_interacted: int
    coverage_percentage: float
    steps: List[ExplorationStep]
    screens: Dict[str, ScreenState]
    issues_found: List[Dict[str, Any]]
    exploration_graph: Dict[str, List[str]]


# ==================== 结构化L1→L2导航相关 ====================

class EngineState(Enum):
    """探索引擎状态机"""
    DISCOVER_L1 = "discover_l1"
    DISCOVER_L2 = "discover_l2"
    TEST_L2 = "test_l2"
    CHECK_BLOCK = "check_block"
    CHECK_BLOCK_LOADING = "check_block_loading"
    HANDLE_POPUP = "handle_popup"
    HANDLE_LOGIN = "handle_login"
    SWITCH_L1 = "switch_l1"
    TEST_L1_DIRECT = "test_l1_direct"
    CHECK_L1_BLOCK = "check_l1_block"
    COMPLETE = "complete"


@dataclass
class MenuItemInfo:
    """一个菜单项（L1底部导航 或 L2顶部Tab）"""
    name: str
    element_text: str
    element_name: str
    coordinates: tuple
    level: int
    is_selected: bool = False
    status: str = "pending"
    block_result: str = ""
    screenshot_path: str = ""


@dataclass
class MenuStructure:
    """发现的完整菜单结构"""
    l1_items: List[MenuItemInfo] = field(default_factory=list)
    l2_map: Dict[str, List[MenuItemInfo]] = field(default_factory=dict)
    current_l1_index: int = 0
    current_l2_index: int = 0

    def current_l1(self) -> Optional[MenuItemInfo]:
        if 0 <= self.current_l1_index < len(self.l1_items):
            return self.l1_items[self.current_l1_index]
        return None

    def current_l2_list(self) -> List[MenuItemInfo]:
        l1 = self.current_l1()
        if l1 and l1.name in self.l2_map:
            return self.l2_map[l1.name]
        return []

    def current_l2(self) -> Optional[MenuItemInfo]:
        l2_list = self.current_l2_list()
        if 0 <= self.current_l2_index < len(l2_list):
            return l2_list[self.current_l2_index]
        return None

    def advance_l2(self) -> bool:
        """移到下一个L2，返回False表示当前L1的L2全部测完"""
        self.current_l2_index += 1
        return self.current_l2_index < len(self.current_l2_list())

    def advance_l1(self) -> bool:
        """移到下一个L1，返回False表示全部L1测完"""
        self.current_l1_index += 1
        self.current_l2_index = 0
        return self.current_l1_index < len(self.l1_items)

    def all_done(self) -> bool:
        return self.current_l1_index >= len(self.l1_items)

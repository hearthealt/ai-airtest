# -*- encoding=utf8 -*-
"""核心探索引擎：结构化L1→L2导航阻断测试状态机。"""

import time
import logging
from typing import List, Dict, Optional

from .config import Config
from .models import (
    AIDecision, ActionType, Priority, UIElement, ControlType,
    ExplorationStep, ExplorationResult,
    EngineState, MenuItemInfo, MenuStructure,
)
from .ai_client import AIClient
from .ui_analyzer import UIAnalyzer
from .screen_state import ScreenManager
from .action_executor import ActionExecutor
from .logger import ExplorationLogger

logger = logging.getLogger(__name__)


class ExplorationEngine:
    """
    核心探索引擎 - 结构化L1→L2阻断测试：
    1. 发现L1菜单（底部导航栏）
    2. 对每个L1，发现其L2标签（顶部Tab）
    3. 确定性遍历每个L2，检查阻断状态
    4. 阻断失败则终止，全部成功则完成
    """

    def __init__(self, device_driver, config: Config):
        self.dd = device_driver
        self.config = config
        self.ai_client = AIClient(config.ai)
        self.ui_analyzer = UIAnalyzer(device_driver, config.exploration, config.l_class)
        self.screen_manager = ScreenManager(config.exploration.similarity_threshold)
        self.action_executor = ActionExecutor(device_driver, config.exploration)
        self.exploration_logger = ExplorationLogger(config.logdir, config.l_class)

        # 步骤记录
        self.steps: List[ExplorationStep] = []
        self.issues_found: List[Dict] = []
        self.exploration_graph: Dict[str, List[str]] = {}
        self.consecutive_errors = 0
        self.start_time = 0.0

        # 状态机
        self.state = EngineState.DISCOVER_L1
        self.menu_structure = MenuStructure()
        self.previous_state: Optional[EngineState] = None
        self.blocking_failure = False
        self.tested_controls: List[str] = []

        # 阻断检查
        self.last_clicked_target = ""
        self.loading_retry_count = 0
        self.max_loading_retries = 2

        # 弹窗处理
        self._pending_popup_coords: Optional[tuple] = None
        self._pending_popup_text: str = ""
        self._pending_popup_type: str = ""  # 弹窗类型: login/permission/ad/other
        self.popup_retry_count = 0
        self.max_popup_retries = 3

        # 登录处理
        self._login_step: int = 0  # 登录子步骤
        self._login_analysis: dict = {}  # AI返回的登录分析结果
        self._login_retries: int = 0
        self.max_login_retries: int = 3

        # 返回导航（L2跳转新页面后需返回）
        self._back_retry_count: int = 0
        self._max_back_retries: int = 3

    def run(self, app_package: str = "") -> ExplorationResult:
        self.start_time = time.time()
        self.app_package = app_package
        logger.info(f"=== 结构化阻断测试开始 | 应用={app_package} ===")

        if app_package:
            try:
                self.dd.start_app(app_package)
                time.sleep(3)
            except Exception as e:
                logger.error(f"启动应用失败: {e}")

        self.state = EngineState.DISCOVER_L1

        step_number = 0
        while not self._should_stop(step_number):
            step_number += 1
            step_start = time.time()

            try:
                step = self._execute_state_step(step_number)
                step.duration_ms = int((time.time() - step_start) * 1000)
                self.steps.append(step)
                self.exploration_logger.log_step(step)

                if step.action_result == "error":
                    self.consecutive_errors += 1
                else:
                    self.consecutive_errors = 0

                if self.blocking_failure and self.config.mode == 0:
                    logger.error(f"★ 阻断失败！'{self.last_clicked_target}' 页面正常加载了数据，测试终止")
                    break

                if self.state == EngineState.COMPLETE:
                    logger.info("所有L1和L2菜单测试完成")
                    break

                time.sleep(self.config.exploration.action_delay)

            except Exception as e:
                logger.error(f"步骤{step_number}异常: {e}")
                self.consecutive_errors += 1
                time.sleep(self.config.exploration.action_delay)

        if self.config.mode == 1:
            # 功能测试模式：统计正常/异常
            successes = [i for i in self.issues_found if i["type"] == "function_success"]
            failures = [i for i in self.issues_found if i["type"] == "function_failure"]
            logger.info(f"=== 功能测试结果: {len(successes)}个正常, {len(failures)}个异常 | 已测控件: {self.tested_controls} ===")
        elif self.blocking_failure:
            logger.info(f"=== 测试结果: 阻断失败 | 共{len(self.steps)}步 ===")
        else:
            logger.info(f"=== 测试结果: 全部阻断成功 | 已测控件: {self.tested_controls} ===")
        return self._build_result(app_package)

    # ==================== 状态机调度 ====================

    def _execute_state_step(self, step_number: int) -> ExplorationStep:
        """根据当前状态调度到对应的处理方法"""
        logger.info("=" * 60)

        if self.state == EngineState.DISCOVER_L1:
            return self._step_discover_l1(step_number)
        elif self.state == EngineState.DISCOVER_L2:
            return self._step_discover_l2(step_number)
        elif self.state == EngineState.SWITCH_L1:
            return self._step_switch_l1(step_number)
        elif self.state == EngineState.TEST_L2:
            return self._step_test_l2(step_number)
        elif self.state == EngineState.TEST_L1_DIRECT:
            return self._step_test_l1_direct(step_number)
        elif self.state in (EngineState.CHECK_BLOCK, EngineState.CHECK_BLOCK_LOADING):
            return self._step_check_block(step_number)
        elif self.state == EngineState.CHECK_L1_BLOCK:
            return self._step_check_l1_block(step_number)
        elif self.state == EngineState.HANDLE_POPUP:
            return self._step_handle_popup(step_number)
        elif self.state == EngineState.HANDLE_LOGIN:
            return self._step_handle_login(step_number)
        else:
            return self._make_error_step(step_number, "", f"未知状态: {self.state.value}")

    # ==================== 各状态处理方法 ====================

    def _step_discover_l1(self, step_number: int) -> ExplorationStep:
        """发现L1菜单：截图→AI识别底部导航栏→存储菜单结构"""
        screenshot_path, elements, ui_tree_text = self._capture_and_analyze(step_number)
        if not screenshot_path:
            return self._make_error_step(step_number, "", "截图捕获失败")

        logger.info(f"[步骤{step_number}] AI识别L1底部导航栏...")
        result = self.ai_client.discover_l1_menus(screenshot_path, ui_tree_text)

        # 弹窗检测
        if result.get("has_popup") and result.get("popup_close_button"):
            self.previous_state = EngineState.DISCOVER_L1
            self.state = EngineState.HANDLE_POPUP
            btn = result["popup_close_button"]
            raw_coords = self._normalize_coords(tuple(btn.get("coordinates", (0.5, 0.5))))
            self._pending_popup_coords = self._refine_popup_coords(raw_coords, elements)
            self._pending_popup_text = btn.get("text", "关闭")
            self._pending_popup_type = result.get("popup_type", "other")
            return self._make_info_step(step_number, screenshot_path, elements, "发现弹窗，准备处理")

        # 解析L1菜单项
        l1_items = []
        for item in result.get("l1_items", []):
            coords = item.get("coordinates", [0, 0])
            l1_items.append(MenuItemInfo(
                name=item.get("name", ""),
                element_text=item.get("element_text", item.get("name", "")),
                element_name=item.get("element_name", ""),
                coordinates=tuple(coords) if coords else (0, 0),
                level=1,
                is_selected=item.get("is_selected", False),
            ))

        self.menu_structure.l1_items = l1_items
        l1_names = [i.name for i in l1_items]
        logger.info(f"[步骤{step_number}] 发现 {len(l1_items)} 个L1菜单: {l1_names}")

        if not l1_items:
            self.state = EngineState.COMPLETE
            return self._make_info_step(step_number, screenshot_path, elements, "未发现L1菜单，测试完成")

        # 第一个L1通常已选中，直接进入发现L2
        self.state = EngineState.DISCOVER_L2
        return self._make_info_step(step_number, screenshot_path, elements,
                                    f"发现L1菜单: {l1_names}")

    def _step_discover_l2(self, step_number: int) -> ExplorationStep:
        """发现L2标签：截图→AI识别当前L1页面的顶部Tab"""
        l1 = self.menu_structure.current_l1()
        if not l1:
            self.state = EngineState.COMPLETE
            return self._make_info_step(step_number, "", [], "无L1菜单可处理")

        screenshot_path, elements, ui_tree_text = self._capture_and_analyze(step_number)
        if not screenshot_path:
            return self._make_error_step(step_number, "", "截图捕获失败")

        logger.info(f"[步骤{step_number}] AI识别L1'{l1.name}'的L2顶部Tab...")
        result = self.ai_client.discover_l2_tabs(screenshot_path, ui_tree_text, l1.name)

        # 弹窗检测
        if result.get("has_popup") and result.get("popup_close_button"):
            self.previous_state = EngineState.DISCOVER_L2
            self.state = EngineState.HANDLE_POPUP
            btn = result["popup_close_button"]
            raw_coords = self._normalize_coords(tuple(btn.get("coordinates", (0.5, 0.5))))
            self._pending_popup_coords = self._refine_popup_coords(raw_coords, elements)
            self._pending_popup_text = btn.get("text", "关闭")
            self._pending_popup_type = result.get("popup_type", "other")
            return self._make_info_step(step_number, screenshot_path, elements, "发现弹窗，准备处理")

        # 解析L2标签
        has_l2 = result.get("has_l2_tabs", False)
        l2_items = []
        if has_l2:
            found_selected = False
            for item in result.get("l2_items", []):
                coords = item.get("coordinates", [0, 0])
                # is_selected只信第一个被AI标记为selected的tab
                ai_selected = item.get("is_selected", False)
                is_selected = False
                if ai_selected and not found_selected:
                    is_selected = True
                    found_selected = True
                l2_items.append(MenuItemInfo(
                    name=item.get("name", ""),
                    element_text=item.get("element_text", item.get("name", "")),
                    element_name=item.get("element_name", ""),
                    coordinates=tuple(coords) if coords else (0, 0),
                    level=2,
                    is_selected=is_selected,
                ))

        self.menu_structure.l2_map[l1.name] = l2_items
        self.menu_structure.current_l2_index = 0

        if l2_items:
            l2_names = [i.name for i in l2_items]
            logger.info(f"[步骤{step_number}] L1'{l1.name}'有 {len(l2_items)} 个L2标签: {l2_names}")
            self.state = EngineState.TEST_L2
            return self._make_info_step(step_number, screenshot_path, elements,
                                        f"L1'{l1.name}'的L2标签: {l2_names}")
        else:
            self.state = EngineState.TEST_L1_DIRECT
            return self._make_info_step(step_number, screenshot_path, elements,
                                        f"L1'{l1.name}'无L2标签")

    def _step_switch_l1(self, step_number: int) -> ExplorationStep:
        """切换L1：点击下一个L1底部导航项。

        点击前先检查目标L1是否在UI树中可见，不可见说明上一个L2跳转了新页面，需要先返回。
        """
        l1 = self.menu_structure.current_l1()
        if not l1:
            self.state = EngineState.COMPLETE
            return self._make_info_step(step_number, "", [], "所有L1测试完成")

        # 检查目标L1是否在当前页面（上一个L2可能跳转了新页面）
        back_step = self._check_l1_and_back_if_needed(step_number, l1)
        if back_step:
            return back_step

        logger.info(f"[步骤{step_number}] 切换到L1: '{l1.name}'")

        # 构建点击动作
        action = AIDecision(
            action=ActionType.CLICK,
            coordinates=l1.coordinates,
            priority=Priority.HIGH,
            reasoning=f"切换到L1: {l1.name}",
        )
        action_result = self.action_executor.execute(action)

        if action_result != "success":
            logger.warning(f"[步骤{step_number}] L1'{l1.name}'点击失败: {action_result}，重新AI发现L1")
            self.state = EngineState.DISCOVER_L1
            return ExplorationStep(
                step_number=step_number, timestamp=time.time(),
                screenshot_path="", screen_description=f"L1'{l1.name}'点击失败，重新发现",
                ui_tree_summary="", action_taken=action, action_result=action_result,
                screen_fingerprint="",
            )

        # 点击后等待页面加载
        time.sleep(self.config.exploration.action_delay)

        # 使用已有的L2数据（同一次运行中之前发现过的）
        # 区分：key存在(已发现，可能为空) vs key不存在(未发现，需AI识别)
        if l1.name in self.menu_structure.l2_map:
            l2_cached = self.menu_structure.l2_map[l1.name]
            if l2_cached:
                self.menu_structure.current_l2_index = 0
                self.state = EngineState.TEST_L2
            else:
                self.state = EngineState.TEST_L1_DIRECT
        else:
            self.state = EngineState.DISCOVER_L2

        return ExplorationStep(
            step_number=step_number,
            timestamp=time.time(),
            screenshot_path="",
            screen_description=f"切换L1: {l1.name}",
            ui_tree_summary="",
            action_taken=action,
            action_result=action_result,
            screen_fingerprint="",
        )

    def _step_test_l2(self, step_number: int) -> ExplorationStep:
        """测试L2：点击当前L2标签（优先Poco文本）→ 转CHECK_BLOCK

        点击前先在UI树中查找目标L2控件：
        - 找到 → 直接点击
        - 找不到且页面上无任何L2标签 → 说明跳转了新页面，先返回
        - 找不到但其他L2标签可见 → 可能是可滚动tab，用Poco文本兜底
        """
        l1 = self.menu_structure.current_l1()
        l2 = self.menu_structure.current_l2()
        if not l1 or not l2:
            # L2遍历完毕，切下一个L1
            self._advance_to_next_l1()
            return self._make_info_step(step_number, "", [], "当前L1的L2全部测完")

        # 非第一个L2时，检查目标L2是否在当前页面
        if self.menu_structure.current_l2_index > 0:
            back_step = self._check_l2_and_back_if_needed(step_number, l2)
            if back_step:
                return back_step

        target_name = f"{l1.name}-{l2.name}"
        logger.info(f"[步骤{step_number}] 点击L2: '{l2.name}'（L1={l1.name}）")

        # 点击L2（Poco文本匹配 + 坐标兜底，不用element_name避免匹配到错误元素）
        target_element = UIElement(
            name="", text=l2.element_text or l2.name, desc="", type="tab",
            control_type=ControlType.TAB, bounds={}, center=l2.coordinates or (),
            clickable=True, enabled=True, visible=True,
        )
        action = AIDecision(
            action=ActionType.CLICK,
            target_element=target_element,
            coordinates=l2.coordinates,
            priority=Priority.HIGH,
            reasoning=f"测试L2: {l2.name}",
        )
        action_result = self.action_executor.execute(action)
        if action_result != "success":
            logger.warning(f"[步骤{step_number}] L2'{l2.name}'点击失败: {action_result}，重新AI发现L2")
            self.state = EngineState.DISCOVER_L2
            return ExplorationStep(
                step_number=step_number, timestamp=time.time(),
                screenshot_path="", screen_description=f"L2'{l2.name}'点击失败，重新发现",
                ui_tree_summary="", action_taken=action, action_result=action_result,
                screen_fingerprint="",
            )

        # 记录已测控件
        self.last_clicked_target = target_name
        if target_name not in self.tested_controls:
            self.tested_controls.append(target_name)

        self.loading_retry_count = 0
        self._back_retry_count = 0
        self.state = EngineState.CHECK_BLOCK

        return ExplorationStep(
            step_number=step_number,
            timestamp=time.time(),
            screenshot_path="",
            screen_description=f"点击L2: {target_name}",
            ui_tree_summary="",
            action_taken=AIDecision(
                action=ActionType.CLICK,
                coordinates=l2.coordinates,
                priority=Priority.HIGH,
                reasoning=f"测试L2: {target_name}",
            ),
            action_result="success",
            screen_fingerprint="",
        )

    def _step_test_l1_direct(self, step_number: int) -> ExplorationStep:
        """L1无L2标签时，直接检查L1页面的阻断状态"""
        l1 = self.menu_structure.current_l1()
        if not l1:
            self.state = EngineState.COMPLETE
            return self._make_info_step(step_number, "", [], "无L1可处理")

        self.last_clicked_target = l1.name
        if l1.name not in self.tested_controls:
            self.tested_controls.append(l1.name)
        self.loading_retry_count = 0
        self.state = EngineState.CHECK_L1_BLOCK

        return self._make_info_step(step_number, "", [],
                                    f"L1'{l1.name}'无L2，准备检查阻断")

    def _step_check_block(self, step_number: int) -> ExplorationStep:
        """检查页面状态：截图→AI判断→记录结果→前进到下一个L2"""
        # 等待页面加载
        time.sleep(self.config.exploration.screenshot_delay)

        screenshot_path, elements, ui_tree_text = self._capture_and_analyze(step_number)
        if not screenshot_path:
            return self._make_error_step(step_number, "", "截图捕获失败")

        mode = self.config.mode
        mode_label = "功能检查" if mode == 1 else "阻断检查"
        logger.info(f"[步骤{step_number}] AI{mode_label}: '{self.last_clicked_target}'...")
        result = self.ai_client.check_block_status(screenshot_path, ui_tree_text, self.last_clicked_target, mode=mode)

        # 弹窗检测
        if result.get("has_popup") and result.get("popup_close_button"):
            self.previous_state = self.state  # CHECK_BLOCK 或 CHECK_BLOCK_LOADING
            self.state = EngineState.HANDLE_POPUP
            btn = result["popup_close_button"]
            raw_coords = self._normalize_coords(tuple(btn.get("coordinates", (0.5, 0.5))))
            self._pending_popup_coords = self._refine_popup_coords(raw_coords, elements)
            self._pending_popup_text = btn.get("text", "关闭")
            self._pending_popup_type = result.get("popup_type", "other")
            return self._make_info_step(step_number, screenshot_path, elements, "发现弹窗，准备处理")

        is_error = result.get("is_error_screen", False)
        is_loading = result.get("is_loading", False)
        desc = result.get("error_description", "") or result.get("screen_description", "")

        if mode == 1:
            return self._check_block_mode1(step_number, screenshot_path, elements, is_error, is_loading, desc)
        else:
            return self._check_block_mode0(step_number, screenshot_path, elements, is_error, is_loading, desc)

    def _check_block_mode0(self, step_number, screenshot_path, elements, is_error, is_loading, desc):
        """mode=0 阻断模式：is_error=阻断成功, 正常加载=阻断失败→终止"""
        if is_error:
            # ★ 阻断成功
            logger.info(f"[步骤{step_number}] ✓ 阻断成功: '{self.last_clicked_target}' → {desc}")
            self.loading_retry_count = 0
            self._record_block_result(step_number, "block_success", desc, screenshot_path)
            self._update_current_menu_item("block_success", desc, screenshot_path)
            if not self.menu_structure.advance_l2():
                self._advance_to_next_l1()
            else:
                self.state = EngineState.TEST_L2
            return ExplorationStep(
                step_number=step_number, timestamp=time.time(),
                screenshot_path=screenshot_path,
                screen_description=f"[阻断成功] {self.last_clicked_target}",
                ui_tree_summary=f"{len(elements)}个元素",
                action_taken=AIDecision(action=ActionType.WAIT, priority=Priority.HIGH, reasoning="阻断成功，继续下一个"),
                action_result="block_success", screen_fingerprint="",
            )
        elif is_loading:
            # ★ 加载中
            self.loading_retry_count += 1
            if self.loading_retry_count >= self.max_loading_retries:
                logger.info(f"[步骤{step_number}] ✓ 阻断成功: '{self.last_clicked_target}' → 持续无法加载（重试{self.loading_retry_count}次）")
                self.loading_retry_count = 0
                self._record_block_result(step_number, "block_success", f"持续loading: {desc}", screenshot_path)
                self._update_current_menu_item("block_success", f"持续loading: {desc}", screenshot_path)
                if not self.menu_structure.advance_l2():
                    self._advance_to_next_l1()
                else:
                    self.state = EngineState.TEST_L2
                return ExplorationStep(
                    step_number=step_number, timestamp=time.time(),
                    screenshot_path=screenshot_path,
                    screen_description=f"[阻断成功] {self.last_clicked_target}（持续loading）",
                    ui_tree_summary=f"{len(elements)}个元素",
                    action_taken=AIDecision(action=ActionType.WAIT, priority=Priority.HIGH, reasoning="持续loading=阻断成功"),
                    action_result="block_success", screen_fingerprint="",
                )
            else:
                logger.info(f"[步骤{step_number}] ⏳ 加载中({self.loading_retry_count}/{self.max_loading_retries}): '{self.last_clicked_target}'")
                self.state = EngineState.CHECK_BLOCK_LOADING
                return ExplorationStep(
                    step_number=step_number, timestamp=time.time(),
                    screenshot_path=screenshot_path,
                    screen_description=f"[加载中] {self.last_clicked_target}",
                    ui_tree_summary=f"{len(elements)}个元素",
                    action_taken=AIDecision(action=ActionType.WAIT, priority=Priority.MEDIUM, reasoning="页面加载中，等待重试"),
                    action_result="loading", screen_fingerprint="",
                )
        else:
            # ★ 阻断失败
            logger.error(f"[步骤{step_number}] ✗ 阻断失败: '{self.last_clicked_target}' → {desc}")
            self._record_block_result(step_number, "block_failure", desc, screenshot_path)
            self._update_current_menu_item("block_failure", desc, screenshot_path)
            self.blocking_failure = True
            return ExplorationStep(
                step_number=step_number, timestamp=time.time(),
                screenshot_path=screenshot_path,
                screen_description=f"[阻断失败] {self.last_clicked_target}",
                ui_tree_summary=f"{len(elements)}个元素",
                action_taken=AIDecision(action=ActionType.WAIT, priority=Priority.CRITICAL, reasoning="阻断失败: 页面正常加载了数据"),
                action_result="block_failure", screen_fingerprint="",
            )

    def _check_block_mode1(self, step_number, screenshot_path, elements, is_error, is_loading, desc):
        """mode=1 功能测试：正常加载=功能正常, is_error=功能异常（记录但不终止）"""
        if is_error:
            # ★ 功能异常（记录但继续）
            logger.warning(f"[步骤{step_number}] ✗ 功能异常: '{self.last_clicked_target}' → {desc}")
            self.loading_retry_count = 0
            self._record_block_result(step_number, "function_failure", desc, screenshot_path)
            self._update_current_menu_item("function_failure", desc, screenshot_path)
            if not self.menu_structure.advance_l2():
                self._advance_to_next_l1()
            else:
                self.state = EngineState.TEST_L2
            return ExplorationStep(
                step_number=step_number, timestamp=time.time(),
                screenshot_path=screenshot_path,
                screen_description=f"[功能异常] {self.last_clicked_target}",
                ui_tree_summary=f"{len(elements)}个元素",
                action_taken=AIDecision(action=ActionType.WAIT, priority=Priority.HIGH, reasoning="功能异常，记录并继续"),
                action_result="function_failure", screen_fingerprint="",
            )
        elif is_loading:
            # ★ 加载中
            self.loading_retry_count += 1
            if self.loading_retry_count >= self.max_loading_retries:
                logger.warning(f"[步骤{step_number}] ✗ 功能异常: '{self.last_clicked_target}' → 持续无法加载（重试{self.loading_retry_count}次）")
                self.loading_retry_count = 0
                self._record_block_result(step_number, "function_failure", f"持续loading: {desc}", screenshot_path)
                self._update_current_menu_item("function_failure", f"持续loading: {desc}", screenshot_path)
                if not self.menu_structure.advance_l2():
                    self._advance_to_next_l1()
                else:
                    self.state = EngineState.TEST_L2
                return ExplorationStep(
                    step_number=step_number, timestamp=time.time(),
                    screenshot_path=screenshot_path,
                    screen_description=f"[功能异常] {self.last_clicked_target}（持续loading）",
                    ui_tree_summary=f"{len(elements)}个元素",
                    action_taken=AIDecision(action=ActionType.WAIT, priority=Priority.HIGH, reasoning="持续loading=功能异常"),
                    action_result="function_failure", screen_fingerprint="",
                )
            else:
                logger.info(f"[步骤{step_number}] ⏳ 加载中({self.loading_retry_count}/{self.max_loading_retries}): '{self.last_clicked_target}'")
                self.state = EngineState.CHECK_BLOCK_LOADING
                return ExplorationStep(
                    step_number=step_number, timestamp=time.time(),
                    screenshot_path=screenshot_path,
                    screen_description=f"[加载中] {self.last_clicked_target}",
                    ui_tree_summary=f"{len(elements)}个元素",
                    action_taken=AIDecision(action=ActionType.WAIT, priority=Priority.MEDIUM, reasoning="页面加载中，等待重试"),
                    action_result="loading", screen_fingerprint="",
                )
        else:
            # ★ 功能正常
            logger.info(f"[步骤{step_number}] ✓ 功能正常: '{self.last_clicked_target}' → {desc}")
            self.loading_retry_count = 0
            self._record_block_result(step_number, "function_success", desc, screenshot_path)
            self._update_current_menu_item("function_success", desc, screenshot_path)
            if not self.menu_structure.advance_l2():
                self._advance_to_next_l1()
            else:
                self.state = EngineState.TEST_L2
            return ExplorationStep(
                step_number=step_number, timestamp=time.time(),
                screenshot_path=screenshot_path,
                screen_description=f"[功能正常] {self.last_clicked_target}",
                ui_tree_summary=f"{len(elements)}个元素",
                action_taken=AIDecision(action=ActionType.WAIT, priority=Priority.HIGH, reasoning="功能正常，继续下一个"),
                action_result="function_success", screen_fingerprint="",
            )

    def _step_check_l1_block(self, step_number: int) -> ExplorationStep:
        """检查L1页面状态（无L2的情况），完成后切换到下一个L1"""
        # 等待页面加载
        time.sleep(self.config.exploration.screenshot_delay)

        screenshot_path, elements, ui_tree_text = self._capture_and_analyze(step_number)
        if not screenshot_path:
            return self._make_error_step(step_number, "", "截图捕获失败")

        mode = self.config.mode
        mode_label = "功能检查" if mode == 1 else "L1阻断检查"
        logger.info(f"[步骤{step_number}] AI{mode_label}: '{self.last_clicked_target}'...")
        result = self.ai_client.check_block_status(screenshot_path, ui_tree_text, self.last_clicked_target, mode=mode)

        # 弹窗检测
        if result.get("has_popup") and result.get("popup_close_button"):
            self.previous_state = EngineState.CHECK_L1_BLOCK
            self.state = EngineState.HANDLE_POPUP
            btn = result["popup_close_button"]
            raw_coords = self._normalize_coords(tuple(btn.get("coordinates", (0.5, 0.5))))
            self._pending_popup_coords = self._refine_popup_coords(raw_coords, elements)
            self._pending_popup_text = btn.get("text", "关闭")
            self._pending_popup_type = result.get("popup_type", "other")
            return self._make_info_step(step_number, screenshot_path, elements, "发现弹窗，准备处理")

        is_error = result.get("is_error_screen", False)
        is_loading = result.get("is_loading", False)
        desc = result.get("error_description", "") or result.get("screen_description", "")

        if mode == 1:
            return self._check_l1_block_mode1(step_number, screenshot_path, elements, is_error, is_loading, desc)
        else:
            return self._check_l1_block_mode0(step_number, screenshot_path, elements, is_error, is_loading, desc)

    def _check_l1_block_mode0(self, step_number, screenshot_path, elements, is_error, is_loading, desc):
        """mode=0 L1阻断模式"""
        if is_error:
            logger.info(f"[步骤{step_number}] ✓ L1阻断成功: '{self.last_clicked_target}' → {desc}")
            self._record_block_result(step_number, "block_success", desc, screenshot_path)
            l1 = self.menu_structure.current_l1()
            if l1:
                l1.status = "block_success"
                l1.block_result = desc
                l1.screenshot_path = screenshot_path
            self._advance_to_next_l1()
            return ExplorationStep(
                step_number=step_number, timestamp=time.time(),
                screenshot_path=screenshot_path,
                screen_description=f"[L1阻断成功] {self.last_clicked_target}",
                ui_tree_summary=f"{len(elements)}个元素",
                action_taken=AIDecision(action=ActionType.WAIT, priority=Priority.HIGH, reasoning="L1阻断成功"),
                action_result="block_success", screen_fingerprint="",
            )
        elif is_loading:
            self.loading_retry_count += 1
            if self.loading_retry_count >= self.max_loading_retries:
                logger.info(f"[步骤{step_number}] ✓ L1阻断成功（持续loading）: '{self.last_clicked_target}'")
                self.loading_retry_count = 0
                self._record_block_result(step_number, "block_success", f"持续loading: {desc}", screenshot_path)
                l1 = self.menu_structure.current_l1()
                if l1:
                    l1.status = "block_success"
                    l1.block_result = f"持续loading: {desc}"
                    l1.screenshot_path = screenshot_path
                self._advance_to_next_l1()
                return ExplorationStep(
                    step_number=step_number, timestamp=time.time(),
                    screenshot_path=screenshot_path,
                    screen_description=f"[L1阻断成功] {self.last_clicked_target}（持续loading）",
                    ui_tree_summary=f"{len(elements)}个元素",
                    action_taken=AIDecision(action=ActionType.WAIT, priority=Priority.HIGH, reasoning="L1持续loading=阻断成功"),
                    action_result="block_success", screen_fingerprint="",
                )
            else:
                logger.info(f"[步骤{step_number}] ⏳ L1加载中({self.loading_retry_count}/{self.max_loading_retries})")
                return ExplorationStep(
                    step_number=step_number, timestamp=time.time(),
                    screenshot_path=screenshot_path,
                    screen_description=f"[L1加载中] {self.last_clicked_target}",
                    ui_tree_summary=f"{len(elements)}个元素",
                    action_taken=AIDecision(action=ActionType.WAIT, priority=Priority.MEDIUM, reasoning="L1加载中，等待重试"),
                    action_result="loading", screen_fingerprint="",
                )
        else:
            logger.error(f"[步骤{step_number}] ✗ L1阻断失败: '{self.last_clicked_target}' → {desc}")
            self._record_block_result(step_number, "block_failure", desc, screenshot_path)
            l1 = self.menu_structure.current_l1()
            if l1:
                l1.status = "block_failure"
                l1.block_result = desc
                l1.screenshot_path = screenshot_path
            self.blocking_failure = True
            return ExplorationStep(
                step_number=step_number, timestamp=time.time(),
                screenshot_path=screenshot_path,
                screen_description=f"[L1阻断失败] {self.last_clicked_target}",
                ui_tree_summary=f"{len(elements)}个元素",
                action_taken=AIDecision(action=ActionType.WAIT, priority=Priority.CRITICAL, reasoning="L1阻断失败"),
                action_result="block_failure", screen_fingerprint="",
            )

    def _check_l1_block_mode1(self, step_number, screenshot_path, elements, is_error, is_loading, desc):
        """mode=1 L1功能测试"""
        l1 = self.menu_structure.current_l1()
        if is_error:
            logger.warning(f"[步骤{step_number}] ✗ L1功能异常: '{self.last_clicked_target}' → {desc}")
            self._record_block_result(step_number, "function_failure", desc, screenshot_path)
            if l1:
                l1.status = "function_failure"
                l1.block_result = desc
                l1.screenshot_path = screenshot_path
            self._advance_to_next_l1()
            return ExplorationStep(
                step_number=step_number, timestamp=time.time(),
                screenshot_path=screenshot_path,
                screen_description=f"[L1功能异常] {self.last_clicked_target}",
                ui_tree_summary=f"{len(elements)}个元素",
                action_taken=AIDecision(action=ActionType.WAIT, priority=Priority.HIGH, reasoning="L1功能异常，记录并继续"),
                action_result="function_failure", screen_fingerprint="",
            )
        elif is_loading:
            self.loading_retry_count += 1
            if self.loading_retry_count >= self.max_loading_retries:
                logger.warning(f"[步骤{step_number}] ✗ L1功能异常（持续loading）: '{self.last_clicked_target}'")
                self.loading_retry_count = 0
                self._record_block_result(step_number, "function_failure", f"持续loading: {desc}", screenshot_path)
                if l1:
                    l1.status = "function_failure"
                    l1.block_result = f"持续loading: {desc}"
                    l1.screenshot_path = screenshot_path
                self._advance_to_next_l1()
                return ExplorationStep(
                    step_number=step_number, timestamp=time.time(),
                    screenshot_path=screenshot_path,
                    screen_description=f"[L1功能异常] {self.last_clicked_target}（持续loading）",
                    ui_tree_summary=f"{len(elements)}个元素",
                    action_taken=AIDecision(action=ActionType.WAIT, priority=Priority.HIGH, reasoning="L1持续loading=功能异常"),
                    action_result="function_failure", screen_fingerprint="",
                )
            else:
                logger.info(f"[步骤{step_number}] ⏳ L1加载中({self.loading_retry_count}/{self.max_loading_retries})")
                return ExplorationStep(
                    step_number=step_number, timestamp=time.time(),
                    screenshot_path=screenshot_path,
                    screen_description=f"[L1加载中] {self.last_clicked_target}",
                    ui_tree_summary=f"{len(elements)}个元素",
                    action_taken=AIDecision(action=ActionType.WAIT, priority=Priority.MEDIUM, reasoning="L1加载中，等待重试"),
                    action_result="loading", screen_fingerprint="",
                )
        else:
            logger.info(f"[步骤{step_number}] ✓ L1功能正常: '{self.last_clicked_target}' → {desc}")
            self._record_block_result(step_number, "function_success", desc, screenshot_path)
            if l1:
                l1.status = "function_success"
                l1.block_result = desc
                l1.screenshot_path = screenshot_path
            self._advance_to_next_l1()
            return ExplorationStep(
                step_number=step_number, timestamp=time.time(),
                screenshot_path=screenshot_path,
                screen_description=f"[L1功能正常] {self.last_clicked_target}",
                ui_tree_summary=f"{len(elements)}个元素",
                action_taken=AIDecision(action=ActionType.WAIT, priority=Priority.HIGH, reasoning="L1功能正常"),
                action_result="function_success", screen_fingerprint="",
            )

    def _step_handle_popup(self, step_number: int) -> ExplorationStep:
        """处理弹窗：登录弹窗可转HANDLE_LOGIN，其他弹窗点关闭→缓存→返回之前状态"""
        if not self._pending_popup_coords:
            self.state = self.previous_state or EngineState.DISCOVER_L1
            return self._make_info_step(step_number, "", [], "无弹窗坐标")

        # 登录弹窗：如果配置了需要登录，则转入登录流程
        if self._pending_popup_type == "login" and self.config.login_required:
            logger.info(f"[步骤{step_number}] 检测到登录弹窗，进入自动登录流程")
            self._login_step = 0
            self._login_analysis = {}
            self._login_retries = 0
            self.state = EngineState.HANDLE_LOGIN
            # 清除弹窗信息（登录流程会自行处理）
            self._pending_popup_coords = None
            self._pending_popup_text = ""
            self._pending_popup_type = ""
            return self._make_info_step(step_number, "", [], "登录弹窗，转入自动登录")

        logger.info(f"[步骤{step_number}] 关闭弹窗: '{self._pending_popup_text}'")

        # 构建target_element，让ActionExecutor优先用Poco文本匹配点击（比坐标更准）
        target_element = None
        if self._pending_popup_text:
            target_element = UIElement(
                name="", text=self._pending_popup_text, desc="", type="button",
                control_type=ControlType.BUTTON, bounds={}, center=self._pending_popup_coords or (),
                clickable=True, enabled=True, visible=True,
            )

        action = AIDecision(
            action=ActionType.CLICK,
            target_element=target_element,
            coordinates=self._pending_popup_coords,
            is_popup=True,
            priority=Priority.HIGH,
            reasoning=f"关闭弹窗: {self._pending_popup_text}",
        )
        action_result = self.action_executor.execute(action)

        # 清除弹窗信息，回到之前状态
        self._pending_popup_coords = None
        self._pending_popup_text = ""
        self._pending_popup_type = ""
        self.state = self.previous_state or EngineState.DISCOVER_L1
        self.previous_state = None

        time.sleep(self.config.exploration.action_delay)

        return ExplorationStep(
            step_number=step_number,
            timestamp=time.time(),
            screenshot_path="",
            screen_description=f"关闭弹窗",
            ui_tree_summary="",
            action_taken=action,
            action_result=action_result,
            screen_fingerprint="",
        )

    def _step_handle_login(self, step_number: int) -> ExplorationStep:
        """自动登录：AI分析登录界面→按步骤填写凭据→点击登录"""

        # 子步骤0：AI分析登录界面
        if self._login_step == 0:
            screenshot_path, elements, ui_tree_text = self._capture_and_analyze(step_number)
            if not screenshot_path:
                return self._login_fail(step_number, "截图失败")

            logger.info(f"[步骤{step_number}] AI分析登录界面...")
            analysis = self.ai_client.analyze_login_screen(
                screenshot_path, ui_tree_text, self.config.login_method
            )

            if not analysis.get("is_login_screen"):
                # 不是登录界面，可能已经登录成功或弹窗已消失
                logger.info(f"[步骤{step_number}] 当前不是登录界面，返回之前状态")
                self.state = self.previous_state or EngineState.DISCOVER_L1
                self.previous_state = None
                return self._make_info_step(step_number, screenshot_path, elements, "不是登录界面，跳过")

            self._login_analysis = analysis
            steps = analysis.get("steps", [])
            if not steps:
                # 没有可执行的步骤，尝试关闭或跳过
                return self._login_close_or_skip(step_number, analysis, screenshot_path, elements)

            self._login_step = 1  # 转入执行步骤
            return self._make_info_step(step_number, screenshot_path, elements,
                                        f"登录界面分析完成，{len(steps)}个操作步骤")

        # 子步骤1+：按AI返回的steps顺序执行
        steps = self._login_analysis.get("steps", [])
        exec_index = self._login_step - 1  # step1对应steps[0]

        if exec_index >= len(steps):
            # 所有步骤执行完毕，等待并验证
            time.sleep(3)  # 等待登录请求完成
            screenshot_path, elements, ui_tree_text = self._capture_and_analyze(step_number)
            if not screenshot_path:
                return self._login_fail(step_number, "登录后截图失败")

            # 再次分析，看是否还在登录界面
            analysis = self.ai_client.analyze_login_screen(
                screenshot_path, ui_tree_text, self.config.login_method
            )
            if analysis.get("is_login_screen"):
                self._login_retries += 1
                if self._login_retries >= self.max_login_retries:
                    logger.warning(f"[步骤{step_number}] 登录重试{self._login_retries}次仍在登录界面，放弃")
                    return self._login_fail(step_number, "多次登录尝试失败")
                logger.warning(f"[步骤{step_number}] 登录后仍在登录界面，重试({self._login_retries}/{self.max_login_retries})")
                self._login_step = 0  # 重新分析
                return self._make_info_step(step_number, screenshot_path, elements, "登录后仍在登录界面，重试")

            # 登录成功
            logger.info(f"[步骤{step_number}] ✓ 登录成功，返回之前状态")
            self.state = self.previous_state or EngineState.DISCOVER_L1
            self.previous_state = None
            self._login_step = 0
            self._login_analysis = {}
            return self._make_info_step(step_number, screenshot_path, elements, "登录成功")

        # 执行当前步骤
        step_info = steps[exec_index]
        action_type = step_info.get("action", "click")
        coords = step_info.get("coordinates")
        text = step_info.get("text", "")
        target = step_info.get("target", "")

        if action_type == "skip":
            logger.info(f"[步骤{step_number}] 登录步骤{exec_index+1}跳过: {target}")
            self._login_step += 1
            return self._make_info_step(step_number, "", [], f"跳过: {target}")

        if action_type == "input_text":
            # 填写凭据：根据目标判断填手机号还是密码
            input_text = text
            if "手机" in target or "账号" in target or "phone" in target.lower():
                input_text = self.config.login_phone
            elif "密码" in target or "password" in target.lower():
                input_text = self.config.login_password

            if not input_text:
                logger.warning(f"[步骤{step_number}] 登录步骤{exec_index+1}需要输入但文本为空: {target}")
                self._login_step += 1
                return self._make_info_step(step_number, "", [], f"输入为空，跳过: {target}")

            logger.info(f"[步骤{step_number}] 登录步骤{exec_index+1}: 输入'{target}'")
            action = AIDecision(
                action=ActionType.TEXT_INPUT,
                coordinates=tuple(coords) if coords else None,
                text_input=input_text,
                priority=Priority.HIGH,
                reasoning=f"登录输入: {target}",
            )
            action_result = self.action_executor.execute(action)
            time.sleep(1)

        elif action_type == "click":
            logger.info(f"[步骤{step_number}] 登录步骤{exec_index+1}: 点击'{target}'")
            action = AIDecision(
                action=ActionType.CLICK,
                coordinates=tuple(coords) if coords else None,
                priority=Priority.HIGH,
                reasoning=f"登录点击: {target}",
            )
            action_result = self.action_executor.execute(action)
            time.sleep(1)

        elif action_type == "wait":
            time.sleep(2)
            action_result = "success"

        else:
            logger.warning(f"[步骤{step_number}] 未知登录操作类型: {action_type}")
            action_result = "failed"

        self._login_step += 1
        return ExplorationStep(
            step_number=step_number, timestamp=time.time(),
            screenshot_path="", screen_description=f"登录步骤{exec_index+1}: {target}",
            ui_tree_summary="",
            action_taken=AIDecision(
                action=ActionType.CLICK, coordinates=tuple(coords) if coords else None,
                priority=Priority.HIGH, reasoning=f"登录: {target}",
            ),
            action_result=action_result, screen_fingerprint="",
        )

    def _login_fail(self, step_number: int, reason: str) -> ExplorationStep:
        """登录失败：回退到之前的状态"""
        logger.warning(f"[步骤{step_number}] 自动登录失败: {reason}")
        self.state = self.previous_state or EngineState.DISCOVER_L1
        self.previous_state = None
        self._login_step = 0
        self._login_analysis = {}
        return self._make_info_step(step_number, "", [], f"登录失败: {reason}")

    def _login_close_or_skip(self, step_number, analysis, screenshot_path, elements):
        """登录界面无可执行步骤时，尝试关闭/游客登录/跳过"""
        # 优先尝试游客登录按钮
        guest = analysis.get("guest_button")
        if guest and guest.get("coordinates"):
            logger.info(f"[步骤{step_number}] 点击游客登录: {guest.get('text', '游客')}")
            action = AIDecision(
                action=ActionType.CLICK,
                coordinates=tuple(guest["coordinates"]),
                priority=Priority.HIGH,
                reasoning="游客登录",
            )
            self.action_executor.execute(action)
            time.sleep(2)
            self.state = self.previous_state or EngineState.DISCOVER_L1
            self.previous_state = None
            self._login_step = 0
            return self._make_info_step(step_number, screenshot_path, elements, "点击游客登录")

        # 其次关闭登录弹窗
        close = analysis.get("close_button")
        if close and close.get("coordinates"):
            logger.info(f"[步骤{step_number}] 关闭登录弹窗: {close.get('text', 'X')}")
            action = AIDecision(
                action=ActionType.CLICK,
                coordinates=tuple(close["coordinates"]),
                is_popup=True,
                priority=Priority.HIGH,
                reasoning="关闭登录弹窗",
            )
            self.action_executor.execute(action)
            time.sleep(self.config.exploration.action_delay)
            self.state = self.previous_state or EngineState.DISCOVER_L1
            self.previous_state = None
            self._login_step = 0
            return self._make_info_step(step_number, screenshot_path, elements, "关闭登录弹窗")

        return self._login_fail(step_number, "无可执行的登录步骤")

    # ==================== 辅助方法 ====================

    def _check_l1_and_back_if_needed(self, step_number: int, target_l1: MenuItemInfo) -> Optional[ExplorationStep]:
        """检查目标L1底部导航是否在当前页面可见，不可见则返回。

        场景：上一个L1的最后一个L2跳转了新页面，底部导航栏消失了。
        """
        if self._back_retry_count >= self._max_back_retries:
            self._back_retry_count = 0
            return None

        elements = self.ui_analyzer.extract_ui_tree()

        # 在UI树中查找目标L1
        target_found = False
        for elem in elements:
            elem_text = (elem.text or "").strip()
            if elem_text and (elem_text == target_l1.name or elem_text == target_l1.element_text):
                target_found = True
                break

        if target_found:
            self._back_retry_count = 0
            return None

        # 目标L1没找到，检查其他L1是否可见
        other_found = 0
        for l1 in self.menu_structure.l1_items:
            if l1.name == target_l1.name:
                continue
            for elem in elements:
                elem_text = (elem.text or "").strip()
                if elem_text and (elem_text == l1.name or elem_text == l1.element_text):
                    other_found += 1
                    break

        if other_found > 0:
            self._back_retry_count = 0
            return None

        # 所有L1都找不到 → 页面跳转了，需要返回
        logger.info(f"[步骤{step_number}] 页面跳转，返回底部导航")

        back_elem = self._find_back_button(elements)
        if back_elem:
            action = AIDecision(
                action=ActionType.CLICK,
                target_element=back_elem,
                coordinates=back_elem.center,
                priority=Priority.HIGH,
                reasoning=f"点击返回按钮，回到底部导航",
            )
        else:
            action = AIDecision(
                action=ActionType.BACK,
                priority=Priority.HIGH,
                reasoning=f"系统返回键，回到底部导航",
            )

        action_result = self.action_executor.execute(action)
        self._back_retry_count += 1
        time.sleep(self.config.exploration.action_delay)

        # 保持SWITCH_L1状态，下次循环重新检测
        return ExplorationStep(
            step_number=step_number, timestamp=time.time(),
            screenshot_path="", screen_description=f"返回底部导航(准备切换L1:{target_l1.name})",
            ui_tree_summary=f"{len(elements)}个元素",
            action_taken=action, action_result=action_result, screen_fingerprint="",
        )

    def _check_l2_and_back_if_needed(self, step_number: int, target_l2: MenuItemInfo) -> Optional[ExplorationStep]:
        """检查目标L2控件是否在当前页面存在，不存在则判断是否需要返回。

        三级判断：
        1. 目标L2在UI树中找到 → 不需要返回，正常点击
        2. 目标L2没找到，但其他L2标签可见 → 不需要返回（可能是可滚动tab栏，Poco文本兜底）
        3. 所有L2标签都找不到 → 页面跳转了，执行返回
        """
        if self._back_retry_count >= self._max_back_retries:
            self._back_retry_count = 0
            return None

        elements = self.ui_analyzer.extract_ui_tree()

        # 在UI树中查找目标L2
        target_found = False
        for elem in elements:
            elem_text = (elem.text or "").strip()
            if elem_text and (elem_text == target_l2.name or elem_text == target_l2.element_text):
                target_found = True
                break

        if target_found:
            # 目标L2存在，不需要返回
            self._back_retry_count = 0
            return None

        # 目标L2没找到，检查其他L2标签是否可见
        l2_list = self.menu_structure.current_l2_list()
        other_found = 0
        for l2 in l2_list:
            if l2.name == target_l2.name:
                continue
            for elem in elements:
                elem_text = (elem.text or "").strip()
                if elem_text and (elem_text == l2.name or elem_text == l2.element_text):
                    other_found += 1
                    break

        if other_found > 0:
            self._back_retry_count = 0
            return None

        # 所有L2标签都找不到 → 页面跳转了，需要返回
        logger.info(f"[步骤{step_number}] 页面跳转，返回L1页面")

        back_elem = self._find_back_button(elements)
        if back_elem:
            action = AIDecision(
                action=ActionType.CLICK,
                target_element=back_elem,
                coordinates=back_elem.center,
                priority=Priority.HIGH,
                reasoning=f"点击返回按钮，回到L1页面",
            )
        else:
            action = AIDecision(
                action=ActionType.BACK,
                priority=Priority.HIGH,
                reasoning=f"系统返回键，回到L1页面",
            )

        action_result = self.action_executor.execute(action)
        self._back_retry_count += 1
        time.sleep(self.config.exploration.action_delay)

        # 保持TEST_L2状态，下次循环重新检测
        return ExplorationStep(
            step_number=step_number, timestamp=time.time(),
            screenshot_path="", screen_description=f"返回L1页面(准备点击{target_l2.name})",
            ui_tree_summary=f"{len(elements)}个元素",
            action_taken=action, action_result=action_result, screen_fingerprint="",
        )

    @staticmethod
    def _find_back_button(elements: list) -> Optional[UIElement]:
        """在UI树中搜索顶部左上角的返回按钮。

        匹配条件：
        - 位于左上角: x < 0.2, y < 0.15
        - 可点击
        - desc/text含"返回"/"back"，或无文字的小图标按钮（如 < 箭头）
        """
        desc_match = None
        icon_match = None

        for elem in elements:
            if not elem.clickable:
                continue
            ex, ey = elem.center
            if ex > 0.2 or ey > 0.15:
                continue

            desc_lower = (elem.desc or "").strip().lower()
            text_lower = (elem.text or "").strip().lower()

            # 优先：有明确的"返回"描述
            if "返回" in desc_lower or "返回" in text_lower or "back" in desc_lower:
                desc_match = elem
                break

            # 次选：左上角的小可点击元素（可能是返回图标 < ）
            if not elem.text and not elem.desc:
                w = elem.bounds.get("width", 0)
                h = elem.bounds.get("height", 0)
                if 0 < w < 0.15 and 0 < h < 0.08 and icon_match is None:
                    icon_match = elem

        return desc_match or icon_match

    def _capture_and_analyze(self, step_number: int):
        """通用：截图 + 提取UI树。返回 (screenshot_path, elements, ui_tree_text)"""
        screenshot_path = self.ui_analyzer.capture_screenshot(
            self.config.logdir, f"step{step_number}"
        )
        if not screenshot_path:
            return "", [], ""
        elements = self.ui_analyzer.extract_ui_tree()
        ui_tree_text = self.ui_analyzer.format_ui_tree_text(elements)
        return screenshot_path, elements, ui_tree_text

    def _advance_to_next_l1(self):
        """前进到下一个L1，如果还有则切换，否则完成"""
        if self.menu_structure.advance_l1():
            self.state = EngineState.SWITCH_L1
        else:
            self.state = EngineState.COMPLETE

    def _record_block_result(self, step_number: int, result_type: str, desc: str, screenshot_path: str):
        """记录阻断测试结果"""
        self.issues_found.append({
            "step": step_number,
            "type": result_type,
            "target": self.last_clicked_target,
            "description": f"{'阻断成功' if result_type == 'block_success' else '阻断失败'}: {desc}",
            "screenshot": screenshot_path,
        })

    def _update_current_menu_item(self, status: str, desc: str, screenshot_path: str):
        """更新当前测试中的菜单项状态"""
        l2 = self.menu_structure.current_l2()
        if l2:
            l2.status = status
            l2.block_result = desc
            l2.screenshot_path = screenshot_path

    def _normalize_coords(self, coords: tuple) -> tuple:
        """将坐标归一化到0-1范围。如果AI返回了绝对像素坐标(>1.0)，自动转换。"""
        if not coords or len(coords) < 2:
            return coords
        x, y = coords[0], coords[1]
        if x > 1.0 or y > 1.0:
            try:
                screen_w, screen_h = self.action_executor._get_screen_size()
                x = x / screen_w
                y = y / screen_h
                return (x, y)
            except Exception:
                pass
        return coords

    @staticmethod
    def _refine_popup_coords(coords: tuple, elements: list) -> tuple:
        """当AI返回的弹窗关闭按钮坐标可疑时，从UI树中搜索可能的关闭按钮。

        关闭按钮特征：小尺寸、可点击、无文字、位于弹窗右上角区域。
        典型案例：android.view.View, pos=(0.852, 0.393), size=(0.118, 0.053), touchable=True, 无text/desc
        """
        if not coords or len(coords) < 2 or not elements:
            return coords

        x, y = coords[0], coords[1]
        # 判断坐标是否可疑：在屏幕中心附近（0.35~0.65），通常是AI猜测的默认值
        is_suspicious = (0.35 <= x <= 0.65) and (0.35 <= y <= 0.65)
        if not is_suspicious:
            return coords

        candidates = []
        for elem in elements:
            if not elem.clickable:
                continue

            ex, ey = elem.center
            if ex == 0 and ey == 0:
                continue

            w = elem.bounds.get("width", 0)
            h = elem.bounds.get("height", 0)

            # 关闭按钮特征：小尺寸 + 右侧 + 上半屏 + 无文字或文字为×/X/关闭
            is_small = (0 < w < 0.18) and (0 < h < 0.12)
            is_right = ex > 0.65
            is_upper = 0.05 < ey < 0.65
            text = (elem.text or "").strip()
            desc = (elem.desc or "").strip()
            has_close_indicator = text in ("", "×", "X", "x", "✕", "✖", "关闭") and desc in ("", "关闭", "close", "Close")

            if is_small and is_right and is_upper and has_close_indicator:
                # 评分：越靠右上角越可能是关闭按钮
                score = ex + (1 - ey)
                candidates.append((score, elem))

        if candidates:
            candidates.sort(key=lambda c: c[0], reverse=True)
            best = candidates[0][1]
            return best.center

        return coords

    @staticmethod
    def _make_info_step(step_number, screenshot_path, elements, description):
        """创建信息性步骤（非操作）"""
        return ExplorationStep(
            step_number=step_number,
            timestamp=time.time(),
            screenshot_path=screenshot_path if screenshot_path else "",
            screen_description=description,
            ui_tree_summary=f"{len(elements)}个元素" if elements else "",
            action_taken=AIDecision(
                action=ActionType.WAIT, priority=Priority.LOW,
                reasoning=description,
            ),
            action_result="success",
            screen_fingerprint="",
        )

    @staticmethod
    def _make_error_step(step_number, screenshot_path, reason):
        return ExplorationStep(
            step_number=step_number, timestamp=time.time(),
            screenshot_path=screenshot_path, screen_description=reason,
            ui_tree_summary="错误",
            action_taken=AIDecision(action=ActionType.WAIT, priority=Priority.LOW, reasoning=reason),
            action_result="error", screen_fingerprint="",
        )

    def _should_stop(self, step_number: int) -> bool:
        if self.blocking_failure and self.config.mode == 0:
            return True
        if self.state == EngineState.COMPLETE:
            return True
        if step_number >= self.config.exploration.max_steps:
            logger.info("停止: 达到最大步数")
            return True
        elapsed = time.time() - self.start_time
        if elapsed >= self.config.exploration.max_duration_seconds:
            logger.info(f"停止: 达到最大时长({elapsed:.0f}秒)")
            return True
        if self.consecutive_errors >= self.config.exploration.max_errors:
            logger.info(f"停止: 连续{self.consecutive_errors}次出错")
            return True
        return False

    def _build_result(self, package: str) -> ExplorationResult:
        # 从MenuStructure计算真实统计
        total_l1 = len(self.menu_structure.l1_items)
        total_l2 = sum(len(v) for v in self.menu_structure.l2_map.values())
        total_menu_items = total_l1 + total_l2
        tested_count = len(self.tested_controls)
        coverage = (tested_count / total_menu_items * 100) if total_menu_items > 0 else 0

        for i in range(len(self.steps) - 1):
            src, dst = self.steps[i].screen_fingerprint, self.steps[i + 1].screen_fingerprint
            if src and dst and src != dst:
                self.exploration_graph.setdefault(src, [])
                if dst not in self.exploration_graph[src]:
                    self.exploration_graph[src].append(dst)
        return ExplorationResult(
            app_package=package, platform=self.config.device.platform,
            start_time=self.start_time, end_time=time.time(),
            total_steps=len(self.steps),
            unique_screens=total_l1,
            total_elements_found=total_menu_items,
            elements_interacted=tested_count,
            coverage_percentage=coverage,
            steps=self.steps, screens=self.screen_manager.screens,
            issues_found=self.issues_found, exploration_graph=self.exploration_graph,
        )

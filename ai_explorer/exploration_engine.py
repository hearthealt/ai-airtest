# -*- encoding=utf8 -*-
"""核心探索引擎：结构化L1→L2导航阻断测试状态机。"""

import os
import time
import logging
from typing import List, Dict, Optional

from .config import Config
from .models import (
    AIDecision, ActionType, Priority,
    ExplorationStep, ExplorationResult,
    EngineState, MenuItemInfo, MenuStructure,
)
from .ai_client import AIClient
from .ui_analyzer import UIAnalyzer
from .screen_state import ScreenManager
from .action_executor import ActionExecutor
from .logger import ExplorationLogger
from .nav_cache import NavCacheDB

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
        self.popup_retry_count = 0
        self.max_popup_retries = 3

        # 导航缓存
        db_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "nav_cache.db")
        self.nav_cache = NavCacheDB(db_path)

    def run(self, app_package: str = "") -> ExplorationResult:
        self.start_time = time.time()
        self.app_package = app_package  # 保存供缓存使用
        logger.info(f"=== 结构化阻断测试开始 | 应用={app_package} ===")

        if app_package:
            try:
                self.dd.start_app(app_package)
                time.sleep(3)
                logger.info(f"应用已启动: {app_package}")
            except Exception as e:
                logger.error(f"启动应用失败: {e}")

        # 应用启动后，先尝试消除已知弹窗（不用AI）
        if self.config.use_nav_cache and app_package and self.nav_cache.has_popups(app_package, self.config.l_class):
            self._dismiss_cached_popups(app_package)

        # 尝试从缓存加载导航结构
        if self.config.use_nav_cache and app_package and self.nav_cache.has_cache(app_package, self.config.l_class):
            self.menu_structure = self.nav_cache.load_menu_structure(app_package, self.config.l_class)
            l1_count = len(self.menu_structure.l1_items)
            l2_count = sum(len(v) for v in self.menu_structure.l2_map.values())
            logger.info(f"从缓存加载导航: {l1_count}个L1, {l2_count}个L2")
            self.state = EngineState.SWITCH_L1  # 跳过发现，直接开始测试
        else:
            self.state = EngineState.DISCOVER_L1  # 首次运行，AI发现

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

                if self.blocking_failure:
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

        if self.blocking_failure:
            logger.info(f"=== 测试结果: 阻断失败 | 共{len(self.steps)}步 ===")
        else:
            logger.info(f"=== 测试结果: 全部阻断成功 | 已测控件: {self.tested_controls} ===")
        return self._build_result(app_package)

    # ==================== 状态机调度 ====================

    def _execute_state_step(self, step_number: int) -> ExplorationStep:
        """根据当前状态调度到对应的处理方法"""
        logger.info(f"[步骤{step_number}] 状态: {self.state.value}")

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
            self._pending_popup_coords = tuple(btn.get("coordinates", (0.5, 0.5)))
            self._pending_popup_text = btn.get("text", "关闭")
            logger.info(f"[步骤{step_number}] 发现弹窗，先关闭: {self._pending_popup_text}")
            return self._make_info_step(step_number, screenshot_path, elements, "发现弹窗，准备关闭")

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

        # 保存L1到缓存（L2将在发现后逐步更新）
        if self.config.use_nav_cache and self.app_package:
            self.nav_cache.save_menu_structure(self.app_package, self.config.l_class, self.menu_structure)

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
            self._pending_popup_coords = tuple(btn.get("coordinates", (0.5, 0.5)))
            self._pending_popup_text = btn.get("text", "关闭")
            logger.info(f"[步骤{step_number}] 发现弹窗，先关闭: {self._pending_popup_text}")
            return self._make_info_step(step_number, screenshot_path, elements, "发现弹窗，准备关闭")

        # 解析L2标签
        has_l2 = result.get("has_l2_tabs", False)
        l2_items = []
        if has_l2:
            for item in result.get("l2_items", []):
                coords = item.get("coordinates", [0, 0])
                l2_items.append(MenuItemInfo(
                    name=item.get("name", ""),
                    element_text=item.get("element_text", item.get("name", "")),
                    element_name=item.get("element_name", ""),
                    coordinates=tuple(coords) if coords else (0, 0),
                    level=2,
                    is_selected=item.get("is_selected", False),
                ))

        self.menu_structure.l2_map[l1.name] = l2_items
        self.menu_structure.current_l2_index = 0

        # 更新缓存中该L1的L2数据
        if self.config.use_nav_cache and self.app_package:
            self.nav_cache.update_l2_for_l1(self.app_package, self.config.l_class, l1.name, l2_items)

        if l2_items:
            l2_names = [i.name for i in l2_items]
            logger.info(f"[步骤{step_number}] L1'{l1.name}'有 {len(l2_items)} 个L2标签: {l2_names}")
            self.state = EngineState.TEST_L2
            return self._make_info_step(step_number, screenshot_path, elements,
                                        f"L1'{l1.name}'的L2标签: {l2_names}")
        else:
            logger.info(f"[步骤{step_number}] L1'{l1.name}'没有L2标签，直接测试L1页面")
            self.state = EngineState.TEST_L1_DIRECT
            return self._make_info_step(step_number, screenshot_path, elements,
                                        f"L1'{l1.name}'无L2标签")

    def _step_switch_l1(self, step_number: int) -> ExplorationStep:
        """切换L1：确定性点击下一个L1的坐标（无AI调用）"""
        l1 = self.menu_structure.current_l1()
        if not l1:
            self.state = EngineState.COMPLETE
            return self._make_info_step(step_number, "", [], "所有L1测试完成")

        logger.info(f"[步骤{step_number}] 切换到L1: '{l1.name}' 坐标={l1.coordinates}")

        # 构建点击动作
        action = AIDecision(
            action=ActionType.CLICK,
            coordinates=l1.coordinates,
            priority=Priority.HIGH,
            reasoning=f"切换到L1: {l1.name}",
        )
        action_result = self.action_executor.execute(action)

        if action_result != "success":
            logger.warning(f"[步骤{step_number}] L1'{l1.name}'点击失败: {action_result}")
            if self.config.use_nav_cache:
                logger.warning(f"缓存坐标可能过期，重新AI发现L1")
                self.nav_cache.delete_cache(self.app_package, self.config.l_class)
                self.state = EngineState.DISCOVER_L1
                return ExplorationStep(
                    step_number=step_number, timestamp=time.time(),
                    screenshot_path="", screen_description=f"L1'{l1.name}'点击失败，重新发现",
                    ui_tree_summary="", action_taken=action, action_result=action_result,
                    screen_fingerprint="",
                )

        # 点击后等待页面加载
        time.sleep(self.config.exploration.action_delay)

        # 切换L1后，尝试消除该页面可能出现的缓存弹窗
        if self.config.use_nav_cache and self.app_package:
            self._dismiss_cached_popups(self.app_package)

        # 优先使用缓存的L2数据，避免重复AI识别
        # 区分：key存在(已缓存，可能为空) vs key不存在(未缓存，需AI识别)
        if l1.name in self.menu_structure.l2_map:
            l2_cached = self.menu_structure.l2_map[l1.name]
            if l2_cached:
                logger.info(f"从缓存使用L1'{l1.name}'的L2: {len(l2_cached)}个标签 {[i.name for i in l2_cached]}")
                self.menu_structure.current_l2_index = 0
                self.state = EngineState.TEST_L2
            else:
                logger.info(f"缓存确认L1'{l1.name}'无L2标签，直接测试L1页面")
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
        """测试L2：确定性点击当前L2标签的坐标（无AI调用）→ 转CHECK_BLOCK"""
        l1 = self.menu_structure.current_l1()
        l2 = self.menu_structure.current_l2()
        if not l1 or not l2:
            # L2遍历完毕，切下一个L1
            self._advance_to_next_l1()
            return self._make_info_step(step_number, "", [], "当前L1的L2全部测完")

        target_name = f"{l1.name}-{l2.name}"
        logger.info(f"[步骤{step_number}] 点击L2: '{l2.name}'（L1={l1.name}）坐标={l2.coordinates}")

        # 如果是当前已选中的L2，跳过点击，直接检查阻断
        if l2.is_selected:
            logger.info(f"[步骤{step_number}] L2'{l2.name}'已选中，直接检查阻断")
            l2.is_selected = False  # 只跳过一次
        else:
            # 确定性点击L2
            action = AIDecision(
                action=ActionType.CLICK,
                coordinates=l2.coordinates,
                priority=Priority.HIGH,
                reasoning=f"测试L2: {l2.name}",
            )
            action_result = self.action_executor.execute(action)
            if action_result != "success":
                logger.warning(f"[步骤{step_number}] L2'{l2.name}'点击失败: {action_result}")
                if self.config.use_nav_cache:
                    logger.warning(f"缓存坐标可能过期，重新AI发现L2")
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

        logger.info(f"[步骤{step_number}] L1'{l1.name}'无L2，直接检查阻断状态")
        return self._make_info_step(step_number, "", [],
                                    f"L1'{l1.name}'无L2，准备检查阻断")

    def _step_check_block(self, step_number: int) -> ExplorationStep:
        """检查阻断状态：截图→AI判断→记录结果→前进到下一个L2"""
        # 等待页面加载
        time.sleep(self.config.exploration.screenshot_delay)

        screenshot_path, elements, ui_tree_text = self._capture_and_analyze(step_number)
        if not screenshot_path:
            return self._make_error_step(step_number, "", "截图捕获失败")

        logger.info(f"[步骤{step_number}] AI检查阻断: '{self.last_clicked_target}'...")
        result = self.ai_client.check_block_status(screenshot_path, ui_tree_text, self.last_clicked_target)

        # 弹窗检测
        if result.get("has_popup") and result.get("popup_close_button"):
            self.previous_state = self.state  # CHECK_BLOCK 或 CHECK_BLOCK_LOADING
            self.state = EngineState.HANDLE_POPUP
            btn = result["popup_close_button"]
            self._pending_popup_coords = tuple(btn.get("coordinates", (0.5, 0.5)))
            self._pending_popup_text = btn.get("text", "关闭")
            logger.info(f"[步骤{step_number}] 阻断检查时发现弹窗，先关闭")
            return self._make_info_step(step_number, screenshot_path, elements, "发现弹窗，准备关闭")

        is_error = result.get("is_error_screen", False)
        is_loading = result.get("is_loading", False)
        desc = result.get("error_description", "") or result.get("screen_description", "")

        if is_error:
            # ★ 阻断成功
            logger.info(f"[步骤{step_number}] ✓ 阻断成功: '{self.last_clicked_target}' → {desc}")
            self.loading_retry_count = 0
            self._record_block_result(step_number, "block_success", desc, screenshot_path)
            self._update_current_menu_item("block_success", desc, screenshot_path)
            # 前进到下一个L2
            if not self.menu_structure.advance_l2():
                self._advance_to_next_l1()
            else:
                self.state = EngineState.TEST_L2
            return ExplorationStep(
                step_number=step_number,
                timestamp=time.time(),
                screenshot_path=screenshot_path,
                screen_description=f"[阻断成功] {self.last_clicked_target}",
                ui_tree_summary=f"{len(elements)}个元素",
                action_taken=AIDecision(
                    action=ActionType.WAIT, priority=Priority.HIGH,
                    reasoning="阻断成功，继续下一个",
                ),
                action_result="block_success",
                screen_fingerprint="",
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
                    step_number=step_number,
                    timestamp=time.time(),
                    screenshot_path=screenshot_path,
                    screen_description=f"[阻断成功] {self.last_clicked_target}（持续loading）",
                    ui_tree_summary=f"{len(elements)}个元素",
                    action_taken=AIDecision(
                        action=ActionType.WAIT, priority=Priority.HIGH,
                        reasoning="持续loading=阻断成功",
                    ),
                    action_result="block_success",
                    screen_fingerprint="",
                )
            else:
                logger.info(f"[步骤{step_number}] ⏳ 加载中({self.loading_retry_count}/{self.max_loading_retries}): '{self.last_clicked_target}'")
                self.state = EngineState.CHECK_BLOCK_LOADING
                return ExplorationStep(
                    step_number=step_number,
                    timestamp=time.time(),
                    screenshot_path=screenshot_path,
                    screen_description=f"[加载中] {self.last_clicked_target}",
                    ui_tree_summary=f"{len(elements)}个元素",
                    action_taken=AIDecision(
                        action=ActionType.WAIT, priority=Priority.MEDIUM,
                        reasoning="页面加载中，等待重试",
                    ),
                    action_result="loading",
                    screen_fingerprint="",
                )

        else:
            # ★ 阻断失败
            logger.error(f"[步骤{step_number}] ✗ 阻断失败: '{self.last_clicked_target}' → {desc}")
            self._record_block_result(step_number, "block_failure", desc, screenshot_path)
            self._update_current_menu_item("block_failure", desc, screenshot_path)
            self.blocking_failure = True
            return ExplorationStep(
                step_number=step_number,
                timestamp=time.time(),
                screenshot_path=screenshot_path,
                screen_description=f"[阻断失败] {self.last_clicked_target}",
                ui_tree_summary=f"{len(elements)}个元素",
                action_taken=AIDecision(
                    action=ActionType.WAIT, priority=Priority.CRITICAL,
                    reasoning="阻断失败: 页面正常加载了数据",
                ),
                action_result="block_failure",
                screen_fingerprint="",
            )

    def _step_check_l1_block(self, step_number: int) -> ExplorationStep:
        """检查L1页面阻断（无L2的情况），完成后切换到下一个L1"""
        # 等待页面加载
        time.sleep(self.config.exploration.screenshot_delay)

        screenshot_path, elements, ui_tree_text = self._capture_and_analyze(step_number)
        if not screenshot_path:
            return self._make_error_step(step_number, "", "截图捕获失败")

        logger.info(f"[步骤{step_number}] AI检查L1阻断: '{self.last_clicked_target}'...")
        result = self.ai_client.check_block_status(screenshot_path, ui_tree_text, self.last_clicked_target)

        # 弹窗检测
        if result.get("has_popup") and result.get("popup_close_button"):
            self.previous_state = EngineState.CHECK_L1_BLOCK
            self.state = EngineState.HANDLE_POPUP
            btn = result["popup_close_button"]
            self._pending_popup_coords = tuple(btn.get("coordinates", (0.5, 0.5)))
            self._pending_popup_text = btn.get("text", "关闭")
            return self._make_info_step(step_number, screenshot_path, elements, "发现弹窗，准备关闭")

        is_error = result.get("is_error_screen", False)
        is_loading = result.get("is_loading", False)
        desc = result.get("error_description", "") or result.get("screen_description", "")

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
                # 保持在CHECK_L1_BLOCK状态，下一步重试
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

    def _step_handle_popup(self, step_number: int) -> ExplorationStep:
        """处理弹窗：点击关闭按钮→保存到缓存→返回之前的状态"""
        if not self._pending_popup_coords:
            self.state = self.previous_state or EngineState.DISCOVER_L1
            return self._make_info_step(step_number, "", [], "无弹窗坐标")

        logger.info(f"[步骤{step_number}] 关闭弹窗: '{self._pending_popup_text}' 坐标={self._pending_popup_coords}")

        action = AIDecision(
            action=ActionType.CLICK,
            coordinates=self._pending_popup_coords,
            is_popup=True,
            priority=Priority.HIGH,
            reasoning=f"关闭弹窗: {self._pending_popup_text}",
        )
        action_result = self.action_executor.execute(action)

        # 点击成功后缓存弹窗按钮信息
        if action_result == "success" and self.config.use_nav_cache and self.app_package:
            self.nav_cache.save_popup(
                self.app_package, self.config.l_class, self._pending_popup_text,
                self._pending_popup_coords[0], self._pending_popup_coords[1]
            )

        # 清除弹窗信息，回到之前状态
        self._pending_popup_coords = None
        self._pending_popup_text = ""
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

    # ==================== 辅助方法 ====================

    def _dismiss_cached_popups(self, app_package: str):
        """应用启动后，通过UI树验证弹窗是否存在再逐个点击（非盲点）"""
        popups = self.nav_cache.load_popups(app_package, self.config.l_class)
        if not popups:
            return

        logger.info(f"从缓存消除已知弹窗: {len(popups)}个")

        # 尝试用UI树验证弹窗是否真实存在
        elements = self.ui_analyzer.extract_ui_tree()
        if not elements:
            # UI树不可用，回退到按顺序盲点（旧行为）
            logger.info("  UI树不可用，按顺序盲点所有缓存弹窗")
            for p in popups:
                coords = (p["coord_x"], p["coord_y"])
                text = p["button_text"]
                logger.info(f"  点击缓存弹窗按钮: '{text}' 坐标={coords}")
                action = AIDecision(
                    action=ActionType.CLICK,
                    coordinates=coords,
                    is_popup=True,
                    priority=Priority.HIGH,
                    reasoning=f"缓存弹窗: {text}",
                )
                self.action_executor.execute(action)
                time.sleep(self.config.exploration.action_delay)
            logger.info("已知弹窗处理完毕")
            return

        # 智能模式：逐轮检测UI树，确认弹窗存在后再点击
        remaining = list(popups)
        max_rounds = len(popups)

        for round_num in range(max_rounds):
            if not remaining:
                break

            if round_num > 0:
                # 上一轮点击后重新提取UI树
                time.sleep(self.config.exploration.action_delay)
                elements = self.ui_analyzer.extract_ui_tree()
                if not elements:
                    break

            # 收集当前UI树中所有文本和名称
            ui_texts = set()
            for el in elements:
                if el.text:
                    ui_texts.add(el.text.strip())
                if el.name:
                    ui_texts.add(el.name.strip())

            # 在当前UI中找到第一个匹配的弹窗按钮并点击
            found = False
            new_remaining = []
            for p in remaining:
                text = p["button_text"]
                coords = (p["coord_x"], p["coord_y"])

                if not found and text in ui_texts:
                    logger.info(f"  点击缓存弹窗按钮: '{text}' 坐标={coords}")
                    action = AIDecision(
                        action=ActionType.CLICK,
                        coordinates=coords,
                        is_popup=True,
                        priority=Priority.HIGH,
                        reasoning=f"缓存弹窗: {text}",
                    )
                    self.action_executor.execute(action)
                    found = True
                else:
                    new_remaining.append(p)

            remaining = new_remaining
            if not found:
                # 当前UI中没有找到任何缓存弹窗，停止
                break

        if remaining:
            skip_names = [p["button_text"] for p in remaining]
            logger.info(f"  {len(remaining)}个缓存弹窗未在当前UI中发现，跳过: {skip_names}")
        logger.info("已知弹窗处理完毕")

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
            l1 = self.menu_structure.current_l1()
            logger.info(f"准备切换到下一个L1: '{l1.name if l1 else '?'}'")
        else:
            self.state = EngineState.COMPLETE
            logger.info("所有L1菜单已遍历完成")

    def _record_block_result(self, step_number: int, result_type: str, desc: str, screenshot_path: str):
        """记录阻断测试结果"""
        self.issues_found.append({
            "step": step_number,
            "type": result_type,
            "target": self.last_clicked_target,
            "description": f"{'阻断成功' if result_type == 'block_success' else '阻断失败'}: {desc}",
            "screenshot": screenshot_path,
        })

        # 保存到数据库
        if self.config.use_nav_cache and self.app_package:
            # 解析item_name, item_level, parent_name
            target = self.last_clicked_target
            if "-" in target:
                parts = target.split("-", 1)
                parent_name, item_name = parts[0], parts[1]
                item_level = 2
            else:
                item_name = target
                parent_name = ""
                item_level = 1
            self.nav_cache.save_test_result(
                self.app_package, self.config.l_class,
                item_name, item_level, parent_name,
                result_type, desc, screenshot_path
            )

    def _update_current_menu_item(self, status: str, desc: str, screenshot_path: str):
        """更新当前测试中的菜单项状态"""
        l2 = self.menu_structure.current_l2()
        if l2:
            l2.status = status
            l2.block_result = desc
            l2.screenshot_path = screenshot_path

    def _make_info_step(self, step_number, screenshot_path, elements, description):
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

    def _make_error_step(self, step_number, screenshot_path, reason):
        return ExplorationStep(
            step_number=step_number, timestamp=time.time(),
            screenshot_path=screenshot_path, screen_description=reason,
            ui_tree_summary="错误",
            action_taken=AIDecision(action=ActionType.WAIT, priority=Priority.LOW, reasoning=reason),
            action_result="error", screen_fingerprint="",
        )

    def _should_stop(self, step_number: int) -> bool:
        if self.blocking_failure:
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
            app_package=package, platform=self.config.app.platform,
            start_time=self.start_time, end_time=time.time(),
            total_steps=len(self.steps),
            unique_screens=total_l1,
            total_elements_found=total_menu_items,
            elements_interacted=tested_count,
            coverage_percentage=coverage,
            steps=self.steps, screens=self.screen_manager.screens,
            issues_found=self.issues_found, exploration_graph=self.exploration_graph,
        )

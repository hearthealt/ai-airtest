# -*- encoding=utf8 -*-
"""核心探索引擎：结构化L1→L2导航阻断测试状态机。"""

import re
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
from .playbook import Playbook, PlaybookStep, VerifyCondition, PlaybackVerifier

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
        self.ui_analyzer = UIAnalyzer(device_driver, config.exploration)
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
        self.tested_controls: List[str] = []

        # 阻断检查
        self.last_clicked_target = ""
        self.loading_retry_count = 0
        self.max_loading_retries = 2
        self.l1_discover_retry_count = 0
        self.max_l1_discover_retries = 1  # 第一次失败后只再试一次（共两次）
        self.l1_discover_retry_wait_seconds = 30

        # 弹窗处理
        self._pending_popup_coords: Optional[tuple] = None
        self._pending_popup_text: str = ""
        self._pending_popup_type: str = ""  # 弹窗类型: login/permission/ad/other
        self.popup_retry_count = 0
        self.max_popup_retries = 3
        self.non_closable_overlay_retry_count = 0
        self.max_non_closable_overlay_retries = 2
        self._onboarding_step_count = 0  # onboarding引导页操作计数
        self._max_onboarding_steps = 10  # onboarding最多操作次数，防止死循环

        # 登录处理
        self._login_actions_done: list = []  # 已完成的登录操作描述列表
        self._login_retries: int = 0
        self.max_login_retries: int = 3
        self._max_login_steps: int = 15

        # 返回导航（L2跳转新页面后需返回）
        self._back_retry_count: int = 0
        self._max_back_retries: int = 3

        # Playbook 录制/回放
        import os
        playbook_dir = config.playbook_dir or os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "playbooks"
        )
        self.playbook = Playbook(config.package_name, playbook_dir, mode=config.mode)
        self.playback_verifier = PlaybackVerifier(device_driver)

    def run(self, app_package: str = "") -> ExplorationResult:
        self.start_time = time.time()
        self.app_package = app_package

        # 确定运行模式
        replay_mode = self.config.replay_mode
        if replay_mode == "auto":
            replay_mode = "replay" if self.playbook.exists() else "record"

        if replay_mode == "replay":
            logger.info(f"=== 回放模式开始 | 应用={app_package} ===")
            return self._run_replay(app_package)
        else:
            logger.info(f"=== 录制模式开始 | 应用={app_package} ===")
            return self._run_record(app_package)

    def _run_record(self, app_package: str) -> ExplorationResult:
        """录制模式：正常AI驱动探索 + 每步录制到playbook"""
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

                # 录制这一步
                self._record_current_step(step_number, step)

                if step.action_result == "error":
                    self.consecutive_errors += 1
                else:
                    self.consecutive_errors = 0

                if self.state == EngineState.COMPLETE:
                    logger.info("所有L1和L2菜单测试完成")
                    break

                time.sleep(self.config.exploration.action_delay)

            except Exception as e:
                logger.error(f"步骤{step_number}异常: {e}")
                self.consecutive_errors += 1
                time.sleep(self.config.exploration.action_delay)

        # 保存菜单结构到playbook
        self._save_menu_structure_to_playbook()
        self.playbook.save()

        if self.config.mode == 1:
            successes = [i for i in self.issues_found if i["type"] == "function_success"]
            failures = [i for i in self.issues_found if i["type"] == "function_failure"]
            logger.info(f"=== 功能测试结果: {len(successes)}个正常, {len(failures)}个异常 | 已测控件: {self.tested_controls} ===")
        else:
            successes = [i for i in self.issues_found if i["type"] == "block_success"]
            failures = [i for i in self.issues_found if i["type"] == "block_failure"]
            logger.info(f"=== 阻断测试结果: {len(successes)}个阻断成功, {len(failures)}个阻断失败 | 已测控件: {self.tested_controls} ===")
        return self._build_result(app_package)

    def _run_replay(self, app_package: str) -> ExplorationResult:
        """回放模式：加载playbook，逐步执行，check步调AI，其他步直接操作"""
        if not self.playbook.load():
            logger.warning("Playbook加载失败，降级为录制模式")
            return self._run_record(app_package)

        if app_package:
            try:
                self.dd.start_app(app_package)
                time.sleep(3)
            except Exception as e:
                logger.error(f"启动应用失败: {e}")

        # 从playbook恢复菜单结构
        self._load_menu_structure_from_playbook()

        step_number = 0
        for i, pb_step in enumerate(self.playbook.steps):
            # discover_l1 / discover_l2 等信息步骤，回放时静默跳过
            if pb_step.action not in ("check", "close_popup", "click_l1", "click_l2", "back"):
                continue

            step_number += 1
            step_start = time.time()
            logger.info("=" * 60)

            try:
                if pb_step.action == "check":
                    # check步骤必须调AI
                    logger.info(f"[回放-步骤{step_number}] AI检查: {pb_step.description}")
                    step = self._replay_check_step(step_number, pb_step)

                elif pb_step.action == "close_popup":
                    step = self._replay_close_popup(step_number, pb_step)

                elif pb_step.action in ("click_l1", "click_l2"):
                    step = self._replay_click(step_number, pb_step)

                elif pb_step.action == "back":
                    logger.info(f"[回放-步骤{step_number}] 返回: {pb_step.description}")
                    # 返回前截图
                    screenshot_path = self._replay_screenshot(step_number)
                    # 有坐标说明是点击返回按钮，否则用系统返回键
                    if pb_step.coordinates:
                        target_element = None
                        if pb_step.target_text or pb_step.target_name:
                            target_element = UIElement(
                                name=pb_step.target_name or "", text=pb_step.target_text or "",
                                desc="", type="button",
                                control_type=ControlType.BUTTON, bounds={},
                                center=pb_step.coordinates,
                                clickable=True, enabled=True, visible=True,
                            )
                        action = AIDecision(
                            action=ActionType.CLICK,
                            target_element=target_element,
                            coordinates=pb_step.coordinates,
                            priority=Priority.HIGH,
                            reasoning=f"回放点击返回按钮: {pb_step.target_text}",
                        )
                    else:
                        action = AIDecision(
                            action=ActionType.BACK,
                            priority=Priority.HIGH,
                            reasoning="回放系统返回键",
                        )
                    result = self.action_executor.execute(action)
                    step = self._make_info_step(step_number, screenshot_path, [], pb_step.description)
                    step.action_taken = action
                    step.action_result = result

                step.duration_ms = int((time.time() - step_start) * 1000)
                self.steps.append(step)
                self.exploration_logger.log_step(step)

                time.sleep(self.config.exploration.action_delay)

            except Exception as e:
                logger.error(f"回放步骤{step_number}异常: {e}")
                # 降级：从当前位置切换到录制模式
                logger.warning(f"回放异常，从步骤{step_number}开始降级为AI模式")
                self._fallback_to_record(step_number)
                break

        if self.config.mode == 1:
            successes = [i for i in self.issues_found if i["type"] == "function_success"]
            failures = [i for i in self.issues_found if i["type"] == "function_failure"]
            logger.info(f"=== 功能测试结果: {len(successes)}个正常, {len(failures)}个异常 | 已测控件: {self.tested_controls} ===")
        else:
            successes = [i for i in self.issues_found if i["type"] == "block_success"]
            failures = [i for i in self.issues_found if i["type"] == "block_failure"]
            logger.info(f"=== 阻断测试结果: {len(successes)}个阻断成功, {len(failures)}个阻断失败 | 已测控件: {self.tested_controls} ===")
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
            if self.l1_discover_retry_count < self.max_l1_discover_retries:
                self.l1_discover_retry_count += 1
                wait_s = self.l1_discover_retry_wait_seconds
                logger.warning(f"[步骤{step_number}] 首次未发现L1菜单，等待{wait_s}秒后进行第2次识别")
                time.sleep(wait_s)
                self.state = EngineState.DISCOVER_L1
                return self._make_info_step(step_number, screenshot_path, elements, f"未发现L1菜单，等待{wait_s}秒后重试")

            logger.warning(f"[步骤{step_number}] 第2次仍未发现L1菜单，转入口页状态检查")
            self.l1_discover_retry_count = 0
            self.last_clicked_target = "入口页(无L1)"
            if self.last_clicked_target not in self.tested_controls:
                self.tested_controls.append(self.last_clicked_target)
            self.loading_retry_count = 0
            self.state = EngineState.CHECK_L1_BLOCK
            return self._make_info_step(step_number, screenshot_path, elements, "第2次未发现L1菜单，转入口页状态检查")

        self.l1_discover_retry_count = 0

        # 确保从第一个L1开始：如果第一个L1未选中，先点击它
        first_l1 = l1_items[0]
        if not first_l1.is_selected and first_l1.coordinates and first_l1.coordinates != (0, 0):
            logger.info(f"[步骤{step_number}] 当前不在第一个L1'{first_l1.name}'，先点击切换")
            action = AIDecision(
                action=ActionType.CLICK,
                coordinates=first_l1.coordinates,
                priority=Priority.HIGH,
                reasoning=f"切换到第一个L1: {first_l1.name}",
            )
            self.action_executor.execute(action)
            time.sleep(self.config.exploration.action_delay)

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

        # 按配置限制每个L1测试的L2数量（0=不限制）
        max_l2_per_l1 = max(0, int(getattr(self.config.exploration, "max_l2_per_l1", 0)))
        if max_l2_per_l1 > 0 and len(l2_items) > max_l2_per_l1:
            original_count = len(l2_items)
            l2_items = l2_items[:max_l2_per_l1]
            logger.info(
                f"[步骤{step_number}] L1'{l1.name}'按配置仅保留前{max_l2_per_l1}个L2"
                f"（原{original_count}个）"
            )

        # ======== L2去重校验：防止弹窗关闭后回到错误L1导致重复测试 ========
        if l2_items:
            curr_l2_names = {i.name for i in l2_items}
            for prev_l1_name, prev_l2_list in self.menu_structure.l2_map.items():
                if prev_l1_name == l1.name:
                    continue
                prev_l2_names = {i.name for i in prev_l2_list}
                if not prev_l2_names:
                    continue
                if curr_l2_names == prev_l2_names:
                    logger.warning(
                        f"[步骤{step_number}] L1'{l1.name}'的L2与已测L1'{prev_l1_name}'"
                        f"完全相同({curr_l2_names})，判定页面未正确切换，跳过L2直接检查L1"
                    )
                    self.last_clicked_target = l1.name
                    if l1.name not in self.tested_controls:
                        self.tested_controls.append(l1.name)
                    self.loading_retry_count = 0
                    self.state = EngineState.CHECK_L1_BLOCK
                    return self._make_info_step(
                        step_number, screenshot_path, elements,
                        f"L2与已测L1'{prev_l1_name}'重复，跳过L2直接检查L1"
                    )
        # ======== L2去重校验结束 ========

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
            logger.info(f"[步骤{step_number}] L1'{l1.name}'无L2标签")
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
            # ★ 阻断失败（记录但继续测下一个）
            logger.error(f"[步骤{step_number}] ✗ 阻断失败: '{self.last_clicked_target}' → {desc}")
            self._record_block_result(step_number, "block_failure", desc, screenshot_path)
            self._update_current_menu_item("block_failure", desc, screenshot_path)
            if not self.menu_structure.advance_l2():
                self._advance_to_next_l1()
            else:
                self.state = EngineState.TEST_L2
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
            self._advance_to_next_l1()
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
            self.non_closable_overlay_retry_count = 0
            self.state = self.previous_state or EngineState.DISCOVER_L1
            return self._make_info_step(step_number, "", [], "无弹窗坐标")

        # 登录弹窗判断：popup_type=="login" 或按钮文字含"登录"（AI有时未正确设置popup_type）
        _is_login_popup = (
            self._pending_popup_type == "login"
            or (self._pending_popup_text and "登录" in self._pending_popup_text
                and self._pending_popup_text not in ("关闭", "×", "X", "x", "<", "←", "返回"))
        )

        # 登录弹窗：如果配置了需要登录，则转入登录流程
        if _is_login_popup and self.config.login_required:
            logger.info(f"[步骤{step_number}] 检测到登录弹窗(type={self._pending_popup_type}, text='{self._pending_popup_text}')，进入自动登录流程")
            self._login_actions_done = []
            self._login_retries = 0
            self.non_closable_overlay_retry_count = 0
            self.state = EngineState.HANDLE_LOGIN
            # 清除弹窗信息（登录流程会自行处理）
            self._pending_popup_coords = None
            self._pending_popup_text = ""
            self._pending_popup_type = ""
            return self._make_info_step(step_number, "", [], "登录弹窗，转入自动登录")

        # 登录页面不需要登录：直接用坐标点击返回/关闭按钮，跳过弹窗存在性检查
        # （登录页是全屏页面，不会自动消失，Poco也很难匹配到返回按钮）
        if _is_login_popup and not self.config.login_required:
            logger.info(f"[步骤{step_number}] 登录页面(不需要登录)，直接点击返回/关闭: '{self._pending_popup_text}'")
            action = AIDecision(
                action=ActionType.CLICK,
                coordinates=self._pending_popup_coords,
                is_popup=True,
                priority=Priority.HIGH,
                reasoning=f"关闭登录页: {self._pending_popup_text}",
            )
            self.action_executor.execute(action)
            self._pending_popup_coords = None
            self._pending_popup_text = ""
            self._pending_popup_type = ""
            self.non_closable_overlay_retry_count = 0
            self.state = self.previous_state or EngineState.DISCOVER_L1
            self.previous_state = None
            time.sleep(self.config.exploration.action_delay)
            return self._make_info_step(step_number, "", [], "登录页面已关闭，不需要登录")

        # 不可关闭的状态遮罩（如：业务处理中/加载中）不走“关闭弹窗”流程，直接等待并回到原状态
        if self._is_non_closable_overlay(self._pending_popup_text, self._pending_popup_type):
            self.non_closable_overlay_retry_count += 1
            logger.info(
                f"[步骤{step_number}] 检测到不可关闭状态层: '{self._pending_popup_text}'"
                f" ({self.non_closable_overlay_retry_count}/{self.max_non_closable_overlay_retries})"
            )

            # mode=0阻断测试：长期卡在不可关闭状态层，按阻断成功处理并推进流程
            if (self.config.mode == 0
                    and self.non_closable_overlay_retry_count >= self.max_non_closable_overlay_retries):
                return self._handle_stuck_non_closable_overlay_mode0(step_number)

            self._pending_popup_coords = None
            self._pending_popup_text = ""
            self._pending_popup_type = ""
            self.popup_retry_count = 0
            self.state = self.previous_state or EngineState.DISCOVER_L1
            self.previous_state = None
            time.sleep(self.config.exploration.action_delay)
            return self._make_info_step(step_number, "", [], "不可关闭状态层，等待后继续")

        # onboarding轮播引导页：AI返回swipe_left，直接左滑，跳过弹窗存在性检查
        if self._pending_popup_text == "swipe_left":
            logger.info(f"[步骤{step_number}] onboarding轮播引导页，执行左滑翻页")
            action = AIDecision(
                action=ActionType.SWIPE,
                swipe_direction="left",
                is_popup=True,
                priority=Priority.HIGH,
                reasoning="onboarding轮播引导页，左滑翻页",
            )
            action_result = self.action_executor.execute(action)

            popup_type = self._pending_popup_type
            self._pending_popup_coords = None
            self._pending_popup_text = ""
            self._pending_popup_type = ""
            self.non_closable_overlay_retry_count = 0
            self.state = self.previous_state or EngineState.DISCOVER_L1
            self.previous_state = None

            self._onboarding_step_count += 1
            if self._onboarding_step_count >= self._max_onboarding_steps:
                logger.warning(f"[步骤{step_number}] onboarding引导页操作已达{self._max_onboarding_steps}次上限，强制跳过")
                self._onboarding_step_count = 0

            time.sleep(self.config.exploration.action_delay)
            return ExplorationStep(
                step_number=step_number, timestamp=time.time(),
                screenshot_path="", screen_description="引导页左滑翻页",
                ui_tree_summary="", action_taken=action,
                action_result=action_result, screen_fingerprint="",
            )

        # 点击前重新检查弹窗是否还存在（弹窗可能已自动消失）
        popup_text = self._pending_popup_text
        popup_still_exists = False

        poco = getattr(self.dd, 'poco', None)

        # 方式1：用Poco直接查找弹窗按钮文本
        if poco and popup_text:
            try:
                elem = poco(text=popup_text)
                if elem.exists():
                    popup_still_exists = True
                    logger.info(f"  -> 弹窗检查: Poco文本匹配到'{popup_text}'，弹窗仍在")
                else:
                    # 去空格模糊匹配（处理"准 备 好 啦！"这类字间有空格的文本）
                    normalized = popup_text.replace(" ", "").replace("\u3000", "")
                    if normalized and len(normalized) >= 2:
                        pattern = r"\s*".join(re.escape(c) for c in normalized)
                        elem2 = poco(textMatches=pattern)
                        if elem2.exists():
                            popup_still_exists = True
                            logger.info(f"  -> 弹窗检查: Poco文本模糊匹配到'{popup_text}'，弹窗仍在")
                        else:
                            logger.info(f"  -> 弹窗检查: Poco文本未匹配到'{popup_text}'")
                    else:
                        logger.info(f"  -> 弹窗检查: Poco文本未匹配到'{popup_text}'")
            except Exception as e:
                logger.info(f"  -> 弹窗检查: Poco文本查找异常: {e}")

        # 方式2：文本像是图标描述（×、X、关闭、<、←、返回等），用Poco通过name关键词查找按钮
        if not popup_still_exists and poco and popup_text in ('×', 'X', 'x', '关闭', '<', '←', '返回'):
            _close_keywords = ('close', 'dismiss', 'cancel', 'shut', 'exit', 'back', 'return', 'navigate_up', 'nav_back')
            try:
                for kw in _close_keywords:
                    elem = poco(nameMatches=f'.*{kw}.*')
                    if elem.exists():
                        popup_still_exists = True
                        logger.info(f"  -> 弹窗检查: Poco name关键词'{kw}'匹配到关闭按钮，弹窗仍在")
                        break
                if not popup_still_exists:
                    logger.info(f"  -> 弹窗检查: Poco name关键词均未匹配到关闭按钮")
            except Exception as e:
                logger.info(f"  -> 弹窗检查: Poco name查找异常: {e}")

        # 方式3：用UI树文本匹配兜底
        if not popup_still_exists and popup_text:
            current_elements = self.ui_analyzer.extract_ui_tree()
            normalized_popup = popup_text.replace(" ", "").replace("\u3000", "")
            if current_elements:
                # 第一轮：全等匹配（最精确）
                for el in current_elements:
                    el_text = el.text or ""
                    el_name = el.name or ""
                    el_desc = el.desc or ""
                    if popup_text == el_text or popup_text == el_name or popup_text == el_desc:
                        popup_still_exists = True
                        logger.info(f"  -> 弹窗检查: UI树全等匹配到'{popup_text}' (text='{el_text}', name='{el_name}', desc='{el_desc}')")
                        break
                # 第二轮：子串匹配（排除反义：如搜"同意"不应匹配"不同意"）
                if not popup_still_exists:
                    _neg_prefixes = ("不", "没", "未", "非", "别", "勿")
                    for el in current_elements:
                        el_text = el.text or ""
                        el_name = el.name or ""
                        el_desc = el.desc or ""
                        for field in (el_text, el_name, el_desc):
                            if popup_text in field:
                                # 检查popup_text前面是否紧跟否定词
                                idx = field.find(popup_text)
                                if idx > 0 and field[idx - 1] in _neg_prefixes:
                                    continue  # "不同意"包含"同意"但是反义，跳过
                                popup_still_exists = True
                                logger.info(f"  -> 弹窗检查: UI树匹配到'{popup_text}' (text='{el_text}', name='{el_name}', desc='{el_desc}')")
                                break
                        if popup_still_exists:
                            break
                # 第三轮：去空格模糊匹配
                if not popup_still_exists and normalized_popup and len(normalized_popup) >= 2:
                    for el in current_elements:
                        el_text = (el.text or "").replace(" ", "").replace("\u3000", "")
                        el_name = (el.name or "").replace(" ", "").replace("\u3000", "")
                        el_desc = (el.desc or "").replace(" ", "").replace("\u3000", "")
                        if normalized_popup in el_text or normalized_popup in el_name or normalized_popup in el_desc:
                            popup_still_exists = True
                            logger.info(f"  -> 弹窗检查: UI树模糊匹配到'{popup_text}' (text='{el.text}', name='{el.name}', desc='{el.desc}')")
                            break
                if not popup_still_exists:
                    logger.info(f"  -> 弹窗检查: UI树{len(current_elements)}个元素均未匹配到'{popup_text}'")
            else:
                popup_still_exists = True
                logger.info(f"  -> 弹窗检查: UI树提取失败，保守认为弹窗仍在")

        # 没有文本信息，无法判断，保守地认为弹窗还在
        if not popup_text:
            popup_still_exists = True
            logger.info(f"  -> 弹窗检查: 无文本信息，保守认为弹窗仍在")

        if not popup_still_exists:
            logger.info(f"[步骤{step_number}] 弹窗已自动消失，跳过点击: '{popup_text}'")
            self._pending_popup_coords = None
            self._pending_popup_text = ""
            self._pending_popup_type = ""
            self.non_closable_overlay_retry_count = 0
            self.state = self.previous_state or EngineState.DISCOVER_L1
            self.previous_state = None
            return self._make_info_step(step_number, "", current_elements,
                                        f"弹窗'{popup_text}'已自动消失，跳过关闭")

        logger.info(f"[步骤{step_number}] 关闭弹窗: '{popup_text}'")

        # 构建target_element，让ActionExecutor优先用Poco文本匹配点击（比坐标更准）
        target_element = None
        if popup_text:
            target_element = UIElement(
                name="", text=popup_text, desc="", type="button",
                control_type=ControlType.BUTTON, bounds={}, center=self._pending_popup_coords or (),
                clickable=True, enabled=True, visible=True,
            )

        action = AIDecision(
            action=ActionType.CLICK,
            target_element=target_element,
            coordinates=self._pending_popup_coords,
            is_popup=True,
            priority=Priority.HIGH,
            reasoning=f"关闭弹窗: {popup_text}",
        )
        action_result = self.action_executor.execute(action)

        if action_result == "success":
            self.popup_retry_count = 0
            self.non_closable_overlay_retry_count = 0
        else:
            self.popup_retry_count += 1
            logger.warning(
                f"[步骤{step_number}] 关闭弹窗失败({self.popup_retry_count}/{self.max_popup_retries}): '{popup_text}'"
            )
            if self.popup_retry_count >= self.max_popup_retries:
                logger.warning(f"[步骤{step_number}] 弹窗连续关闭失败，按不可关闭状态层处理并继续")
                self.popup_retry_count = 0
                self._pending_popup_coords = None
                self._pending_popup_text = ""
                self._pending_popup_type = ""
                self.non_closable_overlay_retry_count = 0
                self.state = self.previous_state or EngineState.DISCOVER_L1
                self.previous_state = None
                time.sleep(self.config.exploration.action_delay)
                return self._make_info_step(step_number, "", [], "弹窗连续关闭失败，按不可关闭状态层跳过")

        # onboarding弹窗需要多步操作（选性别→选年龄→下一步→...），
        # 点击后不清除弹窗状态，回到之前状态让AI重新识别，直到引导结束
        popup_type = self._pending_popup_type
        self._pending_popup_coords = None
        self._pending_popup_text = ""
        self._pending_popup_type = ""
        self.non_closable_overlay_retry_count = 0
        self.state = self.previous_state or EngineState.DISCOVER_L1
        self.previous_state = None

        # onboarding计数，超限则强制跳过
        if popup_type == "onboarding":
            self._onboarding_step_count += 1
            if self._onboarding_step_count >= self._max_onboarding_steps:
                logger.warning(f"[步骤{step_number}] onboarding引导页操作已达{self._max_onboarding_steps}次上限，强制跳过")
                self._onboarding_step_count = 0
        else:
            self._onboarding_step_count = 0

        time.sleep(self.config.exploration.action_delay)

        desc = f"关闭弹窗"
        if popup_type == "onboarding":
            desc = f"引导页操作: {popup_text}（将重新识别引导状态）"
            logger.info(f"[步骤{step_number}] onboarding引导页，点击'{popup_text}'后将重新检测")

        return ExplorationStep(
            step_number=step_number,
            timestamp=time.time(),
            screenshot_path="",
            screen_description=desc,
            ui_tree_summary="",
            action_taken=action,
            action_result=action_result,
            screen_fingerprint="",
        )

    def _step_handle_login(self, step_number: int) -> ExplorationStep:
        """自动登录：每次截图→AI分析下一步→执行→循环直到完成"""

        # 超出最大步骤限制
        if len(self._login_actions_done) >= self._max_login_steps:
            return self._login_fail(step_number, f"登录步骤超过{self._max_login_steps}步上限")

        screenshot_path, elements, ui_tree_text = self._capture_and_analyze(step_number)
        if not screenshot_path:
            return self._login_fail(step_number, "截图失败")

        # 构建已完成操作的上下文
        actions_done_text = "\n".join(
            f"  {i+1}. {a}" for i, a in enumerate(self._login_actions_done)
        ) if self._login_actions_done else "  (暂无，这是第一步)"

        logger.info(f"[步骤{step_number}] AI分析登录界面(已完成{len(self._login_actions_done)}步)...")
        analysis = self.ai_client.analyze_login_screen(
            screenshot_path, ui_tree_text, self.config.login_method, actions_done_text
        )

        # 不是登录界面 → 登录成功或已离开
        if not analysis.get("is_login_screen"):
            logger.info(f"[步骤{step_number}] 已不在登录界面，登录流程完成")
            self.state = self.previous_state or EngineState.DISCOVER_L1
            self.previous_state = None
            self._login_actions_done = []
            return self._make_info_step(step_number, screenshot_path, elements, "登录成功")

        next_action = analysis.get("next_action")
        if not next_action:
            return self._login_fail(step_number, "AI未返回操作步骤", screenshot_path)

        action_type = next_action.get("action", "click")
        coords = next_action.get("coordinates")
        target = next_action.get("target", "")
        reasoning = next_action.get("reasoning", "")

        # 检测登录操作是否卡在同一步骤（如阻断时"获取验证码"一直失败）
        if action_type == "click" and target and len(self._login_actions_done) >= 2:
            # 提取最近的操作target关键词，去掉坐标部分比对
            recent_targets = []
            for a in self._login_actions_done[-2:]:
                if a.startswith("点击'"):
                    # "点击'获取验证码按钮' → (0.50, 0.39)" → "获取验证码按钮"
                    t = a.split("'")[1] if "'" in a else ""
                    recent_targets.append(t)
            # 连续2次都是点击同一个target，当前又是第3次
            if len(recent_targets) == 2 and recent_targets[0] == recent_targets[1] == target:
                logger.warning(f"[步骤{step_number}] 登录操作卡住：连续3次点击'{target}'无进展，退出登录")
                return self._login_fail(step_number, f"连续点击'{target}'无进展（可能网络阻断导致无法获取验证码）", screenshot_path)

        # done = AI认为登录操作已完成（如已点击登录按钮），等待验证
        if action_type == "done":
            self._login_retries += 1
            if self._login_retries >= self.max_login_retries:
                return self._login_fail(step_number, "多次验证后仍在登录界面", screenshot_path)
            logger.info(f"[步骤{step_number}] AI认为登录完成，等待验证...")
            self._login_actions_done.append(f"等待登录验证({self._login_retries}/{self.max_login_retries})")
            time.sleep(self.config.exploration.action_delay * 2)
            return self._make_info_step(step_number, screenshot_path, elements,
                                        f"登录操作完成，等待验证({self._login_retries}/{self.max_login_retries})")

        # 执行操作
        if action_type == "input_text":
            # 根据target描述判断填什么
            if "手机" in target or "账号" in target or "phone" in target.lower():
                input_text = self.config.login_phone
                desc = "输入手机号"
            elif "邮箱" in target or "email" in target.lower() or "mail" in target.lower():
                input_text = self.config.login_email
                desc = "输入邮箱"
            elif "密码" in target or "password" in target.lower():
                input_text = self.config.login_password
                desc = "输入密码"
            elif "验证码" in target:
                input_text = ""
                desc = "输入验证码"
            else:
                input_text = ""
                desc = f"输入: {target}"

            if not input_text:
                self._login_actions_done.append(f"跳过{desc}(无内容)")
                return self._make_info_step(step_number, screenshot_path, elements, f"跳过{desc}")

            refined_coords = self._login_find_element(target, coords)
            logger.info(f"[步骤{step_number}] 登录: {desc}")
            action = AIDecision(
                action=ActionType.TEXT_INPUT,
                coordinates=refined_coords,
                text_input=input_text,
                priority=Priority.HIGH,
                reasoning=f"登录{desc}",
            )
            self.action_executor.execute(action)
            self._login_actions_done.append(f"{desc} → ({refined_coords[0]:.2f}, {refined_coords[1]:.2f})")

        elif action_type == "click":
            refined_coords = self._login_find_element(target, coords)
            logger.info(f"[步骤{step_number}] 登录: 点击'{target}'")
            target_element = UIElement(
                name="", text=target, desc="", type="button",
                control_type=ControlType.BUTTON, bounds={}, center=refined_coords,
                clickable=True, enabled=True, visible=True,
            ) if target else None
            action = AIDecision(
                action=ActionType.CLICK,
                target_element=target_element,
                coordinates=refined_coords,
                priority=Priority.HIGH,
                reasoning=f"登录点击: {target}",
            )
            self.action_executor.execute(action)
            self._login_actions_done.append(f"点击'{target}' → ({refined_coords[0]:.2f}, {refined_coords[1]:.2f})")

        else:
            logger.warning(f"[步骤{step_number}] 未知登录操作类型: {action_type}")
            self._login_actions_done.append(f"未知操作: {action_type}")

        time.sleep(self.config.exploration.action_delay)

        return ExplorationStep(
            step_number=step_number, timestamp=time.time(),
            screenshot_path=screenshot_path,
            screen_description=f"登录: {target}",
            ui_tree_summary="",
            action_taken=AIDecision(
                action=ActionType.CLICK if action_type == "click" else ActionType.TEXT_INPUT,
                coordinates=tuple(coords) if coords else None,
                priority=Priority.HIGH, reasoning=reasoning,
            ),
            action_result="success", screen_fingerprint="",
        )

    def _login_find_element(self, target: str, ai_coords) -> tuple:
        """登录时用Poco精确查找元素坐标，找不到就用AI坐标兜底。"""
        poco = getattr(self.dd, 'poco', None)
        fallback = tuple(ai_coords) if ai_coords else (0.5, 0.5)
        if not poco or not target:
            return fallback

        # 从target描述提取关键词用于匹配
        # target示例: "密码登录Tab", "手机号输入框", "登录按钮", "同意协议复选框"
        search_keywords = []
        use_desc_first = False  # 优先用desc搜索（适用于复选框等无text的元素）

        if "密码登录" in target:
            search_keywords = ["密码登录", "密码"]
        elif "验证码登录" in target:
            search_keywords = ["验证码登录", "验证码"]
        elif "手机号登录" in target:
            search_keywords = ["手机号登录", "手机", "一键登录"]
        elif "手机" in target or "账号" in target:
            search_keywords = ["手机号", "账号", "请输入手机号", "请输入账号", "phone"]
        elif "密码" in target and "输入" in target:
            search_keywords = ["请输入密码", "密码", "password"]
        elif "登录" in target and "按钮" in target:
            search_keywords = ["登录", "登 录", "login", "Login"]
        elif "同意" in target or "协议" in target or "复选" in target or "勾选" in target or "隐私" in target:
            search_keywords = ["同意", "已阅读", "服务协议", "隐私政策", "隐私"]
            use_desc_first = True  # 复选框通常text为空，desc有内容如"未选中，同意"
        elif "跳过" in target:
            search_keywords = ["跳过", "skip"]
        else:
            search_keywords = [target]

        for keyword in search_keywords:
            # 对复选框类元素，优先用desc匹配可点击的元素
            if use_desc_first:
                try:
                    elem = poco(descMatches=f".*{keyword}.*", touchable=True)
                    if elem.exists():
                        pos = elem.get_position()
                        size = elem.get_size()
                        # 复选框点击左侧勾选区域，避免命中文字中的超链接（如"服务协议"/"隐私政策"）
                        left_x = max(0.02, pos[0] - size[0] * 0.5 + 0.02)
                        logger.info(f"  -> 登录Poco desc匹配(复选框): '{keyword}' → ({left_x:.3f}, {pos[1]:.3f})")
                        return (left_x, pos[1])
                except Exception:
                    pass

            try:
                # 精确文本匹配
                elem = poco(text=keyword)
                if elem.exists():
                    pos = elem.get_position()
                    if use_desc_first:
                        size = elem.get_size()
                        left_x = max(0.02, pos[0] - size[0] * 0.5 + 0.02)
                        logger.info(f"  -> 登录Poco匹配(复选框): text='{keyword}' → ({left_x:.3f}, {pos[1]:.3f})")
                        return (left_x, pos[1])
                    logger.info(f"  -> 登录Poco匹配: text='{keyword}' → ({pos[0]:.3f}, {pos[1]:.3f})")
                    return (pos[0], pos[1])
            except Exception:
                pass
            try:
                # textMatches模糊匹配
                elem = poco(textMatches=f".*{keyword}.*")
                if elem.exists():
                    pos = elem.get_position()
                    if use_desc_first:
                        size = elem.get_size()
                        left_x = max(0.02, pos[0] - size[0] * 0.5 + 0.02)
                        logger.info(f"  -> 登录Poco模糊匹配(复选框): '{keyword}' → ({left_x:.3f}, {pos[1]:.3f})")
                        return (left_x, pos[1])
                    logger.info(f"  -> 登录Poco模糊匹配: '{keyword}' → ({pos[0]:.3f}, {pos[1]:.3f})")
                    return (pos[0], pos[1])
            except Exception:
                pass

        # 复选框专用兜底：直接用控件类型CheckBox查找
        if use_desc_first:
            try:
                elem = poco(type="android.widget.CheckBox")
                if elem.exists():
                    pos = elem.get_position()
                    size = elem.get_size()
                    left_x = max(0.02, pos[0] - size[0] * 0.5 + 0.02)
                    logger.info(f"  -> 登录Poco CheckBox类型匹配 → ({left_x:.3f}, {pos[1]:.3f})")
                    return (left_x, pos[1])
            except Exception:
                pass

        logger.info(f"  -> 登录Poco未匹配到'{target}'，使用AI坐标{fallback}")
        return fallback

    def _login_fail(self, step_number: int, reason: str, screenshot_path: str = "") -> ExplorationStep:
        """登录失败：阻断模式下视为阻断成功并结束，功能模式下回退继续"""
        logger.warning(f"[步骤{step_number}] 自动登录失败: {reason}")
        self._login_actions_done = []

        # 阻断模式(mode=0)：登录失败=网络阻断生效，无法登录=阻断成功
        if self.config.mode == 0:
            self.last_clicked_target = "登录页面"
            if "登录页面" not in self.tested_controls:
                self.tested_controls.append("登录页面")
            logger.info(f"[步骤{step_number}] ✓ 阻断成功: 登录失败（{reason}）→ 阻断生效，结束探索")
            self._record_block_result(step_number, "block_success", f"登录失败: {reason}", screenshot_path)
            self.state = EngineState.COMPLETE
            self.previous_state = None
            return self._make_info_step(step_number, screenshot_path, [],
                                        f"[阻断成功] 登录失败: {reason}")

        # 功能模式(mode=1)：回退到之前的状态继续
        self.state = self.previous_state or EngineState.DISCOVER_L1
        self.previous_state = None
        return self._make_info_step(step_number, screenshot_path, [], f"登录失败: {reason}")

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
            self._login_actions_done = []
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
            self._login_actions_done = []
            return self._make_info_step(step_number, screenshot_path, elements, "关闭登录弹窗")

        return self._login_fail(step_number, "无可执行的登录步骤", screenshot_path)

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
        """检查是否还在L1的L2标签页面，跳转了新页面则返回。

        判断逻辑：用L1底部导航 + L2顶部标签联合比对。
        - 收集页面上所有文本，同时匹配当前L1名称和L2标签名称
        - L1可见 且 至少2个L2标签可见 → 还在原页面
        - 否则 → 跳转了新页面，需要返回
        """
        if self._back_retry_count >= self._max_back_retries:
            self._back_retry_count = 0
            return None

        elements = self.ui_analyzer.extract_ui_tree()

        # 收集页面上所有文本
        page_texts = set()
        for elem in elements:
            t = (elem.text or "").strip()
            if t:
                page_texts.add(t)

        # 检查当前L1是否可见
        l1 = self.menu_structure.current_l1()
        l1_visible = False
        if l1:
            l1_visible = l1.name in page_texts or (l1.element_text and l1.element_text in page_texts)

        # 检查有多少个L2标签可见
        l2_list = self.menu_structure.current_l2_list()
        l2_visible_count = 0
        for l2 in l2_list:
            if l2.name in page_texts or (l2.element_text and l2.element_text in page_texts):
                l2_visible_count += 1

        # L1可见 且 至少2个L2标签可见 → 还在原页面
        if l1_visible and l2_visible_count >= 2:
            self._back_retry_count = 0
            return None

        # 只有1个或0个L2但L1可见，且L2总数本身就<=1 → 也算在原页面
        if l1_visible and len(l2_list) <= 1:
            self._back_retry_count = 0
            return None

        # L1可见且至少1个L2可见（但不足2个）→ 也视为还在原页面，避免误判跳转
        if l1_visible and l2_visible_count >= 1:
            self._back_retry_count = 0
            return None

        # L1可见但L2一个都识别不到：最多只尝试一次返回，避免反复BACK死循环
        if l1_visible and len(l2_list) > 1 and l2_visible_count == 0 and self._back_retry_count >= 1:
            logger.info(
                f"[步骤{step_number}] L1可见但L2未识别到（0/{len(l2_list)}），已尝试返回仍无变化，按未跳转处理并继续"
            )
            self._back_retry_count = 0
            return None

        # 判定为跳转了新页面
        logger.info(f"[步骤{step_number}] 检测到页面跳转（L1可见={l1_visible}, L2可见={l2_visible_count}/{len(l2_list)}），返回L1页面")
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
        """通用：等待页面加载 + 截图 + 提取UI树。返回 (screenshot_path, elements, ui_tree_text)"""
        time.sleep(self.config.exploration.action_delay)
        screenshot_path = self.ui_analyzer.capture_screenshot(f"step{step_number}")
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
    def _is_non_closable_overlay(popup_text: str, popup_type: str) -> bool:
        """判断是否为不可关闭的状态遮罩（业务处理中/加载中/请稍候等）。"""
        if popup_type == "busy":
            return True
        text = (popup_text or "").strip()
        if not text:
            return False
        keywords = (
            "业务处理中", "处理中", "加载中", "正在加载", "请稍候", "提交中",
            "请等待", "加载", "处理中...", "loading",
        )
        return any(k in text for k in keywords)

    def _handle_stuck_non_closable_overlay_mode0(self, step_number: int) -> ExplorationStep:
        """mode=0：不可关闭状态层持续出现时，按阻断成功处理并推进流程。"""
        reason = "长时间业务处理中/加载中（不可关闭状态层）"
        prev = self.previous_state
        target = self.last_clicked_target or "入口页(无L1)"
        self.last_clicked_target = target
        if target not in self.tested_controls:
            self.tested_controls.append(target)

        self._pending_popup_coords = None
        self._pending_popup_text = ""
        self._pending_popup_type = ""
        self.popup_retry_count = 0
        self.non_closable_overlay_retry_count = 0

        # L2检查上下文：按L2阻断成功处理
        if prev in (EngineState.CHECK_BLOCK, EngineState.CHECK_BLOCK_LOADING):
            logger.info(f"[步骤{step_number}] ✓ 阻断成功: '{target}' → {reason}")
            self.loading_retry_count = 0
            self._record_block_result(step_number, "block_success", reason, "")
            self._update_current_menu_item("block_success", reason, "")
            if not self.menu_structure.advance_l2():
                self._advance_to_next_l1()
            else:
                self.state = EngineState.TEST_L2
            self.previous_state = None
            return ExplorationStep(
                step_number=step_number, timestamp=time.time(),
                screenshot_path="",
                screen_description=f"[阻断成功] {target}（持续处理中）",
                ui_tree_summary="",
                action_taken=AIDecision(action=ActionType.WAIT, priority=Priority.HIGH, reasoning="持续不可关闭状态层=阻断成功"),
                action_result="block_success", screen_fingerprint="",
            )

        # 其他上下文（L1直测/入口页）：按L1阻断成功处理
        logger.info(f"[步骤{step_number}] ✓ L1阻断成功: '{target}' → {reason}")
        self._record_block_result(step_number, "block_success", reason, "")
        l1 = self.menu_structure.current_l1()
        if l1:
            l1.status = "block_success"
            l1.block_result = reason
            l1.screenshot_path = ""
            self._advance_to_next_l1()
        else:
            self.state = EngineState.COMPLETE
        self.previous_state = None
        return ExplorationStep(
            step_number=step_number, timestamp=time.time(),
            screenshot_path="",
            screen_description=f"[L1阻断成功] {target}（持续处理中）",
            ui_tree_summary="",
            action_taken=AIDecision(action=ActionType.WAIT, priority=Priority.HIGH, reasoning="持续不可关闭状态层=阻断成功"),
            action_result="block_success", screen_fingerprint="",
        )

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
        # 有L2的L1不单独计入，只算其L2；无L2的L1才算独立菜单项
        l1_with_l2 = sum(1 for v in self.menu_structure.l2_map.values() if len(v) > 0)
        total_menu_items = (total_l1 - l1_with_l2) + total_l2
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

    # ==================== Playbook 录制辅助 ====================

    def _record_current_step(self, step_number: int, step: ExplorationStep):
        """根据当前状态和步骤信息，录制一条PlaybookStep"""
        action_taken = step.action_taken
        desc = step.screen_description

        # 根据步骤描述和状态判断action类型
        if "关闭弹窗" in desc or (action_taken and action_taken.is_popup):
            pb_action = "close_popup"
            target_text = ""
            target_name = ""
            coords = ()
            if action_taken and action_taken.target_element:
                target_text = action_taken.target_element.text or ""
                target_name = action_taken.target_element.name or ""
                coords = action_taken.coordinates or action_taken.target_element.center or ()
            elif action_taken:
                coords = action_taken.coordinates or ()
            # verify: 文本或name
            verify = VerifyCondition()
            if target_text and target_text not in ('×', 'X', 'x', '✕', '✖'):
                verify.has_text = target_text
            if target_name:
                verify.has_name = target_name

        elif "弹窗已自动消失" in desc or "弹窗'" in desc:
            # 弹窗自动消失，跳过步骤
            pb_action = "skip_popup"
            target_text = ""
            target_name = ""
            coords = ()
            verify = VerifyCondition()

        elif "点击L2" in desc:
            pb_action = "click_l2"
            l2 = self.menu_structure.current_l2()
            l1 = self.menu_structure.current_l1()
            target_text = l2.element_text if l2 else ""
            target_name = l2.element_name if l2 else ""
            coords = l2.coordinates if l2 else ()
            l1_name = l1.name if l1 else ""
            # verify: L1 + L2名同时存在
            verify = VerifyCondition()
            verify_texts = []
            if l1:
                verify_texts.append(l1.name)
            if l2:
                verify_texts.append(l2.name)
            if len(verify_texts) >= 2:
                verify.has_all_text = verify_texts
            self.playbook.record_step(PlaybookStep(
                step=step_number, action=pb_action,
                target_text=target_text, target_name=target_name,
                coordinates=coords, l1_name=l1_name,
                description=desc, verify=verify,
            ))
            return

        elif "返回" in desc:
            pb_action = "back"
            target_text = ""
            target_name = ""
            coords = ()
            # 从action_taken提取返回按钮信息（点击返回按钮 vs 系统返回键）
            if action_taken and action_taken.action == ActionType.CLICK:
                if action_taken.target_element:
                    target_text = action_taken.target_element.text or ""
                    target_name = action_taken.target_element.name or ""
                    coords = action_taken.coordinates or action_taken.target_element.center or ()
                else:
                    coords = action_taken.coordinates or ()
            self.playbook.record_step(PlaybookStep(
                step=step_number, action=pb_action, description=desc,
                target_text=target_text, target_name=target_name,
                coordinates=coords,
            ))
            return

        elif "切换L1" in desc or "切换到L1" in desc:
            pb_action = "click_l1"
            l1 = self.menu_structure.current_l1()
            target_text = l1.element_text if l1 else ""
            target_name = l1.element_name if l1 else ""
            coords = l1.coordinates if l1 else ()
            # verify: 任意一个L1名称存在
            verify = VerifyCondition()
            l1_names = [item.name for item in self.menu_structure.l1_items]
            if l1_names:
                verify.has_any_text = l1_names
            self.playbook.record_step(PlaybookStep(
                step=step_number, action=pb_action,
                target_text=target_text, target_name=target_name,
                coordinates=coords, description=desc, verify=verify,
            ))
            return

        elif step.action_result in (
            "block_success", "block_failure", "function_success", "function_failure"
        ):
            pb_action = "check"
            self.playbook.record_step(PlaybookStep(
                step=step_number, action=pb_action,
                target_text=self.last_clicked_target,
                description=desc,
                expected_result=step.action_result,
            ))
            return

        elif "L1" in desc and ("底部导航" in desc or "菜单" in desc):
            pb_action = "discover_l1"
            self.playbook.record_step(PlaybookStep(
                step=step_number, action=pb_action, description=desc,
            ))
            return

        elif "L2" in desc and ("标签" in desc or "Tab" in desc):
            pb_action = "discover_l2"
            self.playbook.record_step(PlaybookStep(
                step=step_number, action=pb_action, description=desc,
            ))
            return

        else:
            # 其他信息性步骤
            pb_action = "info"
            self.playbook.record_step(PlaybookStep(
                step=step_number, action=pb_action, description=desc,
            ))
            return

        # close_popup / skip_popup
        self.playbook.record_step(PlaybookStep(
            step=step_number, action=pb_action,
            target_text=target_text, target_name=target_name,
            coordinates=coords, description=desc, verify=verify,
        ))

    def _save_menu_structure_to_playbook(self):
        """把菜单结构保存到playbook"""
        l1_data = []
        for item in self.menu_structure.l1_items:
            l1_data.append({
                "name": item.name,
                "element_text": item.element_text,
                "element_name": item.element_name,
                "coordinates": list(item.coordinates) if item.coordinates else [],
            })

        l2_data = {}
        for l1_name, l2_list in self.menu_structure.l2_map.items():
            l2_data[l1_name] = []
            for item in l2_list:
                l2_data[l1_name].append({
                    "name": item.name,
                    "element_text": item.element_text,
                    "element_name": item.element_name,
                    "coordinates": list(item.coordinates) if item.coordinates else [],
                    "is_selected": item.is_selected,
                })

        self.playbook.menu_structure = {
            "l1_items": l1_data,
            "l2_map": l2_data,
        }

    def _load_menu_structure_from_playbook(self):
        """从playbook恢复菜单结构"""
        ms = self.playbook.menu_structure
        if not ms:
            return

        self.menu_structure = MenuStructure()
        for item in ms.get("l1_items", []):
            coords = item.get("coordinates", [])
            self.menu_structure.l1_items.append(MenuItemInfo(
                name=item.get("name", ""),
                element_text=item.get("element_text", ""),
                element_name=item.get("element_name", ""),
                coordinates=tuple(coords) if coords else (),
                level=1,
            ))

        for l1_name, l2_list in ms.get("l2_map", {}).items():
            self.menu_structure.l2_map[l1_name] = []
            for item in l2_list:
                coords = item.get("coordinates", [])
                self.menu_structure.l2_map[l1_name].append(MenuItemInfo(
                    name=item.get("name", ""),
                    element_text=item.get("element_text", ""),
                    element_name=item.get("element_name", ""),
                    coordinates=tuple(coords) if coords else (),
                    level=2,
                    is_selected=item.get("is_selected", False),
                ))

    # ==================== Playbook 回放辅助 ====================

    def _replay_screenshot(self, step_number: int) -> str:
        """回放时轻量截图（不等action_delay，不提取UI树）"""
        try:
            return self.ui_analyzer.capture_screenshot(f"step{step_number}") or ""
        except Exception:
            return ""

    def _replay_close_popup(self, step_number: int, pb_step: PlaybookStep) -> ExplorationStep:
        """回放关闭弹窗：多种方式验证弹窗是否存在"""
        popup_exists = False
        poco = getattr(self.dd, 'poco', None)

        # 方式1：verify条件验证
        if self.playback_verifier.verify(pb_step.verify):
            popup_exists = True
            logger.info(f"  -> 回放弹窗检查: verify条件通过")

        # 方式2：用target_text直接搜索
        if not popup_exists and poco and pb_step.target_text:
            try:
                if poco(text=pb_step.target_text).exists():
                    popup_exists = True
                    logger.info(f"  -> 回放弹窗检查: Poco文本匹配到'{pb_step.target_text}'")
            except Exception:
                pass

        # 方式3：用target_name搜索
        if not popup_exists and poco and pb_step.target_name:
            try:
                if poco(nameMatches=f".*{pb_step.target_name}.*").exists():
                    popup_exists = True
                    logger.info(f"  -> 回放弹窗检查: Poco name匹配到'{pb_step.target_name}'")
            except Exception:
                pass

        # 方式4：常见关闭按钮name关键词兜底
        if not popup_exists and poco and pb_step.target_text in ('关闭', '×', 'X', 'x', '✕'):
            for kw in ('close', 'dismiss', 'cancel'):
                try:
                    if poco(nameMatches=f'.*{kw}.*').exists():
                        popup_exists = True
                        logger.info(f"  -> 回放弹窗检查: Poco name关键词'{kw}'匹配到关闭按钮")
                        break
                except Exception:
                    pass

        if not popup_exists:
            logger.info(f"[回放-步骤{step_number}] 跳过: {pb_step.description}（弹窗未出现）")
            time.sleep(self.config.exploration.action_delay)
            return self._make_info_step(step_number, "", [], f"跳过: {pb_step.description}")

        logger.info(f"[回放-步骤{step_number}] 关闭弹窗: {pb_step.description}")
        # 点击前截图
        screenshot_path = self._replay_screenshot(step_number)
        # 构建点击动作
        target_element = None
        if pb_step.target_text:
            target_element = UIElement(
                name=pb_step.target_name or "", text=pb_step.target_text, desc="", type="button",
                control_type=ControlType.BUTTON, bounds={}, center=pb_step.coordinates or (),
                clickable=True, enabled=True, visible=True,
            )
        action = AIDecision(
            action=ActionType.CLICK,
            target_element=target_element,
            coordinates=pb_step.coordinates,
            is_popup=True,
            priority=Priority.HIGH,
            reasoning=f"回放关闭弹窗: {pb_step.target_text}",
        )
        result = self.action_executor.execute(action)
        step = self._make_info_step(step_number, screenshot_path, [], f"关闭弹窗: {pb_step.target_text}")
        step.action_taken = action
        step.action_result = result
        return step

    def _replay_click(self, step_number: int, pb_step: PlaybookStep) -> ExplorationStep:
        """回放点击L1/L2：先验证页面状态"""
        verified = self.playback_verifier.verify(pb_step.verify)
        if not verified:
            logger.warning(f"[回放-步骤{step_number}] 验证失败: {pb_step.description}，降级AI")
            return self._replay_fallback_ai(step_number, pb_step)

        logger.info(f"[回放-步骤{step_number}] 点击: {pb_step.description}")
        # 点击前截图
        screenshot_path = self._replay_screenshot(step_number)
        target_element = UIElement(
            name=pb_step.target_name or "", text=pb_step.target_text, desc="", type="tab",
            control_type=ControlType.TAB, bounds={}, center=pb_step.coordinates or (),
            clickable=True, enabled=True, visible=True,
        )
        action = AIDecision(
            action=ActionType.CLICK,
            target_element=target_element,
            coordinates=pb_step.coordinates,
            priority=Priority.HIGH,
            reasoning=f"回放点击: {pb_step.target_text}",
        )
        result = self.action_executor.execute(action)

        # 更新tested_controls
        if pb_step.action == "click_l2" and pb_step.l1_name:
            target_name = f"{pb_step.l1_name}-{pb_step.target_text}"
        else:
            target_name = pb_step.target_text or ""
        # target_text为空时从description提取名称兜底
        if not target_name and pb_step.description:
            if ": " in pb_step.description:
                target_name = pb_step.description.split(": ", 1)[1]
            elif "：" in pb_step.description:
                target_name = pb_step.description.split("：", 1)[1]
        self.last_clicked_target = target_name
        if target_name and target_name not in self.tested_controls:
            self.tested_controls.append(target_name)

        step = self._make_info_step(step_number, screenshot_path, [], pb_step.description)
        step.action_taken = action
        step.action_result = result
        return step

    def _replay_check_step(self, step_number: int, pb_step: PlaybookStep) -> ExplorationStep:
        """回放check步骤：必须调AI"""
        self.last_clicked_target = pb_step.target_text or self.last_clicked_target
        # 复用现有的check逻辑
        screenshot_path, elements, ui_tree_text = self._capture_and_analyze(step_number)
        if not screenshot_path:
            return self._make_error_step(step_number, "", "截图捕获失败")

        mode = self.config.mode
        mode_label = "功能检查" if mode == 1 else "阻断检查"
        logger.info(f"[回放-步骤{step_number}] AI{mode_label}: '{self.last_clicked_target}'...")
        result = self.ai_client.check_block_status(screenshot_path, ui_tree_text, self.last_clicked_target, mode=mode)

        # 弹窗检测
        if result.get("has_popup") and result.get("popup_close_button"):
            btn = result["popup_close_button"]
            raw_coords = self._normalize_coords(tuple(btn.get("coordinates", (0.5, 0.5))))
            refined_coords = self._refine_popup_coords(raw_coords, elements)
            popup_text = btn.get("text", "关闭")
            # 直接关闭弹窗再重新检查
            logger.info(f"[回放-步骤{step_number}] 检测到弹窗，关闭: '{popup_text}'")
            popup_target = UIElement(
                name="", text=popup_text, desc="", type="button",
                control_type=ControlType.BUTTON, bounds={}, center=refined_coords,
                clickable=True, enabled=True, visible=True,
            )
            self.action_executor.execute(AIDecision(
                action=ActionType.CLICK, target_element=popup_target,
                coordinates=refined_coords, is_popup=True,
                priority=Priority.HIGH, reasoning=f"回放关闭弹窗: {popup_text}",
            ))
            time.sleep(self.config.exploration.action_delay)
            # 重新截图检查
            screenshot_path, elements, ui_tree_text = self._capture_and_analyze(step_number)
            if not screenshot_path:
                return self._make_error_step(step_number, "", "截图捕获失败")
            result = self.ai_client.check_block_status(screenshot_path, ui_tree_text, self.last_clicked_target, mode=mode)

        is_error = result.get("is_error_screen", False)
        is_loading = result.get("is_loading", False)
        desc = result.get("error_description", "") or result.get("screen_description", "")

        if mode == 1:
            return self._check_block_mode1(step_number, screenshot_path, elements, is_error, is_loading, desc)
        else:
            return self._check_block_mode0(step_number, screenshot_path, elements, is_error, is_loading, desc)

    def _replay_fallback_ai(self, step_number: int, pb_step: PlaybookStep) -> ExplorationStep:
        """回放验证失败时，降级调AI分析当前页面"""
        screenshot_path, elements, ui_tree_text = self._capture_and_analyze(step_number)
        if not screenshot_path:
            return self._make_error_step(step_number, "", "降级截图失败")

        # 根据目标类型调用不同的AI
        if pb_step.action == "click_l1":
            logger.info(f"[回放降级] AI重新识别L1菜单")
            result = self.ai_client.discover_l1_menus(screenshot_path, ui_tree_text)
            # 尝试在AI结果中找到目标L1
            for item in result.get("l1_items", []):
                if item.get("name") == pb_step.target_text:
                    coords = tuple(item.get("coordinates", []))
                    target = UIElement(
                        name="", text=pb_step.target_text, desc="", type="tab",
                        control_type=ControlType.TAB, bounds={}, center=coords,
                        clickable=True, enabled=True, visible=True,
                    )
                    action = AIDecision(
                        action=ActionType.CLICK, target_element=target,
                        coordinates=coords, priority=Priority.HIGH,
                        reasoning=f"降级AI点击L1: {pb_step.target_text}",
                    )
                    action_result = self.action_executor.execute(action)
                    self.last_clicked_target = pb_step.target_text
                    if pb_step.target_text and pb_step.target_text not in self.tested_controls:
                        self.tested_controls.append(pb_step.target_text)
                    step = self._make_info_step(step_number, screenshot_path, elements,
                                                f"降级AI点击L1: {pb_step.target_text}")
                    step.action_taken = action
                    step.action_result = action_result
                    return step

        elif pb_step.action == "click_l2":
            l1_name = pb_step.l1_name or ""
            logger.info(f"[回放降级] AI重新识别L2标签 (L1={l1_name})")
            result = self.ai_client.discover_l2_tabs(screenshot_path, ui_tree_text, l1_name)
            for item in result.get("l2_items", []):
                if item.get("name") == pb_step.target_text:
                    coords = tuple(item.get("coordinates", []))
                    target = UIElement(
                        name="", text=pb_step.target_text, desc="", type="tab",
                        control_type=ControlType.TAB, bounds={}, center=coords,
                        clickable=True, enabled=True, visible=True,
                    )
                    action = AIDecision(
                        action=ActionType.CLICK, target_element=target,
                        coordinates=coords, priority=Priority.HIGH,
                        reasoning=f"降级AI点击L2: {pb_step.target_text}",
                    )
                    action_result = self.action_executor.execute(action)
                    target_name = f"{l1_name}-{pb_step.target_text}" if l1_name else pb_step.target_text
                    self.last_clicked_target = target_name
                    if target_name not in self.tested_controls:
                        self.tested_controls.append(target_name)
                    step = self._make_info_step(step_number, screenshot_path, elements,
                                                f"降级AI点击L2: {pb_step.target_text}")
                    step.action_taken = action
                    step.action_result = action_result
                    return step

        # 降级也找不到 → 返回错误
        logger.error(f"[回放降级] AI也找不到目标: {pb_step.target_text}")
        return self._make_error_step(step_number, screenshot_path, f"回放降级失败: {pb_step.description}")

    def _fallback_to_record(self, from_step: int):
        """回放异常时，从当前位置切换到录制模式继续"""
        logger.info(f"从步骤{from_step}开始切换到AI录制模式")
        self.state = EngineState.DISCOVER_L1

        step_number = from_step
        while not self._should_stop(step_number):
            step_number += 1
            step_start = time.time()
            try:
                step = self._execute_state_step(step_number)
                step.duration_ms = int((time.time() - step_start) * 1000)
                self.steps.append(step)
                self.exploration_logger.log_step(step)
                self._record_current_step(step_number, step)

                if step.action_result == "error":
                    self.consecutive_errors += 1
                else:
                    self.consecutive_errors = 0

                if self.state == EngineState.COMPLETE:
                    break

                time.sleep(self.config.exploration.action_delay)
            except Exception as e:
                logger.error(f"步骤{step_number}异常: {e}")
                self.consecutive_errors += 1
                time.sleep(self.config.exploration.action_delay)

        self._save_menu_structure_to_playbook()
        self.playbook.save()

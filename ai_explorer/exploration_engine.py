# -*- encoding=utf8 -*-
"""AI驱动的统一循环探索引擎。三阶段架构：DISCOVER → TEST → COMPLETE。"""

import os
import time
import logging

from .ai_client import AIClient
from .ui_analyzer import UIAnalyzer
from .action_executor import ActionExecutor
from .screen_state import ScreenManager
from .logger import ExplorationLogger
from .playbook import Playbook, PlaybookStep, PlaybackVerifier
from .config import Config
from .models import (
    ActionType, ControlType, Priority, Phase,
    AIDecision, UIElement, ExplorationStep, ExplorationResult,
    MenuItemInfo, MenuStructure,
)

logger = logging.getLogger(__name__)

# 各阶段最大迭代次数
MAX_DISCOVER_ITERATIONS = 60
MAX_CHECK_ITERATIONS = 20
MAX_LOADING_RETRIES = 3
MAX_LOGIN_STEPS = 15


class ActionHistory:
    """操作历史管理，用于传给AI提供上下文"""

    def __init__(self, max_context_items: int = 15):
        self.items: list = []
        self.max_context_items = max_context_items

    def add(self, desc: str, result: str = "done"):
        self.items.append({"step": len(self.items) + 1, "desc": desc, "result": result})

    def get_context(self) -> str:
        recent = self.items[-self.max_context_items:]
        if not recent:
            return "(暂无操作历史)"
        return "\n".join(f"{i['step']}. {i['desc']} → {i['result']}" for i in recent)

    def clear(self):
        self.items.clear()


class ExplorationEngine:
    """统一AI循环驱动的探索引擎"""

    def __init__(self, device_driver, config: Config):
        self.dd = device_driver
        self.config = config
        self.ai_client = AIClient(config.ai)
        self.ui_analyzer = UIAnalyzer(device_driver, config.exploration)
        self.screen_manager = ScreenManager(config.exploration.similarity_threshold)
        self.action_executor = ActionExecutor(device_driver, config.exploration)
        self.exploration_logger = ExplorationLogger(config.logdir, config.l_class)

        # 步骤记录
        self.steps: list = []
        self.issues_found: list = []
        self.exploration_graph: dict = {}
        self.tested_controls: list = []
        self.start_time: float = 0
        self.app_package: str = ""

        # 菜单结构
        self.menu_structure = MenuStructure()
        self._l1_discover_attempts: dict = {}  # L1名 -> 尝试次数

        # 操作历史
        self.history = ActionHistory(max_context_items=15)

        # 阶段
        self.phase = Phase.DISCOVER

        # 计数器
        self.step_count = 0
        self.consecutive_errors = 0
        self.last_clicked_target = ""

        # Playbook
        playbook_dir = config.playbook_dir or os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "playbooks"
        )
        self.playbook = Playbook(config.package_name, playbook_dir, mode=config.mode)
        self.playback_verifier = PlaybackVerifier(device_driver)

    # ==================== 入口 ====================

    def run(self, app_package: str = "") -> ExplorationResult:
        self.start_time = time.time()
        self.app_package = app_package

        replay_mode = self.config.replay_mode
        if replay_mode == "auto":
            replay_mode = "replay" if self.playbook.exists() else "record"

        if replay_mode == "replay":
            return self._run_replay(app_package)
        else:
            return self._run_record(app_package)

    # ==================== 录制模式：三阶段 ====================

    def _run_record(self, app_package: str) -> ExplorationResult:
        # 启动应用
        if app_package:
            try:
                self.dd.start_app(app_package)
                time.sleep(3)
            except Exception as e:
                logger.error(f"启动应用失败: {e}")

        # 阶段1：发现菜单结构
        self.phase = Phase.DISCOVER
        logger.info("=" * 60)
        logger.info("阶段1：发现菜单结构")
        logger.info("=" * 60)
        self._discover_phase()

        # 阶段2：逐项测试
        self.phase = Phase.TEST
        logger.info("=" * 60)
        logger.info(f"阶段2：测试菜单项 (mode={'阻断' if self.config.mode == 0 else '功能'})")
        logger.info("=" * 60)
        self._test_phase()

        # 保存playbook
        self._save_menu_structure_to_playbook()
        self.playbook.save()

        self.phase = Phase.COMPLETE
        return self._build_result(app_package)

    def _discover_phase(self):
        """发现阶段：统一AI循环发现L1和L2菜单"""
        login_step_count = 0
        # 追踪当前正在尝试发现L2的L1，以及连续navigate action次数
        discovering_l1_name = ""
        navigate_action_count = 0
        max_navigate_actions = 5  # 连续5次navigate还没产出menu_found就放弃

        for iteration in range(MAX_DISCOVER_ITERATIONS):
            if self._should_stop_global():
                break

            self.step_count += 1
            step_number = self.step_count

            screenshot, elements, ui_tree = self._capture(step_number)
            if not screenshot:
                self._record_step(step_number, self._make_error_step(step_number, "", "截图失败"))
                self.consecutive_errors += 1
                continue

            result = self.ai_client.discover_call(
                screenshot_path=screenshot,
                ui_tree_text=ui_tree,
                history=self.history.get_context(),
                login_config=self._get_login_context(),
                partial_menu=self._get_partial_menu_summary(),
            )

            if not result:
                self._record_step(step_number, self._make_error_step(step_number, screenshot, "AI调用失败"))
                self.consecutive_errors += 1
                continue

            self.consecutive_errors = 0
            resp_type = result.get("type", "")

            if resp_type == "action":
                action_name = result.get("action", "")
                purpose = result.get("purpose", "")
                reasoning = result.get("reasoning", "")
                coords = result.get("coordinates")
                text = result.get("text", "")
                target = result.get("target", text)

                logger.info(f"[步骤{step_number}] 发现阶段: {purpose} - {reasoning}")

                # 登录完成
                if action_name == "done":
                    self.history.add(f"登录完成", "done")
                    login_step_count = 0
                    time.sleep(self.config.exploration.action_delay)
                    continue

                # 登录步数限制
                if purpose == "login":
                    login_step_count += 1
                    if login_step_count > MAX_LOGIN_STEPS:
                        logger.warning(f"登录步骤超过{MAX_LOGIN_STEPS}步上限，跳过")
                        self.history.add("登录超限，跳过", "failed")
                        login_step_count = 0
                        continue

                # 追踪连续navigate/handle_popup次数，检测页面无法加载的情况
                if discovering_l1_name and purpose in ("navigate", "handle_popup"):
                    navigate_action_count += 1
                    if navigate_action_count >= max_navigate_actions:
                        logger.info(f"L1'{discovering_l1_name}'连续{navigate_action_count}次操作仍无法识别L2，标记为无L2跳过")
                        self.menu_structure.l2_map[discovering_l1_name] = []
                        self.history.add(f"L1'{discovering_l1_name}'页面无法加载，跳过L2发现", "skip")
                        discovering_l1_name = ""
                        navigate_action_count = 0
                        # 继续尝试下一个L1
                        if self._need_discover_more_l2():
                            continue
                        else:
                            break

                # 执行动作
                step = self._execute_and_record(step_number, screenshot, result)
                self._record_step(step_number, step)

            elif resp_type == "menu_found":
                logger.info(f"[步骤{step_number}] AI发现菜单结构")
                # 打印本次识别到的L1和L2
                l1_names = [item.get("name", "") for item in result.get("l1_items", [])]
                l2_names = [item.get("name", "") for item in result.get("l2_items", [])]
                selected_l1 = next((item.get("name") for item in result.get("l1_items", []) if item.get("is_selected")), l1_names[0] if l1_names else "?")
                logger.info(f"  L1: {l1_names}, 当前选中: {selected_l1}")
                logger.info(f"  L2: {l2_names if l2_names else '(无)'}")
                self._parse_menu_from_result(result)
                step = self._make_info_step(step_number, screenshot, elements, "发现菜单结构")
                self._record_step(step_number, step)

                # 重置navigate计数
                navigate_action_count = 0

                # 检查是否还需要切换L1来发现更多L2
                if self._need_discover_more_l2():
                    discovering_l1_name = self._get_current_discovering_l1()
                    continue
                else:
                    break

            else:
                logger.warning(f"[步骤{step_number}] AI返回未知类型: {resp_type}")
                self.consecutive_errors += 1

        # 发现阶段结束，打印菜单
        self._log_menu_structure()

    def _test_phase(self):
        """测试阶段：确定性遍历菜单 + AI循环判断每个页面"""
        menu = self.menu_structure

        if not menu.l1_items:
            logger.warning("未发现任何L1菜单项，跳过测试阶段")
            return

        for l1_idx, l1 in enumerate(menu.l1_items):
            if self._should_stop_global():
                break

            # 点击L1
            logger.info(f"--- 切换到L1: {l1.name} ({l1_idx + 1}/{len(menu.l1_items)}) ---")
            self._click_menu_item(l1, "click_l1")
            time.sleep(self.config.exploration.action_delay)

            l2_list = menu.l2_map.get(l1.name, [])
            if not l2_list:
                # 无L2，直接检查L1页面
                target = l1.name
                self.last_clicked_target = target
                status = self._check_page_loop(target)
                self._record_test_result(l1, None, status)
                continue

            for l2_idx, l2 in enumerate(l2_list):
                if self._should_stop_global():
                    break

                # 点击L2
                target = f"{l1.name}-{l2.name}"
                logger.info(f"  测试L2: {l2.name} ({l2_idx + 1}/{len(l2_list)})")
                self._click_menu_item(l2, "click_l2")
                self.last_clicked_target = target
                time.sleep(self.config.exploration.action_delay)

                # AI循环检查页面
                status = self._check_page_loop(target)
                self._record_test_result(l1, l2, status)

    def _check_page_loop(self, target: str) -> str:
        """统一AI循环：处理弹窗 + 判断页面状态。返回 blocked/loaded/loading"""
        loading_retries = 0
        login_step_count = 0

        for iteration in range(MAX_CHECK_ITERATIONS):
            if self._should_stop_global():
                return "blocked" if self.config.mode == 0 else "error"

            self.step_count += 1
            step_number = self.step_count

            screenshot, elements, ui_tree = self._capture(step_number)
            if not screenshot:
                self._record_step(step_number, self._make_error_step(step_number, "", "截图失败"))
                continue

            result = self.ai_client.test_call(
                screenshot_path=screenshot,
                ui_tree_text=ui_tree,
                target=target,
                mode=self.config.mode,
                history=self.history.get_context(),
                tested_summary=self._get_tested_summary(),
                login_config=self._get_login_context(),
            )

            if not result:
                self._record_step(step_number, self._make_error_step(step_number, screenshot, "AI调用失败"))
                continue

            resp_type = result.get("type", "")

            if resp_type == "action":
                action_name = result.get("action", "")
                purpose = result.get("purpose", "")
                reasoning = result.get("reasoning", "")

                logger.info(f"[步骤{step_number}] 检查'{target}': {purpose} - {reasoning}")

                # 不需要登录时，拦截login动作，强制返回
                if purpose == "login" and not self.config.login_required:
                    logger.info(f"  -> 登录未启用，跳过登录，执行返回")
                    self.history.add("登录未启用，跳过登录弹窗", "skip")
                    self.driver.back()
                    time.sleep(self.config.exploration.action_delay)
                    continue

                if action_name == "done":
                    self.history.add(f"登录完成", "done")
                    login_step_count = 0
                    time.sleep(self.config.exploration.action_delay)
                    continue

                if purpose == "login":
                    login_step_count += 1
                    if login_step_count > MAX_LOGIN_STEPS:
                        logger.warning(f"登录步骤超限，视为阻断")
                        return "blocked" if self.config.mode == 0 else "error"

                step = self._execute_and_record(step_number, screenshot, result)
                self._record_step(step_number, step)

            elif resp_type == "page_status":
                status = result.get("status", "")
                desc = result.get("description", "")
                reasoning = result.get("reasoning", "")
                logger.info(f"[步骤{step_number}] 页面状态'{target}': {status} - {desc}")

                step = self._make_info_step(step_number, screenshot, elements, f"{target}: {status} - {desc}")
                self._record_step(step_number, step)
                # playbook记录check步
                self.playbook.record_step(PlaybookStep(
                    step=step_number, action="check",
                    target_text=target, description=f"{status}: {desc}",
                    expected_result=status,
                ))

                if status == "loading":
                    loading_retries += 1
                    if loading_retries >= MAX_LOADING_RETRIES:
                        logger.info(f"  加载重试{loading_retries}次后超时")
                        return "blocked" if self.config.mode == 0 else "error"
                    time.sleep(self.config.exploration.action_delay)
                    continue

                return status  # "blocked" or "loaded"

            else:
                logger.warning(f"[步骤{step_number}] AI返回未知类型: {resp_type}")

        # 循环超时
        return "blocked" if self.config.mode == 0 else "error"

    # ==================== 动作执行 ====================

    def _execute_and_record(self, step_number: int, screenshot: str, ai_result: dict) -> ExplorationStep:
        """执行AI返回的动作并记录"""
        action_name = ai_result.get("action", "click")
        coords = ai_result.get("coordinates")
        text = ai_result.get("text", "")
        target = ai_result.get("target", text)
        purpose = ai_result.get("purpose", "")
        reasoning = ai_result.get("reasoning", "")

        # 坐标归一化
        if coords:
            coords = self._normalize_coords(tuple(coords))

        # 构建AIDecision
        action_type = self._map_action_type(action_name)

        # 输入框处理：确定填什么内容
        text_input = None
        if action_type == ActionType.TEXT_INPUT:
            text_input = self._resolve_input_content(target)

        # 构建target_element用于Poco文本匹配
        target_element = None
        if text and action_type == ActionType.CLICK:
            target_element = UIElement(
                name="", text=text, desc="", type="button",
                control_type=ControlType.BUTTON, bounds={}, center=coords or (),
                clickable=True, enabled=True, visible=True,
            )

        decision = AIDecision(
            action=action_type,
            target_element=target_element,
            coordinates=coords,
            text_input=text_input,
            swipe_direction="left" if action_name == "swipe_left" else None,
            is_popup=(purpose == "handle_popup"),
            priority=Priority.HIGH,
            reasoning=reasoning,
        )

        action_result = self.action_executor.execute(decision)
        self.history.add(f"{purpose}: {action_name} '{target}'", action_result)

        # playbook录制
        pb_action = "close_popup" if purpose == "handle_popup" else purpose or action_name
        self.playbook.record_step(PlaybookStep(
            step=step_number, action=pb_action,
            target_text=text or target,
            coordinates=tuple(coords) if coords else (),
            description=f"{purpose}: {reasoning}",
        ))

        time.sleep(self.config.exploration.action_delay)

        return ExplorationStep(
            step_number=step_number, timestamp=time.time(),
            screenshot_path=screenshot,
            screen_description=f"{purpose}: {action_name} '{target}'",
            ui_tree_summary="",
            action_taken=decision,
            action_result=action_result,
            screen_fingerprint="",
        )

    # ==================== 回放模式 ====================

    def _run_replay(self, app_package: str) -> ExplorationResult:
        """回放模式：加载playbook逐步执行"""
        if not self.playbook.load():
            logger.warning("Playbook加载失败，降级为录制模式")
            return self._run_record(app_package)

        if app_package:
            try:
                self.dd.start_app(app_package)
                time.sleep(3)
            except Exception as e:
                logger.error(f"启动应用失败: {e}")

        self._load_menu_structure_from_playbook()

        for idx, pb_step in enumerate(self.playbook.steps):
            if self._should_stop_global():
                break

            self.step_count += 1
            step_number = self.step_count
            action = pb_step.action

            try:
                if action == "check":
                    step = self._replay_check_step(step_number, pb_step)
                elif action in ("close_popup", "handle_popup"):
                    step = self._replay_action_step(step_number, pb_step)
                elif action in ("click_l1", "click_l2", "navigate"):
                    step = self._replay_action_step(step_number, pb_step)
                elif action == "back":
                    self.dd.back()
                    time.sleep(self.config.exploration.action_delay)
                    step = self._make_info_step(step_number, "", [], f"返回: {pb_step.target_text}")
                else:
                    step = self._replay_action_step(step_number, pb_step)

                self._record_step(step_number, step)

            except Exception as e:
                logger.error(f"回放步骤{idx}异常: {e}")
                logger.info("降级为录制模式继续")
                return self._fallback_to_record_and_finish(app_package)

        return self._build_result(app_package)

    def _replay_check_step(self, step_number: int, pb_step: PlaybookStep) -> ExplorationStep:
        """回放check步：必须调AI重新判断"""
        screenshot, elements, ui_tree = self._capture(step_number)
        if not screenshot:
            return self._make_error_step(step_number, "", "截图失败")

        target = pb_step.target_text or ""
        mode = self.config.mode

        result = self.ai_client.test_call(
            screenshot_path=screenshot,
            ui_tree_text=ui_tree,
            target=target,
            mode=mode,
            history=self.history.get_context(),
        )

        status = "unknown"
        desc = ""
        if result and result.get("type") == "page_status":
            status = result.get("status", "unknown")
            desc = result.get("description", "")
        elif result and result.get("type") == "action":
            # 回放时遇到弹窗，先处理
            self._execute_and_record(step_number, screenshot, result)
            # 再递归check
            return self._replay_check_step(step_number, pb_step)

        logger.info(f"[回放步骤{step_number}] check '{target}': {status} - {desc}")

        # 记录测试结果
        self.last_clicked_target = target
        self._record_block_result(step_number, status, desc, screenshot)

        return self._make_info_step(step_number, screenshot, elements, f"check {target}: {status}")

    def _replay_action_step(self, step_number: int, pb_step: PlaybookStep) -> ExplorationStep:
        """回放操作步：用坐标/文字执行"""
        coords = tuple(pb_step.coordinates) if pb_step.coordinates else None
        text = pb_step.target_text or pb_step.description or ""

        target_element = None
        if text:
            target_element = UIElement(
                name="", text=text, desc="", type="button",
                control_type=ControlType.BUTTON, bounds={}, center=coords or (),
                clickable=True, enabled=True, visible=True,
            )

        decision = AIDecision(
            action=ActionType.CLICK,
            target_element=target_element,
            coordinates=coords,
            is_popup=(pb_step.action in ("close_popup", "handle_popup")),
            priority=Priority.HIGH,
            reasoning=f"replay: {pb_step.action}",
        )

        action_result = self.action_executor.execute(decision)
        time.sleep(self.config.exploration.action_delay)

        return ExplorationStep(
            step_number=step_number, timestamp=time.time(),
            screenshot_path="",
            screen_description=f"replay {pb_step.action}: {text}",
            ui_tree_summary="",
            action_taken=decision,
            action_result=action_result,
            screen_fingerprint="",
        )

    def _fallback_to_record_and_finish(self, app_package: str) -> ExplorationResult:
        """回放异常时降级为录制模式完成剩余测试"""
        logger.info("降级为录制模式，从当前位置继续")
        self._test_phase()
        self.playbook.save()
        return self._build_result(app_package)

    # ==================== 辅助方法 ====================

    def _capture(self, step_number: int) -> tuple:
        """截图 + 提取UI树。返回 (screenshot_path, elements, ui_tree_text)"""
        time.sleep(0.5)  # 短暂等待页面稳定
        screenshot_path = self.ui_analyzer.capture_screenshot(f"step{step_number}")
        if not screenshot_path:
            return "", [], ""
        elements = self.ui_analyzer.extract_ui_tree()
        ui_tree_text = self.ui_analyzer.format_ui_tree_text(elements) if elements else ""
        return screenshot_path, elements, ui_tree_text

    def _click_menu_item(self, item: MenuItemInfo, pb_action: str):
        """点击一个菜单项（L1或L2），优先用Poco文本匹配"""
        target_element = UIElement(
            name="", text=item.element_text or item.name, desc="",
            type="button", control_type=ControlType.TAB,
            bounds={}, center=item.coordinates,
            clickable=True, enabled=True, visible=True,
        )
        decision = AIDecision(
            action=ActionType.CLICK,
            target_element=target_element,
            coordinates=item.coordinates,
            priority=Priority.HIGH,
            reasoning=f"点击{item.name}",
        )
        self.action_executor.execute(decision)
        self.history.add(f"点击{item.name}", "done")

        # playbook录制
        self.playbook.record_step(PlaybookStep(
            step=self.step_count, action=pb_action,
            target_text=item.element_text or item.name,
            coordinates=tuple(item.coordinates) if item.coordinates else (),
            description=f"点击{item.name}",
        ))

    def _parse_menu_from_result(self, result: dict):
        """从AI返回结果解析菜单结构"""
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

        l2_items = []
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

        if l1_items:
            self.menu_structure.l1_items = l1_items
            # 当前选中的L1的L2
            current_l1_name = ""
            for l1 in l1_items:
                if l1.is_selected:
                    current_l1_name = l1.name
                    break
            if not current_l1_name and l1_items:
                current_l1_name = l1_items[0].name

            if current_l1_name:
                # 有L2就记录，没L2也标记为空列表（表示已发现、无L2）
                self.menu_structure.l2_map[current_l1_name] = l2_items

    def _need_discover_more_l2(self) -> bool:
        """检查是否还有L1没有发现L2，按顺序切换下一个。
        对每个L1最多尝试2次，超过则标记为空L2（可能需要登录等原因无法进入）。
        """
        max_attempts = 2
        for l1 in self.menu_structure.l1_items:
            if l1.name not in self.menu_structure.l2_map:
                attempts = self._l1_discover_attempts.get(l1.name, 0)
                if attempts >= max_attempts:
                    logger.info(f"L1'{l1.name}'已尝试{attempts}次仍无法发现L2，跳过（可能需要登录）")
                    self.menu_structure.l2_map[l1.name] = []
                    continue
                self._l1_discover_attempts[l1.name] = attempts + 1
                logger.info(f"切换到L1'{l1.name}'发现L2...(第{attempts + 1}次)")
                self._click_menu_item(l1, "click_l1")
                time.sleep(self.config.exploration.action_delay)
                return True
        return False

    def _get_current_discovering_l1(self) -> str:
        """获取当前正在尝试发现L2的L1名称"""
        for l1 in self.menu_structure.l1_items:
            if l1.name not in self.menu_structure.l2_map:
                return l1.name
        return ""

    def _record_test_result(self, l1: MenuItemInfo, l2, status: str):
        """记录测试结果"""
        if l2:
            target = f"{l1.name}-{l2.name}"
            l2.status = status
            l2.block_result = status
        else:
            target = l1.name
            l1.status = status
            l1.block_result = status

        if target not in self.tested_controls:
            self.tested_controls.append(target)

        mode_desc = "阻断" if self.config.mode == 0 else "功能"
        if self.config.mode == 0:
            success = (status == "blocked")
            result_type = "block_success" if success else "block_fail"
            logger.info(f"  {'✓' if success else '✗'} {target}: {'阻断成功' if success else '阻断失败'}")
        else:
            success = (status == "loaded")
            result_type = "function_normal" if success else "function_error"
            logger.info(f"  {'✓' if success else '✗'} {target}: {'功能正常' if success else '功能异常'}")

        self.issues_found.append({
            "step": self.step_count,
            "type": result_type,
            "target": target,
            "description": f"{target}: {status}",
            "screenshot": "",
        })

    def _record_block_result(self, step_number: int, status: str, desc: str, screenshot_path: str):
        """记录阻断测试结果（回放模式用）"""
        if self.config.mode == 0:
            result_type = "block_success" if status == "blocked" else "block_fail"
        else:
            result_type = "function_normal" if status == "loaded" else "function_error"

        self.issues_found.append({
            "step": step_number,
            "type": result_type,
            "target": self.last_clicked_target,
            "description": desc,
            "screenshot": screenshot_path,
        })

    def _record_step(self, step_number: int, step: ExplorationStep):
        """记录一个步骤"""
        self.steps.append(step)
        self.exploration_logger.log_step(step)

    def _get_login_context(self) -> str:
        """构建登录配置上下文"""
        if not self.config.login_required:
            return ""
        parts = [f"需要登录: 是"]
        parts.append(f"登录方式: {self.config.login_method or 'password'}")
        if self.config.login_phone:
            parts.append(f"手机号: {self.config.login_phone}")
        if self.config.login_email:
            parts.append(f"邮箱: {self.config.login_email}")
        parts.append("(密码由系统自动填写)")
        return "\n".join(parts)

    def _resolve_input_content(self, target: str) -> str:
        """根据AI返回的target描述确定输入内容"""
        target_lower = target.lower() if target else ""
        if "手机" in target_lower or "账号" in target_lower or "phone" in target_lower:
            return self.config.login_phone or ""
        elif "邮箱" in target_lower or "email" in target_lower:
            return self.config.login_email or ""
        elif "密码" in target_lower or "password" in target_lower:
            return self.config.login_password or ""
        elif "验证码" in target_lower or "code" in target_lower:
            return "123456"  # 默认验证码
        return ""

    def _get_partial_menu_summary(self) -> str:
        """已发现的菜单摘要"""
        if not self.menu_structure.l1_items:
            return ""
        parts = ["L1菜单:"]
        for l1 in self.menu_structure.l1_items:
            l2_list = self.menu_structure.l2_map.get(l1.name, [])
            if l2_list:
                l2_names = ", ".join(l2.name for l2 in l2_list)
                parts.append(f"  {l1.name}: L2=[{l2_names}]")
            elif l1.name in self.menu_structure.l2_map:
                parts.append(f"  {l1.name}: (无L2)")
            else:
                parts.append(f"  {l1.name}: (L2待发现)")
        return "\n".join(parts)

    def _get_tested_summary(self) -> str:
        """已测试项摘要"""
        if not self.tested_controls:
            return ""
        parts = [f"已测试 {len(self.tested_controls)} 个菜单项:"]
        for target in self.tested_controls[-10:]:
            # 从issues中找对应结果
            for issue in reversed(self.issues_found):
                if issue["target"] == target:
                    parts.append(f"  {target}: {issue['type']}")
                    break
        return "\n".join(parts)

    def _log_menu_structure(self):
        """打印发现的菜单结构"""
        menu = self.menu_structure
        if not menu.l1_items:
            logger.info("未发现L1菜单项")
            return
        logger.info(f"发现 {len(menu.l1_items)} 个L1菜单项:")
        for l1 in menu.l1_items:
            l2_list = menu.l2_map.get(l1.name, [])
            if l2_list:
                l2_names = ", ".join(l2.name for l2 in l2_list)
                logger.info(f"  {l1.name}: [{l2_names}]")
            else:
                logger.info(f"  {l1.name}: (无L2)")

    def _should_stop_global(self) -> bool:
        """全局终止条件"""
        if self.step_count >= self.config.exploration.max_steps:
            logger.warning(f"达到最大步数 {self.config.exploration.max_steps}")
            return True
        elapsed = time.time() - self.start_time
        if elapsed >= self.config.exploration.max_duration_seconds:
            logger.warning(f"达到最大时长 {self.config.exploration.max_duration_seconds}s")
            return True
        if self.consecutive_errors >= self.config.exploration.max_errors:
            logger.warning(f"连续 {self.consecutive_errors} 次错误")
            return True
        return False

    def _normalize_coords(self, coords: tuple) -> tuple:
        """将坐标归一化到0-1范围"""
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
    def _map_action_type(action_name: str) -> ActionType:
        """将AI返回的action字符串映射为ActionType"""
        mapping = {
            "click": ActionType.CLICK,
            "input_text": ActionType.TEXT_INPUT,
            "swipe_left": ActionType.SCROLL_LEFT,
            "swipe_right": ActionType.SCROLL_RIGHT,
            "swipe_up": ActionType.SCROLL_UP,
            "swipe_down": ActionType.SCROLL_DOWN,
            "back": ActionType.BACK,
            "wait": ActionType.WAIT,
            "done": ActionType.WAIT,
        }
        return mapping.get(action_name, ActionType.CLICK)

    # ==================== Playbook 序列化 ====================

    def _save_menu_structure_to_playbook(self):
        """保存菜单结构到playbook"""
        menu = self.menu_structure
        data = {
            "l1_items": [],
            "l2_map": {},
        }
        for l1 in menu.l1_items:
            data["l1_items"].append({
                "name": l1.name,
                "element_text": l1.element_text,
                "element_name": l1.element_name,
                "coordinates": list(l1.coordinates) if l1.coordinates else [0, 0],
                "is_selected": l1.is_selected,
                "status": l1.status,
                "block_result": l1.block_result,
            })
        for l1_name, l2_list in menu.l2_map.items():
            data["l2_map"][l1_name] = []
            for l2 in l2_list:
                data["l2_map"][l1_name].append({
                    "name": l2.name,
                    "element_text": l2.element_text,
                    "element_name": l2.element_name,
                    "coordinates": list(l2.coordinates) if l2.coordinates else [0, 0],
                    "is_selected": l2.is_selected,
                    "status": l2.status,
                    "block_result": l2.block_result,
                })
        self.playbook.menu_structure = data

    def _load_menu_structure_from_playbook(self):
        """从playbook恢复菜单结构"""
        data = getattr(self.playbook, 'menu_structure', None)
        if not data:
            return
        menu = MenuStructure()
        for item in data.get("l1_items", []):
            coords = item.get("coordinates", [0, 0])
            menu.l1_items.append(MenuItemInfo(
                name=item.get("name", ""),
                element_text=item.get("element_text", ""),
                element_name=item.get("element_name", ""),
                coordinates=tuple(coords),
                level=1,
                is_selected=item.get("is_selected", False),
                status=item.get("status", "pending"),
                block_result=item.get("block_result", ""),
            ))
        for l1_name, l2_list in data.get("l2_map", {}).items():
            menu.l2_map[l1_name] = []
            for item in l2_list:
                coords = item.get("coordinates", [0, 0])
                menu.l2_map[l1_name].append(MenuItemInfo(
                    name=item.get("name", ""),
                    element_text=item.get("element_text", ""),
                    element_name=item.get("element_name", ""),
                    coordinates=tuple(coords),
                    level=2,
                    is_selected=item.get("is_selected", False),
                    status=item.get("status", "pending"),
                    block_result=item.get("block_result", ""),
                ))
        self.menu_structure = menu

    # ==================== 结果构建 ====================

    def _build_result(self, package: str) -> ExplorationResult:
        total_l1 = len(self.menu_structure.l1_items)
        total_l2 = sum(len(v) for v in self.menu_structure.l2_map.values())
        l1_with_l2 = sum(1 for v in self.menu_structure.l2_map.values() if len(v) > 0)
        total_menu_items = (total_l1 - l1_with_l2) + total_l2
        tested_count = len(self.tested_controls)
        coverage = (tested_count / total_menu_items * 100) if total_menu_items > 0 else 0

        return ExplorationResult(
            app_package=package,
            platform=self.config.device.platform,
            start_time=self.start_time,
            end_time=time.time(),
            total_steps=len(self.steps),
            unique_screens=total_l1,
            total_elements_found=total_menu_items,
            elements_interacted=tested_count,
            coverage_percentage=coverage,
            steps=self.steps,
            screens=self.screen_manager.screens,
            issues_found=self.issues_found,
            exploration_graph=self.exploration_graph,
        )

    @staticmethod
    def _make_info_step(step_number, screenshot_path, elements, description):
        return ExplorationStep(
            step_number=step_number, timestamp=time.time(),
            screenshot_path=screenshot_path if screenshot_path else "",
            screen_description=description,
            ui_tree_summary=f"{len(elements)}个元素" if elements else "",
            action_taken=AIDecision(action=ActionType.WAIT, priority=Priority.LOW, reasoning=description),
            action_result="success", screen_fingerprint="",
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

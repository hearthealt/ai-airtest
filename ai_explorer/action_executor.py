# -*- encoding=utf8 -*-
"""操作执行器：将AI决策映射为Airtest/Poco操作。"""

import re
import time
import logging

from .models import AIDecision, ActionType
from .config import ExplorationConfig

logger = logging.getLogger(__name__)


class ActionExecutor:
    """操作执行器：将AI决定的操作通过Airtest/Poco在设备上执行。"""

    # 通用控件类名前缀，用name匹配会命中大量无关元素，必须跳过
    _GENERIC_NAME_PREFIXES = (
        "android.widget.", "android.view.", "android.webkit.",
        "androidx.", "android.support.",
    )

    def __init__(self, device_driver, config: ExplorationConfig):
        """
        :param device_driver: DeviceDriver或PcDeviceDriver实例
        :param config: 探索配置
        """
        self.dd = device_driver
        self.config = config

    def _is_generic_name(self, name: str) -> bool:
        """判断name是否为通用控件类名（不适合用poco(name=xxx)匹配）"""
        if not name:
            return True
        for prefix in self._GENERIC_NAME_PREFIXES:
            if name.startswith(prefix):
                return True
        return False

    def execute(self, decision: AIDecision) -> str:
        """
        执行一条AI决策操作。

        :param decision: AI决策对象
        :return: "success"（成功）, "failed"（失败）, 或 "error"（异常）
        """
        action = decision.action

        try:
            if action == ActionType.CLICK:
                return self._do_click(decision)
            elif action == ActionType.LONG_PRESS:
                return self._do_long_press(decision)
            elif action in (ActionType.SWIPE, ActionType.SCROLL_DOWN, ActionType.SCROLL_UP,
                            ActionType.SCROLL_LEFT, ActionType.SCROLL_RIGHT):
                return self._do_swipe(decision)
            elif action == ActionType.TEXT_INPUT:
                return self._do_text_input(decision)
            elif action == ActionType.BACK:
                return self._do_back()
            elif action == ActionType.HOME:
                return self._do_home()
            elif action == ActionType.WAIT:
                time.sleep(self.config.action_delay)
                return "success"
            else:
                logger.warning(f"未知操作类型: {action}")
                return "failed"
        except Exception as e:
            logger.error(f"操作执行异常: {e}")
            return "error"

    def _do_click(self, decision: AIDecision) -> str:
        """执行点击操作（Poco文本→描述→名称→坐标兜底）"""
        poco = getattr(self.dd, 'poco', None)
        el = decision.target_element

        # 策略1：通过Poco文本匹配点击
        if poco and el and el.text:
            try:
                elem = poco(text=el.text)
                if elem.exists():
                    elem.click()
                    logger.info(f"  -> Poco文本匹配点击: text='{el.text}'")
                    return "success"
                # 去空格模糊匹配（处理"准 备 好 啦！"这类字间有空格的文本）
                normalized = el.text.replace(" ", "").replace("\u3000", "")
                if normalized and len(normalized) >= 2:
                    pattern = r"\s*".join(re.escape(c) for c in normalized)
                    elem2 = poco(textMatches=pattern)
                    if elem2.exists():
                        elem2.click()
                        logger.info(f"  -> Poco文本模糊匹配点击: text='{el.text}'")
                        return "success"
            except Exception as e:
                pass

        # 策略2：通过Poco描述匹配点击
        if poco and el and getattr(el, 'desc', ''):
            try:
                elem = poco(desc=el.desc)
                if elem.exists():
                    elem.click()
                    logger.info(f"  -> Poco描述匹配点击: desc='{el.desc}'")
                    return "success"
            except Exception as e:
                pass

        # 策略3：通过Poco名称匹配点击（跳过通用控件类名，且要求唯一匹配）
        if poco and el and el.name and not self._is_generic_name(el.name):
            try:
                elems = poco(name=el.name)
                if elems.exists():
                    # 如果匹配到多个元素，跳过（不确定该点哪个）
                    children = elems.offspring() if hasattr(elems, 'offspring') else []
                    try:
                        count = len(list(elems))
                    except Exception:
                        count = 1
                    if count > 1:
                        pass
                    else:
                        elems.click()
                        logger.info(f"  -> Poco名称匹配点击: name='{el.name}'")
                        return "success"
            except Exception as e:
                pass

        # 策略4：通过Poco坐标点击
        coords = decision.coordinates
        if not coords and el:
            coords = el.center

        if coords and poco:
            try:
                poco.click(coords)
                logger.info(f"  -> Poco坐标点击: ({coords[0]:.3f}, {coords[1]:.3f})")
                return "success"
            except Exception as e:
                pass

        # 策略5：通过Airtest绝对坐标点击（最终兜底）
        if coords:
            try:
                screen_w, screen_h = self._get_screen_size()
                abs_x = int(coords[0] * screen_w)
                abs_y = int(coords[1] * screen_h)
                from airtest.core.api import touch
                touch((abs_x, abs_y))
                logger.info(f"  -> Airtest坐标点击: ({abs_x}, {abs_y})")
                return "success"
            except Exception as e:
                logger.error(f"Airtest坐标点击失败: {e}")
                return "error"

        logger.warning("找不到有效的点击目标")
        return "failed"

    def _do_long_press(self, decision: AIDecision) -> str:
        """执行长按操作"""
        coords = decision.coordinates
        if not coords and decision.target_element:
            coords = decision.target_element.center
        if not coords:
            return "failed"

        poco = getattr(self.dd, 'poco', None)
        if poco:
            try:
                poco.long_click(coords)
                return "success"
            except Exception:
                pass

        try:
            screen_w, screen_h = self._get_screen_size()
            abs_x = int(coords[0] * screen_w)
            abs_y = int(coords[1] * screen_h)
            from airtest.core.api import touch
            touch((abs_x, abs_y), duration=1.5)
            return "success"
        except Exception as e:
            logger.error(f"长按操作失败: {e}")
            return "error"

    def _do_swipe(self, decision: AIDecision) -> str:
        """执行滑动/滚动操作"""
        action = decision.action
        direction_map = {
            ActionType.SCROLL_DOWN: "up",     # 内容向下滚动 = 手指向上滑
            ActionType.SCROLL_UP: "down",     # 内容向上滚动 = 手指向下滑
            ActionType.SCROLL_LEFT: "right",  # 内容向左滚动 = 手指向右滑
            ActionType.SCROLL_RIGHT: "left",  # 内容向右滚动 = 手指向左滑
            ActionType.SWIPE: decision.swipe_direction or "up",
        }
        direction = direction_map.get(action, "up")

        try:
            self.dd.swipe(direction=direction, duration=1)
            return "success"
        except Exception as e:
            pass

        # 兜底：使用Airtest滑动
        try:
            screen_w, screen_h = self._get_screen_size()
            cx, cy = screen_w // 2, screen_h // 2
            offsets = {
                "up": (0, -screen_h // 4),
                "down": (0, screen_h // 4),
                "left": (-screen_w // 4, 0),
                "right": (screen_w // 4, 0),
            }
            dx, dy = offsets.get(direction, (0, -screen_h // 4))
            from airtest.core.api import swipe
            swipe((cx, cy), (cx + dx, cy + dy), duration=0.5)
            return "success"
        except Exception as e:
            logger.error(f"滑动操作失败: {e}")
            return "error"

    def _do_text_input(self, decision: AIDecision) -> str:
        """执行文本输入操作"""
        text = decision.text_input
        if not text:
            return "failed"

        # 先点击输入框（如果有目标）
        if decision.target_element or decision.coordinates:
            click_result = self._do_click(decision)
            if click_result != "success":
                logger.warning("输入前点击输入框失败")
            time.sleep(0.8)

        try:
            self.dd.text(text, enter=False)
            logger.info(f"  -> 文本输入完成: '{text[:3]}***'({len(text)}字符)")
            return "success"
        except Exception as e:
            logger.error(f"文本输入失败: {e}")
            return "error"

    def _do_back(self) -> str:
        """执行返回操作"""
        try:
            self.dd.back()
            return "success"
        except Exception:
            try:
                self.dd.keyevent("BACK")
                return "success"
            except Exception as e:
                logger.error(f"返回操作失败: {e}")
                return "error"

    def _do_home(self) -> str:
        """执行主页键操作"""
        try:
            self.dd.keyevent("HOME")
            return "success"
        except Exception as e:
            logger.error(f"主页键操作失败: {e}")
            return "error"

    def _get_screen_size(self) -> tuple:
        """获取屏幕分辨率"""
        try:
            size = self.dd.driver.get_current_resolution()
            return size
        except Exception:
            return (1080, 2400)  # 常见默认分辨率

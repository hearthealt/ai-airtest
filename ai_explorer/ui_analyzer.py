# -*- encoding=utf8 -*-
"""截图捕获与Poco UI树提取模块。"""

import os
import hashlib
import datetime
import logging
from typing import List

from .models import UIElement, ControlType
from .config import ExplorationConfig

logger = logging.getLogger(__name__)

# Android/iOS控件类型到归一化ControlType的映射表
WIDGET_TYPE_MAP = {
    # Android控件
    "android.widget.Button": ControlType.BUTTON,
    "android.widget.ImageButton": ControlType.BUTTON,
    "android.widget.EditText": ControlType.TEXT_FIELD,
    "android.widget.TextView": ControlType.LINK,
    "android.widget.CheckBox": ControlType.CHECKBOX,
    "android.widget.Switch": ControlType.SWITCH,
    "android.widget.ToggleButton": ControlType.SWITCH,
    "android.widget.ImageView": ControlType.IMAGE,
    "android.widget.TabWidget": ControlType.TAB,
    "android.widget.ListView": ControlType.LIST_ITEM,
    "android.widget.RecyclerView": ControlType.LIST_ITEM,
    "android.widget.Spinner": ControlType.MENU,
    "android.widget.RadioButton": ControlType.CHECKBOX,
    # iOS控件
    "Button": ControlType.BUTTON,
    "TextField": ControlType.TEXT_FIELD,
    "SecureTextField": ControlType.TEXT_FIELD,
    "StaticText": ControlType.LINK,
    "Image": ControlType.IMAGE,
    "Switch": ControlType.SWITCH,
    "Cell": ControlType.LIST_ITEM,
    "TabBar": ControlType.TAB,
    "SearchField": ControlType.TEXT_FIELD,
}


class UIAnalyzer:
    """UI分析器：从设备中提取并结构化UI信息。"""

    def __init__(self, device_driver, config: ExplorationConfig, l_class: str = ""):
        """
        :param device_driver: DeviceDriver或PcDeviceDriver实例
        :param config: 探索配置
        :param l_class: 小类ID（用于截图文件命名）
        """
        self.dd = device_driver
        self.config = config
        self.l_class = l_class
        self._screenshot_counter = 0

    def capture_screenshot(self, logdir: str, label: str = "") -> str:
        """
        捕获当前界面截图并返回文件路径。

        :param logdir: 日志输出目录
        :param label: 截图标签（用于文件命名）
        :return: 截图文件路径，失败返回空字符串
        """
        self._screenshot_counter += 1
        prefix = self.l_class
        label_part = f"-{label}" if label else ""
        filename = f"{prefix}{label_part}-{self._screenshot_counter}.jpg"
        filepath = os.path.join(logdir, filename)
        try:
            self.dd.driver.snapshot(filepath)
        except Exception as e:
            logger.error(f"截图捕获失败: {e}")
            # 尝试备用截图方式
            try:
                from airtest.core.api import snapshot
                snapshot(filepath)
            except Exception as e2:
                logger.error(f"备用截图方式也失败了: {e2}")
                return ""
        return filepath

    def extract_ui_tree(self) -> List[UIElement]:
        """
        提取Poco UI树并返回扁平化的UIElement列表。

        :return: UI元素列表
        """
        poco = getattr(self.dd, 'poco', None)
        if poco is None:
            logger.warning("Poco不可用，返回空UI树")
            return []

        elements = []
        try:
            # 方式一：通过Poco agent的hierarchy.dump()获取完整层级
            hierarchy = poco.agent.hierarchy.dump()
            self._flatten_hierarchy(hierarchy, elements, path="")
        except Exception as e:
            logger.warning(f"UI层级dump失败: {e}")
            try:
                # 方式二：通过Poco代理API遍历
                self._traverse_poco_proxy(poco, elements)
            except Exception as e2:
                logger.error(f"备用UI树提取也失败了: {e2}")
        return elements

    def _flatten_hierarchy(self, node: dict, result: list, path: str, depth: int = 0):
        """
        递归展开Poco层级dump为扁平化的UIElement列表。

        :param node: 当前节点字典
        :param result: 结果列表（就地追加）
        :param path: 当前节点路径
        :param depth: 当前递归深度
        """
        if depth > 10 or len(result) > 80:
            return

        payload = node.get("payload", node)
        name = payload.get("name", "")
        node_type = payload.get("type", "")
        text = payload.get("text", "")
        visible = payload.get("visible", True)
        enabled = payload.get("enabled", True)
        clickable = payload.get("clickable", False)
        pos = payload.get("pos", [0, 0])
        size = payload.get("size", [0, 0])

        current_path = f"{path}/{name}" if path else name
        children = node.get("children", [])

        # 跳过不可见元素，但继续递归子节点
        if not visible:
            for child in children:
                self._flatten_hierarchy(child, result, path=current_path, depth=depth + 1)
            return

        # 跳过纯布局容器（无文本且不可点击）
        if node_type in self.config.skip_element_types and not clickable and not text:
            for child in children:
                self._flatten_hierarchy(child, result, path=current_path, depth=depth + 1)
            return

        # 构建UIElement对象
        control_type = WIDGET_TYPE_MAP.get(node_type, ControlType.UNKNOWN)
        cx = pos[0] if len(pos) >= 2 else 0
        cy = pos[1] if len(pos) >= 2 else 0
        w = size[0] if len(size) >= 2 else 0
        h = size[1] if len(size) >= 2 else 0

        bounds = {
            "x": cx - w / 2,
            "y": cy - h / 2,
            "width": w,
            "height": h,
        }

        # 生成唯一元素ID（用于去重）
        id_str = f"{node_type}|{name}|{text}|{cx:.2f}|{cy:.2f}"
        element_id = hashlib.md5(id_str.encode()).hexdigest()[:8]

        elem = UIElement(
            name=name,
            text=text,
            type=node_type,
            control_type=control_type,
            bounds=bounds,
            center=(cx, cy),
            clickable=clickable,
            enabled=enabled,
            visible=True,
            poco_path=current_path,
            element_id=element_id,
        )
        result.append(elem)

        # 递归处理子节点
        for child in children:
            self._flatten_hierarchy(child, result, path=current_path, depth=depth + 1)

    def _traverse_poco_proxy(self, poco, result: list, max_items: int = 80):
        """
        备用方案：通过Poco代理API遍历UI树。

        :param poco: Poco实例
        :param result: 结果列表
        :param max_items: 最大提取元素数
        """
        try:
            root = poco("*")
            for i, node in enumerate(root):
                if i >= max_items:
                    break
                try:
                    attr = node.attr
                    name = attr("name") or ""
                    text = attr("text") or ""
                    node_type = attr("type") or ""
                    visible = attr("visible")
                    enabled = attr("enabled")
                    pos = attr("pos") or [0, 0]
                    size = attr("size") or [0, 0]
                    clickable = attr("clickable") if hasattr(attr, "__call__") else False

                    if not visible:
                        continue

                    control_type = WIDGET_TYPE_MAP.get(node_type, ControlType.UNKNOWN)
                    cx, cy = pos[0], pos[1]
                    w, h = size[0], size[1]

                    id_str = f"{node_type}|{name}|{text}|{cx:.2f}|{cy:.2f}"
                    element_id = hashlib.md5(id_str.encode()).hexdigest()[:8]

                    elem = UIElement(
                        name=name, text=text, type=node_type,
                        control_type=control_type,
                        bounds={"x": cx - w/2, "y": cy - h/2, "width": w, "height": h},
                        center=(cx, cy),
                        clickable=clickable, enabled=enabled, visible=True,
                        element_id=element_id,
                    )
                    result.append(elem)
                except Exception:
                    continue
        except Exception as e:
            logger.error(f"Poco代理遍历失败: {e}")

    def format_ui_tree_text(self, elements: List[UIElement]) -> str:
        """
        将UI元素列表格式化为可读文本，用于AI提示词。

        :param elements: UI元素列表
        :return: 格式化后的文本
        """
        if not elements:
            return "(UI树不可用 - 仅分析截图)"

        lines = [f"元素总数: {len(elements)}", ""]
        for i, elem in enumerate(elements):
            clickable_mark = "[可点击]" if elem.clickable else ""
            text_part = f' text="{elem.text}"' if elem.text else ""
            lines.append(
                f"[{i}] {elem.type} name=\"{elem.name}\"{text_part} "
                f"center=({elem.center[0]:.3f},{elem.center[1]:.3f}) "
                f"{clickable_mark}"
            )

        return "\n".join(lines)

# -*- encoding=utf8 -*-
"""截图捕获与Poco UI树提取模块。"""

import os
import hashlib
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

# 跳过的系统包名前缀（整个子树不提取）
SKIP_PACKAGES = [
    "com.android.systemui",
]


class UIAnalyzer:
    """UI分析器：从设备中提取并结构化UI信息。"""

    def __init__(self, device_driver, config: ExplorationConfig, l_class: str = ""):
        self.dd = device_driver
        self.config = config
        self.l_class = l_class
        self._screenshot_counter = 0

    def capture_screenshot(self, logdir: str, label: str = "") -> str:
        """捕获当前界面截图并返回文件路径。"""
        self._screenshot_counter += 1
        prefix = self.l_class
        label_part = f"-{label}" if label else ""
        filename = f"{prefix}{label_part}-{self._screenshot_counter}.jpg"
        filepath = os.path.join(logdir, filename)
        try:
            self.dd.driver.snapshot(filepath)
        except Exception as e:
            logger.error(f"截图捕获失败: {e}")
            try:
                from airtest.core.api import snapshot
                snapshot(filepath)
            except Exception as e2:
                logger.error(f"备用截图方式也失败了: {e2}")
                return ""
        return filepath

    def extract_ui_tree(self) -> List[UIElement]:
        """提取Poco UI树并返回扁平化的UIElement列表。"""
        poco = getattr(self.dd, 'poco', None)
        if poco is None:
            logger.warning("Poco不可用，返回空UI树")
            return []

        elements = []
        try:
            hierarchy = poco.agent.hierarchy.dump()
            self._flatten_hierarchy(hierarchy, elements, path="")
        except Exception as e:
            logger.warning(f"UI层级dump失败: {e}")
            try:
                self._traverse_poco_proxy(poco, elements)
            except Exception as e2:
                logger.error(f"备用UI树提取也失败了: {e2}")
        return elements

    def _flatten_hierarchy(self, node: dict, result: list, path: str, depth: int = 0):
        """递归展开Poco层级dump为扁平化的UIElement列表。"""
        payload = node.get("payload", node)
        name = payload.get("name", "")
        package = payload.get("package", "")

        # 跳过系统UI层（整个子树）
        if isinstance(package, bytes):
            package = package.decode("utf-8", errors="ignore")
        for skip_pkg in SKIP_PACKAGES:
            if skip_pkg in name or skip_pkg in package:
                return

        node_type = payload.get("type", "")
        text = payload.get("text", "")
        desc = payload.get("desc", "")
        visible = payload.get("visible", True)
        enabled = payload.get("enabled", True)
        clickable = payload.get("clickable", False)
        touchable = payload.get("touchable", False)
        selected = payload.get("selected", False)
        checked = payload.get("checked", False)
        pos = payload.get("pos", [0, 0])
        size = payload.get("size", [0, 0])

        current_path = f"{path}/{name}" if path else name
        children = node.get("children", [])

        # 跳过不可见元素，但继续递归子节点
        if not visible:
            for child in children:
                self._flatten_hierarchy(child, result, path=current_path, depth=depth + 1)
            return

        # 跳过纯布局容器（无文本、无描述、不可点击）
        is_container = node_type in self.config.skip_element_types
        has_content = text or desc or clickable or touchable
        if is_container and not has_content:
            for child in children:
                self._flatten_hierarchy(child, result, path=current_path, depth=depth + 1)
            return

        # 构建UIElement
        control_type = WIDGET_TYPE_MAP.get(node_type, ControlType.UNKNOWN)
        cx = pos[0] if len(pos) >= 2 else 0
        cy = pos[1] if len(pos) >= 2 else 0
        w = size[0] if len(size) >= 2 else 0
        h = size[1] if len(size) >= 2 else 0

        bounds = {"x": cx - w / 2, "y": cy - h / 2, "width": w, "height": h}
        id_str = f"{node_type}|{name}|{text}|{cx:.2f}|{cy:.2f}"
        element_id = hashlib.md5(id_str.encode()).hexdigest()[:8]

        elem = UIElement(
            name=name,
            text=text,
            desc=desc,
            type=node_type,
            control_type=control_type,
            bounds=bounds,
            center=(cx, cy),
            clickable=clickable or touchable,
            enabled=enabled,
            visible=True,
            selected=selected or checked,
            poco_path=current_path,
            element_id=element_id,
        )
        result.append(elem)

        # 递归子节点
        for child in children:
            self._flatten_hierarchy(child, result, path=current_path, depth=depth + 1)

    @staticmethod
    def _traverse_poco_proxy(poco, result: list):
        """备用方案：通过Poco代理API遍历UI树。"""
        try:
            root = poco("*")
            for node in root:
                try:
                    attr = node.attr
                    name = attr("name") or ""
                    text = attr("text") or ""
                    desc = attr("desc") or ""
                    node_type = attr("type") or ""
                    visible = attr("visible")
                    enabled = attr("enabled")
                    pos = attr("pos") or [0, 0]
                    size = attr("size") or [0, 0]
                    clickable = attr("clickable") if hasattr(attr, "__call__") else False
                    selected = attr("selected") if hasattr(attr, "__call__") else False

                    if not visible:
                        continue
                    # 跳过系统UI
                    skip = False
                    for skip_pkg in SKIP_PACKAGES:
                        if skip_pkg in name:
                            skip = True
                            break
                    if skip:
                        continue

                    control_type = WIDGET_TYPE_MAP.get(node_type, ControlType.UNKNOWN)
                    cx, cy = pos[0], pos[1]
                    w, h = size[0], size[1]

                    id_str = f"{node_type}|{name}|{text}|{cx:.2f}|{cy:.2f}"
                    element_id = hashlib.md5(id_str.encode()).hexdigest()[:8]

                    elem = UIElement(
                        name=name, text=text, desc=desc, type=node_type,
                        control_type=control_type,
                        bounds={"x": cx - w/2, "y": cy - h/2, "width": w, "height": h},
                        center=(cx, cy),
                        clickable=clickable, enabled=enabled, visible=True,
                        selected=selected,
                        element_id=element_id,
                    )
                    result.append(elem)
                except Exception:
                    continue
        except Exception as e:
            logger.error(f"Poco代理遍历失败: {e}")

    @staticmethod
    def format_ui_tree_text(elements: List[UIElement]) -> str:
        """将UI元素列表格式化为可读文本，用于AI提示词。"""
        if not elements:
            return "(UI树不可用 - 仅分析截图)"

        lines = [f"元素总数: {len(elements)}", ""]
        for i, elem in enumerate(elements):
            # 紧凑格式：只输出有内容的字段
            parts = [f"[{i}]"]

            # 类型（简化：去掉android.widget.前缀）
            short_type = elem.type.replace("android.widget.", "").replace("android.view.", "")
            parts.append(short_type)

            if elem.name:
                parts.append(f'name="{elem.name}"')
            if elem.text:
                parts.append(f'text="{elem.text}"')
            if elem.desc:
                parts.append(f'desc="{elem.desc}"')

            parts.append(f"pos=({elem.center[0]:.3f},{elem.center[1]:.3f})")

            tags = []
            if elem.clickable:
                tags.append("可点击")
            if elem.selected:
                tags.append("已选中")
            if tags:
                parts.append(f"[{'|'.join(tags)}]")

            lines.append(" ".join(parts))

        return "\n".join(lines)

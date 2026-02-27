# -*- encoding=utf8 -*-
"""界面指纹识别与去重模块。"""

import hashlib
import logging
from typing import List, Dict

from .models import UIElement, ScreenState

logger = logging.getLogger(__name__)


class ScreenManager:
    """界面状态管理器：负责界面指纹生成、去重、探索进度跟踪。"""

    def __init__(self, similarity_threshold: float = 0.85):
        """
        :param similarity_threshold: 界面相似度阈值
        """
        self.screens: Dict[str, ScreenState] = {}
        self.similarity_threshold = similarity_threshold

    def get_fingerprint(self, elements: List[UIElement]) -> str:
        """
        根据UI树结构生成当前界面的指纹。

        :param elements: UI元素列表
        :return: 界面指纹哈希值
        """
        if not elements:
            return "empty_screen"

        # 基于排序后的元素签名（类型+是否有文本+是否可点击）构建指纹
        signatures = []
        for elem in elements:
            sig = f"{elem.type}|{'T' if elem.text else 'N'}|{'C' if elem.clickable else 'X'}"
            signatures.append(sig)
        signatures.sort()

        combined = "\n".join(signatures)
        return hashlib.md5(combined.encode()).hexdigest()

    def register_screen(
        self,
        fingerprint: str,
        description: str,
        screenshot_path: str,
        elements: List[UIElement],
        step_number: int,
    ) -> bool:
        """
        注册一次界面访问。

        :param fingerprint: 界面指纹
        :param description: 界面描述
        :param screenshot_path: 截图路径
        :param elements: UI元素列表
        :param step_number: 当前步骤编号
        :return: 如果是新界面返回True，重复访问返回False
        """
        if fingerprint in self.screens:
            screen = self.screens[fingerprint]
            screen.visit_count += 1
            screen.last_seen_step = step_number
            return False

        self.screens[fingerprint] = ScreenState(
            fingerprint=fingerprint,
            description=description,
            screenshot_path=screenshot_path,
            elements=elements,
            visit_count=1,
            first_seen_step=step_number,
            last_seen_step=step_number,
        )
        return True

    def mark_element_explored(self, fingerprint: str, element_id: str):
        """
        标记某个界面上的某个元素为已探索。

        :param fingerprint: 界面指纹
        :param element_id: 元素唯一标识
        """
        if fingerprint in self.screens:
            self.screens[fingerprint].explored_elements.add(element_id)

    def get_unexplored_elements(self, fingerprint: str) -> List[UIElement]:
        """
        获取某个界面上尚未探索的可交互元素。

        :param fingerprint: 界面指纹
        :return: 未探索的UI元素列表
        """
        if fingerprint not in self.screens:
            return []
        screen = self.screens[fingerprint]
        return [
            elem for elem in screen.elements
            if elem.element_id not in screen.explored_elements
            and elem.clickable
            and elem.enabled
        ]

    def is_screen_fully_explored(self, fingerprint: str) -> bool:
        """
        检查某个界面的所有可点击元素是否已全部探索。

        :param fingerprint: 界面指纹
        :return: 是否已全部探索
        """
        if fingerprint not in self.screens:
            return False
        screen = self.screens[fingerprint]
        clickable = [e for e in screen.elements if e.clickable and e.enabled]
        if not clickable:
            return True
        return len(screen.explored_elements) >= len(clickable)

    def get_exploration_stats(self) -> dict:
        """
        获取整体探索统计信息。

        :return: 包含唯一界面数、总元素数、已探索元素数、覆盖率的字典
        """
        total_elements = 0
        explored_elements = 0
        for screen in self.screens.values():
            clickable = [e for e in screen.elements if e.clickable and e.enabled]
            total_elements += len(clickable)
            explored_elements += len(screen.explored_elements)

        coverage = explored_elements / total_elements if total_elements > 0 else 0
        return {
            "unique_screens": len(self.screens),
            "total_elements": total_elements,
            "explored_elements": explored_elements,
            "coverage": coverage,
        }

    def get_visited_screen_descriptions(self) -> List[str]:
        """获取所有已访问界面的描述列表"""
        return [s.description for s in self.screens.values()]

    def get_explored_element_names(self, fingerprint: str) -> List[str]:
        """
        获取指定界面上已探索元素的名称列表。

        :param fingerprint: 界面指纹
        :return: 已探索元素名称列表
        """
        if fingerprint not in self.screens:
            return []
        screen = self.screens[fingerprint]
        explored_names = []
        for elem in screen.elements:
            if elem.element_id in screen.explored_elements:
                explored_names.append(elem.text or elem.name or elem.element_id)
        return explored_names

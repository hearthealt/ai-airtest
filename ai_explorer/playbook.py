# -*- encoding=utf8 -*-
"""Playbook 录制与回放模块：记录探索步骤，后续直接回放省去AI调用。"""

import json
import os
import logging
from dataclasses import dataclass, field, asdict
from typing import List, Optional, Dict, Any

logger = logging.getLogger(__name__)


@dataclass
class VerifyCondition:
    """回放时的页面验证条件（纯Poco/UI树检查，不调AI）"""
    has_text: str = ""                           # 页面上存在这个文本
    has_name: str = ""                           # 页面上存在这个name（resourceId关键词）
    has_any_text: List[str] = field(default_factory=list)  # 存在其中任意一个文本
    has_all_text: List[str] = field(default_factory=list)  # 同时存在所有这些文本
    not_has_text: str = ""                       # 页面上不存在这个文本

    def is_empty(self) -> bool:
        return (not self.has_text and not self.has_name
                and not self.has_any_text and not self.has_all_text
                and not self.not_has_text)

    def to_dict(self) -> dict:
        d = {}
        if self.has_text:
            d["has_text"] = self.has_text
        if self.has_name:
            d["has_name"] = self.has_name
        if self.has_any_text:
            d["has_any_text"] = self.has_any_text
        if self.has_all_text:
            d["has_all_text"] = self.has_all_text
        if self.not_has_text:
            d["not_has_text"] = self.not_has_text
        return d

    @classmethod
    def from_dict(cls, d: dict) -> 'VerifyCondition':
        if not d:
            return cls()
        return cls(
            has_text=d.get("has_text", ""),
            has_name=d.get("has_name", ""),
            has_any_text=d.get("has_any_text", []),
            has_all_text=d.get("has_all_text", []),
            not_has_text=d.get("not_has_text", ""),
        )


@dataclass
class PlaybookStep:
    """一条回放步骤"""
    step: int
    action: str                          # close_popup / click_l1 / click_l2 / check / back / discover_l1 / discover_l2
    target_text: str = ""                # Poco文本匹配
    target_name: str = ""                # Poco name匹配（resourceId）
    coordinates: tuple = ()              # 坐标兜底
    l1_name: str = ""                    # 所属L1名称
    description: str = ""                # 步骤描述
    expected_result: str = ""            # 期望结果（check步骤用）
    verify: VerifyCondition = field(default_factory=VerifyCondition)

    def to_dict(self) -> dict:
        d = {
            "step": self.step,
            "action": self.action,
            "description": self.description,
        }
        if self.target_text:
            d["target_text"] = self.target_text
        if self.target_name:
            d["target_name"] = self.target_name
        if self.coordinates:
            d["coordinates"] = list(self.coordinates)
        if self.l1_name:
            d["l1_name"] = self.l1_name
        if self.expected_result:
            d["expected_result"] = self.expected_result
        verify_d = self.verify.to_dict()
        if verify_d:
            d["verify"] = verify_d
        return d

    @classmethod
    def from_dict(cls, d: dict) -> 'PlaybookStep':
        coords = d.get("coordinates", [])
        return cls(
            step=d.get("step", 0),
            action=d.get("action", ""),
            target_text=d.get("target_text", ""),
            target_name=d.get("target_name", ""),
            coordinates=tuple(coords) if coords else (),
            l1_name=d.get("l1_name", ""),
            description=d.get("description", ""),
            expected_result=d.get("expected_result", ""),
            verify=VerifyCondition.from_dict(d.get("verify", {})),
        )


class Playbook:
    """步骤剧本：录制、保存、加载。每个应用+模式一个文件。"""

    def __init__(self, app_package: str, playbook_dir: str, mode: int = 0):
        self.app_package = app_package
        self.playbook_dir = playbook_dir
        self.mode = mode
        # 文件名区分模式：app_package_mode0.json / app_package_mode1.json
        self.file_path = os.path.join(playbook_dir, f"{app_package}_mode{mode}.json")
        self.steps: List[PlaybookStep] = []
        self.menu_structure: dict = {}
        self.version: int = 1

    def record_step(self, step: PlaybookStep):
        """录制一步"""
        self.steps.append(step)

    def save(self):
        """保存到JSON文件"""
        os.makedirs(self.playbook_dir, exist_ok=True)
        mode_label = "功能测试" if self.mode == 1 else "阻断测试"
        data = {
            "app_package": self.app_package,
            "mode": self.mode,
            "mode_label": mode_label,
            "version": self.version,
            "menu_structure": self.menu_structure,
            "steps": [s.to_dict() for s in self.steps],
        }
        with open(self.file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        logger.info(f"Playbook已保存: {self.file_path} ({len(self.steps)}步)")

    def load(self) -> bool:
        """从文件加载，返回是否成功"""
        if not os.path.exists(self.file_path):
            return False
        try:
            with open(self.file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.version = data.get("version", 1)
            self.menu_structure = data.get("menu_structure", {})
            self.steps = [PlaybookStep.from_dict(s) for s in data.get("steps", [])]
            logger.info(f"Playbook已加载: {self.file_path} ({len(self.steps)}步)")
            return True
        except Exception as e:
            logger.error(f"Playbook加载失败: {e}")
            return False

    def exists(self) -> bool:
        """playbook文件是否存在"""
        return os.path.exists(self.file_path)

    def update_step(self, index: int, step: PlaybookStep):
        """更新某一步"""
        if 0 <= index < len(self.steps):
            self.steps[index] = step


class PlaybackVerifier:
    """回放时的页面状态验证器，纯Poco操作，不调AI"""

    def __init__(self, device_driver):
        self.poco = getattr(device_driver, 'poco', None)

    def verify(self, condition: VerifyCondition) -> bool:
        """验证当前页面是否满足条件。无条件时直接通过。"""
        if not condition or condition.is_empty():
            return True

        if condition.has_text:
            if not self._text_exists(condition.has_text):
                return False

        if condition.has_name:
            if not self._name_exists(condition.has_name):
                return False

        if condition.has_any_text:
            if not any(self._text_exists(t) for t in condition.has_any_text):
                return False

        if condition.has_all_text:
            if not all(self._text_exists(t) for t in condition.has_all_text):
                return False

        if condition.not_has_text:
            if self._text_exists(condition.not_has_text):
                return False

        return True

    def _text_exists(self, text: str) -> bool:
        """Poco文本查找"""
        if not self.poco:
            return False
        try:
            return self.poco(text=text).exists()
        except Exception:
            return False

    def _name_exists(self, name: str) -> bool:
        """Poco name关键词查找"""
        if not self.poco:
            return False
        try:
            return self.poco(nameMatches=f".*{name}.*").exists()
        except Exception:
            return False

    def check_unknown_popup(self) -> bool:
        """检查是否出现了未知弹窗（通过常见关闭按钮name关键词）"""
        if not self.poco:
            return False
        close_keywords = ('close', 'dismiss', 'cancel')
        for kw in close_keywords:
            try:
                if self.poco(nameMatches=f".*{kw}.*").exists():
                    return True
            except Exception:
                pass
        return False

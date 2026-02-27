# -*- encoding=utf8 -*-
"""结构化探索日志模块。"""

import json
import os
import logging

from .models import ExplorationStep

logger = logging.getLogger(__name__)


class ExplorationLogger:
    """探索日志记录器：将每一步探索记录为结构化的JSON行。"""

    def __init__(self, logdir: str, l_class: str = ""):
        """
        :param logdir: 日志输出目录
        :param l_class: 小类ID（用于文件命名）
        """
        self.logdir = logdir
        self.l_class = l_class
        prefix = l_class
        self.log_file = os.path.join(logdir, f"{prefix}.jsonl")

        # 为ai_explorer包设置文件日志处理器
        log_path = os.path.join(logdir, f"{prefix}_detail.log")
        file_handler = logging.FileHandler(log_path, encoding="utf-8")
        file_handler.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
        ))
        pkg_logger = logging.getLogger("ai_explorer")
        pkg_logger.addHandler(file_handler)
        pkg_logger.setLevel(logging.DEBUG)

    def log_step(self, step: ExplorationStep):
        """
        将一条步骤记录追加写入JSONL日志文件。

        :param step: 探索步骤对象
        """
        record = {
            "step": step.step_number,
            "timestamp": step.timestamp,
            "screenshot": os.path.basename(step.screenshot_path),
            "screen": step.screen_description,
            "fingerprint": step.screen_fingerprint,
            "action": {
                "type": step.action_taken.action.value,
                "target": (step.action_taken.target_element.text or
                           step.action_taken.target_element.name)
                          if step.action_taken.target_element else None,
                "coordinates": step.action_taken.coordinates,
                "reasoning": step.action_taken.reasoning,
            },
            "result": step.action_result,
            "duration_ms": step.duration_ms,
        }

        with open(self.log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

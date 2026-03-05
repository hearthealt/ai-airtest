# -*- encoding=utf8 -*-
"""AI视觉语言模型客户端，封装qwen3-vl-plus的OpenAI兼容API调用。"""

import base64
import io
import json
import re
import time
import logging
from openai import OpenAI
from PIL import Image

from .api_key_helper import ensure_api_key, refresh_if_needed, on_auth_error
from .config import AIConfig
from .prompts import get_discover_prompt, get_test_prompt

logger = logging.getLogger(__name__)


class AIClient:
    """
    AI视觉模型客户端。
    通过OpenAI兼容API向qwen3-vl-plus发送截图+UI树信息，解析结构化JSON响应。
    """

    def __init__(self, config: AIConfig):
        self.config = config

        api_key = ensure_api_key(config)

        self.client = OpenAI(
            api_key=api_key,
            base_url=config.api_base_url,
            timeout=config.timeout,
        )
        self._call_count = 0       # API调用次数
        self._total_tokens = 0     # 累计消耗token数

    def _refresh_key_if_needed(self):
        """iflow API Key 快过期时自动刷新"""
        new_key = refresh_if_needed(self.config)
        if new_key:
            self.client.api_key = new_key

    def _handle_auth_error(self):
        """AI调用认证失败时重新获取key"""
        new_key = on_auth_error(self.config)
        if new_key:
            self.client.api_key = new_key
            return True
        return False

    # ==================== 统一AI循环调用 ====================

    def discover_call(self, screenshot_path: str, ui_tree_text: str,
                      history: str, login_config: str = "",
                      partial_menu: str = "") -> dict:
        """发现阶段AI调用：识别菜单结构 + 处理障碍"""
        system_prompt = get_discover_prompt()
        parts = ["分析当前截图，识别应用的导航菜单结构。"]
        if login_config:
            parts.append(f"\n## 登录配置\n{login_config}")
        if partial_menu:
            parts.append(f"\n## 已发现的菜单\n{partial_menu}")
        if history:
            parts.append(f"\n## 操作历史\n{history}")
        parts.append(f"\n## UI层级结构\n{ui_tree_text}")
        user_prompt = "\n".join(parts)
        return self._call_ai_raw(screenshot_path, system_prompt, user_prompt)

    def test_call(self, screenshot_path: str, ui_tree_text: str,
                  target: str, mode: int, history: str,
                  tested_summary: str = "", login_config: str = "") -> dict:
        """测试阶段AI调用：判断页面状态 + 处理障碍"""
        system_prompt = get_test_prompt(mode)
        parts = [f"刚点击了'{target}'进入了这个页面。检查页面状态。"]
        if login_config:
            parts.append(f"\n## 登录配置\n{login_config}")
        if tested_summary:
            parts.append(f"\n## 已完成的测试\n{tested_summary}")
        if history:
            parts.append(f"\n## 操作历史\n{history}")
        parts.append(f"\n## UI层级结构\n{ui_tree_text}")
        user_prompt = "\n".join(parts)
        return self._call_ai_raw(screenshot_path, system_prompt, user_prompt)

    # ==================== 底层通用方法 ====================

    def _call_ai_raw(self, screenshot_path: str, system_prompt: str, user_prompt: str) -> dict:
        """通用AI调用，返回解析后的JSON dict。失败时返回空dict。"""
        image_b64 = self._encode_image(screenshot_path)
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": [
                {"type": "image_url", "image_url": {
                    "url": f"data:image/jpeg;base64,{image_b64}"
                }},
                {"type": "text", "text": user_prompt},
            ]},
        ]

        self._refresh_key_if_needed()

        for attempt in range(self.config.max_retries):
            try:
                response = self.client.chat.completions.create(
                    model=self.config.model,
                    messages=messages,
                    max_tokens=self.config.max_tokens,
                    temperature=self.config.temperature,
                )
                self._call_count += 1

                # iflow key失效: status=434, choices=None
                if not response.choices:
                    msg = getattr(response, 'msg', '') or ''
                    logger.warning(f"AI返回无效响应: status={getattr(response, 'status', '?')}, msg={msg}")
                    if 'apiKey' in msg or 'apikey' in msg.lower():
                        if self._handle_auth_error():
                            continue
                    raise ValueError(f"AI返回空choices: {msg}")

                raw_text = response.choices[0].message.content
                if response.usage:
                    self._total_tokens += response.usage.total_tokens
                return self._parse_raw_json(raw_text)
            except Exception as e:
                logger.warning(f"AI API调用第{attempt+1}次尝试失败: {e}")
                if attempt < self.config.max_retries - 1:
                    time.sleep(2 ** attempt)
                else:
                    logger.error(f"AI API调用在{self.config.max_retries}次尝试后全部失败")
                    return {}

    @staticmethod
    def _parse_raw_json(raw_text: str) -> dict:
        """解析AI响应为原始JSON dict"""
        text = raw_text.strip()
        if text.startswith("```json"):
            text = text[7:]
        elif text.startswith("```"):
            text = text[3:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            match = re.search(r'\{[\s\S]*\}', text)
            if match:
                try:
                    return json.loads(match.group())
                except json.JSONDecodeError:
                    pass
            logger.warning(f"AI响应JSON解析失败: {raw_text[:200]}")
            return {}

    def _encode_image(self, image_path: str) -> str:
        """将图片压缩、缩放后编码为base64字符串"""
        max_size = self.config.image_max_size
        quality = self.config.image_quality

        try:
            img = Image.open(image_path)
            w, h = img.size
            if max(w, h) > max_size:
                ratio = max_size / max(w, h)
                new_w, new_h = int(w * ratio), int(h * ratio)
                img = img.resize((new_w, new_h), Image.LANCZOS)

            if img.mode != "RGB":
                img = img.convert("RGB")
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=quality)
            b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
            return b64
        except Exception as e:
            logger.warning(f"图片压缩失败，使用原图: {e}")
            with open(image_path, "rb") as f:
                return base64.b64encode(f.read()).decode("utf-8")

    @property
    def stats(self) -> dict:
        """获取API调用统计信息"""
        return {
            "api_calls": self._call_count,
            "total_tokens": self._total_tokens,
        }

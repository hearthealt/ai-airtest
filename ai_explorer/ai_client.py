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
from .models import (
    AIResponse, AIDecision, UIElement,
    ActionType, ControlType, Priority,
)
from .prompts import (
    get_system_prompt, get_user_prompt,
    get_discover_l1_system_prompt, get_discover_l2_system_prompt,
    get_block_check_system_prompt, get_function_check_system_prompt,
    get_login_system_prompt,
)

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

    def analyze_screen(
        self,
        screenshot_path: str,
        ui_tree_text: str,
        exploration_context: str,
        explored_elements: list,
    ) -> AIResponse:
        """
        发送截图+UI树给AI进行界面分析。

        :param screenshot_path: 当前截图文件路径
        :param ui_tree_text: 格式化的Poco UI树文本
        :param exploration_context: 探索上下文描述
        :param explored_elements: 已探索元素名称列表
        :return: 解析后的AI响应
        """
        image_b64 = self._encode_image(screenshot_path)

        system_prompt = get_system_prompt()
        user_prompt = get_user_prompt(
            ui_tree_text=ui_tree_text,
            exploration_context=exploration_context,
            explored_elements=explored_elements,
        )

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
                            continue  # key已刷新，立即重试
                    raise ValueError(f"AI返回空choices: {msg}")

                raw_text = response.choices[0].message.content
                if response.usage:
                    self._total_tokens += response.usage.total_tokens
                return self._parse_response(raw_text)

            except Exception as e:
                logger.warning(f"AI API调用第{attempt+1}次尝试失败: {e}")
                if attempt < self.config.max_retries - 1:
                    time.sleep(2 ** attempt)
                else:
                    logger.error(f"AI API调用在{self.config.max_retries}次尝试后全部失败")
                    return AIResponse(
                        screen_description="AI API调用失败",
                        detected_elements=[],
                        recommended_actions=[AIDecision(
                            action=ActionType.BACK,
                            priority=Priority.LOW,
                            reasoning="AI API调用失败，回退执行返回操作",
                        )],
                        raw_response=str(e),
                    )

    def discover_l1_menus(self, screenshot_path: str, ui_tree_text: str) -> dict:
        """调用AI识别底部导航栏的L1菜单项，返回原始JSON dict"""
        system_prompt = get_discover_l1_system_prompt()
        user_prompt = f"分析截图，识别底部导航栏的所有一级菜单项。\n\n## UI层级结构\n{ui_tree_text}"
        return self._call_ai_raw(screenshot_path, system_prompt, user_prompt)

    def discover_l2_tabs(self, screenshot_path: str, ui_tree_text: str, l1_name: str) -> dict:
        """调用AI识别当前L1页面的顶部Tab栏L2标签，返回原始JSON dict"""
        system_prompt = get_discover_l2_system_prompt()
        user_prompt = f"当前在一级页面'{l1_name}'。分析截图，识别顶部标签栏的所有二级Tab。\n\n## UI层级结构\n{ui_tree_text}"
        return self._call_ai_raw(screenshot_path, system_prompt, user_prompt)

    def check_block_status(self, screenshot_path: str, ui_tree_text: str, target_name: str, mode: int = 0) -> dict:
        """调用AI检查页面状态，返回原始JSON dict。mode=0阻断检查，mode=1功能检查"""
        if mode == 1:
            system_prompt = get_function_check_system_prompt()
            user_prompt = (
                f"你刚刚点击了'{target_name}'进入了这个页面。\n"
                f"请仔细检查：这个页面是否正常加载了真实的业务数据？\n"
                f"还是出现了错误/白屏/崩溃？\n"
                f"注意：弹窗（权限/广告等）不算页面内容，忽略弹窗看底下的页面。\n\n"
                f"## UI层级结构\n{ui_tree_text}"
            )
        else:
            system_prompt = get_block_check_system_prompt()
            user_prompt = (
                f"你刚刚点击了'{target_name}'进入了这个页面。\n"
                f"请仔细检查：这个页面是否被阻断了（加载失败/没有数据/报错）？\n"
                f"还是数据正常加载出来了？\n"
                f"注意：不是所有应用都有L1/L2导航，不要因为看不到L1/L2就判定阻断失败。\n"
                f"注意：如果除了弹窗之外页面主体进不去，只有“加载中”/转圈/空白/灰色占位，也应判定为阻断成功。\n"
                f"注意：弹窗（权限/广告等）不算页面内容，忽略弹窗看底下的页面。\n\n"
                f"## UI层级结构\n{ui_tree_text}"
            )
        return self._call_ai_raw(screenshot_path, system_prompt, user_prompt)

    def analyze_login_screen(self, screenshot_path: str, ui_tree_text: str, login_method: str = "password", actions_done_text: str = "") -> dict:
        """调用AI分析登录界面，返回下一步操作"""
        system_prompt = get_login_system_prompt()
        user_prompt = (
            f"分析当前截图中的登录界面，告诉我下一步该做什么。\n"
            f"期望的登录方式: {login_method}（密码登录=password，验证码登录=sms，邮箱登录=email）\n\n"
            f"## 已完成的操作\n{actions_done_text or '  (暂无，这是第一步)'}\n\n"
            f"## UI层级结构\n{ui_tree_text}"
        )
        return self._call_ai_raw(screenshot_path, system_prompt, user_prompt)

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
        """将图片压缩、缩放后编码为base64字符串，减少API传输体积"""
        max_size = self.config.image_max_size
        quality = self.config.image_quality

        try:
            img = Image.open(image_path)
            # 按最大边长等比缩放
            w, h = img.size
            if max(w, h) > max_size:
                ratio = max_size / max(w, h)
                new_w, new_h = int(w * ratio), int(h * ratio)
                img = img.resize((new_w, new_h), Image.LANCZOS)

            # 转为RGB（去掉alpha通道）再JPEG压缩
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

    def _parse_response(self, raw_text: str) -> AIResponse:
        """解析AI的JSON响应为AIResponse对象"""
        text = raw_text.strip()
        # 去除markdown代码块标记
        if text.startswith("```json"):
            text = text[7:]
        elif text.startswith("```"):
            text = text[3:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()

        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            logger.warning("AI响应JSON解析失败，尝试正则提取")
            match = re.search(r'\{[\s\S]*\}', text)
            if match:
                try:
                    data = json.loads(match.group())
                except json.JSONDecodeError:
                    return self._fallback_response(raw_text)
            else:
                return self._fallback_response(raw_text)

        return self._build_response(data, raw_text)

    def _build_response(self, data: dict, raw_text: str) -> AIResponse:
        """从解析后的JSON字典构建AIResponse对象"""
        # 解析检测到的元素
        elements = []
        for elem_data in data.get("elements", []):
            try:
                elem = UIElement(
                    name=elem_data.get("name", ""),
                    text=elem_data.get("text", ""),
                    type=elem_data.get("type", ""),
                    control_type=self._safe_enum(ControlType, elem_data.get("control_type", "unknown"), ControlType.UNKNOWN),
                    bounds=elem_data.get("bounds", {}),
                    center=tuple(elem_data.get("center", (0, 0))),
                    clickable=elem_data.get("clickable", False),
                    enabled=elem_data.get("enabled", True),
                    visible=elem_data.get("visible", True),
                    poco_path=elem_data.get("poco_path"),
                )
                elements.append(elem)
            except Exception as e:
                logger.warning(f"解析元素失败: {e}")

        # 解析推荐操作
        actions = []
        for action_data in data.get("actions", []):
            try:
                action = AIDecision(
                    action=self._safe_enum(ActionType, action_data.get("action", "click"), ActionType.CLICK),
                    coordinates=tuple(action_data["coordinates"]) if action_data.get("coordinates") else None,
                    text_input=action_data.get("text_input"),
                    swipe_direction=action_data.get("swipe_direction"),
                    priority=self._safe_enum(Priority, action_data.get("priority", 3), Priority.MEDIUM),
                    reasoning=action_data.get("reasoning", ""),
                    confidence=float(action_data.get("confidence", 0.5)),
                    is_popup=bool(action_data.get("is_popup", False)),
                )
                # 关联目标元素（通过索引）
                elem_idx = action_data.get("element_index")
                if elem_idx is not None and 0 <= elem_idx < len(elements):
                    action.target_element = elements[elem_idx]
                actions.append(action)
            except Exception as e:
                logger.warning(f"解析操作失败: {e}")

        # 按优先级排序（优先级越高越靠前，置信度越高越靠前）
        actions.sort(key=lambda a: (a.priority.value, -a.confidence))

        return AIResponse(
            screen_description=data.get("screen_description", ""),
            detected_elements=elements,
            recommended_actions=actions,
            is_error_screen=data.get("is_error_screen", False),
            is_loading=data.get("is_loading", False),
            error_description=data.get("error_description", ""),
            is_duplicate_screen=data.get("is_duplicate_screen", False),
            raw_response=raw_text,
        )

    @staticmethod
    def _fallback_response(raw_text: str) -> AIResponse:
        """解析失败时返回安全的回退响应"""
        return AIResponse(
            screen_description="AI响应解析失败",
            detected_elements=[],
            recommended_actions=[AIDecision(
                action=ActionType.BACK,
                priority=Priority.LOW,
                reasoning="AI响应解析失败，回退执行返回操作",
            )],
            raw_response=raw_text,
        )

    @staticmethod
    def _safe_enum(enum_cls, value, default):
        """安全地将值转换为枚举成员，失败时返回默认值"""
        try:
            return enum_cls(value)
        except (ValueError, KeyError):
            return default

    @property
    def stats(self) -> dict:
        """获取API调用统计信息"""
        return {
            "api_calls": self._call_count,
            "total_tokens": self._total_tokens,
        }

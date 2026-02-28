# -*- encoding=utf8 -*-
"""iflow平台 API Key 自动管理：获取、过期检测、自动刷新（多进程安全）。"""

import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, Any

import requests

from .config import AIConfig

logger = logging.getLogger(__name__)

_PLATFORM_URL = "https://platform.iflow.cn"
_API_KEY_URL = f"{_PLATFORM_URL}/api/openapi/apikey"

# 过期前多少分钟触发刷新
_REFRESH_AHEAD_MINUTES = 20


class IflowKeyManager:
    """
    iflow平台 API Key 管理器（多进程安全）。

    刷新策略：
    1. 快过期时先 GET 获取最新 key（别的进程可能已经重置了）
    2. GET 回来的 key 仍然过期 → 才 POST 重置
    3. AI调用报认证失败 → 触发一次 GET 刷新（别的进程可能重置了key）
    """

    def __init__(self, cookies: Dict[str, str], name: str = ""):
        self.name = name
        self.cookies = cookies
        self.api_key: Optional[str] = None
        self.expire_time: Optional[datetime] = None

    def _request(self, method: str, **kwargs) -> Optional[requests.Response]:
        for attempt in range(3):
            try:
                return requests.request(
                    method, _API_KEY_URL,
                    cookies=self.cookies, timeout=30, **kwargs
                )
            except requests.RequestException as e:
                logger.warning(f"iflow请求失败 (第{attempt + 1}次): {e}")
                if attempt == 2:
                    raise
        return None

    def _parse_result(self, resp: Optional[requests.Response]) -> Dict[str, Any]:
        if not resp or resp.status_code != 200:
            status = resp.status_code if resp else "无响应"
            return {"success": False, "error": f"HTTP {status}"}

        data = resp.json()
        if not data.get("success"):
            return {"success": False, "error": data.get("message", "未知错误")}

        api_data = data.get("data", {})
        self.api_key = api_data.get("apiKey") or api_data.get("apiKeyMask")
        expire_str = api_data.get("expireTime")
        if expire_str:
            try:
                self.expire_time = datetime.strptime(expire_str, "%Y-%m-%d %H:%M")
            except ValueError:
                pass

        return {
            "success": True,
            "api_key": self.api_key,
            "expire_time": self.expire_time,
            "has_expired": api_data.get("hasExpired", False),
        }

    def needs_refresh(self) -> bool:
        """是否需要刷新（过期或即将过期）"""
        if self.expire_time is None:
            return True
        return datetime.now() >= self.expire_time - timedelta(minutes=_REFRESH_AHEAD_MINUTES)

    def is_truly_expired(self) -> bool:
        """是否真的已经过期（不含提前量）"""
        if self.expire_time is None:
            return True
        return datetime.now() >= self.expire_time

    def get_key(self) -> Dict[str, Any]:
        """GET 获取当前 key（不会使其他进程的 key 失效）"""
        try:
            return self._parse_result(self._request("GET"))
        except Exception as e:
            return {"success": False, "error": str(e)}

    def create_key(self) -> Dict[str, Any]:
        """POST 重置 key（会使旧 key 失效，其他进程需要重新获取）"""
        try:
            return self._parse_result(self._request("POST", json={"name": self.name}))
        except Exception as e:
            return {"success": False, "error": str(e)}

    def ensure_valid_key(self) -> Dict[str, Any]:
        """
        确保有有效的 key（多进程安全）：
        1. 先 GET 最新 key（可能别的进程已经重置了）
        2. 如果 GET 到的 key 仍然过期，才 POST 重置
        """
        result = self.get_key()
        if result.get("success") and not result.get("has_expired") and not self.is_truly_expired():
            return result

        logger.info("iflow API Key已过期或获取失败，正在重置...")
        return self.create_key()


# ==================== 模块级接口 ====================

_manager: Optional[IflowKeyManager] = None


def is_iflow_url(api_base_url: str) -> bool:
    return "iflow.cn" in (api_base_url or "")


def ensure_api_key(config: AIConfig) -> str:
    """
    启动时获取有效的 iflow API Key。

    :param config: AI配置
    :return: 有效的 API Key
    """
    if not is_iflow_url(config.api_base_url):
        return config.api_key

    if not config.iflow_cookies:
        logger.warning("iflow地址但未配置iflow_cookies，使用配置中的api_key")
        return config.api_key

    global _manager
    try:
        if _manager is None:
            _manager = IflowKeyManager(
                cookies=config.iflow_cookies,
                name=config.iflow_name,
            )
        result = _manager.ensure_valid_key()
        if result.get("success") and result.get("api_key"):
            logger.info(f"iflow API Key获取成功，过期时间: {result.get('expire_time')}")
            return result["api_key"]
        logger.warning(f"iflow API Key获取失败: {result.get('error')}，使用配置中的api_key")
    except Exception as e:
        logger.warning(f"iflow API Key管理异常: {e}，使用配置中的api_key")
    return config.api_key


def refresh_if_needed(config: AIConfig) -> str:
    """
    每次AI调用前检查：快过期则刷新。
    内部只做一次时间比较，没到刷新时间不发请求。

    :return: 新key（需要更新）或空字符串（不需要）
    """
    if not is_iflow_url(config.api_base_url) or _manager is None:
        return ""

    if not _manager.needs_refresh():
        return ""

    try:
        logger.info("iflow API Key即将过期，正在刷新...")
        result = _manager.ensure_valid_key()
        if result.get("success") and result.get("api_key"):
            logger.info(f"API Key已刷新，新过期时间: {result.get('expire_time')}")
            return result["api_key"]
        logger.error(f"API Key刷新失败: {result.get('error')}")
    except Exception as e:
        logger.error(f"API Key刷新异常: {e}")
    return ""


def on_auth_error(config: AIConfig) -> str:
    """
    AI调用报认证失败时调用：GET最新key（别的进程可能已经重置了）。

    :return: 新key或空字符串
    """
    if not is_iflow_url(config.api_base_url) or _manager is None:
        return ""

    try:
        logger.info("AI认证失败，尝试重新获取iflow API Key...")
        result = _manager.ensure_valid_key()
        if result.get("success") and result.get("api_key"):
            logger.info(f"重新获取API Key成功，过期时间: {result.get('expire_time')}")
            return result["api_key"]
    except Exception as e:
        logger.error(f"重新获取API Key异常: {e}")
    return ""

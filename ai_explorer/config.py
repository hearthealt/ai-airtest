# -*- encoding=utf8 -*-
"""AI探索框架的配置模块。"""

from dataclasses import dataclass, field
from typing import List
import json
import os


@dataclass
class AIConfig:
    """AI模型配置"""
    api_base_url: str = ""
    api_key: str = ""
    model: str = "qwen3-vl-plus"
    max_tokens: int = 4096
    temperature: float = 0.3
    timeout: int = 180
    image_max_size: int = 1280
    image_quality: int = 70
    max_retries: int = 3


@dataclass
class ExplorationConfig:
    """探索行为配置"""
    max_steps: int = 200                   # 最大探索步数
    max_duration_seconds: int = 1800       # 最大探索时长（秒），默认30分钟
    max_consecutive_duplicates: int = 5    # 连续重复界面次数上限（超过则停止）
    max_errors: int = 10                   # 连续错误次数上限
    coverage_target: float = 0.8           # 目标覆盖率（0-1）

    strategy: str = "priority_bfs"         # 探索策略：priority_bfs / bfs / dfs / random
    explore_depth: int = 10                # 最大导航深度

    action_delay: float = 2.0             # 每次操作后等待时间（秒）
    screenshot_delay: float = 10.0         # 截图前等待时间（秒）

    similarity_threshold: float = 0.85    # 界面相似度阈值（用于去重）

    # 跳过的布局容器类型（不直接交互）
    skip_element_types: List[str] = field(default_factory=lambda: [
        "android.view.View",
        "android.widget.FrameLayout",
        "android.widget.LinearLayout",
        "android.widget.RelativeLayout",
    ])


@dataclass
class AppConfig:
    """目标应用配置"""
    package_name: str = ""           # 应用包名
    app_name: str = ""               # 应用名称
    platform: str = "Android"        # 平台：Android / IOS / Windows
    device_uuid: str = ""            # 设备UUID
    device_uri: str = ""             # 设备URI（iOS远程设备用）
    poco_type: str = ""              # Poco类型
    window_name: str = ""            # 窗口名称（Windows应用用）
    login_required: bool = False     # 是否需要登录
    login_credentials: dict = field(default_factory=dict)  # 登录凭据


@dataclass
class RouterConfig:
    """路由器阻断规则配置"""
    router_host: str = ""
    router_port: int = 22
    router_user: str = ""
    router_pwd: str = ""
    router_enable_pwd: str = ""
    extend_device: str = "t1"


@dataclass
class Config:
    """主配置类，整合所有子配置。"""
    ai: AIConfig = field(default_factory=AIConfig)
    exploration: ExplorationConfig = field(default_factory=ExplorationConfig)
    app: AppConfig = field(default_factory=AppConfig)
    router: RouterConfig = field(default_factory=RouterConfig)
    l_class: str = ""                       # 小类ID（阻断规则索引）
    output_dir: str = r"E:\tmp\explore"    # 输出根目录
    logdir: str = ""                        # 实际日志目录（运行时自动生成，无需手动设置）
    use_nav_cache: bool = True              # 是否启用导航缓存

    def build_router_info(self) -> dict:
        """根据配置构建路由器信息字典"""
        return {
            "router_host": self.router.router_host,
            "router_port": self.router.router_port,
            "router_user": self.router.router_user,
            "router_pwd": self.router.router_pwd,
            "router_enable_pwd": self.router.router_enable_pwd,
            "router_index": self.l_class,
            "extend_device": self.router.extend_device,
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'Config':
        """从字典创建配置"""
        cfg = cls()
        if "ai" in data:
            cfg.ai = AIConfig(**data["ai"])
        if "exploration" in data:
            cfg.exploration = ExplorationConfig(**data["exploration"])
        if "app" in data:
            cfg.app = AppConfig(**data["app"])
        if "router" in data:
            cfg.router = RouterConfig(**data["router"])
        cfg.l_class = data.get("l_class", "")
        cfg.output_dir = data.get("output_dir", r"E:\tmp\explore")
        cfg.logdir = data.get("logdir", "")
        cfg.use_nav_cache = data.get("use_nav_cache", True)
        return cfg

    @classmethod
    def from_json_file(cls, path: str) -> 'Config':
        """从JSON文件加载配置"""
        with open(path, "r", encoding="utf-8") as f:
            return cls.from_dict(json.load(f))

    @classmethod
    def from_yaml_file(cls, path: str) -> 'Config':
        """从YAML文件加载配置"""
        import yaml
        with open(path, "r", encoding="utf-8") as f:
            return cls.from_dict(yaml.safe_load(f))

    @classmethod
    def load(cls, config_path: str = "") -> 'Config':
        """自动查找并加载配置文件。优先级：指定路径 > 项目根目录config.yaml"""
        if config_path and os.path.exists(config_path):
            return cls.from_yaml_file(config_path)

        # 自动查找项目根目录的config.yaml
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        default_path = os.path.join(root, "config.yaml")
        if os.path.exists(default_path):
            return cls.from_yaml_file(default_path)

        raise FileNotFoundError(
            f"找不到配置文件。请复制 config.yaml.example 为 config.yaml 并填入实际值。"
        )

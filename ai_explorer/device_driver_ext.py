# -*- encoding=utf8 -*-
"""AIDeviceDriver：在现有DeviceDriver基础上扩展AI探索能力。"""

import os
import logging

from .config import Config
from .exploration_engine import ExplorationEngine
from .report_generator import ReportGenerator
from .models import ExplorationResult

logger = logging.getLogger(__name__)


class AIDeviceDriver:
    """
    AI增强的设备驱动器。
    包装现有的DeviceDriver（或PcDeviceDriver），添加AI驱动的探索测试功能。

    使用示例::

        from common import DeviceDriver
        dd = DeviceDriver(device_info, logdir)

        from ai_explorer.device_driver_ext import AIDeviceDriver
        ai_dd = AIDeviceDriver(dd, config)
        result = ai_dd.explore("com.example.app")
        ai_dd.generate_report(result)
    """

    def __init__(self, device_driver, config: Config):
        """
        :param device_driver: 已初始化的DeviceDriver或PcDeviceDriver实例
        :param config: 主配置对象
        """
        self.dd = device_driver
        self.config = config

        if not config.logdir:
            config.logdir = getattr(device_driver, 'logdir', os.path.join(os.getcwd(), 'explore_logs'))
        os.makedirs(config.logdir, exist_ok=True)

        self.engine = ExplorationEngine(device_driver, config)

    def explore(self, app_package: str = "") -> ExplorationResult:
        """
        运行AI驱动的探索性测试。

        :param app_package: 应用包名（为空则跳过启动，直接从当前界面开始）
        :return: 探索结果对象
        """
        result = self.engine.run(app_package)

        successes = sum(1 for i in result.issues_found if i["type"] == "block_success")
        failures = sum(1 for i in result.issues_found if i["type"] == "block_failure")
        duration = result.end_time - result.start_time
        logger.info(
            f"探索完成: {result.total_steps}步, "
            f"{result.unique_screens}个L1, "
            f"{result.total_elements_found}个菜单项, "
            f"已测{result.elements_interacted}个, "
            f"覆盖率{result.coverage_percentage:.1f}%, "
            f"阻断成功{successes}个, 失败{failures}个, "
            f"耗时{duration:.0f}秒"
        )
        return result

    def generate_report(self, result: ExplorationResult, output_dir: str = "") -> str:
        """
        根据探索结果生成HTML报告。

        :param result: 探索结果对象
        :param output_dir: 报告输出目录（为空则使用日志目录）
        :return: 生成的报告文件路径
        """
        output_dir = output_dir or self.config.logdir
        generator = ReportGenerator()
        return generator.generate_html(result, output_dir, self.config.l_class)

    def ai_click(self, description: str) -> bool:
        """
        AI辅助点击：用自然语言描述要点击的目标，AI自动识别并点击。

        :param description: 点击目标的自然语言描述
        :return: 点击是否成功

        使用示例::

            ai_dd.ai_click("登录按钮")
            ai_dd.ai_click("底部的设置标签")
        """
        from .ai_client import AIClient
        from .ui_analyzer import UIAnalyzer
        from .action_executor import ActionExecutor

        ui = UIAnalyzer(self.dd, self.config.exploration)
        screenshot = ui.capture_screenshot(self.config.logdir, "ai_click")
        elements = ui.extract_ui_tree()
        ui_text = ui.format_ui_tree_text(elements)

        client = AIClient(self.config.ai)
        response = client.analyze_screen(
            screenshot_path=screenshot,
            ui_tree_text=ui_text,
            exploration_context=f"用户想要点击: {description}",
            explored_elements=[],
        )

        if response.recommended_actions:
            executor = ActionExecutor(self.dd, self.config.exploration)
            result = executor.execute(response.recommended_actions[0])
            return result == "success"
        return False

    def ai_assert(self, description: str) -> bool:
        """
        AI辅助断言：用自然语言描述期望在界面上看到的内容，AI判断是否满足。

        :param description: 期望界面状态的自然语言描述
        :return: 断言是否通过

        使用示例::

            ai_dd.ai_assert("显示了登录错误提示")
            ai_dd.ai_assert("已经进入了首页")
        """
        from .ai_client import AIClient
        from .ui_analyzer import UIAnalyzer

        ui = UIAnalyzer(self.dd, self.config.exploration)
        screenshot = ui.capture_screenshot(self.config.logdir, "ai_assert")
        elements = ui.extract_ui_tree()
        ui_text = ui.format_ui_tree_text(elements)

        client = AIClient(self.config.ai)
        response = client.analyze_screen(
            screenshot_path=screenshot,
            ui_tree_text=ui_text,
            exploration_context=f"断言检查: 验证当前界面是否满足 '{description}'",
            explored_elements=[],
        )
        return not response.is_error_screen

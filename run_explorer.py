# -*- encoding=utf8 -*-
"""
AI驱动的探索性UI测试 - 主入口

在IDEA中直接运行本文件即可启动探索测试。
"""
import os
import logging
import warnings

# ====== 必须在所有业务import之前：禁用第三方日志和警告 ======
warnings.filterwarnings("ignore")


class _OnlyMyLogs(logging.Filter):
    """只放行ai_explorer和__main__的日志"""
    _ALLOW = ("ai_explorer", "__main__", "root")

    def filter(self, record):
        return any(record.name == a or record.name.startswith(a + ".") for a in self._ALLOW)


logging.getLogger().addFilter(_OnlyMyLogs())

_original_getLogger = logging.getLogger


def _patched_getLogger(name=None):
    lgr = _original_getLogger(name)
    if name and not any(name == a or name.startswith(a + ".") for a in ("ai_explorer", "__main__")):
        lgr.handlers.clear()
        lgr.addHandler(logging.NullHandler())
        lgr.propagate = False
    return lgr


logging.getLogger = _patched_getLogger

# ====== 补丁就绪，现在安全import业务模块 ======
from ai_explorer.common import DeviceDriver, PcDeviceDriver
from ai_explorer.config import Config
from ai_explorer.device_driver_ext import AIDeviceDriver
from ai_explorer.report_generator import ReportGenerator


def run_exploration(config: Config):
    """
    运行AI探索性测试。

    :param config: 主配置对象
    """
    # l_class必填校验
    if not config.l_class:
        raise ValueError("l_class（小类ID）为必填项，请在配置中设置l_class")

    # 设置日志目录：output_dir/l_class
    if not config.logdir:
        config.logdir = os.path.join(config.output_dir, config.l_class)
    os.makedirs(config.logdir, exist_ok=True)

    # 配置日志输出（只保留自己的日志）
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(
                os.path.join(config.logdir, f"{config.l_class}.log"), encoding="utf-8"
            ),
        ],
    )

    logger = logging.getLogger(__name__)
    logger.info(f"配置信息: value={config.l_class}, 包名={config.package_name}, 平台={config.device.platform}, 模式={'功能测试' if config.mode == 1 else '阻断测试'}(mode={config.mode}), 回放={config.replay_mode}")
    logger.info(f"日志目录: {config.logdir}")

    # 连接设备并创建驱动
    if config.device.platform in ("Android", "IOS"):
        device_info = {
            "platform": config.device.platform,
            "uuid": config.device.device_uuid,
            "uri": config.device.device_uri,
            "poco_type": config.device.poco_type,
        }
        dd = DeviceDriver(device_info, config.logdir)
    elif config.device.platform == "Windows":
        dd = PcDeviceDriver()
        if config.device.window_name:
            dd.connect_device(config.device.window_name)
    else:
        raise ValueError(f"不支持的平台: {config.device.platform}")

    # 下发阻断规则（仅mode=0）
    dev_router = config.build_router_info()
    if config.mode == 0:
        logger.info(f"下发阻断规则: l_class={config.l_class}")
        dd.rule_handle(dev_router, config.l_class)
    else:
        logger.info(f"功能测试模式(mode=1): 不下发阻断规则")

    # 创建AI增强驱动并运行探索
    ai_dd = AIDeviceDriver(dd, config)
    try:
        result = ai_dd.explore(config.package_name)
    finally:
        # 无论成功失败，都取消阻断规则（仅mode=0）
        if config.mode == 0:
            logger.info(f"取消阻断规则: l_class={config.l_class}")
            try:
                dd.rule_handle(dev_router, config.l_class, 1)
            except Exception as e:
                logger.error(f"取消阻断规则失败: {e}")

    # 生成报告
    html_path = ai_dd.generate_report(result)
    json_path = ReportGenerator().generate_json(result, config.logdir, config.l_class)

    # 打印摘要
    print("\n" + "=" * 60)
    if config.mode == 1:
        # 功能测试模式
        successes = [i for i in result.issues_found if i["type"] == "function_success"]
        failures = [i for i in result.issues_found if i["type"] == "function_failure"]
        if failures:
            print(f"★ 测试结果: 功能测试有异常 (FUNCTION FAILURE)")
        else:
            print(f"测试结果: 全部功能正常 (ALL FUNCTIONAL)")
        print(f"  功能正常:   {len(successes)}个控件")
        for s in successes:
            print(f"    ✓ {s.get('target', '?')}: {s['description'][:50]}")
        if failures:
            print(f"  功能异常:   {len(failures)}个控件")
            for f in failures:
                print(f"    ✗ {f.get('target', '?')}: {f['description'][:50]}")
    else:
        # 阻断测试模式
        successes = [i for i in result.issues_found if i["type"] == "block_success"]
        failures = [i for i in result.issues_found if i["type"] == "block_failure"]
        if failures:
            f = failures[0]
            print(f"★ 测试结果: 阻断失败 (BLOCK FAILURE)")
            print(f"  失败控件:   {f.get('target', '未知')}")
            print(f"  失败原因:   {f['description']}")
            print(f"  失败截图:   {f.get('screenshot', '')}")
        else:
            print(f"测试结果: 全部阻断成功 (ALL BLOCKED)")
        print(f"  阻断成功:   {len(successes)}个控件")
        for s in successes:
            print(f"    ✓ {s.get('target', '?')}: {s['description'][:50]}")
        if failures:
            print(f"  阻断失败:   {len(failures)}个控件")
            for f in failures:
                print(f"    ✗ {f.get('target', '?')}: {f['description'][:50]}")

    print(f"  总步骤:     {result.total_steps}")
    print(f"  HTML报告:   {html_path}")
    print(f"  JSON报告:   {json_path}")
    print("=" * 60)


if __name__ == "__main__":
    config = Config.load()
    run_exploration(config)

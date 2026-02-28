# -*- encoding: utf-8 -*-

import datetime
import logging
import paramiko
import os
import time
from airtest.core.api import sleep, init_device, connect_device, Template, TargetNotFoundError, wait, touch
from poco.drivers.android.uiautomation import AndroidUiautomationPoco
from poco.drivers.ios import iosPoco
import subprocess
import win32gui
import signal

logger = logging.getLogger(__name__)


class DeviceDriver(object):
    def __init__(self, device_info: dict, logdir: str):
        self.driver = None
        self.poco = None
        self.device_info = device_info
        self.logdir = logdir
        self.connect()

    def connect(self) -> bool:
        """连接设备"""
        if self.device_info.get("platform") == "Android":
            try:
                self.driver = init_device(platform="Android", uuid=self.device_info.get("uuid"), cap_method="JAVACAP")
                self.poco = AndroidUiautomationPoco(self.driver)
                return True
            except Exception as e:
                logger.error("设备连接失败")
        if self.device_info.get("platform") == "IOS":
            if self.device_info.get('uri'):
                try:
                    self.driver = connect_device(self.device_info.get('uri'))
                    self.poco = iosPoco(self.driver)
                    return True
                except Exception as e:
                    logger.error("设备连接失败")
            else:
                try:
                    self.driver = init_device(platform="IOS", uuid=self.device_info.get("uuid"))
                    self.poco = iosPoco(self.driver)
                    return True
                except Exception as e:
                    logger.error("设备连接失败")
        return False

    def start_app(self, package: str):
        """
        启动app
        :param package: 应用包名
        :return:
        """
        try:
            self.driver.start_app(package)
        except Exception as e:
            raise InterruptedError("包名异常")

    def snapshot(self, message: str = "") -> str:
        """
        自定义截图
        :param message: 截图名字
        :return: 截图文件路径
        """
        current_time = datetime.datetime.now().strftime('%Y-%m-%d-%H%M%S')
        if message:
            filename = 'brd-' + message + f'-{current_time}.jpg'
        else:
            filename = f"brd-{current_time}.jpg"
        filepath = os.path.join(self.logdir, filename)
        self.driver.snapshot(filepath)
        return filepath

    def click(self, target: Template or tuple, timeout: int = 20):
        """
        如果存在指定的目标，则进行点击；target也可以是坐标，可用于关闭弹窗（如广告等）
        :param target: 图片对象
        :param timeout: 超时时间
        :return:
        """
        if isinstance(target, Template):
            try:
                wait(target, timeout)
                touch(target)
            except TargetNotFoundError:
                pass
        if isinstance(target, tuple):
            touch(target)

    def text(self, text: str, enter: bool = True, **kwargs):
        """
        输入文本
        :param text: 文本内容
        :param enter: 是否在输入完毕后，执行一次enter，默认是True
        :param kwargs: 在Android上，有时需要在输入完毕后点击搜索按钮，search=True
        :return:
        """
        try:
            self.driver.text(text, enter=enter, **kwargs)
        except Exception as e:
            raise InterruptedError("输入文本失败")

    def swipe(self, x_proportion: tuple = (0.9, 0.1), y_proportion: tuple = (0.5, 0.5),
              direction: str = 'custom', duration: int = 1):
        """
        上下左右滑动，同时支持自定义滑动(ps: 手机左上角为坐标原点)
        :param x_proportion: 横坐标滑动比例（起始位置和结束位置比例）
        :param y_proportion: 纵坐标滑动比例（起始位置和结束位置比例）
        :param direction: 滑动方向（left、right、up、down、custom（自定义））
        :param duration: 滑动持续时间
        :return:
        """
        if self.device_info.get("platform") == "Android":
            dev_info = self.driver.get_display_info()
            width, height, orientation = dev_info.get("width"), dev_info.get("height"), dev_info.get("orientation")
            if orientation == 1:
                # 横屏
                width, height = height, width
        elif self.device_info.get("platform") == "IOS":
            dev_info = self.driver.display_info
            width, height, orientation = dev_info.get("width"), dev_info.get("height"), dev_info.get("orientation")
            if orientation != 'PORTRAIT':
                # 横屏
                width, height = height, width
        else:
            return
        if direction == 'custom':
            fpos = (int(width * x_proportion[0]), int(height * y_proportion[0]))
            tpos = (int(width * x_proportion[1]), int(height * y_proportion[1]))
        elif direction == 'left':
            fpos = (int(width * 0.9), int(height * 0.5))
            tpos = (int(width * 0.1), int(height * 0.5))
        elif direction == 'right':
            fpos = (int(width * 0.1), int(height * 0.5))
            tpos = (int(width * 0.9), int(height * 0.5))
        elif direction == 'up':
            fpos = (int(width * 0.5), int(height * 0.9))
            tpos = (int(width * 0.5), int(height * 0.1))
        elif direction == 'down':
            fpos = (int(width * 0.5), int(height * 0.2))
            tpos = (int(width * 0.5), int(height * 0.9))
        else:
            return
        if self.device_info.get("platform") == "IOS":
            self.driver.swipe(fpos, tpos, duration=duration)
        if self.device_info.get("platform") == "Android":
            self.driver.swipe(fpos, tpos, duration=duration)

    def keyevent(self, keyname, **kwargs):
        """
        事件方法
        :param keyname: HOME/POWER/MENU/BACK
        :return:
        """
        self.driver.keyevent(keyname, **kwargs)

    def back(self):
        """
        返回
        :return:
        """
        self.keyevent("BACK")

    @staticmethod
    def sleep(sleep_time: int):
        sleep(sleep_time)

    class Router(object):

        def __init__(self, device_info: dict):
            self.ssh = None
            self.device_info = device_info

        def connect(self) -> bool:
            try:
                self.ssh = paramiko.SSHClient()
                self.ssh.set_missing_host_key_policy(paramiko.WarningPolicy())
                self.ssh.connect(self.device_info["router_host"], self.device_info["router_port"],
                                 self.device_info["router_user"], self.device_info["router_pwd"], timeout=30)
                return True
            except Exception as e:
                logger.error(e)
                return False

        def exe_invoke_shell(self, appid: str, clear: int = 0, multi_ids: str = "") -> bool:
            try:
                router_index = self.device_info.get("router_index")
                chan = self.ssh.invoke_shell()
                chan.settimeout(10)
                chan.send('enable' + '\n')
                time.sleep(1)
                chan.send(self.device_info["router_enable_pwd"] + '\n')
                time.sleep(1)
                chan.send('config' + '\n')
                time.sleep(1)
                chan.send(f'ip access-list extended {self.device_info.get("extend_device")}' + '\n')
                time.sleep(1)
                if clear:
                    chan.send(f"no deny ip any any si-appid {appid}" + '\n')
                    time.sleep(1)
                else:
                    chan.send(f"no {router_index}" + '\n')
                    time.sleep(1)
                    chan.send(f'{router_index} deny ip any any si-appid {appid}' + '\n')
                    time.sleep(1)

                if multi_ids:
                    ids_list = multi_ids.strip().split()
                    for app_id in ids_list:
                        if clear:
                            chan.send(f"no deny ip any any si-appid {app_id}" + '\n')
                        else:
                            chan.send(f"no {app_id}" + '\n')
                            time.sleep(1)
                            chan.send(f'{app_id} deny ip any any si-appid {app_id}' + '\n')
                        time.sleep(1)

                chan.send('exit' + '\n')
                chan.send('exit' + '\n')
                return True
            except Exception as e:
                logger.error(e)
                return False

        def close(self):
            if self.ssh:
                try:
                    self.ssh.close()
                except Exception as e:
                    pass

    def rule_handle(self, dev_router: dict, l_class: str, clear: int = 0, multi_ids: str = "", timeout: int = 15) -> bool:
        """
        阻断规则下发与取消
        :param dev_router: 阻断路由器配置
        :param l_class: 小类ID
        :param clear: 0-阻断，1-取消阻断
        :param multi_ids: 多id阻断，空格分隔，例如："19078 19079 19080"
        :param timeout: 下发等待生效时间
        :return: 规则是否下发成功
        """
        router = self.Router(dev_router)
        if not router.connect():
            return False
        flag = router.exe_invoke_shell(l_class, clear, multi_ids)
        router.close()
        time.sleep(timeout)
        return flag


class PcDeviceDriver(object):
    def __init__(self):
        self.tshark_pid = None
        self.app_process = None

    def open_application(self, app_path=None):
        try:
            self.app_process = subprocess.Popen(app_path)
            time.sleep(10)
            return True
        except Exception as e:
            return False

    def close_application(self):
        try:
            if hasattr(self, 'app_process') and self.app_process:
                os.kill(self.app_process.pid, signal.SIGTERM)
                self.app_process = None
                time.sleep(3)
                return True
            return False
        except:
            return False

    @staticmethod
    def click(target: Template or tuple, timeout: int = 20):
        if isinstance(target, Template):
            try:
                wait(target, timeout)
                touch(target)
            except TargetNotFoundError:
                pass
        if isinstance(target, tuple):
            touch(target)

    @staticmethod
    def connect_device(window_name):
        try:
            device_number = win32gui.FindWindow(None, window_name)
            if not device_number:
                return False
            device_info = f'Windows:///{device_number}'
            connect_device(device_info)
            return True
        except:
            return False

    @staticmethod
    def sleep(sleep_time: int):
        sleep(sleep_time)

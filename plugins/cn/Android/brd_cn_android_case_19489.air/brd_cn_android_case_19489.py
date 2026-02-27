# -*- encoding=utf8 -*-
from airtest.core.api import using, Template


def reappear_blocked(dd: object, l_class: int, dev_router: dict) -> bool or str:
    """
    阻断判断，阻断成功返回true，脚本异常或阻断失败返回false
    :param dd: 设备对象
    :param l_class: 小类ID
    :param dev_router: 阻断路由器配置
    :return: 阻断成功与否
    查看包名：adb shell dumpsys window w | findstr \/ | findstr name=
    清除缓存：adb shell pm clear     
    作者：廖钰
    录制日期：2024/07/15
    """
    try:
        # 录制开始
        dd.rule_handle(dev_router, l_class)
        dd.start_app("com.hpbr.bosszhipin")
        dd.sleep(3)
        dd.click(Template(r"tpl1740379327396.png", record_pos=(0.218, 0.558), resolution=(1220, 2712)))
        dd.click(Template(r"tpl1740379333051.png", record_pos=(0.178, 0.944), resolution=(1220, 2712)))
        dd.sleep(3)
       # dd.swipe(direction="left")
        dd.click(Template(r"tpl1740379401356.png", record_pos=(-0.12, -0.559), resolution=(1220, 2712)))
        dd.text("17358607087")
        dd.click(Template(r"tpl1740379436211.png", record_pos=(-0.43, -0.137), resolution=(1220, 2712)))
        dd.click(Template(r"tpl1740379448227.png", record_pos=(-0.002, -0.263), resolution=(1220, 2712)))
        dd.sleep(20)
        dd.add(Template(r"tpl1740379559351.png", record_pos=(0.016, 0.035), resolution=(1220, 2712)),msg="阻断失败")  

        
        # 录制结束
        # 返回阻断结果
        dd.clear_redundant()
        return dd.blocked()
    except InterruptedError as e:
        print(e)
        return str(e)
    except Exception as e:
        print(e)
        return False
    finally:    
        dd.rule_handle(dev_router, l_class, 1)


if __name__ == "__main__":
    # 第一步：配置小类ID、阻断路由器及当前设备信息
    l_class = 19489
    router_info = {
        "router_host": "192.168.253.17",
        "router_port": 22,
        "router_user": "root",
        "router_pwd": "admin",
        "router_enable_pwd": "admin",
        "router_index": l_class,
        "extend_device": "brd_test_2",
    }
    current_device_info = {
        "platform": "Android",  # Android/IOS/Windows
        "uuid": "INW8FEZHOVWGXSH6",
        "uri": "",
        "poco_type": ""
    }
    common_air = r"D:\lic\template\common.air"
    logdir = fr'E:\tmp\tmp\{l_class}'
    using(common_air)
    from common import DeviceDriver
    dd = DeviceDriver(current_device_info, logdir)
    print("是否阻断: {}".format(
        reappear_blocked(dd=dd, l_class=l_class, dev_router=router_info)))





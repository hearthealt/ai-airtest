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
    录制日期：2024/2/2
    """
    try:
        # 录制开始  
        dd.rule_handle(dev_router, l_class)
        dd.start_app("com.zzyt.intelligentparking")
        dd.sleep(3)
        dd.click(Template(r"tpl1734504557595.png", record_pos=(0.239, 0.967), resolution=(1220, 2712)))
        dd.click(Template(r"tpl1734504564779.png", record_pos=(-0.294, 0.947), resolution=(1220, 2712)))
        dd.click(Template(r"tpl1734504570495.png", record_pos=(-0.062, -0.574), resolution=(1220, 2712)))
        dd.text("17358607087")
        dd.click(Template(r"tpl1734504575143.png", record_pos=(-0.239, -0.415), resolution=(1220, 2712)))
        dd.text("Test1735860")
        dd.click(Template(r"tpl1734504600478.png", record_pos=(0.004, -0.235), resolution=(1220, 2712)))
        #dd.sleep()
        dd.add(Template(r"tpl1734504650835.png", record_pos=(-0.012, 0.864), resolution=(1220, 2712)),msg="阻断失败")  
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
    l_class = 43633
    router_info = {
        "router_host": "192.168.254.122",
        "router_port": 22,
        "router_user": "admin",
        "router_pwd": "zaq1,lp-",
        "router_enable_pwd": "zaq1,lp-",
        "router_index": l_class,
        "extend_device": "t1",
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






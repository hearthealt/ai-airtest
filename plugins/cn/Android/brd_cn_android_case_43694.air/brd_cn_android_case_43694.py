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
        dd.start_app("io.jiechengwork.BD4CB37")
        dd.sleep(3)
        dd.click(Template(r"tpl1734505720484.png", record_pos=(-0.005, 0.203), resolution=(1220, 2712)))
        dd.sleep(20)
        dd.add(Template(r"tpl1765360228953.png", record_pos=(-0.002, 0.585), resolution=(1220, 2712)),msg="阻断失败")  
#         dd.click(Template(r"tpl1734505740497.png", record_pos=(-0.189, 1.006), resolution=(1220, 2712)))
#         dd.sleep(20)
#         dd.add(Template(r"tpl1734505769005.png", record_pos=(0.018, 0.839), resolution=(1220, 2712)))
#         dd.click(Template(r"tpl1734505801624.png", record_pos=(0.193, 0.994), resolution=(1220, 2712)))
#         dd.add(Template(r"tpl1734505859141.png", record_pos=(0.011, 0.625), resolution=(1220, 2712)))
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
    l_class = 43694
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
        "uuid": "894XKRBIT8I7QWMZ",
        "uri": "",
        "poco_type": ""
    }
    common_air = r"C:\\Users\\admin\\Desktop\\auto_tools\\common.air"
    logdir = fr"C:\\Users\\admin\\Desktop\\auto_tools\\tmp\\{l_class}"
    using(common_air)
    from common import DeviceDriver
    dd = DeviceDriver(current_device_info, logdir)
    print("是否阻断: {}".format(
        reappear_blocked(dd=dd, l_class=l_class, dev_router=router_info)))






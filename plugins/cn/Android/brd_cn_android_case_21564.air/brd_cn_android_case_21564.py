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
        dd.start_app("com.hexin.plat.android.ZheshangSecurity")
        dd.sleep(3)
        dd.click(Template(r"tpl1759989443808.png", record_pos=(0.188, 0.28), resolution=(1220, 2712)))
        dd.click(Template(r"tpl1735191413308.png", record_pos=(0.027, 0.071), resolution=(1220, 2712)))
        dd.poco_click(name="com.hexin.plat.android.ZheshangSecurity:id/btnBack")
        dd.sleep(20)
        dd.add(Template(r"tpl1735191435615.png", record_pos=(0.017, 0.742), resolution=(1220, 2712)),msg="阻断失败")  
        dd.click(Template(r"tpl1735191464760.png", record_pos=(0.199, 1.001), resolution=(1220, 2712)))
        dd.sleep(20)
        dd.add(Template(r"tpl1735191473294.png", record_pos=(0.009, 0.63), resolution=(1220, 2712)))
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
    l_class = 21564
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






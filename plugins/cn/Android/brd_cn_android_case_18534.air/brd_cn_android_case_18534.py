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
        dd.start_app("net.csdn.csdnplus")
        dd.sleep(3)
        dd.click(Template(r"tpl1735181501735.png", record_pos=(-0.005, 0.476), resolution=(1220, 2712)))
        dd.sleep(3)
        dd.click(Template(r"tpl1735181506936.png", record_pos=(0.003, 0.054), resolution=(1220, 2712)))
        dd.click(Template(r"tpl1765347491914.png", record_pos=(0.305, -0.37), resolution=(1220, 2712)))
        dd.click(Template(r"tpl1765347137216.png", record_pos=(0.425, -0.969), resolution=(1220, 2712)))
        dd.sleep(20)
        dd.add(Template(r"tpl1735181520584.png", record_pos=(0.011, 0.18), resolution=(1220, 2712)),msg="阻断失败")  
        dd.click(Template(r"tpl1735181596444.png", record_pos=(-0.17, -0.787), resolution=(1220, 2712)))
        dd.sleep(20)
        dd.add(Template(r"tpl1735181752032.png", record_pos=(0.02, 0.17), resolution=(1220, 2712)),msg="阻断失败")  
       # dd.add()
#         dd.click(Template(r"tpl1735181534037.png", record_pos=(-0.125, 1.011), resolution=(1220, 2712)))
#        # dd.sleep()
#         dd.add(Template(r"tpl1735181566091.png", record_pos=(-0.303, -0.789), resolution=(1220, 2712)))
      #  dd.click(Template(r"tpl1735181578002.png", record_pos=(0.133, 1.013), resolution=(1220, 2712)))
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
    l_class = 18534
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






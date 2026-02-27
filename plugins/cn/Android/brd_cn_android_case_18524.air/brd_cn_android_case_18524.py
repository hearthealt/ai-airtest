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
        dd.start_app("com.hundsun.winner.pazq")
        dd.sleep(3)
        dd.click(Template(r"tpl1735180323450.png", record_pos=(0.24, 0.53), resolution=(1220, 2712)))
        dd.click(Template(r"tpl1735180329944.png", record_pos=(0.339, -0.971), resolution=(1220, 2712)))
        dd.click(Template(r"tpl1747122164014.png", record_pos=(-0.002, 0.53), resolution=(1080, 2400)),timeout=30)
        dd.click(Template(r"tpl1735180850742.png", record_pos=(-0.102, 0.846), resolution=(1220, 2712)))
        dd.click(Template(r"tpl1735180860071.png", record_pos=(-0.003, 0.442), resolution=(1220, 2712)))
        dd.click(Template(r"tpl1735180871635.png", record_pos=(0.384, 1.016), resolution=(1220, 2712)))
        dd.click(Template(r"tpl1735180906540.png", record_pos=(-0.225, 0.221), resolution=(1220, 2712)))
        dd.click(Template(r"tpl1735180360448.png", record_pos=(-0.152, -0.564), resolution=(1220, 2712)))
        dd.text("17358607087")
        dd.click(Template(r"tpl1735180367546.png", record_pos=(-0.451, -0.208), resolution=(1220, 2712)))
        dd.click(Template(r"tpl1735180414551.png", record_pos=(0.362, -0.438), resolution=(1220, 2712)))
        dd.sleep(20)
        dd.add(Template(r"tpl1735180927421.png", record_pos=(0.003, -0.734), resolution=(1220, 2712)),msg="阻断失败")  
        dd.click(Template(r"tpl1735180448395.png", record_pos=(-0.439, -0.973), resolution=(1220, 2712)))
        dd.click(Template(r"tpl1735180655010.png", record_pos=(0.235, 1.013), resolution=(1220, 2712)))
        #dd.click(Template(r"tpl1735180460065.png", record_pos=(-0.09, 0.848), resolution=(1220, 2712)))
       # dd.sleep()
        dd.add(Template(r"tpl1735180641320.png", record_pos=(-0.045, -0.348), resolution=(1220, 2712)))
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
    l_class = 18524
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
        "uuid": "pzij8599hm7prorg",
        "uri": "",
        "poco_type": ""
    }
    common_air = r"E:\airtest-workspace\common.air"
    logdir = fr'E:\tmp\tmp\{l_class}'
    using(common_air)
    from common import DeviceDriver
    dd = DeviceDriver(current_device_info, logdir)
    print("是否阻断: {}".format(
        reappear_blocked(dd=dd, l_class=l_class, dev_router=router_info)))





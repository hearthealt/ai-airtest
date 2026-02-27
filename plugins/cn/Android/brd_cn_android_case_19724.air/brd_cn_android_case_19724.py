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
        dd.start_app("com.eno.android.cj.page")
        dd.sleep(3)
        dd.click(Template(r"tpl1735190542360.png", record_pos=(0.202, 0.634), resolution=(1220, 2712)))
        dd.click(Template(r"tpl1735190549666.png", record_pos=(-0.001, 0.521), resolution=(1220, 2712)))
        dd.click(Template(r"tpl1760405385593.png", record_pos=(0.001, 0.749), resolution=(1220, 2712)))
        dd.click(Template(r"tpl1760405530195.png", record_pos=(-0.182, 0.222), resolution=(1220, 2712)))
        dd.click(Template(r"tpl1759989275985.png", record_pos=(0.292, -0.752), resolution=(1220, 2712)))
        
        dd.sleep(3)
        dd.swipe(direction="up")
        dd.click(Template(r"tpl1735190564415.png", record_pos=(-0.014, 0.974), resolution=(1220, 2712)))
        dd.sleep(20)
        dd.add(Template(r"tpl1735190575572.png", record_pos=(0.013, 0.715), resolution=(1220, 2712)),msg="阻断失败")  
        dd.click(Template(r"tpl1735190586621.png", record_pos=(-0.209, 1.001), resolution=(1220, 2712)))
        dd.click(Template(r"tpl1735190603495.png", record_pos=(0.008, 0.902), resolution=(1220, 2712)))
        #dd.sleep(20)
        dd.padd(Template(r"tpl1735190647149.png", record_pos=(-0.299, -0.918), resolution=(1220, 2712)),Template(r"tpl1757395113333.png", record_pos=(0.0, -0.032), resolution=(1220, 2712)),msg="阻断失败")
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
    l_class = 19724
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
    common_air = r"E:\\airtest-workspace\\common.air"
    logdir = fr"E:\\tmp\\tmp\\{l_class}"
    using(common_air)
    from common import DeviceDriver
    dd = DeviceDriver(current_device_info, logdir)
    print("是否阻断: {}".format(
        reappear_blocked(dd=dd, l_class=l_class, dev_router=router_info)))







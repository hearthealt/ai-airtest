# -*- encoding=utf8 -*-
from airtest.core.api import using, Template

def reappear_blocked(dd: object, l_class: int, dev_router: dict) -> bool or str:
    """
    阻断判断，阻断成功返回true，脚本异常或阻断失败返回false
    :param dd: 设备对象
    :param l_class: 小类ID
    :param dev_router: 阻断路由器配置
    :return: 阻断成功与否
    作者：曹坤
    录制日期：2024/5/7
    """
    try:
        # 录制开始
        acc = dd.account()
        username = acc["universal"]["username2"]
        password = acc["universal"]["password"]
        dd.rule_handle(dev_router, l_class)
        dd.sleep(3)
        dd.start_app("tv.danmaku.bili")
        dd.click(Template(r"tpl1718783008421.png", record_pos=(-0.005, 0.32), resolution=(1220, 2712)))
        dd.click(Template(r"tpl1718783019828.png", record_pos=(0.202, 0.891), resolution=(1220, 2712)))
        dd.sleep(2)
        dd.cadd(Template(r"tpl1718783091403.png", record_pos=(-0.005, 0.402), resolution=(1220, 2712)),Template(r"tpl1718783473429.png", record_pos=(0.009, -0.084), resolution=(1220, 2712)),timeout=60,msg="首页推荐阻断失败")
        dd.click(Template(r"tpl1718783111067.png", record_pos=(-0.038, -0.839), resolution=(1220, 2712)))
        dd.cadd(Template(r"tpl1718783091403.png", record_pos=(-0.005, 0.402), resolution=(1220, 2712)),Template(r"tpl1718783500674.png", record_pos=(-0.011, -0.368), resolution=(1220, 2712)),timeout=60,msg="首页热门阻断失败")
        dd.click(Template(r"tpl1718783126714.png", record_pos=(0.098, -0.839), resolution=(1220, 2712)))
        dd.cadd(Template(r"tpl1718783177536.png", record_pos=(-0.009, 0.381), resolution=(1220, 2712)),Template(r"tpl1718783555748.png", record_pos=(-0.002, 0.084), resolution=(1220, 2712)),timeout=60,msg="首页追番阻断失败")
        dd.click(Template(r"tpl1718783141741.png", record_pos=(0.23, -0.845), resolution=(1220, 2712)))
        dd.cadd(Template(r"tpl1718783191880.png", record_pos=(-0.002, 0.439), resolution=(1220, 2712)),Template(r"tpl1718783573591.png", record_pos=(0.002, 0.095), resolution=(1220, 2712)),timeout=60,msg="首页影视阻断失败")
        dd.click(Template(r"tpl1718783219006.png", record_pos=(0.195, 1.015), resolution=(1220, 2712)))
        dd.add(Template(r"tpl1718783270210.png", record_pos=(-0.023, -0.175), resolution=(1220, 2712)),msg="会员购阻断失败")
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
    l_class = 23921
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
        "uuid": "QSPVX4IBJVSSMNDQ",
        "uri": "",
        "poco_type": ""
    }
    common_air = r"E:\\workbase\\AutomationToolDevelopment\\common.air"
    logdir = fr'E:\\tmp\\tmp\\{l_class}'
    using(common_air)
    from common import DeviceDriver
    dd = DeviceDriver(current_device_info, logdir)
    print("是否阻断: {}".format(
        reappear_blocked(dd=dd, l_class=l_class, dev_router=router_info)))

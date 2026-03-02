# -*- encoding=utf8 -*-
"""HTML和JSON测试报告生成模块。"""

import base64
import json
import os
import time
import logging
from collections import OrderedDict
from html import escape

from .models import ExplorationResult, ActionType

logger = logging.getLogger(__name__)


class ReportGenerator:
    """测试报告生成器：根据探索结果生成HTML和JSON格式的报告。"""

    def generate_html(self, result: ExplorationResult, output_dir: str,
                      l_class: str = "", mode: int = 0) -> str:
        """生成自包含的HTML测试报告。"""
        # 模式
        is_func = mode == 1
        mode_label = "功能测试" if is_func else "阻断测试"
        ok_type = "function_success" if is_func else "block_success"
        ng_type = "function_failure" if is_func else "block_failure"
        ok_text = "功能正常" if is_func else "阻断成功"
        ng_text = "功能异常" if is_func else "阻断失败"

        # 文件名
        ts = time.strftime('%Y%m%d_%H%M%S', time.localtime(result.start_time))
        prefix = f"{l_class}_" if l_class else ""
        html_path = os.path.join(output_dir, f"{prefix}{mode_label}_{ts}.html")

        # 基础数据
        duration = result.end_time - result.start_time
        dur_str = f"{int(duration // 60)}分{int(duration % 60)}秒"
        successes = [i for i in result.issues_found if i["type"] == ok_type]
        failures = [i for i in result.issues_found if i["type"] == ng_type]
        tested = len(successes) + len(failures)
        all_pass = len(failures) == 0 and len(successes) > 0
        cov = f"{result.coverage_percentage:.0f}%"
        start_str = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(result.start_time))

        # Banner
        if all_pass:
            b_cls, b_ico = "pass", "&#10004;"
            b_title = f"全部{ok_text}"
            b_desc = f"已测试 {tested} 个控件，{'功能均正常' if is_func else '全部阻断生效'}"
        elif failures:
            b_cls, b_ico = "fail", "&#10008;"
            tgt = escape(failures[0].get("target", "?"))
            if is_func:
                b_title = f"发现 {len(failures)} 个功能异常"
                b_desc = f"「{tgt}」等控件功能异常"
            else:
                b_title = "阻断失败"
                b_desc = f"「{tgt}」未被阻断，页面正常加载了数据"
        else:
            b_cls, b_ico = "neutral", "&#8212;"
            b_title = "无测试结果"
            b_desc = "未检测到任何测试控件"

        # 构建区块
        menu_html = self._build_menu_overview(result, ok_type, ng_type, ok_text, ng_text)
        issues_html = self._build_failures(failures, ng_text)
        steps_html = self._build_steps(result)

        # 主题
        theme = "#8b5cf6" if is_func else "#3b82f6"
        grad = "linear-gradient(135deg,#7c3aed,#a78bfa)" if is_func else "linear-gradient(135deg,#2563eb,#60a5fa)"

        html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{mode_label}报告 - {escape(result.app_package)}</title>
<style>
:root{{
  --ok:#10b981;--ok-bg:#ecfdf5;--ok-bd:#a7f3d0;
  --ng:#ef4444;--ng-bg:#fef2f2;--ng-bd:#fecaca;
  --warn:#f59e0b;--info:#6366f1;--info-bg:#eef2ff;
  --mute:#9ca3af;--bg:#f3f4f6;--card:#fff;--bd:#e5e7eb;
  --t1:#111827;--t2:#6b7280;--t3:#9ca3af;
  --r:12px;--theme:{theme};
  --sh:0 1px 3px rgba(0,0,0,.05);--sh-md:0 4px 12px rgba(0,0,0,.08);
}}
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','Microsoft YaHei','PingFang SC',sans-serif;
  background:var(--bg);color:var(--t1);line-height:1.6;-webkit-font-smoothing:antialiased}}
.wrap{{max-width:1060px;margin:0 auto;padding:0 20px 60px}}

/* top */
.top{{background:{grad};padding:32px 0 52px;margin-bottom:-32px}}
.top-in{{max-width:1060px;margin:0 auto;padding:0 20px}}
.top h1{{font-size:22px;font-weight:700;color:#fff;display:flex;align-items:center;gap:10px;margin-bottom:10px}}
.top .tag{{background:rgba(255,255,255,.18);padding:2px 12px;border-radius:20px;font-size:12px;font-weight:500}}
.meta{{display:flex;flex-wrap:wrap;gap:4px 20px;font-size:13px;color:rgba(255,255,255,.75)}}
.meta b{{color:#fff;font-weight:600}}

/* banner */
.banner{{display:flex;align-items:center;gap:18px;padding:22px 26px;border-radius:var(--r);box-shadow:var(--sh-md);position:relative;z-index:1}}
.banner.pass{{background:linear-gradient(135deg,#ecfdf5,#d1fae5);border:1px solid var(--ok-bd)}}
.banner.fail{{background:linear-gradient(135deg,#fef2f2,#fee2e2);border:1px solid var(--ng-bd)}}
.banner.neutral{{background:var(--card);border:1px solid var(--bd)}}
.b-i{{width:52px;height:52px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:26px;color:#fff;flex-shrink:0;box-shadow:0 2px 8px rgba(0,0,0,.12)}}
.pass .b-i{{background:var(--ok)}} .fail .b-i{{background:var(--ng)}} .neutral .b-i{{background:var(--mute)}}
.b-t h2{{font-size:19px;font-weight:700}}
.pass .b-t h2{{color:#065f46}} .fail .b-t h2{{color:#991b1b}} .neutral .b-t h2{{color:var(--t2)}}
.b-t p{{font-size:14px;color:var(--t2);margin-top:2px}}

/* stats */
.stats{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:18px 0 22px}}
@media(max-width:640px){{.stats{{grid-template-columns:repeat(2,1fr)}}}}
.st{{background:var(--card);border:1px solid var(--bd);border-radius:var(--r);padding:18px 14px;text-align:center;box-shadow:var(--sh)}}
.st .v{{font-size:30px;font-weight:800;line-height:1.1}}
.st .l{{font-size:12px;color:var(--t3);margin-top:5px;font-weight:500}}
.st-ok .v{{color:var(--ok)}} .st-ng .v{{color:var(--ng)}} .st-th .v{{color:var(--theme)}}

/* section */
.sec{{background:var(--card);border:1px solid var(--bd);border-radius:var(--r);margin-bottom:18px;box-shadow:var(--sh);overflow:hidden}}
.sec-h{{font-size:16px;font-weight:700;padding:20px 24px 16px;display:flex;align-items:center;gap:8px;border-bottom:1px solid #f3f4f6}}
.sec-h .n{{background:#f3f4f6;color:var(--t2);font-size:11px;font-weight:600;padding:2px 10px;border-radius:20px}}
.sec-body{{padding:0 24px 20px}}

/* ---- L1 groups ---- */
.l1g{{margin-top:18px}}
.l1g:first-child{{margin-top:0}}
.l1-hd{{display:flex;align-items:center;justify-content:space-between;padding:14px 16px;background:#f9fafb;border-radius:10px;margin-bottom:12px}}
.l1-name{{font-size:15px;font-weight:700;display:flex;align-items:center;gap:8px}}
.l1-name .dot{{width:8px;height:8px;border-radius:50%;flex-shrink:0}}
.l1-name .dot-ok{{background:var(--ok)}} .l1-name .dot-ng{{background:var(--ng)}} .l1-name .dot-mix{{background:var(--warn)}}
.l1-stat{{font-size:12px;color:var(--t2);font-weight:500}}
.l1-stat b{{font-weight:700}}
.l1-stat .sok{{color:var(--ok)}} .l1-stat .sng{{color:var(--ng)}}

/* L2 grid */
.l2g{{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:10px}}
@media(max-width:480px){{.l2g{{grid-template-columns:repeat(2,1fr)}}}}
.l2c{{border:1px solid var(--bd);border-radius:10px;overflow:hidden;transition:box-shadow .15s,transform .15s;cursor:default}}
.l2c:hover{{box-shadow:var(--sh-md);transform:translateY(-2px)}}
.l2c-ok{{border-color:var(--ok-bd)}} .l2c-ng{{border-color:var(--ng-bd)}}
.l2-img{{width:100%;height:160px;object-fit:cover;display:block;background:#f3f4f6;cursor:pointer;border-bottom:1px solid var(--bd)}}
.l2-bot{{padding:10px 12px}}
.l2-name{{font-size:14px;font-weight:600;display:flex;align-items:center;justify-content:space-between;gap:6px}}
.l2-badge{{font-size:11px;font-weight:600;padding:1px 8px;border-radius:4px;white-space:nowrap}}
.l2c-ok .l2-badge{{background:var(--ok-bg);color:var(--ok)}}
.l2c-ng .l2-badge{{background:var(--ng-bg);color:var(--ng)}}
.l2-desc{{font-size:12px;color:var(--t2);margin-top:4px;line-height:1.5;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}}

/* ---- failures ---- */
.fl-card{{display:flex;gap:16px;padding:18px 20px;border-radius:10px;background:var(--ng-bg);border:1px solid var(--ng-bd);margin-top:12px}}
.fl-card:first-child{{margin-top:0}}
.fl-img{{width:90px;height:160px;object-fit:cover;border-radius:8px;cursor:pointer;border:1px solid var(--ng-bd);flex-shrink:0;background:#fef2f2}}
.fl-body{{flex:1;min-width:0}}
.fl-tgt{{font-size:16px;font-weight:700;color:#991b1b;margin-bottom:4px}}
.fl-desc{{font-size:13px;color:var(--t2);line-height:1.7;word-break:break-word}}
.fl-meta{{font-size:12px;color:var(--t3);margin-top:8px;display:flex;gap:12px}}

/* ---- steps ---- */
.st-toggle{{display:flex;align-items:center;gap:8px;cursor:pointer;user-select:none;
  font-size:16px;font-weight:700;padding:20px 24px 16px;border-bottom:1px solid #f3f4f6}}
.st-toggle .arr{{display:inline-block;transition:transform .2s;font-size:11px;color:var(--t3)}}
.st-toggle.on .arr{{transform:rotate(90deg)}}
.st-body{{display:none;padding:0 24px 16px}} .st-body.on{{display:block}}
.tbl-w{{overflow-x:auto;-webkit-overflow-scrolling:touch;margin-top:12px}}
table.tbl{{width:100%;border-collapse:collapse;font-size:13px}}
.tbl th{{background:#f9fafb;padding:9px 12px;text-align:left;font-weight:600;color:var(--t2);font-size:11px;
  text-transform:uppercase;letter-spacing:.4px;border-bottom:2px solid var(--bd);white-space:nowrap;position:sticky;top:0}}
.tbl td{{padding:9px 12px;border-bottom:1px solid #f3f4f6;vertical-align:middle}}
.tbl tr:hover{{background:#fafbfc}}
.tbl .n{{font-weight:700;color:var(--t3);font-size:12px;text-align:center;width:32px}}
.tbl .img{{width:40px;height:72px;object-fit:cover;border-radius:6px;cursor:pointer;border:1px solid var(--bd);background:#f9fafb}}
.tbl .desc{{max-width:240px;word-break:break-word}}
.tbl .tgt{{max-width:140px;word-break:break-word;font-weight:500}}
.tbl .dur{{color:var(--t3);white-space:nowrap;font-size:12px}}

/* badges */
.bg{{display:inline-block;padding:2px 9px;border-radius:5px;font-size:11px;font-weight:600;white-space:nowrap}}
.bg-ok{{background:#f0fdf4;color:#16a34a}} .bg-bs{{background:#ecfdf5;color:#059669}}
.bg-bf{{background:#fef2f2;color:#dc2626}} .bg-fs{{background:#f0fdf4;color:#16a34a}}
.bg-ff{{background:#fef2f2;color:#dc2626}} .bg-ld{{background:var(--info-bg);color:var(--info)}}
.bg-fl{{background:var(--ng-bg);color:var(--ng)}} .bg-er{{background:#fffbeb;color:var(--warn)}}
.bg-skip{{background:#f3f4f6;color:var(--t3)}}

/* lightbox */
.lb{{display:none;position:fixed;inset:0;background:rgba(0,0,0,.82);z-index:9999;align-items:center;justify-content:center;backdrop-filter:blur(6px)}}
.lb.on{{display:flex}}
.lb img{{max-width:85vw;max-height:88vh;border-radius:8px;box-shadow:0 20px 60px rgba(0,0,0,.5);user-select:none}}
.lb-btn{{position:fixed;top:50%;transform:translateY(-50%);width:48px;height:48px;border-radius:50%;
  background:rgba(255,255,255,.15);border:none;color:#fff;font-size:24px;cursor:pointer;
  display:flex;align-items:center;justify-content:center;transition:background .2s;z-index:10000}}
.lb-btn:hover{{background:rgba(255,255,255,.3)}}
.lb-prev{{left:16px}} .lb-next{{right:16px}}
.lb-counter{{position:fixed;bottom:20px;left:50%;transform:translateX(-50%);color:rgba(255,255,255,.7);
  font-size:14px;z-index:10000;background:rgba(0,0,0,.4);padding:4px 14px;border-radius:20px}}
.lb-close{{position:fixed;top:16px;right:16px;width:40px;height:40px;border-radius:50%;
  background:rgba(255,255,255,.15);border:none;color:#fff;font-size:20px;cursor:pointer;
  display:flex;align-items:center;justify-content:center;transition:background .2s;z-index:10000}}
.lb-close:hover{{background:rgba(255,255,255,.3)}}

/* footer */
.ft{{text-align:center;font-size:12px;color:var(--t3);margin-top:28px;padding-top:18px;border-top:1px solid var(--bd)}}

/* no-data */
.empty{{padding:40px 20px;text-align:center;color:var(--t3);font-size:14px}}
</style>
</head>
<body>

<div class="top">
  <div class="top-in">
    <h1>{mode_label}报告 <span class="tag">Mode {mode}</span></h1>
    <div class="meta">
      <span>应用 <b>{escape(result.app_package)}</b></span>
      <span>平台 <b>{escape(result.platform)}</b></span>
      <span>耗时 <b>{dur_str}</b></span>
      <span>时间 <b>{start_str}</b></span>
    </div>
  </div>
</div>

<div class="wrap">
  <div class="banner {b_cls}">
    <div class="b-i">{b_ico}</div>
    <div class="b-t"><h2>{b_title}</h2><p>{b_desc}</p></div>
  </div>

  <div class="stats">
    <div class="st st-th"><div class="v">{tested}</div><div class="l">已测控件</div></div>
    <div class="st st-ok"><div class="v">{len(successes)}</div><div class="l">{ok_text}</div></div>
    <div class="st st-ng"><div class="v">{len(failures)}</div><div class="l">{ng_text}</div></div>
    <div class="st"><div class="v">{cov}</div><div class="l">覆盖率</div></div>
  </div>

  {menu_html}
  {issues_html}
  {steps_html}

  <div class="ft">{mode_label}报告 &middot; AI 视觉模型驱动 &middot; {time.strftime('%Y-%m-%d %H:%M:%S')}</div>
</div>

<div class="lb" id="lb">
  <button class="lb-close" onclick="closeLb()" title="关闭">&#10005;</button>
  <button class="lb-btn lb-prev" onclick="navImg(-1)" title="上一张">&#10094;</button>
  <img id="lb-img" src="" alt="">
  <button class="lb-btn lb-next" onclick="navImg(1)" title="下一张">&#10095;</button>
  <div class="lb-counter" id="lb-counter"></div>
</div>
<script>
var lbImgs=[],lbIdx=0;
function initImgs(){{lbImgs=Array.from(document.querySelectorAll('[onclick^="showImg"]')).map(function(e){{return e.src||e.getAttribute('onclick').match(/'([^']+)'/)[1]}})}}
function showImg(s){{if(!lbImgs.length)initImgs();lbIdx=lbImgs.indexOf(s);if(lbIdx<0)lbIdx=0;renderLb();document.getElementById('lb').classList.add('on')}}
function navImg(d){{event.stopPropagation();lbIdx=(lbIdx+d+lbImgs.length)%lbImgs.length;renderLb()}}
function renderLb(){{document.getElementById('lb-img').src=lbImgs[lbIdx];document.getElementById('lb-counter').textContent=(lbIdx+1)+' / '+lbImgs.length}}
function closeLb(){{document.getElementById('lb').classList.remove('on')}}
document.addEventListener('keydown',function(e){{
  if(!document.getElementById('lb').classList.contains('on'))return;
  if(e.key==='Escape')closeLb();
  else if(e.key==='ArrowLeft')navImg(-1);
  else if(e.key==='ArrowRight')navImg(1);
}});
document.querySelectorAll('.st-toggle').forEach(function(t){{
  t.addEventListener('click',function(){{this.classList.toggle('on');this.nextElementSibling.classList.toggle('on')}})
}});
</script>
</body></html>"""

        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html)
        logger.info(f"HTML报告已生成: {html_path}")
        return html_path

    # ==================== L1→L2 分组总览 ====================

    def _build_menu_overview(self, result: ExplorationResult,
                             ok_type: str, ng_type: str,
                             ok_text: str, ng_text: str) -> str:
        """按 L1 分组展示测试结果，每个 L1 下以网格展示其 L2"""
        issues = [i for i in result.issues_found if i["type"] in (ok_type, ng_type)]
        if not issues:
            return '<div class="sec"><div class="sec-h">测试结果</div><div class="empty">未检测到任何测试结果</div></div>'

        # 按 L1 分组（保留顺序）
        groups = OrderedDict()
        for issue in issues:
            target = issue.get("target", "")
            parts = target.split("-", 1)
            l1 = parts[0] or target
            l2 = parts[1] if len(parts) > 1 else ""
            if l1 not in groups:
                groups[l1] = []
            groups[l1].append({
                "l2": l2, "ok": issue["type"] == ok_type,
                "desc": issue.get("description", ""),
                "screenshot": issue.get("screenshot", ""),
                "step": issue.get("step", "?"),
            })

        group_htmls = []
        for l1_name, items in groups.items():
            ok_cnt = sum(1 for i in items if i["ok"])
            ng_cnt = len(items) - ok_cnt
            # 状态点
            if ng_cnt == 0:
                dot = "dot-ok"
            elif ok_cnt == 0:
                dot = "dot-ng"
            else:
                dot = "dot-mix"

            stat_parts = []
            if ok_cnt:
                stat_parts.append(f'<span class="sok">{ok_cnt} {ok_text}</span>')
            if ng_cnt:
                stat_parts.append(f'<span class="sng">{ng_cnt} {ng_text}</span>')

            # L2 网格卡片
            cards = []
            for item in items:
                cls = "l2c-ok" if item["ok"] else "l2c-ng"
                badge = ok_text if item["ok"] else ng_text
                img_src = self._img_src(item["screenshot"])
                img_tag = (f'<img class="l2-img" src="{img_src}" onclick="showImg(this.src)" alt="截图">'
                           if img_src else '<div class="l2-img"></div>')
                desc = escape(self._strip_prefix(item["desc"]))
                name = escape(item["l2"]) if item["l2"] else escape(l1_name)

                cards.append(f"""<div class="l2c {cls}">
                  {img_tag}
                  <div class="l2-bot">
                    <div class="l2-name"><span>{name}</span><span class="l2-badge">{badge}</span></div>
                    <div class="l2-desc">{desc}</div>
                  </div>
                </div>""")

            group_htmls.append(f"""<div class="l1g">
              <div class="l1-hd">
                <div class="l1-name"><span class="dot {dot}"></span>{escape(l1_name)}</div>
                <div class="l1-stat"><b>{ok_cnt}</b>/<b>{len(items)}</b> 通过 &nbsp; {' &nbsp; '.join(stat_parts)}</div>
              </div>
              <div class="l2g">{''.join(cards)}</div>
            </div>""")

        total = len(issues)
        return f"""<div class="sec">
          <div class="sec-h">菜单测试总览 <span class="n">{total} 项</span></div>
          <div class="sec-body">{''.join(group_htmls)}</div>
        </div>"""

    # ==================== 失败详情 ====================

    def _build_failures(self, failures: list, ng_text: str) -> str:
        """失败项大图展示"""
        if not failures:
            return ""

        items = []
        for f in failures:
            img_src = self._img_src(f.get("screenshot", ""))
            img_tag = (f'<img class="fl-img" src="{img_src}" onclick="showImg(this.src)" alt="截图">'
                       if img_src else "")
            desc = escape(self._strip_prefix(f.get("description", "")))
            target = escape(f.get("target", "?"))

            items.append(f"""<div class="fl-card">
              {img_tag}
              <div class="fl-body">
                <div class="fl-tgt">{target}</div>
                <div class="fl-desc">{desc}</div>
                <div class="fl-meta"><span>步骤 #{f.get('step', '?')}</span></div>
              </div>
            </div>""")

        return f"""<div class="sec">
          <div class="sec-h">{ng_text}详情 <span class="n">{len(failures)}</span></div>
          <div class="sec-body">{''.join(items)}</div>
        </div>"""

    # ==================== 步骤明细 ====================

    def _build_steps(self, result: ExplorationResult) -> str:
        """可折叠步骤表，过滤掉内部噪音步骤"""
        # 只保留有意义的步骤
        key_results = {"block_success", "block_failure", "function_success",
                       "function_failure", "loading", "failed"}
        key_actions = {ActionType.CLICK, ActionType.TEXT_INPUT, ActionType.BACK}

        rows = []
        for step in result.steps:
            a = step.action_taken
            # 过滤：只保留有截图、有关键结果、或有关键操作的步骤
            is_key = (step.action_result in key_results
                      or a.action in key_actions
                      or step.screenshot_path)
            if not is_key:
                continue

            target = ""
            if a.target_element:
                target = a.target_element.text or a.target_element.name

            img_src = self._img_src(step.screenshot_path)
            thumb = (f'<img class="img" src="{img_src}" onclick="showImg(this.src)" alt="">'
                     if img_src else "")

            badge = self._badge(step.action_result)
            desc = escape(step.screen_description or "")
            target_esc = escape(target)

            rows.append(f"""<tr>
              <td class="n">{step.step_number}</td>
              <td>{thumb}</td>
              <td class="desc">{desc}</td>
              <td>{a.action.value}</td>
              <td class="tgt">{target_esc}</td>
              <td>{badge}</td>
              <td class="dur">{step.duration_ms}ms</td>
            </tr>""")

        total_shown = len(rows)
        return f"""<div class="sec" style="padding:0">
          <div class="st-toggle" role="button">
            <span class="arr">&#9654;</span> 步骤明细
            <span class="n" style="margin-left:4px">{total_shown} 步</span>
          </div>
          <div class="st-body">
            <div class="tbl-w">
            <table class="tbl">
              <thead><tr>
                <th>#</th><th>截图</th><th>描述</th><th>操作</th><th>目标</th><th>结果</th><th>耗时</th>
              </tr></thead>
              <tbody>{''.join(rows)}</tbody>
            </table>
            </div>
          </div>
        </div>"""

    # ==================== 工具方法 ====================

    @staticmethod
    def _img_src(path: str) -> str:
        """截图转 base64 data URL"""
        if not path or not os.path.exists(path):
            return ""
        try:
            with open(path, "rb") as f:
                data = base64.b64encode(f.read()).decode("utf-8")
            ext = os.path.splitext(path)[1].lower()
            mime = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
                    ".bmp": "image/bmp", ".webp": "image/webp"}.get(ext, "image/png")
            return f"data:{mime};base64,{data}"
        except Exception:
            return "file:///" + path.replace("\\", "/")

    @staticmethod
    def _badge(result_str: str) -> str:
        m = {
            "success": ("成功", "bg-ok"), "block_success": ("阻断成功", "bg-bs"),
            "block_failure": ("阻断失败", "bg-bf"), "function_success": ("功能正常", "bg-fs"),
            "function_failure": ("功能异常", "bg-ff"), "loading": ("加载中", "bg-ld"),
            "failed": ("失败", "bg-fl"), "error": ("异常", "bg-er"),
        }
        text, cls = m.get(result_str, (result_str, "bg-skip"))
        return f'<span class="bg {cls}">{text}</span>'

    @staticmethod
    def _strip_prefix(desc: str) -> str:
        for prefix in ("阻断成功: ", "阻断失败: ", "阻断成功（持续loading）: ",
                        "功能正常: ", "功能异常: ", "功能异常（持续loading）: "):
            if desc.startswith(prefix):
                return desc[len(prefix):]
        return desc

    @staticmethod
    def generate_json(result: ExplorationResult, output_dir: str, l_class: str = "") -> str:
        """生成JSON测试摘要。"""
        json_path = os.path.join(output_dir, f"{l_class}.json")
        summary = {
            "app_package": result.app_package,
            "platform": result.platform,
            "duration_seconds": result.end_time - result.start_time,
            "total_steps": result.total_steps,
            "unique_screens": result.unique_screens,
            "total_elements_found": result.total_elements_found,
            "elements_interacted": result.elements_interacted,
            "coverage_percentage": result.coverage_percentage,
            "issues_found": result.issues_found,
            "steps": [
                {
                    "step": s.step_number,
                    "screen": s.screen_description,
                    "action": s.action_taken.action.value,
                    "target": (s.action_taken.target_element.text or s.action_taken.target_element.name)
                              if s.action_taken.target_element else None,
                    "result": s.action_result,
                    "duration_ms": s.duration_ms,
                }
                for s in result.steps
            ],
        }
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)
        logger.info(f"JSON报告已生成: {json_path}")
        return json_path

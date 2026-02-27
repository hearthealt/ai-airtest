# -*- encoding=utf8 -*-
"""HTML和JSON测试报告生成模块。"""

import base64
import json
import os
import time
import logging
from html import escape

from .models import ExplorationResult, ActionType

logger = logging.getLogger(__name__)


class ReportGenerator:
    """测试报告生成器：根据探索结果生成HTML和JSON格式的报告。"""

    def generate_html(self, result: ExplorationResult, output_dir: str, l_class: str = "") -> str:
        """
        生成自包含的HTML测试报告。

        :param result: 探索结果对象
        :param output_dir: 报告输出目录
        :param l_class: 小类ID（用于文件命名）
        :return: 生成的HTML文件路径
        """
        filename = f"{l_class}.html"
        html_path = os.path.join(output_dir, filename)

        duration = result.end_time - result.start_time
        duration_str = f"{int(duration // 60)}分{int(duration % 60)}秒"

        successes = [i for i in result.issues_found if i["type"] == "block_success"]
        failures = [i for i in result.issues_found if i["type"] == "block_failure"]
        all_pass = len(failures) == 0 and len(successes) > 0

        # 统计点击数（实际点击功能控件的步骤，不含弹窗关闭）
        click_count = sum(
            1 for s in result.steps
            if s.action_taken.action == ActionType.CLICK
            and s.action_result not in ("error",)
        )

        # 构建各区块
        result_cards_html = self._build_result_cards(result)
        issues_html = self._build_issues_section(failures)
        steps_html = self._build_steps_table(result)

        # 总体结果横幅
        if all_pass:
            banner_cls = "banner-pass"
            banner_icon = "&#10004;"
            banner_title = "全部阻断成功"
            banner_desc = f"已测试 {len(successes)} 个控件，全部阻断生效"
        elif failures:
            banner_cls = "banner-fail"
            banner_icon = "&#10008;"
            banner_title = "阻断失败"
            f = failures[0]
            banner_desc = f"控件「{escape(f.get('target', '未知'))}」未被阻断，页面正常加载了数据"
        else:
            banner_cls = "banner-neutral"
            banner_icon = "&#8212;"
            banner_title = "无测试结果"
            banner_desc = "未检测到任何阻断控件"

        start_time_str = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(result.start_time))

        html_content = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>阻断测试报告 - {escape(result.app_package)}</title>
<style>
:root {{
    --pass: #10b981; --pass-bg: #ecfdf5; --pass-bd: #a7f3d0;
    --fail: #ef4444; --fail-bg: #fef2f2; --fail-bd: #fecaca;
    --warn: #f59e0b; --warn-bg: #fffbeb; --warn-bd: #fde68a;
    --info: #6366f1; --info-bg: #eef2ff; --info-bd: #c7d2fe;
    --mute: #6b7280;
    --bg: #f8fafc; --card: #fff; --bd: #e2e8f0;
    --t1: #1e293b; --t2: #64748b; --t3: #94a3b8;
    --r: 12px;
}}
*{{ margin:0; padding:0; box-sizing:border-box; }}
body{{
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Microsoft YaHei', 'PingFang SC', sans-serif;
    background: var(--bg); color: var(--t1); line-height: 1.6;
}}
.wrap{{ max-width:1100px; margin:0 auto; padding:28px 24px 64px; }}

/* ---- header ---- */
.hd{{ display:flex; align-items:baseline; justify-content:space-between; flex-wrap:wrap; gap:8px 24px; margin-bottom:24px; }}
.hd h1{{ font-size:22px; font-weight:700; }}
.hd-meta{{ display:flex; gap:18px; flex-wrap:wrap; font-size:13px; color:var(--t2); }}
.hd-meta b{{ color:var(--t1); font-weight:600; }}

/* ---- banner ---- */
.banner{{ display:flex; align-items:center; gap:18px; padding:22px 28px; border-radius:var(--r); margin-bottom:24px; }}
.banner-pass{{ background:linear-gradient(135deg,#ecfdf5,#d1fae5); border:1px solid var(--pass-bd); }}
.banner-fail{{ background:linear-gradient(135deg,#fef2f2,#fee2e2); border:1px solid var(--fail-bd); }}
.banner-neutral{{ background:linear-gradient(135deg,#f8fafc,#f1f5f9); border:1px solid var(--bd); }}
.b-icon{{ width:54px; height:54px; border-radius:50%; display:flex; align-items:center; justify-content:center; font-size:30px; color:#fff; flex-shrink:0; }}
.banner-pass .b-icon{{ background:var(--pass); }}
.banner-fail .b-icon{{ background:var(--fail); }}
.banner-neutral .b-icon{{ background:var(--mute); }}
.b-txt h2{{ font-size:20px; font-weight:700; }}
.banner-pass .b-txt h2{{ color:#065f46; }}
.banner-fail .b-txt h2{{ color:#991b1b; }}
.banner-neutral .b-txt h2{{ color:var(--t2); }}
.b-txt p{{ font-size:14px; color:var(--t2); margin-top:2px; }}

/* ---- stats ---- */
.stats{{ display:grid; grid-template-columns:repeat(4,1fr); gap:14px; margin-bottom:28px; }}
@media(max-width:640px){{ .stats{{ grid-template-columns:repeat(2,1fr); }} }}
.st{{ background:var(--card); border:1px solid var(--bd); border-radius:var(--r); padding:20px; text-align:center; }}
.st .v{{ font-size:34px; font-weight:800; line-height:1.1; }}
.st .l{{ font-size:13px; color:var(--t3); margin-top:6px; font-weight:500; }}
.st-g .v{{ color:var(--pass); }}
.st-r .v{{ color:var(--fail); }}

/* ---- section ---- */
.sec{{ background:var(--card); border:1px solid var(--bd); border-radius:var(--r); padding:24px; margin-bottom:24px; }}
.sec-t{{ font-size:16px; font-weight:700; margin-bottom:16px; display:flex; align-items:center; gap:8px; }}
.sec-t .cnt{{ background:#f1f5f9; color:var(--t2); font-size:12px; font-weight:600; padding:2px 10px; border-radius:20px; }}

/* ---- result cards ---- */
.rc-list{{ display:flex; flex-direction:column; gap:12px; }}
.rc{{ display:flex; border-radius:10px; border:1px solid var(--bd); overflow:hidden; transition:box-shadow .15s; }}
.rc:hover{{ box-shadow:0 4px 12px rgba(0,0,0,.08); }}
.rc-bar{{ width:50px; display:flex; align-items:center; justify-content:center; flex-shrink:0; font-size:22px; color:#fff; }}
.rc-ok .rc-bar{{ background:var(--pass); }}
.rc-ng .rc-bar{{ background:var(--fail); }}
.rc-ok{{ background:var(--pass-bg); }}
.rc-ng{{ background:var(--fail-bg); }}
.rc-body{{ flex:1; display:flex; align-items:center; gap:16px; padding:14px 18px; }}
.rc-thumb{{ width:68px; height:120px; object-fit:cover; border-radius:6px; cursor:pointer; border:1px solid var(--bd); flex-shrink:0; background:#f1f5f9; }}
.rc-info{{ flex:1; min-width:0; }}
.rc-name{{ font-size:16px; font-weight:700; margin-bottom:4px; }}
.rc-ok .rc-name{{ color:#065f46; }}
.rc-ng .rc-name{{ color:#991b1b; }}
.rc-desc{{ font-size:13px; color:var(--t2); line-height:1.6; word-break:break-word; }}
.rc-step{{ font-size:12px; color:var(--t3); margin-top:6px; }}

/* ---- issues (failures only) ---- */
.issue-card{{ border-left:4px solid var(--fail); padding:14px 18px; margin-bottom:10px; background:var(--fail-bg); border-radius:0 8px 8px 0; display:flex; gap:14px; align-items:flex-start; }}
.issue-card:last-child{{ margin-bottom:0; }}
.issue-thumb{{ width:56px; height:100px; object-fit:cover; border-radius:4px; cursor:pointer; border:1px solid var(--fail-bd); flex-shrink:0; }}
.issue-body{{ flex:1; min-width:0; }}
.issue-target{{ font-weight:700; color:#991b1b; font-size:15px; }}
.issue-desc{{ font-size:13px; color:var(--t2); margin-top:4px; line-height:1.6; word-break:break-word; }}
.issue-meta{{ font-size:12px; color:var(--t3); margin-top:4px; }}

/* ---- steps table ---- */
.tbl-wrap{{ overflow-x:auto; }}
.tbl{{ width:100%; border-collapse:separate; border-spacing:0; font-size:13px; }}
.tbl thead th{{ background:#f8fafc; padding:10px 12px; text-align:left; font-weight:600; color:var(--t2); border-bottom:2px solid var(--bd); white-space:nowrap; position:sticky; top:0; }}
.tbl tbody td{{ padding:10px 12px; border-bottom:1px solid #f1f5f9; vertical-align:middle; }}
.tbl tbody tr:hover{{ background:#f8fafc; }}
.tbl .c-num{{ font-weight:700; color:var(--t3); font-size:12px; text-align:center; width:36px; }}
.tbl .c-img{{ width:48px; height:85px; object-fit:cover; border-radius:4px; cursor:pointer; border:1px solid var(--bd); background:#f1f5f9; }}
.tbl .c-desc{{ max-width:300px; word-break:break-word; }}
.tbl .c-target{{ max-width:160px; word-break:break-word; }}
.tbl .c-reason{{ max-width:260px; word-break:break-word; color:var(--t3); font-size:12px; }}
.tbl .c-dur{{ color:var(--t3); white-space:nowrap; }}

/* ---- badges ---- */
.bg{{ display:inline-block; padding:2px 10px; border-radius:20px; font-size:12px; font-weight:600; white-space:nowrap; }}
.bg-ok{{ background:var(--pass-bg); color:var(--pass); border:1px solid var(--pass-bd); }}
.bg-bs{{ background:#ecfdf5; color:#059669; border:1px solid #a7f3d0; }}
.bg-bf{{ background:#fef2f2; color:#dc2626; border:1px solid #fecaca; }}
.bg-ld{{ background:var(--info-bg); color:var(--info); border:1px solid var(--info-bd); }}
.bg-fl{{ background:var(--fail-bg); color:var(--fail); border:1px solid var(--fail-bd); }}
.bg-er{{ background:var(--warn-bg); color:var(--warn); border:1px solid var(--warn-bd); }}

/* ---- lightbox ---- */
.lb{{ display:none; position:fixed; inset:0; background:rgba(0,0,0,.78); z-index:9999; align-items:center; justify-content:center; cursor:zoom-out; backdrop-filter:blur(4px); }}
.lb.on{{ display:flex; }}
.lb img{{ max-width:92vw; max-height:92vh; border-radius:8px; box-shadow:0 20px 60px rgba(0,0,0,.5); }}

/* ---- footer ---- */
.ft{{ text-align:center; font-size:12px; color:var(--t3); margin-top:40px; padding-top:20px; border-top:1px solid var(--bd); }}
</style>
</head>
<body>
<div class="wrap">

    <!-- header -->
    <div class="hd">
        <h1>阻断测试报告</h1>
        <div class="hd-meta">
            <span>应用 <b>{escape(result.app_package)}</b></span>
            <span>平台 <b>{escape(result.platform)}</b></span>
            <span>耗时 <b>{duration_str}</b></span>
            <span>时间 <b>{start_time_str}</b></span>
        </div>
    </div>

    <!-- banner -->
    <div class="banner {banner_cls}">
        <div class="b-icon">{banner_icon}</div>
        <div class="b-txt">
            <h2>{banner_title}</h2>
            <p>{banner_desc}</p>
        </div>
    </div>

    <!-- stats -->
    <div class="stats">
        <div class="st">
            <div class="v">{result.total_steps}</div>
            <div class="l">总步骤</div>
        </div>
        <div class="st">
            <div class="v">{click_count}</div>
            <div class="l">点击数</div>
        </div>
        <div class="st st-g">
            <div class="v">{len(successes)}</div>
            <div class="l">阻断成功</div>
        </div>
        <div class="st st-r">
            <div class="v">{len(failures)}</div>
            <div class="l">阻断失败</div>
        </div>
    </div>

    <!-- 阻断结果 -->
    {result_cards_html}

    <!-- 发现的问题 -->
    {issues_html}

    <!-- 步骤明细 -->
    {steps_html}

    <div class="ft">AI 阻断测试报告 &middot; 由 qwen3-vl-plus 视觉模型驱动 &middot; {time.strftime('%Y-%m-%d %H:%M:%S')}</div>
</div>

<!-- lightbox -->
<div class="lb" id="lb" onclick="this.classList.remove('on')">
    <img id="lb-img" src="" alt="">
</div>
<script>
function showImg(s){{ document.getElementById('lb-img').src=s; document.getElementById('lb').classList.add('on'); }}
document.addEventListener('keydown',function(e){{ if(e.key==='Escape') document.getElementById('lb').classList.remove('on'); }});
</script>
</body>
</html>"""

        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html_content)

        logger.info(f"HTML报告已生成: {html_path}")
        return html_path

    def generate_json(self, result: ExplorationResult, output_dir: str, l_class: str = "") -> str:
        """
        生成JSON格式的测试摘要报告。

        :param result: 探索结果对象
        :param output_dir: 报告输出目录
        :param l_class: 小类ID（用于文件命名）
        :return: 生成的JSON文件路径
        """
        filename = f"{l_class}.json"
        json_path = os.path.join(output_dir, filename)

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
            "exploration_graph": result.exploration_graph,
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

    # ==================== 私有构建方法 ====================

    def _img_src(self, path: str) -> str:
        """将截图转为base64 data URL，使报告完全自包含"""
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

    def _badge(self, result_str: str) -> str:
        """为步骤结果生成对应样式的标签"""
        m = {
            "success": ("成功", "bg-ok"),
            "block_success": ("阻断成功", "bg-bs"),
            "block_failure": ("阻断失败", "bg-bf"),
            "loading": ("加载中", "bg-ld"),
            "failed": ("失败", "bg-fl"),
            "error": ("异常", "bg-er"),
        }
        text, cls = m.get(result_str, (result_str, "bg-ok"))
        return f'<span class="bg {cls}">{text}</span>'

    def _strip_prefix(self, desc: str) -> str:
        """去掉阻断描述的前缀"""
        for prefix in ("阻断成功: ", "阻断失败: ", "阻断成功（持续loading）: "):
            if desc.startswith(prefix):
                return desc[len(prefix):]
        return desc

    def _build_result_cards(self, result: ExplorationResult) -> str:
        """构建阻断测试结果卡片（全部控件，包括成功和失败）"""
        issues = [i for i in result.issues_found
                  if i["type"] in ("block_success", "block_failure")]
        if not issues:
            return """<div class="sec">
                <div class="sec-t">阻断测试结果</div>
                <p style="color:var(--t3)">未检测到任何阻断结果。</p>
            </div>"""

        cards = []
        for issue in issues:
            ok = issue["type"] == "block_success"
            cls = "rc-ok" if ok else "rc-ng"
            icon = "&#10004;" if ok else "&#10008;"

            img_src = self._img_src(issue.get("screenshot", ""))
            thumb = (f'<img class="rc-thumb" src="{img_src}" '
                     f'onclick="showImg(this.src)" alt="截图">'
                     if img_src else "")

            desc = escape(self._strip_prefix(issue.get("description", "")))
            target = escape(issue.get("target", "未知"))

            cards.append(f"""<div class="rc {cls}">
                <div class="rc-bar">{icon}</div>
                <div class="rc-body">
                    {thumb}
                    <div class="rc-info">
                        <div class="rc-name">{target}</div>
                        <div class="rc-desc">{desc}</div>
                        <div class="rc-step">步骤 #{issue.get('step', '?')}</div>
                    </div>
                </div>
            </div>""")

        return f"""<div class="sec">
            <div class="sec-t">阻断测试结果 <span class="cnt">{len(issues)} 项</span></div>
            <div class="rc-list">{''.join(cards)}</div>
        </div>"""

    def _build_issues_section(self, failures: list) -> str:
        """构建发现的问题区块（仅展示阻断失败项）"""
        if not failures:
            return ""

        items = []
        for f in failures:
            img_src = self._img_src(f.get("screenshot", ""))
            thumb = (f'<img class="issue-thumb" src="{img_src}" '
                     f'onclick="showImg(this.src)" alt="截图">'
                     if img_src else "")

            desc = escape(self._strip_prefix(f.get("description", "")))
            target = escape(f.get("target", "未知"))

            items.append(f"""<div class="issue-card">
                {thumb}
                <div class="issue-body">
                    <div class="issue-target">{target}</div>
                    <div class="issue-desc">{desc}</div>
                    <div class="issue-meta">步骤 #{f.get('step', '?')}</div>
                </div>
            </div>""")

        return f"""<div class="sec">
            <div class="sec-t">发现的问题 <span class="cnt">{len(failures)} 项</span></div>
            {''.join(items)}
        </div>"""

    def _build_steps_table(self, result: ExplorationResult) -> str:
        """构建步骤明细表格（文字不截断，用CSS自动换行）"""
        rows = []
        for step in result.steps:
            action = step.action_taken
            target = ""
            if action.target_element:
                target = action.target_element.text or action.target_element.name

            img_src = self._img_src(step.screenshot_path)
            thumb = (f'<img class="c-img" src="{img_src}" '
                     f'onclick="showImg(this.src)" alt="步骤{step.step_number}">'
                     if img_src else '')

            badge = self._badge(step.action_result)
            desc = escape(step.screen_description or "")
            target_esc = escape(target)
            reason = escape(action.reasoning or "")

            rows.append(f"""<tr>
                <td class="c-num">{step.step_number}</td>
                <td>{thumb}</td>
                <td class="c-desc">{desc}</td>
                <td>{action.action.value}</td>
                <td class="c-target">{target_esc}</td>
                <td>{badge}</td>
                <td class="c-dur">{step.duration_ms}ms</td>
                <td class="c-reason">{reason}</td>
            </tr>""")

        return f"""<div class="sec">
            <div class="sec-t">步骤明细 <span class="cnt">{len(result.steps)} 步</span></div>
            <div class="tbl-wrap">
            <table class="tbl">
                <thead><tr>
                    <th>#</th><th>截图</th><th>界面描述</th><th>操作</th>
                    <th>目标</th><th>结果</th><th>耗时</th><th>AI理由</th>
                </tr></thead>
                <tbody>{''.join(rows)}</tbody>
            </table>
            </div>
        </div>"""

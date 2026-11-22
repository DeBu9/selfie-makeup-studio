#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""给方案卡注入「手法动效示意」。

手法难教的从来不是"用什么产品"，而是三件事：落在哪、往哪个方向走、范围多大。
这三件事正好是规则引擎已经算出来的参数，所以能画准——比模糊的视频有用。

表现方式：笔画按路径"长出来"（stroke-dashoffset 动画），刷头沿同一路径移动并自动
转向（animateMotion rotate=auto），浅色宽底 + 箭头标出范围与方向。三样东西同时动，
才不会出现"只有一个小点在爬"的观感。

关键约束：动画的 0% 帧才是"隐藏"，静止态（去掉 animation）必须是画完的样子——
导出长图/PDF 时会把动画全部关掉，那时看到的应该是完整示意图。
"""
import argparse
import json
import os
import re

START, END = "<!--MOTION_START-->", "<!--MOTION_END-->"

# 极简正脸线稿（viewBox 300x340）。坐标约定：
# 眉 y≈110，眼 cx=125/175（右眼 眼头 160 / 眼尾 190，y=140）
# 鼻梁 y 150–195，鼻头 y≈199，腮红区 y≈180–215，唇 y=240
GRAD = ('<defs>'
        '<radialGradient id="gz1"><stop offset="0" stop-color="#b26a4c" stop-opacity=".44"/>'
        '<stop offset=".60" stop-color="#b26a4c" stop-opacity=".28"/>'
        '<stop offset="1" stop-color="#b26a4c" stop-opacity="0"/></radialGradient>'
        '<filter id="soft" x="-40%" y="-40%" width="180%" height="180%">'
        '<feGaussianBlur stdDeviation="3.2"/></filter>'
        '<radialGradient id="gz2"><stop offset="0" stop-color="#8c7f76" stop-opacity=".34"/>'
        '<stop offset=".60" stop-color="#8c7f76" stop-opacity=".21"/>'
        '<stop offset="1" stop-color="#8c7f76" stop-opacity="0"/></radialGradient>'
        '</defs>')

FACE = GRAD + (
    '<path class="skin" d="M150 22C92 22 62 72 62 142c0 78 40 158 88 158s88-80 88-158'
    'c0-70-30-120-88-120Z"/>'
    '<path class="line" d="M104 110q20-11 42-2"/><path class="line" d="M196 110q-20-11-42-2"/>'
    '<ellipse class="line" cx="125" cy="140" rx="15" ry="7.5"/>'
    '<ellipse class="line" cx="175" cy="140" rx="15" ry="7.5"/>'
    '<path class="line" d="M150 150v42q0 8 9 5"/>'
    '<path class="line" d="M128 240q22-10 44 0"/><path class="line" d="M128 240q22 16 44 0"/>'
)

STYLE = """<style>
.motion{padding:26px 40px; border-bottom:1px solid var(--line)}
.motion .lead{margin:0 0 18px; font-size:13px; color:var(--ink-3); line-height:1.7}
.motion .lead b{color:var(--accent); font-weight:600}
.mgrid{display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:14px}
.mcard{border:1px solid var(--line); border-radius:12px; padding:12px 12px 11px; background:#fffdfa}
.mcard h4{margin:0 0 5px; font-size:12.5px; font-weight:600; color:var(--ink-2)}
.mcard h4 i{display:inline-block; width:5px; height:5px; border-radius:99px; background:var(--accent);
  margin-right:6px; vertical-align:1px; animation:mpulse 3.2s ease-in-out infinite}
.mcard p{margin:0; font-size:11.5px; line-height:1.7; color:var(--ink-3)}
.msvg{width:100%; max-width:336px; height:auto; display:block; margin:0 auto 6px}
.msvg .skin{fill:#fbf0e8}
.msvg .line{fill:none; stroke:#cdbfb2; stroke-width:1.5; stroke-linecap:round}
.msvg .iris{fill:#d3c2b2}
.msvg .zone,.msvg .zone2{opacity:.95; animation:mpulse 3.2s ease-in-out infinite}
.msvg .zone{fill:url(#gz1)}
.msvg .zone2{fill:url(#gz2)}
.msvg .base{fill:none; stroke:rgba(140,74,63,.13); stroke-width:7; stroke-linecap:round}
.msvg .base.trail2{stroke:rgba(111,97,89,.13); stroke-width:6.5}
.msvg .base.swipe{stroke-width:15; stroke:rgba(178,106,76,.20); filter:url(#soft)}
.msvg .draw.swipe{stroke-width:4.2; stroke:#b26a4c}
.msvg .draw{fill:none; stroke:#a4634f; stroke-width:2.9; stroke-linecap:round;
  stroke-dasharray:var(--len); stroke-dashoffset:0;
  animation:mdraw 3.2s linear infinite}
.msvg .draw.trail2{stroke:#6f6159; stroke-width:3}
.msvg .brush{animation:mfade 3.2s ease-in-out infinite}
.msvg .halo{fill:#c4705b; fill-opacity:.20}
.msvg .knob{fill:#c4705b}
.msvg .nono{fill:none; stroke:#c0392b; stroke-width:1.4; stroke-dasharray:4 4; opacity:.6}
@keyframes mdraw{0%{stroke-dashoffset:var(--len); opacity:1} 48%{stroke-dashoffset:0}
  88%{stroke-dashoffset:0; opacity:1} 100%{stroke-dashoffset:0; opacity:0}}
@keyframes mpulse{0%,100%{opacity:.5} 42%{opacity:1} 88%{opacity:1} 100%{opacity:.5}}
@keyframes mfade{0%{opacity:0} 6%{opacity:1} 80%{opacity:1} 88%{opacity:0} 100%{opacity:0}}
@media print{ .msvg .brush{display:none} }
@media (max-width:640px){
  .mgrid{grid-template-columns:1fr}
  .motion{padding-left:22px; padding-right:22px}
}
</style>"""


VIEW = "0 0 300 340"


def wrap(inner, view=VIEW):
    """view 决定放大倍数：整脸 1x 看不清运笔，局部放大到 2x 才看得出"往哪走"。"""
    return f'<svg class="msvg" viewBox="{view}" aria-hidden="true">' + FACE + inner + "</svg>"


def path_len(d, steps=24):
    """粗算路径长度，用来写 stroke-dasharray。

    不依赖 pathLength 属性：老版 WebKit 会忽略它，那时 dasharray 会被当成绝对长度，
    短线一次画完，动效就"消失"了——iPhone 上正好会踩到。
    """
    toks = re.findall(r"[MmLlHhVvQqCcZz]|-?\d*\.?\d+(?:e-?\d+)?", d)
    i, cur, start, total, cmd = 0, (0.0, 0.0), (0.0, 0.0), 0.0, "M"

    def num():
        nonlocal i
        v = float(toks[i]); i += 1
        return v

    def seg(p0, p1, p2, p3=None):
        nonlocal total
        prev = p0
        n = steps if p3 else steps
        for k in range(1, n + 1):
            t = k / n
            if p3 is None:
                x = (1 - t) ** 2 * p0[0] + 2 * (1 - t) * t * p1[0] + t * t * p2[0]
                y = (1 - t) ** 2 * p0[1] + 2 * (1 - t) * t * p1[1] + t * t * p2[1]
            else:
                x = ((1 - t) ** 3 * p0[0] + 3 * (1 - t) ** 2 * t * p1[0]
                     + 3 * (1 - t) * t * t * p2[0] + t ** 3 * p3[0])
                y = ((1 - t) ** 3 * p0[1] + 3 * (1 - t) ** 2 * t * p1[1]
                     + 3 * (1 - t) * t * t * p2[1] + t ** 3 * p3[1])
            total += ((x - prev[0]) ** 2 + (y - prev[1]) ** 2) ** 0.5
            prev = (x, y)
        return prev

    while i < len(toks):
        if toks[i].isalpha():
            cmd = toks[i]; i += 1
            if cmd in "Zz":
                total += ((cur[0] - start[0]) ** 2 + (cur[1] - start[1]) ** 2) ** 0.5
                cur = start
            continue
        rel = cmd.islower()
        c = cmd.upper()
        if c == "M":
            x, y = num(), num()
            cur = (cur[0] + x, cur[1] + y) if rel else (x, y)
            start = cur
            cmd = "l" if rel else "L"
        elif c == "L":
            x, y = num(), num()
            nxt = (cur[0] + x, cur[1] + y) if rel else (x, y)
            total += ((nxt[0] - cur[0]) ** 2 + (nxt[1] - cur[1]) ** 2) ** 0.5
            cur = nxt
        elif c == "H":
            x = num()
            nxt = (cur[0] + x, cur[1]) if rel else (x, cur[1])
            total += abs(nxt[0] - cur[0]); cur = nxt
        elif c == "V":
            y = num()
            nxt = (cur[0], cur[1] + y) if rel else (cur[0], y)
            total += abs(nxt[1] - cur[1]); cur = nxt
        elif c == "Q":
            x1, y1, x, y = num(), num(), num(), num()
            p1 = (cur[0] + x1, cur[1] + y1) if rel else (x1, y1)
            p2 = (cur[0] + x, cur[1] + y) if rel else (x, y)
            cur = seg(cur, p1, p2)
        elif c == "C":
            x1, y1, x2, y2, x, y = num(), num(), num(), num(), num(), num()
            p1 = (cur[0] + x1, cur[1] + y1) if rel else (x1, y1)
            p2 = (cur[0] + x2, cur[1] + y2) if rel else (x2, y2)
            p3 = (cur[0] + x, cur[1] + y) if rel else (x, y)
            cur = seg(cur, p1, p2, p3)
        else:
            i += 1
    return total


def stroke(d, cls="trail", pen=True, delay=0.0):
    """一条笔画 + 沿它移动的刷头。

    浅色宽底=范围，深色线=笔迹（dashoffset 从头长到尾），箭头=方向，刷头=运笔。
    静止态（动画被关掉时）必须是"已经画完"，否则导出的长图会是空白的。
    """
    L = path_len(d)
    out = f'<path class="base {cls}" d="{d}"/>'
    out += (f'<path class="draw {cls}" d="{d}" '
            f'style="--len:{L:.1f}px;animation-delay:{delay}s"/>')
    if pen:
        out += (f'<g class="brush" style="animation-delay:{delay}s">'
                f'<animateMotion path="{d}" dur="3.2s" begin="{delay}s" repeatCount="indefinite" '
                f'rotate="auto" calcMode="linear" keyPoints="0;1;1" keyTimes="0;.48;1"/>'
                f'<circle class="halo" r="9.5"/><ellipse class="knob" rx="5.4" ry="3.6"/>'
                f'<path class="knob" d="M4.6 -2.6 11 0 4.6 2.6Z"/></g>')
    return out


def panel(title, note, inner, view=VIEW):
    return (f'<div class="mcard">{wrap(inner, view)}'
            f'<h4><i></i>{title}</h4><p>{note}</p></div>')


# ── 腮红 ────────────────────────────────────────────
BLUSH = {
    "cheek_center_diagonal": ("腮红 · 从面中斜向上",
        "从鼻翼旁起笔，斜着扫向太阳穴。手往上抬，不要左右平拖——平拖会让脸显宽。",
        '<ellipse class="zone" cx="204" cy="184" rx="27" ry="13" transform="rotate(-24 204 184)"/>',
        "M182 193Q204 178 224 170"),
    "high_cheek": ("腮红 · 高位往太阳穴",
        "落点在眼下、颧骨上方，向太阳穴方向带。位置越高越显精神；低于鼻尖就显脸垮。",
        '<ellipse class="zone" cx="204" cy="176" rx="26" ry="12" transform="rotate(-22 204 176)"/>',
        "M184 185Q204 174 220 169"),
    "apple_to_temple": ("腮红 · 苹果肌到太阳穴",
        "笑一下找到苹果肌最高点，从那里往后斜带，不要往下包住脸颊。",
        '<ellipse class="zone" cx="202" cy="190" rx="28" ry="14" transform="rotate(-18 202 190)"/>',
        "M180 199Q202 186 224 178"),
    "high_under_eye_wide": ("腮红 · 眼下横向铺开",
        "整片压在眼下、横向略微跨过鼻梁。面积大但颜色要极淡，要的是血气感，不是红脸。",
        '<ellipse class="zone" cx="180" cy="170" rx="42" ry="11"/>',
        "M142 172Q180 164 218 170"),
    "cheek_center_sheer": ("腮红 · 面中一点",
        "只在面中一点，向外晕开到看不见边界。宁可看不见，也不要两团色。",
        '<ellipse class="zone" cx="198" cy="188" rx="18" ry="11"/>',
        "M186 192q9-4 18-4"),
    "inner_cheek_sheer": ("腮红 · 内脸颊极淡",
        "贴着鼻翼外侧极淡一层，把面中提亮。范围一大就显脏。",
        '<ellipse class="zone" cx="191" cy="186" rx="16" ry="10"/>',
        "M181 190q8-4 16-4"),
}


def blush_part(placement):
    t = BLUSH.get(placement)
    if not t:
        return {"title": "腮红 · 斜向上", "note": "从面中斜着扫向太阳穴，不要平拖。",
                "view": "130 100 150 170", "zone": "",
                "strokes": [("M160 208Q184 194 206 182", "swipe", True)]}
    title, note, zone, d = t
    return {"title": title, "note": note, "zone": zone, "view": "130 100 150 170",
            "strokes": [(d, "swipe", True)]}


def blush_panel(placement):
    return render_panel(blush_part(placement))


def contour_part(areas, tone):
    """山根起笔、止于鼻头；眉头与起笔之间必须留空——红虚线标的就是这个空档。"""
    joined = " ".join(areas or [])
    strokes = [(("M138 168q-1 16 0 28"), "trail2", True),
               (("M162 168q1 16 0 28"), "trail2", False)]
    if "cheekbone" in joined:
        strokes.append(("M226 190q-7 15-16 26", "trail2", False))
    if "jawline" in joined:
        strokes.append(("M211 238q-13 19-31 27", "trail2", False))
    note = ("从山根两侧垂直往下，上端停在眉头下方。红色虚线就是必须留空的那一段——连上去会形成两撇灰影。"
            if "warm" not in (tone or "") else
            "从山根两侧往下薄扫。红色虚线是眉头到山根的空档，不要连上去。")
    return {"title": "修容 · 山根起、不连眉头", "note": note, "view": "126 106 150 170",
            "zone": '<path class="nono" d="M146 116v34"/>', "strokes": strokes}


def contour_panel(areas, tone):
    return render_panel(contour_part(areas, tone))


LINER = {
    "inner": ("眼线 · 只填内眼线",
        "只把睫毛根的空隙填满，睫毛上方不要画。画上去反而会把眼睛压小。",
        "M160 131q14-1 28-1"),
    "inner_plus_thin_tail": ("眼线 · 内眼线 + 细尾",
        "内眼线填满睫毛根，眼尾再平拉 2–3mm 后收尖。尾巴要细，粗一点就显凶。",
        "M160 131q18-1 32-2"),
    "thin_straight_tail": ("眼线 · 细平拉尾",
        "眼尾平着拉出 3–4mm，不上扬也不下垂——平拉最显眼型干净。",
        "M164 131q18-1 30-2"),
    "outer_half": ("眼线 · 从眼尾 1/3 起笔",
        "从眼尾 1/3 处才开始画，眼头留白。这样眼型显长，而不是显凶。",
        "M176 131q10-1 18-1"),
    "thin_long_wing": ("眼线 · 细长上扬",
        "贴睫毛根拉到眼尾，再向上提 3–5mm 收尖。上扬角度越小越好，大了变凶。",
        "M164 131q18-2 26-6"),
}


def liner_part(liner):
    t = LINER.get((liner or {}).get("type")) or ("眼线 · 只填内眼线",
                                               "把睫毛根填满，不要画在睫毛上方。", "M160 131q14-1 28-1")
    return {"title": t[0], "note": t[1], "zone": "", "view": "134 88 150 170",
            "strokes": [(t[2], "trail", True)]}


def liner_panel(liner):
    return render_panel(liner_part(liner))


SHADOW = {
    "within_crease": ("眼影 · 只到褶皱内",
        "闭眼把颜色铺在双眼皮褶皱内，睁眼刚好露一条。超出眼窝骨就是肿。",
        ('<ellipse class="zone2" cx="174" cy="121" rx="21" ry="6.2"/>', "M158 125q16-3 32 0")),
    "crease_plus_outer": ("眼影 · 褶皱 + 眼尾三角",
        "主体在褶皱内，最深的一笔落在眼尾三角，往前不要带过眼中。",
        ('<ellipse class="zone2" cx="172" cy="121" rx="22" ry="6.6"/>'
         '<path class="zone2" d="M186 122l14 12-6 5-10-12Z"/>', "M156 125q14-4 26-1")),
    "allover_wash": ("眼影 · 全眼皮薄铺",
        "整片薄薄一层就够，淡到像眼皮本色。叠第二层就会显脏。",
        ('<ellipse class="zone2" cx="174" cy="119" rx="26" ry="8.4"/>', "M152 123q21-5 44 0")),
}


def shadow_part(rng):
    t = SHADOW.get(rng)
    if not t:
        return {"title": "眼影 · 只到褶皱内", "note": "颜色不超出眼窝骨，出了就显肿。",
                "view": "134 76 150 170", "zone": "",
                "strokes": [("M158 125q16-3 32 0", "trail2", True)]}
    title, note, (zone, d) = t
    return {"title": title, "note": note, "zone": zone, "view": "134 76 150 170",
            "strokes": [(d, "trail2", True)]}


def shadow_panel(rng):
    return render_panel(shadow_part(rng))


def render_panel(part):
    inner = part["zone"] + "".join(stroke(d, cls, pen, i * 0.22)
                                   for i, (d, cls, pen) in enumerate(part["strokes"]))
    return panel(part["title"], part["note"], inner, part.get("view", VIEW))


def panel_specs(spec):
    """四张示意图的结构化数据：谁都能拿去用（卡片用 SVG，GIF 用逐帧）。"""
    p = spec["plan"]
    eye = p.get("eye", {})
    return [
        blush_part(p["blush"].get("placement", "")),
        contour_part(p["contour"].get("areas", []), p["contour"].get("tone", "")),
        liner_part(eye.get("liner")),
        shadow_part(eye.get("shadow", {}).get("range", "")),
    ]


def build(spec):
    cards = [render_panel(x) for x in panel_specs(spec)]
    return (START + "\n" + STYLE +
            '\n<section class="motion"><h2>手法动效 · 只看位置、方向、范围</h2>'
            '<p class="lead">动的是笔，不是浓淡。每一步只回答一个问题：从哪起笔、往哪走、停在哪。'
            "深色线是笔走过的<b>轨迹</b>，浅色宽带是<b>范围</b>，小刷头从起笔点走到收笔点，循环播放。"
            "打印、或在微信里看长图时会自动定格成完整轨迹。</p>"
            '<div class="mgrid">' + "".join(cards) + "</div></section>\n" + END)


def insert(html, block):
    if START in html and END in html:
        head, _, rest = html.partition(START)
        _, _, tail = rest.partition(END)
        html = head + tail.lstrip("\n")
    idx = html.find("<h2>照这个顺序化")
    if idx == -1:
        idx = html.find("<footer>")
    if idx == -1:
        return html
    head = html.rfind("<section>", 0, idx)
    at = head if head != -1 else idx
    return html[:at] + block + "\n\n  " + html[at:]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", required=True)
    ap.add_argument("--only", help="只处理某套风格 id")
    args = ap.parse_args()

    index = json.load(open(os.path.join(args.plan, "index.json"), encoding="utf-8"))
    n = 0
    for v in index["variants"]:
        sid = v["style_id"]
        if args.only and sid != args.only:
            continue
        sp = os.path.join(args.plan, os.path.basename(v["files"]["spec"]))
        card = os.path.join(args.plan, os.path.basename(v["files"]["card_html"]))
        if not (os.path.exists(sp) and os.path.exists(card)):
            print(f"  skip {sid}")
            continue
        spec = json.load(open(sp, encoding="utf-8"))
        html = open(card, encoding="utf-8").read()
        open(card, "w", encoding="utf-8").write(insert(html, build(spec)))
        n += 1
        print(f"  {sid} ← 手法动效")
    print(f"OK {n} 张卡片")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把方案卡导成「手机能看、微信能发」的两样东西：长图 + PDF。

为什么需要这个：微信不渲染 HTML。.html 发进聊天只是"一个文件"，
手机微信点开会提示无法打开；手机浏览器能开，但前提是先落地成本地文件，
对普通用户来说等于不可用。长图和 PDF 是微信原生支持的格式，点开就能看。

高度不写死：先用无头浏览器在 1 倍缩放下截一张整页，扫描灰度像素找最后一行
非背景内容，再按 1.5 倍渲染并精确裁切。写死高度迟早会把页脚裁掉。
"""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile

BROWSERS = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
]
W = 1000          # 逻辑宽度
SCALE = 1.5       # 长图放大倍数（手机上放大看字不糊）
PROBE_H = 9000    # 探测用的高度上限

# 卡片里的手法图是循环动画，截图/打印会停在随便一帧（常常是刚起笔，看起来像没画）。
# 导出前注入这段：关掉动画、笔迹补到完整、刷头隐藏。只用在这份临时副本上，
# 原卡片的 HTML 仍然保留动效。
STATIC_CSS = ('<style id="wb-static">'
              '*,*::before,*::after{animation:none!important;transition:none!important}'
              '.msvg .brush{display:none!important}'
              '.msvg .draw{stroke-dashoffset:0!important;opacity:1!important}'
              '</style>')


def find_browser():
    for p in BROWSERS:
        if os.path.exists(p):
            return p
    return None


def run_browser(browser, args):
    """每次启动都用独立的 user-data-dir：连开几次 headless 会撞同一个配置目录，
    后面那次会静默失败（返回 0 但没有产物），踩过一次。"""
    profile = tempfile.mkdtemp(prefix="wb-shot-")
    try:
        return subprocess.run([browser, "--headless=new", "--disable-gpu",
                               f"--user-data-dir={profile}"] + args,
                              capture_output=True, text=True)
    finally:
        shutil.rmtree(profile, ignore_errors=True)


def shot(browser, url, out_png, height, scale=1.0):
    out_png = os.path.abspath(out_png)
    r = run_browser(browser, ["--hide-scrollbars",
                              f"--force-device-scale-factor={scale}",
                              f"--window-size={W},{height}",
                              f"--screenshot={out_png}", url])
    if not os.path.exists(out_png):
        raise SystemExit(f"截图失败：{out_png}\n{r.stderr[-300:]}")
    return out_png


def content_bottom(png, height):
    """扫描灰度各行，找最后一行与底部背景色不同的内容行。"""
    gray = png + ".gray"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", png, "-vf", "format=gray",
                    "-f", "rawvideo", gray], check=True)
    data = open(gray, "rb").read()
    os.remove(gray)
    if len(data) < W * height:
        return height

    def row(y):
        return data[y * W:(y + 1) * W]

    tail = row(height - 5)
    bg = max(set(tail), key=tail.count)
    for y in range(height - 1, -1, -1):
        r = row(y)
        if max(abs(p - bg) for p in r) > 6:
            return y + 1
    return height


def static_copy(card_path):
    """生成一份"定格"副本给截图/打印用。必须放在原目录，相对路径才不变。"""
    html = open(card_path, encoding="utf-8").read()
    if "</head>" in html:
        html = html.replace("</head>", STATIC_CSS + "</head>", 1)
    else:
        html = STATIC_CSS + html
    tmp = os.path.join(os.path.dirname(card_path), "_wb_static.html")
    open(tmp, "w", encoding="utf-8").write(html)
    return tmp


def export(card_path, out_dir, browser, name=None):
    card_path = os.path.abspath(card_path)
    out_dir = os.path.abspath(out_dir)   # Edge 的相对路径按它自己的 cwd 解析，必须绝对
    base = name or os.path.splitext(os.path.basename(card_path))[0]
    os.makedirs(out_dir, exist_ok=True)

    still = static_copy(card_path)
    url = "file:///" + still.replace("\\", "/")
    probe = os.path.join(out_dir, "_probe.png")
    shot(browser, url, probe, PROBE_H, 1.0)
    bottom = content_bottom(probe, PROBE_H)
    os.remove(probe)

    pad = 24
    long_png = os.path.join(out_dir, f"{base}_长图.png")
    # Edge 的命令行参数遇到中文文件名会静默失败，一律先写 ASCII 临时名再改名
    raw = os.path.join(out_dir, "_raw.png")
    shot(browser, url, raw, bottom + pad, SCALE)
    os.replace(raw, long_png)
    h = int((bottom + pad) * SCALE) // 2 * 2
    tmp = long_png + ".tmp.png"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", long_png,
                    "-vf", f"crop={int(W * SCALE)}:{h}:0:0", tmp], check=True)
    os.replace(tmp, long_png)

    jpg = os.path.join(out_dir, f"{base}_长图.jpg")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", long_png, "-q:v", "3", jpg], check=True)

    pdf = os.path.join(out_dir, f"{base}.pdf")
    raw_pdf = os.path.join(out_dir, "_raw.pdf")
    run_browser(browser, ["--no-pdf-header-footer", f"--print-to-pdf={raw_pdf}", url])
    if not os.path.exists(raw_pdf):
        raise SystemExit("PDF 生成失败（Edge 返回 0 但没有产物）")
    os.replace(raw_pdf, pdf)
    try:
        os.remove(still)
    except OSError:
        pass
    return long_png, jpg, pdf, bottom


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--card", required=True, help="card_0X_*.html，可逗号分隔多个")
    ap.add_argument("--out", default="makeup_out/share")
    ap.add_argument("--name", help="输出文件名（单卡时用）")
    args = ap.parse_args()

    browser = find_browser()
    if not browser:
        raise SystemExit("找不到 Edge / Chrome")

    for c in [x.strip() for x in args.card.split(",") if x.strip()]:
        png, jpg, pdf, h = export(c, args.out, browser, args.name)
        mb = os.path.getsize(jpg) / 1024 / 1024
        print(f"  {os.path.basename(png)}  {h}px  {mb:.1f}MB")
        print(f"  {os.path.basename(pdf)}")
    print(f"OK → {os.path.abspath(args.out)}")


if __name__ == "__main__":
    main()

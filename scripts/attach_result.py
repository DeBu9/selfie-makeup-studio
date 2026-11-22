#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把原图与妆后效果图嵌进方案卡。

规则引擎只产出参数和提示词，图片是之后才生成的，所以图片要在出图后再注入。
注入采用 base64 编码内嵌，卡片保持单文件自包含——可直接打印、转发、发给别人看。

用法：
    python scripts/attach_result.py \
        --plan makeup_out/plan \
        --original selfie.jpg \
        --map korean_glass=makeup_out/images/korean.png \
        --map smart_office=makeup_out/images/office.png

也可以不写 --map，把图片按 `<style_id>.png` 命名放进 --images-dir，脚本自动匹配。
"""
import argparse
import base64
import io
import json
import os
import sys

MAX_SIDE = 1000      # 卡片里图片的最大边长，够打印 A4 且不让 HTML 变几 MB
JPEG_QUALITY = 88
START, END = "<!--BA_START-->", "<!--BA_END-->"


def encode(path, max_side=MAX_SIDE):
    """读图 → 等比缩放 → JPEG → base64。失败时退回原始文件直接内嵌。"""
    try:
        from PIL import Image
    except ImportError:
        with open(path, "rb") as f:
            raw = f.read()
        mime = "image/png" if path.lower().endswith(".png") else "image/jpeg"
        return f"data:{mime};base64," + base64.b64encode(raw).decode()

    im = Image.open(path)
    im = im.convert("RGB")
    w, h = im.size
    scale = min(1.0, max_side / max(w, h))
    if scale < 1.0:
        im = im.resize((int(w * scale), max(1, int(h * scale))), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=JPEG_QUALITY, optimize=True, progressive=True)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def build_block(original_data, result_data, style_name, intensity_text):
    figs = []
    if original_data:
        figs.append(
            '<figure><img src="' + original_data + '" alt="原始照片">'
            '<figcaption><b>原始照片</b><span>分析基准</span></figcaption></figure>'
        )
    if result_data:
        figs.append(
            '<figure><img src="' + result_data + '" alt="妆后效果">'
            '<figcaption><b>妆后效果</b><span>' + style_name + "</span>"
            '<span class="badge">强度 ' + intensity_text + "</span></figcaption></figure>"
        )
    if not figs:
        return ""
    cls = "shots" if (original_data and result_data) else "shots solo"
    return START + '\n<section class="' + cls + '">\n  ' + "\n  ".join(figs) + "\n</section>\n" + END


def strip_block(html):
    """已有注入块就先摘掉，保证重复执行不会叠图。"""
    if START in html and END in html:
        head, _, rest = html.partition(START)
        _, _, tail = rest.partition(END)
        return head + tail.lstrip("\n")
    return html


def insert_after_header(html, block):
    if not block:
        return html
    idx = html.find("</header>")
    if idx == -1:
        return html
    cut = idx + len("</header>")
    return html[:cut] + "\n" + block + html[cut:]


def attach_one(card_path, block, result_path, md_path=None):
    html = strip_block(open(card_path, encoding="utf-8").read())
    html = insert_after_header(html, block)
    open(card_path, "w", encoding="utf-8").write(html)

    if md_path and os.path.exists(md_path) and result_path:
        md = open(md_path, encoding="utf-8").read()
        lines = md.split("\n")
        marker = "> 妆后效果图："
        lines = [ln for ln in lines if not ln.startswith(marker)]
        # 放在引用块（风格 ID 那行）之后的第一处空行前
        at = 1
        for i, ln in enumerate(lines[:6]):
            if ln.startswith(">"):
                at = i + 1
        lines.insert(at, marker + os.path.basename(result_path))
        lines.insert(at + 1, "")
        open(md_path, "w", encoding="utf-8").write("\n".join(lines))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", required=True, help="plan_makeup.py 的输出目录（含 index.json）")
    ap.add_argument("--original", help="用户原始自拍，作为对比左侧")
    ap.add_argument("--images-dir", help="按 <style_id>.png 约定命名的图片目录")
    ap.add_argument("--map", action="append", default=[], metavar="STYLE=PATH",
                    help="显式指定某套方案用哪张图，可重复")
    ap.add_argument("--no-md", action="store_true", help="不改 Markdown 卡片")
    args = ap.parse_args()

    index_path = os.path.join(args.plan, "index.json")
    if not os.path.exists(index_path):
        sys.exit(f"找不到 {index_path}，先跑 plan_makeup.py")
    index = json.load(open(index_path, encoding="utf-8"))

    explicit = {}
    for item in args.map:
        if "=" not in item:
            sys.exit(f"--map 需要 STYLE=PATH 的形式，收到：{item}")
        k, v = item.split("=", 1)
        explicit[k.strip()] = v.strip()

    original_data = None
    if args.original:
        if not os.path.exists(args.original):
            sys.exit(f"找不到原图：{args.original}")
        original_data = encode(args.original)

    hits = 0
    for v in index.get("variants", []):
        sid = v["style_id"]
        img = explicit.get(sid)
        if not img and args.images_dir:
            for ext in (".png", ".jpg", ".jpeg", ".webp"):
                cand = os.path.join(args.images_dir, sid + ext)
                if os.path.exists(cand):
                    img = cand
                    break
        files = v.get("files", {})
        card = os.path.join(args.plan, os.path.basename(files.get("card_html", "")))
        if not os.path.exists(card):
            print(f"  skip {sid}：没有卡片 {card}")
            continue
        if not img or not os.path.exists(img):
            # 没有对应图就只放原图，卡片仍然可用
            block = build_block(original_data, None, v["style_name"], f"{v['intensity']:.2f}")
            attach_one(card, block, None, None if args.no_md else os.path.join(
                args.plan, os.path.basename(files.get("card_md", ""))))
            print(f"  {sid}：仅原图（缺效果图）")
            continue
        block = build_block(original_data, encode(img), v["style_name"], f"{v['intensity']:.2f}")
        md = os.path.join(args.plan, os.path.basename(files.get("card_md", "")))
        attach_one(card, block, img, None if args.no_md else md)
        hits += 1
        print(f"  {sid} ← {os.path.basename(img)}")

    print(f"OK 注入 {hits} 张卡片 → {args.plan}")


if __name__ == "__main__":
    main()

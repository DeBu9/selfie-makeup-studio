#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
自拍妆效 · 确定性规则引擎
输入：面部分析 JSON（视觉模型产出）+ 用户需求 JSON
输出：3 套候选妆容规格（Makeup Spec）、每套的图像编辑提示词、方案卡（md + html）

用法：
  python plan_makeup.py --analysis analysis.json --brief brief.json --out out/
  python plan_makeup.py --analysis a.json --brief b.json --out out/ --count 3
  python plan_makeup.py --analysis a.json --brief '{"occasion":"通勤"}' --out out/ --styles water,chinese_mood
  python plan_makeup.py --selftest
"""
import argparse
import json
import os
import re
import sys

try:
    import pathlib
    BASE = pathlib.Path(__file__).resolve().parent.parent
except Exception:  # pragma: no cover
    BASE = pathlib.Path(".")
DATA_DIR = BASE / "data"
TEMPLATE = BASE / "assets" / "plan-card.html"


# ────────────────────────────── 基础工具 ──────────────────────────────
def load_json(p):
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


FR = load_json(DATA_DIR / "face_rules.json")
RECIPES = {r["id"]: r for r in load_json(DATA_DIR / "style_recipes.json")["recipes"]}
PALETTES = load_json(DATA_DIR / "palettes.json")

COLOR_FAMILIES = PALETTES["color_families"]
SEASONS = PALETTES["seasons"]


def g(d, *path, default=None):
    cur = d
    for p in path:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(p)
        if cur is None:
            return default
    return cur


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def uniq(seq):
    out = []
    for x in seq:
        if x and x not in out:
            out.append(x)
    return out


# ────────────────────────────── 枚举映射 ──────────────────────────────
FACE_SHAPE_ALIAS = {
    "oval": "oval", "egg": "oval", "鹅蛋脸": "oval",
    "round": "round", "圆脸": "round",
    "square": "square", "rectangle": "square", "方脸": "square",
    "soft_square": "soft_square", "方圆脸": "soft_square",
    "long": "long", "oblong": "long", "长脸": "long",
    "diamond": "diamond", "菱形脸": "diamond",
    "heart": "heart", "inverted_triangle": "heart", "心形脸": "heart",
    "triangle": "triangle", "pear": "triangle", "梨形脸": "triangle",
}
SHAPE_ZH = {
    "oval": "鹅蛋脸", "round": "圆脸", "square": "方脸", "soft_square": "方圆脸",
    "long": "长脸", "diamond": "菱形脸", "heart": "心形脸", "triangle": "梨形脸",
}
FEATURE_MAP = {
    "small": "light_feature", "light": "light_feature", "淡颜": "light_feature", "light_feature": "light_feature",
    "medium": "medium_feature", "medium_feature": "medium_feature", "中等": "medium_feature",
    "large": "dense_feature", "dense": "dense_feature", "浓颜": "dense_feature", "dense_feature": "dense_feature",
}
FEATURE_ZH = {"light_feature": "淡颜", "medium_feature": "中等量感", "dense_feature": "浓颜"}

OCCASION_MULT = {
    "上课": 0.80, "通勤": 0.85, "面试": 0.85, "会议": 0.85, "商务": 0.90,
    "日常": 1.00, "周末": 0.95, "咖啡厅": 0.95, "咖啡": 0.95, "见家长": 0.95,
    "约会": 1.05, "出游": 1.05, "旅行": 1.05, "咖啡约会": 1.00,
    "拍照": 1.15, "自拍": 1.10, "拍照出片": 1.15, "婚礼": 1.20,
    "派对": 1.30, "晚宴": 1.30, "音乐节": 1.25, "夜店": 1.30, "酒吧": 1.25,
}

# 不同场合的"理想强度"：晚宴要的是明显妆感，通勤要的是几乎看不出
TARGET_BY_OCC = {"派对": 0.55, "晚宴": 0.55, "音乐节": 0.55, "夜店": 0.55, "酒吧": 0.50,
                 "婚礼": 0.50, "拍照": 0.42, "自拍": 0.40, "出游": 0.35, "约会": 0.35,
                 "周末": 0.30, "日常": 0.30, "上课": 0.25, "通勤": 0.28, "面试": 0.28}

FINISH_ZH = {"dewy": "水光微湿", "satin": "缎面薄透", "soft_matte": "柔雾半哑光",
             "matte": "全哑光", "cloud": "雾面柔焦", "none": "不上底妆"}
UNDERTONE_ZH = {"warm": "暖调", "cool": "冷调", "neutral": "中性调", "olive": "橄榄调"}
SEASON_ZH = {"light_spring": "浅春", "true_spring": "正春", "bright_spring": "亮春",
             "light_summer": "浅夏", "true_summer": "正夏", "soft_summer": "柔夏",
             "soft_autumn": "柔秋", "true_autumn": "正秋", "deep_autumn": "深秋",
             "deep_winter": "深冬", "true_winter": "正冬", "bright_winter": "亮冬"}
DEPTH_ZH = lambda d: ("几乎看不出，只修光影" if d < 0.18 else
                      "淡淡一层，社交距离刚好" if d < 0.28 else
                      "看得见颜色，但不重" if d < 0.40 else
                      "层次清楚，边缘要更用力晕" if d < 0.58 else
                      "饱和度高，一次只加一点")
COVERAGE_ZH = {"sheer": "极薄，只匀肤色", "light": "薄涂一层", "light_medium": "薄涂可叠第二层",
               "medium": "中等遮瑕", "medium_full": "中高遮瑕", "full": "高遮瑕"}
POWDER_ZH = {"none": "不定妆", "t_zone": "只压 T 区", "t_zone_only": "只压 T 区 + 眼下",
             "t_zone_and_cheek": "T 区 + 脸颊外缘", "t_zone_full": "T 区重点压",
             "minimal": "只用定妆喷雾", "cheek_light": "脸颊轻扫", "full": "全脸薄定",
             "spray_only": "只用喷雾"}

BROW_SHAPE_ZH = {
    "soft_natural": "柔和自然眉", "slight_arch": "微挑眉", "slight_arch_defined": "清晰的微挑眉",
    "curved": "弧形眉（眉峰圆润）", "flat_slightly_long": "平直微长眉", "flat_soft_curve": "平缓微弧眉",
    "flat_slight_arch": "平直微挑眉", "natural": "自然眉", "natural_arch": "自然带弧",
    "natural_hair_stroke": "毛流感自然眉", "wild_soft_arch": "野生柔弧眉（毛流向上）",
    "wild_arched": "野生高挑眉", "high_arch_defined": "高挑眉（轮廓清晰）",
    "defined_arch": "清晰拱形眉", "brushed_up": "梳起的野生眉", "thin_slight_arch": "细微挑眉",
    "defined": "轮廓清晰的眉", "soft_arch": "柔和弧眉",
}
BROW_ARCH_ZH = {"low": "低平（几乎不上扬）", "medium": "中等弧度", "high": "高挑", "natural": "顺原生眉骨"}
BROW_THICK_ZH = {"thin": "细", "medium": "中等", "medium_thick": "中等偏粗", "thick": "粗"}

EYE_RANGE_ZH = {
    "within_crease": "双眼皮褶皱内（睁眼刚好露出）",
    "crease_plus_outer": "褶皱内 + 眼尾三角",
    "visible_lid_only": "单眼皮睁眼可见的那条边（不超过 3mm）",
    "allover_wash": "整个眼窝薄薄一层",
    "smoked_outer": "眼尾向外烟熏，范围不超过眼窝",
    "cut_crease": "截断式：先浅色铺满眼窝，再用深色画出假褶皱",
    "lid_allover": "整个上眼皮",
    "slightly_above_crease": "褶皱上缘一点点（不超过 2mm）",
}
LINER_ZH = {
    "inner_only": "只填内眼线（睫毛根空隙），眼尾不拉出",
    "inner": "内眼线 + 眼尾顺眼型收住",
    "inner_plus_thin_tail": "内眼线 + 眼尾极细平拉 2–3mm",
    "outer_half": "从眼尾 1/3 起笔，贴睫毛根向外，眼尾平拉 3mm",
    "thin_straight_tail": "细线，眼尾平拉 4mm",
    "thin_long_wing": "细线，眼尾上扬 3–5mm",
    "cat_eye": "猫眼，眼尾上扬 6mm 以上",
    "full_soft_wing": "整条眼线 + 柔和上扬",
    "brown_soft_wing": "棕色柔和上扬眼线",
    "none_or_tightline": "不画眼线（最多填内眼线）",
    "silver_lower": "上眼线极细 + 下眼睑银色线",
}
LASH_ZH = {
    "thin": "薄刷一层，只加密不加长", "medium": "刷两层，重点眼尾",
    "thick": "浓密（可加单簇假睫毛）", "cluster_outer": "眼尾加三簇单簇假睫毛",
    "cluster_fox_eye": "眼尾拉长型簇状假睫毛", "dense_false": "整排假睫毛",
    "dense": "浓密睫毛膏两层以上", "none_or_thin": "不刷或极薄",
}
CONTOUR_AREA_ZH = {
    "nose_side_light": "鼻梁两侧（极淡）", "nose_side": "鼻梁两侧", "nose": "鼻部整体",
    "nose_double_c": "山根两侧 + 鼻头小括号", "jawline": "下颌线（耳下→嘴角）",
    "jawline_broad": "下颌线偏外（耳下→下颌角下方）", "jaw_angle": "下颌角",
    "jaw_angle_arc": "下颌角（从角上方起笔画弧线）", "jaw_angle_soft": "下颌角（轻扫）",
    "jawline_light": "下颌线（轻）", "jawline_soft": "下颌线（轻扫）",
    "cheekbone": "颧骨外下方", "cheekbone_soft": "颧骨外下方（轻）",
    "cheekbone_hollow": "颧骨下方凹陷（从后向前）", "cheekbone_below": "颧骨最高点之下",
    "hairline": "发际线", "hairline_sides": "两侧发际线", "hairline_top": "额顶发际线",
    "temple": "太阳穴", "temple_light_adjacent": "太阳穴旁", "chin_bottom": "下巴底缘",
    "eyelid": "眼窝",
}
HL_MATTE_ZH = {
    "under_eye_triangle": "眼下三角（泪沟区）", "under_eye": "眼下", "nasolabial": "法令纹",
    "nasal_base": "鼻基底", "midface": "面中（眼下→鼻翼）",
    "midface_vertical": "面中竖条（鼻梁→下巴方向）", "temple": "太阳穴凹陷",
    "chin_center": "下巴中央", "jawline": "下颌线外侧", "chin_sides": "下巴两侧",
    "forehead_center": "额头中央",
}
HL_SHIM_ZH = {
    "nose_tip": "鼻尖（一小点）", "nose_bridge": "鼻梁中段（断开，不拉通条）",
    "nose_bridge_center": "鼻梁中段中央", "inner_corner": "眼头 3mm 内",
    "cheekbone_top": "颧骨最高点", "cheekbone_top_wide": "颧骨最高点（横向带开）",
    "cheekbone_top_inner": "颧骨偏内高点", "brow_bone": "眉骨下", "cupid_bow": "唇峰",
    "center_lid": "眼皮中央",
}
BLUSH_PLACE_ZH = {
    "cheek_center_sheer": "苹果肌中央，范围不超过一枚硬币，颜色最淡",
    "cheek_center_diagonal": "苹果肌中央起笔，向太阳穴斜上带 3cm",
    "diagonal_up": "从苹果肌下缘斜向上扫到太阳穴方向，刷子约 45°",
    "diagonal_up_outward": "从鼻翼外侧斜向上扫到太阳穴，落点比常规再靠外 1cm",
    "diagonal_up_across_nose_bridge": "从一侧颧骨下方斜向上，刷子横过鼻梁上缘（不低于鼻尖）连到另一侧，形成一条截断中庭的横带",
    "horizontal_across_cheek": "横向平扫：从鼻翼旁水平向外，宽约 2cm，不向上带",
    "rounded_apple": "在苹果肌上打圈，边界晕成圆形",
    "inner_cheek": "面中偏内（靠近鼻翼），范围小而淡",
    "inner_cheek_sheer": "面中偏内、极淡，只提气色",
    "high_cheek": "颧骨上方、靠近眼下的高位",
    "high_under_eye_wide": "眼下高位，横向带开，可轻扫过鼻梁",
    "lower_apple": "苹果肌偏下，但不低于鼻尖",
    "upper_cheek_diagonal": "上颊斜扫",
    "cheeks_and_nose_bridge": "两颊 + 鼻梁横带（晒伤妆画法）",
    "below_cheekbone_blend": "颧骨下方，与修容融合",
    "apple_to_temple": "从苹果肌向太阳穴带开",
    "outer_cheek_sculpt": "脸颊外侧，与修容同一方向",
}
LIP_SHAPE_ZH = {
    "blurred_edge": "模糊唇线（用唇线笔向内晕成影子）", "defined": "唇线清晰，用唇线笔勾形",
    "defined_soft": "唇线清晰但边缘略柔", "gradient": "咬唇：中心深、边缘淡",
    "inner": "只涂唇内侧，向外晕开", "overlined_0_5mm": "唇线外扩 0.5mm 造厚度",
    "overlined": "唇线外扩约 1mm", "overlined_soft": "唇线外扩 0.5–1mm 并晕开",
    "corner_lifted": "嘴角微微上扬", "v_bow_line": "唇峰画成小 V", "natural": "按原生唇形涂满",
}
LIP_FINISH_ZH = {"soft_matte": "丝绒雾面", "matte": "全哑光", "glossy": "镜面（有水光）", "satin": "缎面"}

# 英文（提示词）映射
EN_FINISH = {"dewy": "dewy glass-skin finish with a soft sheen but no grease",
             "satin": "soft satin finish with natural light reflection",
             "soft_matte": "soft-matte blurred finish", "matte": "fully matte finish",
             "cloud": "soft-focus cloud-skin finish"}
EN_COVERAGE = {"sheer": "sheer", "light": "light", "light_medium": "light to medium",
               "medium": "medium", "medium_full": "medium to full", "full": "full"}
EN_BROW = {
    "soft_natural": "soft natural", "slight_arch": "slightly arched", "slight_arch_defined": "slightly arched and clearly defined",
    "curved": "curved with a rounded arch", "flat_slightly_long": "flat and slightly longer",
    "flat_soft_curve": "flat with a soft curve", "flat_slight_arch": "flat and slightly arched",
    "natural": "natural", "natural_arch": "naturally arched", "natural_hair_stroke": "natural with visible hair strokes",
    "wild_soft_arch": "soft wild-arched with visible upward hair strokes", "wild_arched": "bold wild-arched",
    "high_arch_defined": "high-arched and sharply defined", "defined_arch": "defined arched",
    "brushed_up": "brushed up with clear brow gel", "thin_slight_arch": "thin and slightly arched",
    "defined": "defined", "soft_arch": "soft arched",
}
EN_ARCH = {"low": "low", "medium": "medium", "high": "high", "natural": "natural"}
EN_THICK = {"thin": "thin", "medium": "medium", "medium_thick": "medium-thick", "thick": "thick"}
EN_RANGE = {
    "within_crease": "inside the eyelid crease", "crease_plus_outer": "inside the crease and extended into the outer-corner triangle",
    "visible_lid_only": "only on the narrow visible band of the eyelid", "allover_wash": "as a sheer wash over the whole eyelid",
    "smoked_outer": "smoked outward from the outer corner", "cut_crease": "in a cut-crease shape",
    "lid_allover": "across the whole upper lid", "slightly_above_crease": "just above the crease",
}
EN_LINER = {
    "inner_only": "a thin inner lash-line liner only, no wing", "inner": "a thin inner liner following the eye shape",
    "inner_plus_thin_tail": "an inner liner with a very short straight tail",
    "outer_half": "an outer-half liner starting at the outer third with a 3 mm straight tail",
    "thin_straight_tail": "a thin liner with a 4 mm straight tail",
    "thin_long_wing": "a thin liner with a slight 3-5 mm upward wing",
    "cat_eye": "a black cat-eye wing", "full_soft_wing": "a full soft-winged liner",
    "brown_soft_wing": "a soft brown wing", "none_or_tightline": "no visible eyeliner",
    "silver_lower": "a silver line along the lower lash line",
}
EN_LASH = {"thin": "thin natural mascara", "medium": "two coats of mascara concentrated on the outer lashes",
           "thick": "dense lashes", "cluster_outer": "three cluster lashes at the outer corner",
           "cluster_fox_eye": "fox-eye cluster lashes", "dense_false": "a full set of false lashes",
           "dense": "dense mascara", "none_or_thin": "barely any mascara"}
EN_CONTOUR = {
    "nose_side_light": "a very light shading along the nose sides", "nose_side": "shading along the nose sides",
    "nose": "shading on the nose", "nose_double_c": "the nose sides plus a small bracket at the nose tip",
    "jawline": "the jawline from below the ear toward the mouth corner", "jawline_broad": "the outer jawline",
    "jaw_angle": "the jaw angle", "jaw_angle_arc": "a curved arc above the jaw angle",
    "jaw_angle_soft": "a soft touch on the jaw angle", "jawline_light": "a light touch on the jawline",
    "jawline_soft": "a light sweep along the jawline",
    "cheekbone": "below the outer cheekbone", "cheekbone_soft": "a soft touch below the outer cheekbone",
    "cheekbone_hollow": "the hollow under the cheekbone blended from back to front",
    "cheekbone_below": "just below the highest point of the cheekbone", "hairline": "the hairline",
    "hairline_sides": "the side hairlines", "hairline_top": "the top hairline", "temple": "the temples",
    "temple_light_adjacent": "beside the temples", "chin_bottom": "under the chin", "eyelid": "the eyelids",
}
EN_HL_MATTE = {
    "under_eye_triangle": "the under-eye triangle", "under_eye": "under the eyes", "nasolabial": "the nasolabial folds",
    "nasal_base": "the nasal base", "midface": "the center of the face", "midface_vertical": "a vertical strip through the center of the face",
    "temple": "the temples", "chin_center": "the center of the chin", "jawline": "along the jawline",
    "chin_sides": "the sides of the chin", "forehead_center": "the center of the forehead",
}
EN_HL_SHIM = {
    "nose_tip": "a tiny point on the nose tip", "nose_bridge": "the middle of the nose bridge, broken not continuous",
    "nose_bridge_center": "the center of the nose bridge", "inner_corner": "the inner eye corners",
    "cheekbone_top": "the top of the cheekbones", "cheekbone_top_wide": "a wide band across the cheekbone tops",
    "cheekbone_top_inner": "the inner cheekbone tops", "brow_bone": "under the brow bone",
    "cupid_bow": "the cupid's bow", "center_lid": "the center of the lids",
}
EN_BLUSH = {
    "cheek_center_sheer": "on the center of the cheeks, sheer",
    "cheek_center_diagonal": "from the center of the cheek diagonally toward the temple",
    "diagonal_up": "swept diagonally upward from the lower cheek toward the temple",
    "diagonal_up_outward": "swept diagonally up and out from beside the nose toward the temple",
    "diagonal_up_across_nose_bridge": "swept diagonally upward and carried lightly across the bridge of the nose, never below the nose tip",
    "horizontal_across_cheek": "swept horizontally outward from beside the nose in a 2 cm band",
    "rounded_apple": "in a soft round shape on the apples of the cheeks",
    "inner_cheek": "on the inner cheek near the nose, small and sheer",
    "inner_cheek_sheer": "on the inner cheek, extremely sheer",
    "high_cheek": "high on the cheekbones just under the eyes",
    "high_under_eye_wide": "high under the eyes and blended wide",
    "lower_apple": "low on the apples of the cheeks but above the nose tip",
    "upper_cheek_diagonal": "diagonally across the upper cheeks",
    "cheeks_and_nose_bridge": "across both cheeks and the bridge of the nose",
    "below_cheekbone_blend": "under the cheekbones blended into the contour",
    "apple_to_temple": "from the apples toward the temples",
    "outer_cheek_sculpt": "on the outer cheeks in the same direction as the contour",
}
EN_LIP_SHAPE = {
    "blurred_edge": "blurred edges", "defined": "a crisp defined lip line",
    "defined_soft": "a defined but softly blended edge", "gradient": "a gradient, deeper in the center",
    "inner": "applied only to the inner lip and blended out", "overlined_0_5mm": "over-lined by half a millimeter",
    "overlined": "over-lined", "overlined_soft": "softly over-lined",
    "corner_lifted": "with the corners lifted a touch", "v_bow_line": "with a small V at the cupid's bow",
    "natural": "following the natural lip shape",
}
EN_LIP_FINISH = {"soft_matte": "soft-matte", "matte": "matte", "glossy": "glossy", "satin": "satin"}

STYLE_EN = {
    "water": "barely-there skin-first makeup", "thai_water": "Thai water-light makeup",
    "korean_glass": "Korean glass-skin makeup", "japanese_sheer": "Japanese sheer transparent makeup",
    "chinese_mood": "modern Chinese atmospheric makeup", "thai_glam": "Thai glam sculpted makeup",
    "western_sculpt": "Western sculpted makeup", "abg": "ABG bronzed makeup",
    "latte": "latte monochrome makeup", "clean_girl": "clean-girl minimal makeup",
    "strawberry": "strawberry sun-kissed makeup", "y2k_ice": "Y2K icy-blue makeup",
    "smart_office": "clean smart-office makeup", "evening_glam": "evening red-carpet makeup",
}

# 冷/暖核心色系：只在这些色上做"冷暖纠正"，避免把风格特色一起抹掉
COOL_CORE = {"cool_pink", "milk_pink", "mauve", "rose", "fuchsia", "plum", "berry",
             "wine", "blue_red", "icy_white", "color_ice_blue"}
WARM_CORE = {"peach", "apricot", "terracotta", "coral", "tomato", "brick", "pumpkin",
             "chocolate", "warm_bronze", "latte_strong_warm"}

# 用于"三套方案要给三种气质"的分组
STYLE_GROUP = {
    "water": "clean", "clean_girl": "clean", "japanese_sheer": "clean", "korean_glass": "clean",
    "thai_water": "refined", "smart_office": "refined", "latte": "refined", "strawberry": "refined",
    "chinese_mood": "mood", "y2k_ice": "editorial",
    "thai_glam": "bold", "abg": "bold", "western_sculpt": "bold", "evening_glam": "bold",
}


# ────────────────────────────── 分析归一化 ──────────────────────────────
def normalize(an):
    """把视觉模型的输出规整成规则表能吃的形状，缺失字段给安全默认。"""
    a = an.get("analysis", an)
    fs = a.get("face_shape", {}) or {}
    ft = a.get("features", {}) or {}
    imp = a.get("impression", {}) or {}

    shape_raw = str(fs.get("label", "")).strip().lower()
    shape = FACE_SHAPE_ALIAS.get(shape_raw, "oval")
    ratios = fs.get("ratios", {}) or {}
    uml = ratios.get("upper_mid_lower") or [1.0, 1.0, 1.0]
    while len(uml) < 3:
        uml.append(1.0)

    eyes = ft.get("eyes", {}) or {}
    nose = ft.get("nose", {}) or {}
    lips = ft.get("lips", {}) or {}
    brows = ft.get("brows", {}) or {}
    skin = ft.get("skin", {}) or {}
    hair = a.get("hair", {}) or {}

    feature_key = FEATURE_MAP.get(str(imp.get("feature_size", "medium")).lower(), "medium_feature")

    eye_shape_raw = str(eyes.get("shape", "double")).lower()
    eye_shape = eye_shape_raw if eye_shape_raw in FR["eyes"] else (
        "puffy" if "puff" in eye_shape_raw or "hood" in eye_shape_raw else
        "mono" if "mono" in eye_shape_raw or "single" in eye_shape_raw else
        "inner_double" if "inner" in eye_shape_raw else "double"
    )

    nose_key = "low_bridge"
    if str(nose.get("tip", "")).lower() in ("bulbous", "bulbous_tip", "rounded_wide"):
        nose_key = "bulbous_tip"
    elif str(nose.get("width", "")).lower() in ("wide", "broad"):
        nose_key = "wide"
    elif str(nose.get("length", "")).lower() == "long":
        nose_key = "long"
    elif str(nose.get("length", "")).lower() == "short":
        nose_key = "short"
    elif str(nose.get("dorsum", "")).lower() in ("aquiline", "hooked"):
        nose_key = "aquiline"
    else:
        nose_key = "low_bridge"

    lip_key = "thin" if str(lips.get("volume", "")).lower() in ("thin", "small") else (
        "full" if str(lips.get("volume", "")).lower() in ("full", "thick") else
        "large" if str(lips.get("volume", "")).lower() in ("large", "wide") else "soft_bow"
    )

    undertone_raw = str(skin.get("undertone", "neutral")).lower()
    if "olive" in undertone_raw:
        undertone = "olive"
    elif "warm" in undertone_raw:
        undertone = "warm"
    elif "cool" in undertone_raw:
        undertone = "cool"
    else:
        undertone = "neutral"

    return {
        "shape": shape,
        "ratios": {
            "length_width": float(ratios.get("length_width", 1.35) or 1.35),
            "upper": float(uml[0]), "mid": float(uml[1]), "lower": float(uml[2]),
            "temple_hollow": bool(ratios.get("temple_hollow", False)),
            "midface_flat": bool(ratios.get("midface_flat", False)),
        },
        "eye_shape": eye_shape,
        "eye_spacing": str(eyes.get("spacing", "neutral")).lower(),
        "puffiness": float(eyes.get("puffiness", 0.2) or 0.2),
        "nose": nose_key,
        "lip": lip_key,
        "brow_density": str(brows.get("density", "medium")).lower(),
        "skin_texture": str(skin.get("texture", "normal")).lower().replace(" ", "_"),
        "skin_depth": int(skin.get("depth", 4) or 4),
        "undertone": undertone,
        "season": str(skin.get("season", "")).lower(),
        "concerns": [str(c).lower() for c in (skin.get("concerns") or [])],
        "feature": feature_key,
        "bone": float(imp.get("bone_vs_flesh", 0.5) or 0.5),
        "contrast": str(imp.get("contrast", "medium")).lower(),
        "hair_color": str(hair.get("color", "")).lower(),
        "raw": a,
    }


# ────────────────────────────── 选风格 ──────────────────────────────
def occasion_mult(occasion):
    for k, v in OCCASION_MULT.items():
        if k in (occasion or ""):
            return v
    return 1.0


def reject_hits(recipe, n, occ):
    """配方的"什么情况下不适合"是数据里写好的，别在代码里再硬编码一遍。"""
    hits = []
    for cond in (recipe.get("reject_when") or []):
        c = str(cond).lower()
        bad = False
        if c in ("light_feature", "very_light_feature"):
            bad = n["feature"] == "light_feature"
        elif c in ("round_face", "round_soft_face_only"):
            bad = n["shape"] in ("round", "soft_square")
        elif "flat" in c:
            bad = n["ratios"]["midface_flat"]
        elif c.startswith("long_midface"):
            bad = n["ratios"]["mid"] > 1.04
        elif c == "active_acne":
            bad = n["skin_texture"] == "active_acne" or "acne" in n["concerns"]
        elif c == "very_oily_skin":
            bad = n["skin_texture"] == "oily"
        elif c == "cool_undertone":
            bad = n["undertone"] == "cool"
        elif c == "warm_undertone":
            bad = n["undertone"] == "warm"
        elif c == "warm_olive_undertone":
            bad = n["undertone"] == "olive"
        elif c in ("deep_autumn", "deep_winter", "high_contrast_winter"):
            bad = n["season"] == c
        elif c in ("needs_coverage", "heavy_blemish_needs_coverage"):
            bad = any(x in n["concerns"] for x in ("acne", "pores"))
        elif c == "sharp_bone_structure":
            bad = n["bone"] > 0.65
        elif c == "daily_office_context":
            bad = any(k in occ for k in ("通勤", "面试", "会议", "商务"))
        if bad:
            hits.append(cond)
    return hits


def score_recipe(rid, r, n, brief):
    score, why = 0.0, []
    occ = brief.get("occasion", "") or ""

    if occ and any(occ in o or o in occ for o in r.get("occasions", [])):
        score += 3.0
        why.append("场合匹配")

    imp = FR["impression"].get(n["feature"], {})
    if rid in imp.get("prefer_styles", []):
        score += 2.0
    if rid in imp.get("reject_styles", []):
        score -= 8.0
    hits = reject_hits(r, n, occ)
    if hits:
        score -= 3.0 * len(hits)

    # 冷暖 / 季型一致性
    reject_colors = FR["undertone_rules"][n["undertone"]].get("reject", [])
    fams = (r.get("blush", {}).get("families", []) or []) + (r.get("lip", {}).get("families", []) or [])
    for f in fams:
        if f in reject_colors:
            score -= 2.5
    if n["season"] in SEASONS:
        for f in r.get("lip", {}).get("families", []) or []:
            if f in SEASONS[n["season"]].get("avoid", []):
                score -= 2.0

    # 脸型 / 比例
    if rid in ("western_sculpt", "evening_glam", "abg") and n["shape"] in ("round", "soft_square", "oval"):
        score -= 4.0
    if rid in ("japanese_sheer",) and n["ratios"]["mid"] > 1.04:
        score -= 2.0
    if rid == "chinese_mood" and n["ratios"]["mid"] > 1.04:
        score -= 1.5
    if rid == "y2k_ice" and n["undertone"] != "cool":
        score -= 5.0
    if rid == "clean_girl" and n["skin_texture"] in ("active_acne", "texture_rough"):
        score -= 3.0
    if rid == "korean_glass" and n["skin_texture"] in ("active_acne",):
        score -= 2.5
    if g(r, "base", "finish") == "dewy" and n["skin_texture"] in ("oily", "active_acne", "texture_rough"):
        score -= 2.5
    if "acne" in n["concerns"] and rid in ("korean_glass", "clean_girl"):
        score -= 2.0
    if rid == "abg" and n["skin_depth"] <= 3:
        score -= 3.0

    # 强度可行性：配方本身超上限要扣分，但真正比较的是截断后的强度
    cap = min(imp.get("intensity_cap", 0.7),
              FR["face_shape"][n["shape"]]["intensity_cap"],
              FR["eyes"].get(n["eye_shape"], {}).get("intensity_cap", 1.0) or 1.0)
    eff = r["intensity"] * occasion_mult(occ)
    final = clamp(eff, 0.12, min(cap, 0.9))
    if eff > cap:
        score -= (eff - cap) * 3.0

    # 用户明确指定
    if brief.get("style") and brief["style"] != "auto":
        if rid == brief["style"]:
            score += 100.0
        else:
            score -= 50.0

    # 强度目标：和"这个场合的理想强度"比，而不是一律偏淡
    target = brief.get("intensity")
    if isinstance(target, (int, float)):
        score -= abs(final - float(target)) * 6.0
    else:
        # 用户要伪素颜时不罚"太淡"
        want_bare = any(k in (str(brief.get("note", "")) + occ) for k in ("伪素颜", "素颜", "看不出"))
        if not want_bare:
            tgt = 0.30
            for k, v in TARGET_BY_OCC.items():
                if k in occ:
                    tgt = v
                    break
            # 浓颜化 0.28 等于没化，淡颜化 0.45 会显脏，目标强度要跟着量感走
            tgt *= {"light_feature": 0.95, "medium_feature": 1.10, "dense_feature": 1.25}[n["feature"]]
            score -= abs(final - min(tgt, cap * 0.95)) * 8.0

    return score, why


def rank_styles(n, brief):
    scored = []
    for rid, r in RECIPES.items():
        s, _ = score_recipe(rid, r, n, brief)
        scored.append((s, rid))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return scored


def select_variants(scored, n, brief, count):
    """三套方案 = 主推 + 换个气质 + 妆感更明显的那版。
    先保"合适"（分数），再保"看得出区别"（气质组 + 强度差），不为了差异硬塞不合适的风格。"""
    if not scored:
        return []
    chosen = [scored[0][1]]
    used_groups = {STYLE_GROUP.get(chosen[0])}
    remaining = [x for x in scored if x[1] not in chosen]

    # 第 2 套：换一个气质组
    if count >= 2 and remaining:
        best = None
        for s, rid in remaining:
            val = s + (1.2 if STYLE_GROUP.get(rid) not in used_groups else 0.0)
            if best is None or val > best[0]:
                best = (val, rid)
        chosen.append(best[1])
        used_groups.add(STYLE_GROUP.get(best[1]))
        remaining = [x for x in remaining if x[1] not in chosen]

    # 第 3 套起：优先给"强度上一个台阶"的版本，但分数不能掉太多
    while len(chosen) < count and remaining:
        base_i = intent_of(chosen[0], n, brief)
        best_any = max(remaining, key=lambda x: x[0])
        hi = [x for x in remaining if intent_of(x[1], n, brief) >= base_i + 0.05]
        if hi:
            best_hi = max(hi, key=lambda x: x[0])
            pick = best_hi[1] if best_hi[0] >= best_any[0] - 2.0 else best_any[1]
        else:
            pick = best_any[1]
        chosen.append(pick)
        used_groups.add(STYLE_GROUP.get(pick))
        remaining = [x for x in remaining if x[1] != pick]
    return chosen[:count]


def effective_cap(n, rid=None):
    imp = FR["impression"].get(n["feature"], {})
    cap = min(imp.get("intensity_cap", 0.7), FR["face_shape"][n["shape"]]["intensity_cap"])
    if n["skin_texture"] in ("active_acne", "texture_rough"):
        cap = min(cap, 0.6)
    return cap


def intent_of(rid, n, brief):
    r = RECIPES[rid]
    occ = brief.get("occasion", "") or ""
    v = r["intensity"] * occasion_mult(occ)
    if isinstance(brief.get("intensity"), (int, float)):
        v = float(brief["intensity"])
    return clamp(v, 0.12, min(effective_cap(n, rid), 0.9))


# ────────────────────────────── 生成 Spec ──────────────────────────────
def align_families(fams, n, fallback):
    """冷暖纠正：暖皮去掉冷粉系、冷皮去掉暖橘系；全被去掉时用季型/肤色推荐色兜底。"""
    out = []
    for f in fams:
        if n["undertone"] == "warm" and f in COOL_CORE:
            continue
        if n["undertone"] == "cool" and f in WARM_CORE:
            continue
        if n["undertone"] == "olive" and f in ("pumpkin", "coral", "milk_pink", "neon_pink"):
            continue
        out.append(f)
    return out or list(fallback)[:1]


def build_spec(rid, n, brief):
    r = RECIPES[rid]
    shape_rule = FR["face_shape"][n["shape"]]
    eye_rule = FR["eyes"].get(n["eye_shape"], {})
    nose_rule = FR["nose"].get(n["nose"], {})
    lip_rule = FR["lips"].get(n["lip"], {})
    imp = FR["impression"].get(n["feature"], {})
    skin_rule = FR["skin"].get(n["skin_texture"], {})
    cap = effective_cap(n, rid)

    intensity = intent_of(rid, n, brief)
    trace = []

    # ── 底妆
    finish = g(r, "base", "finish", default="satin")
    if skin_rule.get("finish") and finish not in skin_rule["finish"]:
        finish = skin_rule["finish"][0]
        trace.append(f"肤质 {n['skin_texture']} → 妆效改为 {FINISH_ZH.get(finish, finish)}")
    coverage = g(r, "base", "coverage", default="light")
    if intensity < 0.25 and coverage in ("medium", "medium_full", "full"):
        coverage = "light"
        trace.append("强度 < 0.25 → 遮瑕降为薄涂，保留皮肤质感")
    powder = g(r, "base", "powder", default="t_zone")

    # ── 眉
    brow_shape = g(r, "brow", "shape", default="natural")
    if brow_shape in ("soft_natural", "natural", "natural_arch") and shape_rule.get("brow", {}).get("shape"):
        brow_shape = shape_rule["brow"]["shape"]
        trace.append(f"{SHAPE_ZH[n['shape']]} → 眉形改为{BROW_SHAPE_ZH.get(brow_shape, brow_shape)}")
    brow = {
        "shape": brow_shape,
        "arch": g(r, "brow", "arch", default=shape_rule.get("brow", {}).get("arch", "medium")),
        "thickness": g(r, "brow", "thickness", default=shape_rule.get("brow", {}).get("thickness", "medium")),
        "color": g(r, "brow", "color", default="gray_brown"),
        "density": g(r, "brow", "density", default=0.5),
        "note": g(r, "brow", "note", default="补空隙，不勾外框；眉头最淡"),
    }

    # ── 眼
    shadow = dict(g(r, "eye", "shadow", default={}) or {})
    shadow.setdefault("families", ["earth"])
    shadow.setdefault("finish", "matte")
    shadow.setdefault("range", "within_crease")
    shadow.setdefault("depth", 0.25)
    if n["eye_shape"] == "puffy" or n["puffiness"] >= 0.3:
        if shadow["finish"] in ("shimmer", "satin", "matte_plus_shimmer"):
            shadow["finish"] = "matte"
        shadow["range"] = "crease_plus_outer"
        trace.append("眼皮偏肿 → 眼影全哑光，范围压到褶皱 + 眼尾三角")
    if eye_rule.get("shadow_range") and n["eye_shape"] in ("mono", "inner_double"):
        shadow["range"] = eye_rule["shadow_range"]
    if n["raw"].get("features", {}).get("eyes", {}).get("lid_space") == "narrow" and shadow["range"] == "slightly_above_crease":
        shadow["range"] = "within_crease"
    shadow["depth"] = round(clamp(float(shadow["depth"]) * (0.7 + intensity), 0.08, 0.85), 2)

    liner = dict(g(r, "eye", "liner", default={}) or {})
    liner.setdefault("type", "inner_only")
    liner.setdefault("color", "dark_brown")
    if n["eye_shape"] in ("mono", "inner_double") and liner["type"] in ("cat_eye", "full_soft_wing", "brown_soft_wing") and intensity < 0.6:
        liner["type"] = "outer_half"
        trace.append("眼型偏内双/单眼皮 → 眼线改成从眼尾 1/3 起笔，不做全包")
    if eye_rule.get("liner") in ("outer_half_only", "inner_fill_thin") and intensity < 0.45:
        liner["type"] = "inner_only" if eye_rule["liner"] == "inner_fill_thin" else "outer_half"

    lash = dict(g(r, "eye", "lash", default={}) or {})
    lash.setdefault("coat", "thin")
    if n["eye_shape"] == "mono" and lash.get("coat") == "thick":
        lash["coat"] = "medium"

    aegyo = dict(g(r, "eye", "aegyo_sal", default={}) or {})
    if not aegyo and eye_rule.get("aegyo_sal"):
        aegyo = {"shadow": 0.15}
    if "假睫毛" in " ".join(brief.get("avoid", []) or []) and lash.get("type"):
        lash.pop("type", None)
        lash["coat"] = "thin"

    # ── 修容
    contour_strength = float(g(r, "contour", "strength", default=0.2))
    contour_strength = min(contour_strength, float(shape_rule.get("contour", {}).get("strength", 0.3)) + 0.08, 0.6)
    contour_areas = uniq((g(r, "contour", "areas", default=[]) or []) +
                         (shape_rule.get("contour", {}).get("areas", []) or []))
    contour = {
        "strength": round(clamp(contour_strength * (0.6 + intensity * 0.9), 0.06, 0.6), 2),
        "areas": contour_areas[:4],
        "tone": g(r, "contour", "tone", default="cool_gray_brown"),
    }
    if shape_rule.get("contour", {}).get("style") == "arc":
        contour["style"] = "arc"

    # ── 高光
    hl_mat = uniq((g(r, "highlight", "matte", default=[]) or []) +
                  (shape_rule.get("highlight", {}).get("matte", []) or []))
    hl_shim = uniq((g(r, "highlight", "shimmer", default=[]) or []) +
                   (shape_rule.get("highlight", {}).get("shimmer", []) or []))
    if intensity < 0.25:
        hl_shim = hl_shim[:2]
    highlight = {"matte": hl_mat[:4], "shimmer": hl_shim[:3]}

    # ── 腮红
    blush_place = g(r, "blush", "placement", default="cheek_center_sheer")
    if n["ratios"]["mid"] > 1.04:
        blush_place = "diagonal_up_across_nose_bridge"
        trace.append(f"中庭偏长（{n['ratios']['mid']:.2f}）→ 腮红横打截断中庭，鼻影不连眉头")
    elif n["ratios"]["mid"] < 0.96 and blush_place in ("cheek_center_sheer", "diagonal_up"):
        blush_place = "diagonal_up_outward"
    elif intensity <= 0.35 and shape_rule.get("blush", {}).get("placement") and blush_place in ("cheek_center_sheer", "diagonal_up"):
        blush_place = shape_rule["blush"]["placement"]
        trace.append(f"{SHAPE_ZH[n['shape']]} → 腮红位置按脸型改为 {blush_place}")

    raw_blush = g(r, "blush", "families", default=["peach"]) or ["peach"]
    blush_fams = [f for f in raw_blush
                  if f not in FR["undertone_rules"][n["undertone"]].get("reject", [])]
    season_blush = SEASONS.get(n["season"], {}).get("blush") or FR["undertone_rules"][n["undertone"]]["blush_families"]
    blush_fams = align_families(blush_fams or season_blush, n, season_blush)
    if set(blush_fams) != set(raw_blush):
        trace.append(f"{UNDERTONE_ZH[n['undertone']]} → 腮红色系由 {'/'.join(raw_blush)} 校正为 {'/'.join(blush_fams)}")
    blush = {
        "families": blush_fams[:2],
        "placement": blush_place,
        "intensity": round(clamp(float(g(r, "blush", "intensity", default=0.25)) * (0.85 + intensity * 0.6), 0.1, 0.45), 2),
        "finish": g(r, "blush", "finish", default="cream"),
    }
    if n["ratios"]["mid"] > 1.04:
        blush["note"] = "横向截断中庭，不低于鼻尖"

    # ── 唇
    raw_lip = g(r, "lip", "families", default=["nude_pink"]) or ["nude_pink"]
    lip_fams = [f for f in raw_lip
                if f not in FR["undertone_rules"][n["undertone"]].get("reject", [])]
    season_lip = SEASONS.get(n["season"], {}).get("lip") or FR["undertone_rules"][n["undertone"]]["lip_families"]
    lip_fams = align_families(lip_fams or season_lip, n, season_lip)
    if set(lip_fams) != set(raw_lip):
        trace.append(f"唇色由 {'/'.join(raw_lip)} 校正为 {'/'.join(lip_fams)}（避免与肤色冷暖冲突）")
    lip_shape = g(r, "lip", "shape", default="blurred_edge")
    lip_finish = g(r, "lip", "finish", default="soft_matte")
    if n["lip"] == "thin":
        lip_shape = "overlined_0_5mm"
        if lip_finish == "matte":
            lip_finish = "soft_matte"
        trace.append("唇偏薄 → 唇线外扩 0.5mm，用光泽感增加体积")
    elif n["lip"] == "large":
        lip_shape = "inner"
    lip = {
        "families": lip_fams[:2],
        "finish": lip_finish,
        "shape": lip_shape,
        "intensity": round(clamp(float(g(r, "lip", "intensity", default=0.3)) * (0.8 + intensity * 0.7), 0.12, 0.75), 2),
    }

    # ── 记录主决策
    trace.insert(0, f"{SHAPE_ZH[n['shape']]} → 轮廓重点：{'、'.join(CONTOUR_AREA_ZH.get(a, a) for a in contour['areas'][:2]) or '鼻梁两侧轻扫'}")
    trace.insert(1, f"{FEATURE_ZH[n['feature']]} → 强度上限 {cap:.2f}，主色不超过 {imp.get('max_main_colors', 2)} 个")
    trace.append(f"肤色{UNDERTONE_ZH[n['undertone']]} / {SEASON_ZH.get(n['season'], n['season'] or '季型未判定')} → 唇颊用 {'、'.join(uniq(blush_fams + lip_fams))}，避开冷暖冲突色")
    if brief.get("occasion"):
        trace.append(f"场合「{brief['occasion']}」→ 强度系数 x{occasion_mult(brief['occasion']):.2f}")

    spec = {
        "style_id": rid,
        "style_name": r["name"],
        "style_en": STYLE_EN.get(rid, rid.replace("_", " ")),
        "intensity": round(intensity, 2),
        "why_short": r.get("suit", ""),
        "occasions": r.get("occasions", []),
        "keywords": r.get("keywords", []),
        "plan": {
            "base": {"finish": finish, "coverage": coverage,
                     "conceal": g(r, "base", "conceal", default=["undereye"]) or [],
                     "powder": powder},
            "brow": brow,
            "eye": {"shadow": shadow, "liner": liner, "lash": lash,
                    "aegyo_sal": aegyo},
            "contour": contour,
            "highlight": highlight,
            "blush": blush,
            "lip": lip,
            "hair": {"change": "none", "preserve": ["hairline", "parting", "hair_color"]},
        },
        "trace": trace,
        "avoid": r.get("avoid", [])[:4],
        "prompt_fragment": r.get("prompt_fragment", ""),
        "analysis": {
            "shape": n["shape"], "shape_zh": SHAPE_ZH[n["shape"]],
            "feature": n["feature"], "feature_zh": FEATURE_ZH[n["feature"]],
            "eye_shape": n["eye_shape"], "undertone": n["undertone"],
            "season": n["season"], "mid": n["ratios"]["mid"],
        },
    }
    return spec


# ────────────────────────────── 提示词 ──────────────────────────────
def depth_words(d):
    if d < 0.18:
        return "a barely-there wash"
    if d < 0.28:
        return "a soft sheer wash"
    if d < 0.40:
        return "a clearly visible but soft wash"
    if d < 0.58:
        return "a well-blended medium depth"
    return "a deep, saturated application"


def render_prompt(spec):
    p = spec["plan"]
    base, brow, eye, ct, hl, bl, lip = (p["base"], p["brow"], p["eye"], p["contour"],
                                        p["highlight"], p["blush"], p["lip"])
    sh = eye["shadow"]
    lines = []

    lines.append("Edit the attached selfie. Use it as the identity reference: keep the exact same face shape, "
                 "jawline, eye shape and spacing, nose shape, lip shape, skin tone, skin depth, hairline, "
                 "hairstyle, hair color, facial expression, camera angle, framing and background. "
                 "The person must remain unmistakably the same individual.")
    lines.append("Apply makeup only. Do not reshape or slim the face, do not lift or enlarge the eyes, "
                 "do not change the nose or lip volume, do not lighten the skin, do not erase skin texture, "
                 "do not add jewelry or change clothing.")

    lines.append(f"Makeup look: {STYLE_EN.get(spec['style_id'], spec['style_id'])}. "
                 f"Overall intensity {spec['intensity']:.2f} out of 1.0, kept subtle and believable.")

    lines.append(f"Base: {EN_FINISH.get(base['finish'], base['finish'])}, {EN_COVERAGE.get(base['coverage'], base['coverage'])} coverage, "
                 f"even skin tone with natural texture and visible pores, no plastic sheen.")
    conceal = [c for c in base.get("conceal", []) if c not in ("all",)]
    if conceal:
        lines.append("Concealer: only where needed - " + ", ".join(
            {"undereye": "under the eyes", "redness": "around the nose wings",
             "redness_nose_wing": "around the nose wings", "blemish": "on blemishes",
             "spot": "on small spots", "nose_wing_redness": "around the nose wings",
             "dark_circle": "under the eyes"}.get(c, c.replace("_", " ")) for c in conceal) +
            "; thin and seamless, never lightening the overall skin depth.")

    lines.append(f"Brows: {EN_BROW.get(brow['shape'], brow['shape'])}, {EN_ARCH.get(brow['arch'], brow['arch'])} arch, "
                 f"{EN_THICK.get(brow['thickness'], brow['thickness'])} density, {str(brow['color']).replace('_', ' ')} tone, "
                 f"soft feathered hair strokes, lighter at the inner head, no drawn-on outline.")

    eye_line = (f"Eyes: {EN_RANGE.get(sh['range'], sh['range'])} {', '.join(str(f).replace('_', ' ') for f in sh['families'])} "
                f"eyeshadow in a {str(sh['finish']).replace('_', ' ')} finish, {depth_words(float(sh['depth']))}, "
                f"blended with no hard edges and deeper at the outer corner.")
    if sh.get("lower_lid"):
        eye_line += f" A little shadow along the outer lower lash line only."
    lines.append(eye_line)
    lines.append(f"Liner: {EN_LINER.get(eye['liner']['type'], eye['liner']['type'])}, {str(eye['liner'].get('color', 'dark brown')).replace('_', ' ')}.")
    lines.append(f"Lashes: {EN_LASH.get(eye['lash'].get('coat', 'thin'), eye['lash'].get('coat', 'thin'))}, separated, never clumpy.")
    if eye.get("aegyo_sal"):
        lines.append("Aegyo-sal: a soft shadow 2-3 mm under the lower lash line with a matte highlight on the little roll, very subtle.")

    if ct.get("areas") and ct.get("strength", 0) > 0.05:
        strength_word = ("barely visible" if ct["strength"] < 0.18 else
                         "soft and fully blended" if ct["strength"] < 0.32 else
                         "clearly visible but blended" if ct["strength"] < 0.48 else "defined")
        lines.append(f"Contour: {strength_word} {ct['tone'].replace('_', ' ')} shading on " +
                     ", ".join(EN_CONTOUR.get(a, a.replace("_", " ")) for a in ct["areas"]) +
                     "; no muddy patches, no visible lines.")
    if hl.get("matte"):
        lines.append("Matte highlight: " + ", ".join(EN_HL_MATTE.get(a, a.replace("_", " ")) for a in hl["matte"]) + ".")
    if hl.get("shimmer"):
        lines.append("Shimmer highlight: only " + ", ".join(EN_HL_SHIM.get(a, a.replace("_", " ")) for a in hl["shimmer"]) + ", finely milled, not metallic.")
    lines.append(f"Blush: {' and '.join(str(f).replace('_', ' ') for f in bl['families'])} cream blush, "
                 f"{EN_BLUSH.get(bl['placement'], bl['placement'])}, sheer and blurred into the skin, no visible edges.")
    lines.append(f"Lips: {', '.join(str(f).replace('_', ' ') for f in lip['families'])}, "
                 f"{EN_LIP_FINISH.get(lip['finish'], lip['finish'])} finish, {EN_LIP_SHAPE.get(lip['shape'], lip['shape'])}, "
                 f"applied at about {lip['intensity']:.2f} intensity, no color bleeding outside the lip line.")
    if spec.get("prompt_fragment"):
        lines.append(f"Look reference: {spec['prompt_fragment']}.")
    lines.append("Keep the same framing, lighting direction, background, hair and clothing. "
                 "Photorealistic portrait, 85mm lens, soft directional window light, natural color grading, "
                 "sharp focus on the face, natural skin texture, no beauty filter, no glow, no smoothing.")

    negative = (
        "different person, different face, changed face shape, slimmed jaw, v-shaped jaw, enlarged eyes, "
        "lifted eyelids, changed eye shape, reshaped nose, thinner nose, fuller lips, different skin tone, "
        "lightened skin, whitened skin, over-smoothed skin, plastic skin, waxy skin, heavy beauty filter, "
        "airbrushed, greasy shine, harsh unblended lines, muddy contour, smudged makeup, clumpy lashes, "
        "thick black eyeliner, heavy false lashes, colored contact lenses, extra jewelry, different hairstyle, "
        "different hair color, different background, different camera angle, changed clothing, cartoon, 3d render"
    )
    if spec["intensity"] < 0.3:
        negative += ", heavy smokey eyes, bold red lip, strong contour, visible glitter"

    return "\n\n".join(lines), negative


# ────────────────────────────── 方案卡：步骤生成 ──────────────────────────────
def build_steps(spec, n):
    p = spec["plan"]
    base, brow, eye, ct, hl, bl, lip = (p["base"], p["brow"], p["eye"], p["contour"],
                                        p["highlight"], p["blush"], p["lip"])
    sh = eye["shadow"]
    steps = []

    # 1 妆前
    tex = n["skin_texture"]
    prep = {
        "dry": ("两颊厚一点，T 区薄一点", "保湿霜全脸薄涂，干的地方可叠一层；等 1 分钟吸收"),
        "oily": ("T 区、鼻翼用控油妆前，两颊只保湿", "控油妆前只涂 T 区与鼻翼，不要全脸（会卡粉）"),
        "combination": ("分区：T 区控油、两颊保湿", "两颊保湿，T 区与鼻翼局部控油妆前"),
        "sensitive": ("只做保湿，减少层数", "保湿霜一层，避开含酒精的妆前"),
        "active_acne": ("避开破口，痘区不叠加妆前", "保湿一层，痘周轻拍，不要摩擦"),
        "texture_rough": ("重点做平滑，不堆产品", "保湿后仅在毛孔明显处薄涂毛孔妆前"),
        "normal": ("全脸保湿，局部填毛孔", "保湿霜薄涂，毛孔明显处局部填平"),
    }.get(tex, ("全脸保湿", "保湿霜薄涂，等 1 分钟吸收"))
    steps.append({
        "phase": "打底", "title": "护肤 + 防晒", "mins": 2,
        "tools": "保湿霜 / 控油妆前（按需）/ 防晒",
        "amount": "保湿黄豆大小；防晒一枚硬币大小",
        "where": prep[0],
        "how": prep[1] + "；防晒等 3–5 分钟完全成膜再上底妆",
        "stop": "手背轻碰脸不粘手，说明成膜了",
        "pitfall": "没成膜就上粉底 → 搓泥、斑驳，后面全毁",
    })

    # 2 底妆
    steps.append({
        "phase": "打底", "title": f"底妆（{FINISH_ZH.get(base['finish'], base['finish'])}）", "mins": 3,
        "tools": "粉底液 + 湿海绵（或粉底刷 + 海绵收尾）",
        "amount": "全脸 1 泵（约黄豆大小），不够再补，不要一次倒多",
        "where": "面中先点：鼻翼两侧、额头中央、下巴，再向外推",
        "how": "由内向外拍开，边缘越往外越薄；最后用干净海绵按压收尾，把边界压进皮肤",
        "stop": "脸和脖子没有明显色差；毛孔还看得见（不是一张塑料皮）",
        "pitfall": "反复叠加遮瑕 → 卡粉、假面。宁可薄，局部再补",
    })

    # 3 遮瑕
    conceal = base.get("conceal") or []
    concerns = n["concerns"]
    spots = []
    if "undereye" in conceal or "dark_circle" in concerns or "undereye_circle" in concerns:
        spots.append("黑眼圈：先用橘调薄薄中和一层，再用比粉底亮半号的点在泪沟最深处")
    if "redness" in conceal or "nose_wing_redness" in conceal or "redness" in concerns:
        spots.append("鼻翼泛红：用绿调或比粉底深半号的少量点拍")
    if "blemish" in conceal or "acne" in " ".join(concerns):
        spots.append("痘点：用极细刷点涂，只盖痘心，不盖周围一圈")
    if not spots:
        spots.append("只在需要的地方点：眼下、鼻翼、嘴角暗沉")
    steps.append({
        "phase": "打底", "title": "遮瑕（只补该补的地方）", "mins": 2,
        "tools": "遮瑕液 + 小号扁刷或指腹",
        "amount": "每处米粒大小，永远少量多次",
        "where": "；".join(spots),
        "how": "点戳，不拖拽；越靠下越薄，边缘用指腹温度压进去",
        "stop": "低头看没有灰紫色；笑起来眼下不卡纹",
        "pitfall": "遮瑕涂到睫毛根 → 卡粉、显干纹，反而更显疲惫",
    })

    # 4 定妆
    if base["powder"] in ("none", "spray_only"):
        steps.append({
            "phase": "打底", "title": "定妆（这套妆不压粉）", "mins": 1,
            "tools": "定妆喷雾（可选）",
            "amount": "距离 20cm 喷两下，不要喷到滴水",
            "where": "全脸，避开眼周",
            "how": "喷雾在空中画 X 和 T 各一次，让它自然落下；不要用手拍",
            "stop": "脸上有微微湿润感但不流，1 分钟自然干",
            "pitfall": "这套妆靠光泽感，一压散粉就变成哑光厚妆，等于毁掉妆效",
        })
    else:
        powder_text = {
            "t_zone": "散粉只压 T 区与鼻翼，两颊不压",
            "t_zone_only": "散粉只压 T 区与眼下，两颊用余粉带过",
            "t_zone_and_cheek": "T 区重点压，脸颊外缘轻扫",
            "t_zone_full": "T 区多压一点，两颊薄薄一层",
            "minimal": "只用定妆喷雾，两颊尽量不压粉",
            "cheek_light": "两颊轻扫，T 区正常压",
            "full": "全脸薄薄一层，按压不扫",
            "spray_only": "只用定妆喷雾，不压散粉",
        }.get(base["powder"], "散粉压 T 区，两颊轻带")
        steps.append({
            "phase": "打底", "title": "定妆", "mins": 1,
            "tools": "散粉 + 粉扑（或定妆喷雾）",
            "amount": "粉扑取粉后在手背弹掉九成再用",
            "where": powder_text,
            "how": "按压，不要来回扫；扫会把底妆带走",
            "stop": "手指按上去不留指纹，但不发干、不扬粉",
            "pitfall": "全脸厚压 → 妆感变厚、显纹，把清透感全压没了",
        })

    # 5 眉
    steps.append({
        "phase": "眉眼", "title": f"眉毛（{BROW_SHAPE_ZH.get(brow['shape'], brow['shape'])}）", "mins": 4,
        "tools": "细头眉笔（或眉粉）+ 螺旋刷 + 透明眉胶",
        "amount": "眉笔每次转出 2mm；眉粉取一次够画半条眉",
        "where": f"① 眉底线：眉骨下缘，决定整条眉的高度　② 眉峰：黑眼球外缘向上延伸的位置（{BROW_ARCH_ZH.get(brow['arch'], brow['arch'])}）　③ 眉尾：鼻翼到外眼角的延长线",
        "how": "先画眉底线 → 定眉峰 → 连眉尾 → 用笔尖一根根补空隙（不勾外框）→ 眉头用螺旋刷向前晕开，做到最淡",
        "stop": "颜色呈「轻-重-轻-重」：眉头最淡、眉峰最重、眉尾收细；眉毛里还能看到皮肤",
        "pitfall": "整条描边 → 蜡笔小新；眉色比发色深 → 显凶、显硬",
    })

    # 6 眼影
    fam_zh = "、".join(str(f).replace("_", " ") for f in sh["families"])
    if sh["families"] and float(sh["depth"]) > 0.02:
        layers = [
            f"① 打底：最浅的哑光色铺满{EYE_RANGE_ZH.get('within_crease', '眼窝')}，把肤色压匀",
            f"② 主色：中号刷蘸 {fam_zh} 的中间色，落在{EYE_RANGE_ZH.get(sh['range'], sh['range'])}",
            f"③ 加深：最小号刷蘸最深色，贴着睫毛根画到眼尾，范围不超过眼尾 1/3，向外晕开",
        ]
        if hl.get("shimmer") and "center_lid" in hl["shimmer"]:
            layers.append("④ 提亮：珠光点在眼皮正中央，范围不超过食指指甲盖")
        steps.append({
            "phase": "眉眼", "title": f"眼影（{fam_zh} · {DEPTH_ZH(float(sh['depth']))}）", "mins": 5,
            "tools": "3 把刷子：大蓬松（打底）、中号（主色）、小扁头（加深）",
            "amount": "每层取粉后在纸巾上敲一下，宁少勿多，可叠不可减",
            "where": "；".join(layers),
            "how": "每上一层都用干净刷子把边界晕开；深色只在眼尾三角，不往前带",
            "stop": "睁眼能看到颜色、闭眼没有明显边界线；颜色没有超出眼窝骨",
            "pitfall": "范围超出眼窝 → 显肿；边缘不晕 → 显脏",
        })

    # 7 眼线
    steps.append({
        "phase": "眉眼", "title": "眼线", "mins": 3,
        "tools": "极细眼线液笔 / 眼线胶笔（内眼线用胶笔更稳）",
        "amount": "笔尖只露 2mm，一次画 1cm，分三段接",
        "where": LINER_ZH.get(eye["liner"]["type"], eye["liner"]["type"]),
        "how": "手肘撑在桌上稳住；内眼线填睫毛根的空隙，不要画在睫毛上方；眼尾用「点-连」的方式拉，不要一笔拖",
        "stop": "睁眼时睫毛根没有留白，眼尾线条粗细一致、不抖",
        "pitfall": "全包粗眼线 → 眼睛反而变小；眼尾上扬过高 → 显凶",
    })

    # 8 睫毛
    steps.append({
        "phase": "眉眼", "title": "睫毛", "mins": 3,
        "tools": "睫毛夹 + 睫毛膏（+ 单簇假睫毛，按需）",
        "amount": "睫毛膏先在纸巾上蹭掉多余膏体",
        "where": LASH_ZH.get(eye["lash"].get("coat", "thin"), eye["lash"].get("coat", "thin")),
        "how": "三段夹：根部、中段、梢部各夹 3 秒；Z 字刷根部再向上提拉，重点眼尾",
        "stop": "根根分明、不结块；侧面看有弧度但不夸张",
        "pitfall": "反复涂 → 苍蝇腿、压塌弧度，眼妆立刻显脏",
    })

    # 9 卧蚕
    if eye.get("aegyo_sal"):
        steps.append({
            "phase": "眉眼", "title": "卧蚕（提神的关键一步）", "mins": 2,
            "tools": "小扁刷 + 哑光提亮（不要大颗珠光）",
            "amount": "阴影取一次粉，提亮米粒大小",
            "where": "下睫毛根下方 2–3mm 的条状隆起；阴影画在卧蚕下缘，提亮画在隆起最高处",
            "how": "眯眼笑找到卧蚕位置 → 阴影从眼头 1/3 画到眼尾，两端淡 → 提亮只点在中央 1/2",
            "stop": "不笑时也有淡淡的立体感，笑时自然鼓起，不出现一条硬线",
            "pitfall": "阴影画太宽 → 变成黑眼圈；珠光太大颗 → 显肿",
        })

    # 10 修容
    if ct.get("areas") and ct["strength"] > 0.05:
        area_txt = "；".join(CONTOUR_AREA_ZH.get(a, a) for a in ct["areas"])
        lvl = ("几乎看不见，只修光影" if ct["strength"] < 0.2 else
               "日常可见，社交距离自然" if ct["strength"] < 0.35 else
               "有明确轮廓感" if ct["strength"] < 0.5 else "轮廓明显（拍照用）")
        arc = "；下颌角用弧线，从角上方起笔，不要画成直线" if ct.get("style") == "arc" else ""
        steps.append({
            "phase": "轮廓", "title": f"修容（强度 {ct['strength']:.2f} · {lvl}）", "mins": 4,
            "tools": "斜角紧实刷 + 冷灰棕修容粉（不要暖棕、不要带闪）",
            "amount": "刷子蘸粉后在手上抖两下，宁淡勿浓",
            "where": area_txt + arc,
            "how": "少量多次，每次画完都用干净刷子把边界晕开；鼻影从山根两侧起笔，向下晕到鼻头，上缘不连眉头",
            "stop": "正面看是阴影不是颜色；侧脸有转折，正面不脏",
            "pitfall": "暖棕修容 → 显脏显黄；边界不晕 → 两撇胡子",
        })

    # 11 高光
    hl_parts = []
    if hl.get("matte"):
        hl_parts.append("哑光提亮：" + "、".join(HL_MATTE_ZH.get(a, a) for a in hl["matte"]))
    if hl.get("shimmer"):
        hl_parts.append("珠光点光：" + "、".join(HL_SHIM_ZH.get(a, a) for a in hl["shimmer"]))
    if hl_parts:
        steps.append({
            "phase": "轮廓", "title": "高光（分开做：哑光提亮 + 珠光点光）", "mins": 2,
            "tools": "小号扁刷（哑光）+ 指腹或小号火苗刷（珠光）",
            "amount": "珠光只需米粒大小，宁少勿多",
            "where": "；".join(hl_parts),
            "how": "哑光高光用小刷精准压在凹陷处，边界拍开；珠光只点，不扫，鼻梁要断开不要拉通条",
            "stop": "转动脸时才有光，正面不反光成一条油线",
            "pitfall": "珠光大面积扫 → 毛孔放大、显油；鼻梁一条直通 → 假",
        })

    # 12 腮红
    steps.append({
        "phase": "轮廓", "title": f"腮红（{'/'.join(str(f).replace('_', ' ') for f in bl['families'])}）", "mins": 2,
        "tools": "膏状腮红 + 指腹或湿海绵（干皮友好）；油皮可再薄盖一层粉状",
        "amount": "每次米粒大小，分两次上，先淡后补",
        "where": BLUSH_PLACE_ZH.get(bl["placement"], bl["placement"]) + (
            f"（{bl['note']}）" if bl.get("note") and "截断" not in BLUSH_PLACE_ZH.get(bl["placement"], "") else ""),
        "how": "从落点向外轻拍/轻扫，边缘一定要晕到没有边界；面中留白，不要糊满整张脸",
        "stop": "像自己透出来的血色，不是两团颜色；两边高低一致",
        "pitfall": "低于鼻尖 → 显脸垮；和修容重叠 → 妆面脏",
    })

    # 13 唇
    steps.append({
        "phase": "收尾", "title": f"唇（{LIP_FINISH_ZH.get(lip['finish'], lip['finish'])}）", "mins": 3,
        "tools": "润唇 + 唇线笔（可选）+ 口红/唇釉 + 棉签",
        "amount": "口红一次转出 3mm，先薄涂一层看颜色",
        "where": LIP_SHAPE_ZH.get(lip["shape"], lip["shape"]),
        "how": "润唇打底去死皮 → 深唇先用遮瑕薄薄中和 → 色料从唇中央点涂向外晕 → 棉签把唇线向内晕成影子",
        "stop": "边缘柔、颜色从中间向外变淡；说话不粘牙、不结块",
        "pitfall": "深唇直接上浅色 → 发灰发紫；唇线画太实 → 显老",
    })

    # 14 收尾
    steps.append({
        "phase": "收尾", "title": "最后 60 秒：三处检查", "mins": 1,
        "tools": "干净海绵、棉签、定妆喷雾",
        "amount": "—",
        "where": "① 脸脖交界 ② 眼影边界 ③ 唇线外侧",
        "how": "海绵把脸脖过渡压匀；棉签擦掉眼影掉粉；喷雾距离 20cm 喷两下",
        "stop": "站在自然光下看，没有一条能看见的边界线",
        "pitfall": "不检查就出门 → 掉粉、色差、唇线溢出，全是小细节毁掉整体",
    })
    return steps


def palette_items(spec):
    p = spec["plan"]
    items = []
    for f in p["eye"]["shadow"].get("families", []):
        items.append(("眼影", f))
    for f in p["blush"].get("families", []):
        items.append(("腮红", f))
    for f in p["lip"].get("families", []):
        items.append(("唇", f))
    items.append(("修容", p["contour"].get("tone", "cool_gray_brown")))
    if p["highlight"].get("shimmer"):
        items.append(("高光", "champagne" if spec["analysis"]["undertone"] == "warm" else "icy_white"))
    seen, out = set(), []
    for role, fam in items:
        if fam in seen:
            continue
        seen.add(fam)
        meta = COLOR_FAMILIES.get(fam, {"hex": ["#C9A18A"], "desc": fam.replace("_", " ")})
        out.append({"role": role, "family": fam, "desc": meta["desc"], "hex": meta["hex"]})
    return out[:7]


PITFALL_ADVICE = {
    "油光": "T 区与鼻翼必须压住，水光只留在颧骨高点和鼻尖；全脸发亮叫油，不叫水光。",
    "厚覆盖": "遮瑕只点在该遮的地方，全脸厚盖会让皮肤变塑料，原本的质感全没了。",
    "厚粉底": "底妆只上薄薄一层，要遮的地方用遮瑕点补；厚涂会卡粉显纹，清透感一次就毁。",
    "厚底妆": "底妆薄到能看见毛孔；厚度一上来，这套妆就变成面具。",
    "底妆不均": "脸脖交界一定要过渡到没有色差，色差比任何一处妆都显眼。",
    "底妆不干净": "底妆是这套妆的全部，宁可薄到透出本色，也不要斑驳卡粉。",
    "底妆氧化": "选和脖子同色的粉底，上妆后等 10 分钟再看是否变暗，别用一上脸就正好的色号。",
    "全脸定妆": "只在 T 区与鼻翼压粉，两颊用余粉带过；全脸压粉会把水光感压成哑光。",
    "高饱和口红": "唇色压到低饱和的豆沙、奶茶区间，正红和玫红会抢掉整张脸的重点。",
    "唇色过艳": "唇色降一档饱和，艳色在这套妆里会显得用力过猛。",
    "唇色过深压气场": "唇色明度跟着整体走，太深会压掉这套妆的轻感。",
    "深色唇": "唇色保持在中浅明度，深色会让干净的底妆显得沉重。",
    "哑光深暗唇": "只降饱和、不降明度；唇色一暗就显老。",
    "哑光厚唇": "哑光唇要薄涂，涂厚会把唇纹全部放大。",
    "唇色与肤色脱节": "唇色必须和肤色同冷暖；暖皮配冷粉紫会发灰。",
    "唇线模糊": "唇线可以柔但不能糊出界，最后用棉签把边缘收干净。",
    "明显外眼线": "只填内眼线，眼尾不要拉出明显的线；外眼线一出现，整张脸就变凶。",
    "浓黑粗眼线": "眼线只用深棕细线，粗黑一条会把眼睛压小。",
    "任何眼影": "这套妆不需要眼影，只做睫毛和内眼线就够，多一层就过头。",
    "眼影范围失控": "眼影不超出眼窝骨，出了眼窝就是肿。",
    "高饱和橘红眼影": "眼影控制在低饱和棕调，橘红一上眼就显肿显脏。",
    "珠光过多": "珠光只点鼻尖、眼头、颧骨三点，大面积会放大毛孔。",
    "黑色睫毛膏": "睫毛膏换棕色，黑色在这套妆里太重。",
    "过深眉色": "眉色比发色浅半号；太深会显凶、显硬。",
    "深色修容": "只用比肤色深 1–1.5 号的冷灰棕，深色修容在自然光下就是两撇灰影。",
    "过重古铜修容": "古铜粉只扫颧骨外缘与下颌线，量控制在侧面看得到、正面看不见。",
    "过度修容": "修容只修光影不修形状；正面对着镜子看不出颜色才对。",
    "修容过重发脏": "每次只蘸一点粉，画完必须用干净刷子把边界晕开。",
    "修容边界不晕": "修容的边界比颜色重要，晕开的时间永远不嫌多。",
    "冷调修容": "暖皮用纯冷调修容会发灰，选带一点暖的灰棕。",
    "粉状厚修容": "膏状打底、粉状只做定形；粉压厚了会浮在脸上。",
    "鼻梁横带太宽": "鼻梁高光要窄、要断开，横向一条会显得鼻子更塌。",
    "腮红过重": "腮红宁少勿多，先上一层看自然光效果，再决定补不补。",
    "腮红过重成高原红": "只在颧骨上淡淡一层；两团圆红最显土。",
    "腮红位置过低": "腮红不要低于鼻尖，低了整张脸会往下坠。",
    "冷调粉腮红": "暖皮换成陶土、珊瑚、豆沙；冷粉在暖皮上会显脏。",
    "全脸堆色": "一套妆最多两个主色，颜色越多越显脏。",
    "多种高饱和色": "眼、颊、唇只留一个重点，其余全部降饱和。",
    "追求白皙": "不要为显白选浅一号的粉底，色差和灰调比深一点更难看。",
    "哑光厚粉": "哑光不等于厚粉；粉一厚就显干纹。",
    "哑光妆效": "这套妆要的是光，全哑光会把水光感抹平。",
    "淡颜硬上": "淡颜不要硬上浓妆，强度过了只会显脏显老。",
    "暖皮使用": "这套配色偏冷，暖皮上脸会发黄发脏，最多借它的眼妆部分。",
    "日常通勤场景": "这套妆强度偏高，通勤会显得用力过猛，留给约会或拍照更合适。",
    "冰蓝与暖棕混搭": "冷色眼影和暖棕修容不要同时出现，冷暖打架会让妆面发脏。",
    "闪光灯下过亮的高光": "高光颗粒要极细；太闪的会在闪光灯下反成一块白。",
}

FILLER_PITFALLS = [
    ("边缘永远比颜色重要", "任何一块颜色的边界没晕开都会显脏，晕开花的时间永远不嫌多。"),
    ("宁可少量多次", "每次只取一点产品，不够再叠；一次性上足量几乎一定会过头。"),
    ("先在自然光下检查再出门", "室内灯光会藏住色差和边界，走到窗边看一眼再定。"),
]


def build_pitfalls(spec, n):
    out = []
    for a in spec.get("avoid", [])[:2]:
        out.append((a, PITFALL_ADVICE.get(a) or f"{a} 一旦做过头，整张脸的重点就全跑到这一处，宁可少做。"))
    if n["ratios"]["mid"] > 1.04:
        out.append(("腮红不要低于鼻尖", "中庭偏长的人腮红一旦画低，脸会显更长、更垮。"))
    elif n["shape"] in ("round", "soft_square"):
        out.append(("不要横向平铺腮红", "圆脸/方圆脸横向打腮红会显得脸更宽，必须斜向上带。"))
    if n["feature"] == "light_feature":
        out.append(("全脸主色不超过 2 个", "淡颜一旦堆三种以上颜色，会显脏、显老，不是显精致。"))
    if n["skin_texture"] == "oily":
        out.append(("不要全脸水光", "油皮全脸水光会变油光，只在颧骨高点与鼻尖点一点。"))
    if n["eye_shape"] in ("mono", "inner_double"):
        out.append(("眼线不要画粗", "内双/单眼皮画粗眼线会被褶皱吃掉，睁眼反而显脏，只填内眼线。"))
    # 去重 + 补足 3 条
    uniq_out, seen = [], set()
    for t, d in out:
        if t in seen:
            continue
        seen.add(t)
        uniq_out.append((t, d))
    for t, d in FILLER_PITFALLS:
        if len(uniq_out) >= 3:
            break
        if t not in seen:
            seen.add(t)
            uniq_out.append((t, d))
    return uniq_out[:3]


def duration_text(steps):
    m = sum(s["mins"] for s in steps)
    return f"{m} 分钟" if m < 60 else f"{m // 60} 小时 {m % 60} 分"


# ────────────────────────────── 卡片渲染 ──────────────────────────────
def steps_md(steps):
    out = []
    for i, s in enumerate(steps, 1):
        out.append(f"### {i}. {s['title']}")
        out.append(f"- **工具**：{s['tools']}")
        out.append(f"- **用量**：{s['amount']}")
        out.append(f"- **位置**：{s['where']}")
        out.append(f"- **手法**：{s['how']}")
        out.append(f"- **做到这样就停**：{s['stop']}")
        out.append(f"- **常见错**：{s['pitfall']}")
        out.append("")
    return "\n".join(out)


def why_parts(spec, n):
    mid = n["ratios"]["mid"]
    mid_txt = "中庭偏长" if mid > 1.04 else ("中庭偏短" if mid < 0.96 else "三庭比例比较均衡")
    lead = (f"你的底子是{SHAPE_ZH[n['shape']]}、{FEATURE_ZH[n['feature']]}，{mid_txt}，"
            f"{UNDERTONE_ZH[n['undertone']]}肤色。这套「{spec['style_name']}」的处理逻辑是：")
    return lead, spec["trace"]


def card_md(spec, n, steps, palette, pitfalls):
    tags = " · ".join(spec.get("keywords", [])[:3])
    lead, bullets = why_parts(spec, n)
    lines = [
        f"# {spec['style_name']}｜{SHAPE_ZH[n['shape']]} · {FEATURE_ZH[n['feature']]} · {UNDERTONE_ZH[n['undertone']]}",
        "",
        f"> 风格 ID：`{spec['style_id']}`　强度：{spec['intensity']:.2f} / 1.0　"
        f"妆效：{FINISH_ZH.get(spec['plan']['base']['finish'], '')}　预计 {duration_text(steps)}",
        "",
        f"**关键词**：{tags}",
        "",
        "## 为什么这套适合你",
        "",
        lead,
        "",
    ]
    lines += [f"- {t}" for t in bullets]
    lines += [
        "",
        "## 这套妆用到的色",
        "",
        "| 部位 | 色系 | 说明 | 参考色 |",
        "|---|---|---|---|",
    ]
    for it in palette:
        lines.append(f"| {it['role']} | {it['family']} | {it['desc']} | {' '.join(it['hex'][:2])} |")
    lines += ["", "## 照这个顺序化", ""]
    lines.append(steps_md(steps))
    lines += ["## 三个最容易翻车的地方", ""]
    for i, (t, d) in enumerate(pitfalls, 1):
        lines.append(f"{i}. **{t}** —— {d}")
    lines += ["", "---", "",
              "边界说明：这是妆容预览与化妆建议，不是医美或整形模拟。效果图由 AI 依据原照片生成，"
              "用于参考配色与手法，真人上妆会受光线、产品与手法影响。AI 生成内容，请勿用于身份冒用。", ""]
    return "\n".join(lines)


def esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def steps_html(steps):
    out = []
    for s in steps:
        out.append(
            '<div class="step">'
            f'<div class="phase">{esc(s["phase"])}</div>'
            f'<h3>{esc(s["title"])}</h3>'
            '<dl class="rows">'
            f'<div class="row"><dt>工具</dt><dd>{esc(s["tools"])}</dd></div>'
            f'<div class="row"><dt>用量</dt><dd>{esc(s["amount"])}</dd></div>'
            f'<div class="row"><dt>位置</dt><dd>{esc(s["where"])}</dd></div>'
            f'<div class="row"><dt>手法</dt><dd>{esc(s["how"])}</dd></div>'
            f'<div class="row"><dt>停手</dt><dd class="stop">{esc(s["stop"])}</dd></div>'
            f'<div class="row"><dt>避坑</dt><dd class="pit">{esc(s["pitfall"])}</dd></div>'
            '</dl></div>'
        )
    return "\n".join(out)


def palette_html(palette):
    out = []
    for it in palette:
        dots = "".join(f'<i class="dot" style="background:{h}"></i>' for h in it["hex"][:3])
        out.append(
            '<div class="sw"><div class="dots">' + dots + '</div>'
            f'<div><b>{esc(it["desc"])}</b><span>{esc(it["role"])} · {esc(it["family"])}</span></div></div>'
        )
    return "\n".join(out)


def pitfalls_html(pitfalls):
    return "\n".join(
        f'<div><i>{i}</i><p><b>{esc(t)}</b>{esc(d)}</p></div>'
        for i, (t, d) in enumerate(pitfalls, 1)
    )


def card_html(spec, n, steps, palette, pitfalls):
    tpl = TEMPLATE.read_text(encoding="utf-8")
    tags = "".join(f'<span class="tag">{esc(k)}</span>' for k in spec.get("keywords", [])[:3])
    tags += f'<span class="tag ghost">{esc(SHAPE_ZH[n["shape"]])}</span>'
    tags += f'<span class="tag ghost">{esc(FEATURE_ZH[n["feature"]])}</span>'
    tags += f'<span class="tag ghost">{esc(UNDERTONE_ZH[n["undertone"]])}</span>'
    if n["season"]:
        tags += f'<span class="tag ghost">{esc(SEASON_ZH.get(n["season"], n["season"]))}型</span>'
    lead, bullets = why_parts(spec, n)
    why_html = ('<p class="why">' + esc(lead) + "</p><ul class=\"why-list\">" +
                "".join(f"<li>{esc(b)}</li>" for b in bullets) + "</ul>")
    repl = {
        "{{CARD_TITLE}}": f"{spec['style_name']} · 妆容方案卡",
        "{{STYLE_ID}}": spec["style_id"],
        "{{TITLE}}": spec["style_name"],
        "{{SUBTITLE}}": f"{SHAPE_ZH[n['shape']]} · {FEATURE_ZH[n['feature']]} · {UNDERTONE_ZH[n['undertone']]} · 强度 {spec['intensity']:.2f}",
        "{{TAGS}}": tags,
        "{{INTENSITY_TEXT}}": f"{spec['intensity']:.2f}",
        "{{INTENSITY_PCT}}": str(int(spec["intensity"] * 100)),
        "{{FINISH_TEXT}}": FINISH_ZH.get(spec["plan"]["base"]["finish"], ""),
        "{{COVERAGE_TEXT}}": COVERAGE_ZH.get(spec["plan"]["base"]["coverage"], ""),
        "{{DURATION}}": duration_text(steps),
        "{{WHY}}": why_html,
        "{{PALETTE}}": palette_html(palette),
        "{{STEPS}}": steps_html(steps),
        "{{PITFALLS}}": pitfalls_html(pitfalls),
        "{{INTENSITY}}": f"{spec['intensity']:.2f}",
        "{{FINISH}}": spec["plan"]["base"]["finish"],
    }
    for k, v in repl.items():
        tpl = tpl.replace(k, v)
    return tpl


# ────────────────────────────── 主流程 ──────────────────────────────
def run(analysis, brief, out_dir, count=3, styles=None, quiet=False):
    n = normalize(analysis)
    brief = brief or {}
    if not isinstance(brief, dict):
        brief = {}
    brief.setdefault("style", "auto")
    brief.setdefault("occasion", "日常")
    brief.setdefault("avoid", [])

    scored = rank_styles(n, brief)
    if styles:
        chosen = [s.strip() for s in styles if s.strip() in RECIPES][:count]
    else:
        chosen = select_variants(scored, n, brief, count)

    os.makedirs(out_dir, exist_ok=True)
    index = {"analysis_summary": {
        "shape": SHAPE_ZH[n["shape"]], "feature": FEATURE_ZH[n["feature"]],
        "eye_shape": n["eye_shape"], "undertone": n["undertone"],
        "season": n["season"], "skin_texture": n["skin_texture"],
        "midface_ratio": n["ratios"]["mid"],
    }, "variants": []}

    for rank, rid in enumerate(chosen, 1):
        spec = build_spec(rid, n, brief)
        prompt, negative = render_prompt(spec)
        steps = build_steps(spec, n)
        pal = palette_items(spec)
        pits = build_pitfalls(spec, n)

        tag = f"{rank:02d}_{rid}"
        spec_out = dict(spec)
        spec_out["render"] = {
            "prompt": prompt, "negative": negative,
            "input_fidelity": "high",
            "preserve": ["face shape", "eye spacing", "nose", "lips", "skin tone",
                         "hairline", "hairstyle", "expression", "camera angle", "background"],
        }
        spec_out["card"] = {"steps": steps, "palette": pal,
                            "pitfalls": [{"title": t, "why": d} for t, d in pits]}

        with open(os.path.join(out_dir, f"spec_{tag}.json"), "w", encoding="utf-8") as f:
            json.dump(spec_out, f, ensure_ascii=False, indent=2)
        with open(os.path.join(out_dir, f"prompt_{tag}.txt"), "w", encoding="utf-8") as f:
            f.write(prompt + "\n\n--- negative ---\n" + negative + "\n")
        with open(os.path.join(out_dir, f"card_{tag}.md"), "w", encoding="utf-8") as f:
            f.write(card_md(spec, n, steps, pal, pits))
        with open(os.path.join(out_dir, f"card_{tag}.html"), "w", encoding="utf-8") as f:
            f.write(card_html(spec, n, steps, pal, pits))

        index["variants"].append({
            "rank": rank,
            "style_id": rid,
            "style_name": spec["style_name"],
            "intensity": spec["intensity"],
            "keywords": spec["keywords"],
            "one_line": "；".join(spec["trace"][:2]),
            "files": {
                "spec": f"spec_{tag}.json",
                "prompt": f"prompt_{tag}.txt",
                "card_md": f"card_{tag}.md",
                "card_html": f"card_{tag}.html",
            },
        })

    with open(os.path.join(out_dir, "index.json"), "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=2)

    if not quiet:
        print(f"OK  {len(index['variants'])} 套方案 → {out_dir}")
        for v in index["variants"]:
            print(f"  [{v['rank']}] {v['style_name']}  intensity={v['intensity']:.2f}  "
                  f"{'·'.join(v['keywords'][:3])}")
            print(f"      {v['one_line']}")
    return index


def selftest():
    demo = load_json(DATA_DIR / "makeup-spec.example.json")
    brief = {"style": "auto", "occasion": "通勤", "avoid": ["浓眼线", "假睫毛"]}
    here = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_selftest")
    idx = run(demo, brief, here, count=3)
    for v in idx["variants"]:
        p = os.path.join(here, v["files"]["prompt"])
        txt = open(p, encoding="utf-8").read()
        assert "identity reference" in txt, p
        assert len(txt) > 800, p
    print("selftest passed →", here)


def main():
    ap = argparse.ArgumentParser(description="自拍妆效规则引擎")
    ap.add_argument("--analysis", help="面部分析 JSON 文件路径")
    ap.add_argument("--brief", default="{}", help='需求 JSON 文件路径或内联 JSON，如 {"occasion":"通勤"}')
    ap.add_argument("--out", default="makeup_out", help="输出目录")
    ap.add_argument("--count", type=int, default=3, help="候选方案数量，默认 3")
    ap.add_argument("--styles", default="", help="指定风格 ID，逗号分隔（跳过自动推荐）")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        selftest()
        return
    if not args.analysis:
        ap.error("--analysis 必填")

    analysis = load_json(args.analysis)
    if os.path.exists(args.brief):
        brief = load_json(args.brief)
    else:
        brief = json.loads(args.brief)

    styles = [s for s in args.styles.split(",") if s] if args.styles else None
    run(analysis, brief, args.out, count=args.count, styles=styles)


if __name__ == "__main__":
    main()

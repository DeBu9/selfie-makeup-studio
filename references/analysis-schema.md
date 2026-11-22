# 面部分析输出规范（analysis.json）

规则引擎只认枚举值，不认形容词。`scripts/plan_makeup.py` 会按这些字段去查 `data/face_rules.json`，
字段写错或缺失会直接导致选错风格、配错色。所以这一步必须严格按下面的 JSON 结构输出。

---

## 一、完整骨架

```json
{
  "analysis": {
    "face_shape": {
      "label": "round",
      "confidence": 0.81,
      "ratios": {
        "length_width": 1.22,
        "upper_mid_lower": [1.00, 1.08, 0.96],
        "jaw_angle_deg": 132,
        "cheekbone_prominence": 0.28,
        "temple_hollow": false,
        "midface_flat": true
      }
    },
    "features": {
      "eyes":  { "shape": "inner_double", "spacing": "slightly_wide", "tilt": "neutral",
                 "puffiness": 0.25, "lid_space": "narrow" },
      "nose":  { "bridge_height": "low", "tip": "rounded", "width": "medium", "length": "normal",
                 "dorsum": "straight" },
      "lips":  { "volume": "thin", "cupid_bow": "soft", "corner": "neutral", "philtrum": "normal" },
      "brows": { "density": "sparse", "shape": "natural", "arch": "low" },
      "skin":  { "depth": 4, "undertone": "neutral_warm", "season": "soft_autumn",
                 "ita_deg": 44, "texture": "normal",
                 "concerns": ["dullness", "undereye_circle"] }
    },
    "impression": {
      "contrast": "low",
      "feature_size": "small",
      "bone_vs_flesh": 0.38,
      "visual_age": "youthful"
    },
    "hair": { "color": "cool_brown", "length": "shoulder", "texture": "fine", "parting": "side" }
  }
}
```

---

## 二、字段取值与判据

### face_shape.label
`oval` 鹅蛋 / `round` 圆 / `square` 方 / `soft_square` 方圆 / `long` 长 / `diamond` 菱形 /
`heart` 心形 / `triangle` 梨形

判据（同时满足两条以上再定）：
- **长宽比** = 脸长 ÷ 颧宽。>1.5 偏长，1.35–1.5 标准，<1.35 偏短
- **下颌角**（量下颌线与耳下水平线夹角）：>140° 圆润，120–140° 适中，<120° 方
- **最宽处位置**：额/颧/下颌。颧最宽且额头下巴都窄 = 菱形；下颌最宽 = 梨形
- 拿不准就选 `oval` 并在 `confidence` 写低值——`oval` 的动作最保守，不会做错事

### ratios
- `upper_mid_lower`：发际线→眉、眉→鼻底、鼻底→下巴 三段比值，以中段为 1.00 归一。
  中段 >1.04 → 中庭偏长（影响腮红位置，规则引擎会强制改成横打）
- `length_width`：脸长 ÷ 颧宽
- `jaw_angle_deg`：目测即可，用于判断方圆
- `temple_hollow`：太阳穴是否凹陷（灯光下有明显阴影 → true）
- `midface_flat`：面中是否平（侧面看鼻基底是否凹陷）

### features.eyes.shape
`mono` 单眼皮 / `inner_double` 内双 / `double` 双眼皮 / `puffy` 肿眼泡 /
`upturned` 上挑 / `downturned` 下垂 / `elongated` 细长 / `round` 圆眼 / `deep_set` 深眼窝

- `puffiness`：0–1。眼皮浮肿、脂肪厚、睁眼时褶皱被吃掉 ≥0.3
- `lid_space`：`wide` / `narrow`。睁眼时可见眼皮宽度，<3mm 记 narrow
- `spacing`：`wide` / `slightly_wide` / `neutral` / `slightly_narrow` / `narrow`（两眼间距 vs 一只眼宽）

### features.nose
- `bridge_height`: `low` / `medium` / `high`
- `tip`: `rounded` / `bulbous` 蒜头 / `pointed` / `upturned` / `hooked`
- `width`: `narrow` / `medium` / `wide`
- `length`: `short` / `normal` / `long`

### features.lips
- `volume`: `thin` / `medium` / `full` / `large`
- `cupid_bow`: `sharp` / `soft` / `flat`
- `corner`: `upturned` / `neutral` / `downturned`

### features.skin
- `depth`：1–10。1 极白，4 自然偏白，6 小麦，8 深棕。**这是"深浅"，不是"好坏"**
- `undertone`：`warm` / `cool` / `neutral` / `neutral_warm` / `neutral_cool` / `olive`
  判断顺序：看颈侧与脸颊底色 → 血管偏绿=暖、偏蓝紫=冷 → 戴金饰好看=暖、银饰=冷 →
  两者都行=中性 → 脸色发绿灰、粉底总显红=橄榄
- `season`：十二季型之一（`light_spring` … `bright_winter`），不确定就留空字符串
- `texture`：`normal` / `dry` / `oily` / `combination` / `sensitive` / `active_acne` / `texture_rough`
- `concerns`：数组，可用值 `dullness` `undereye_circle` `redness` `acne` `pores` `melasma` `dehydration`

### impression
- `contrast`：`low` / `medium` / `high` / `very_high`。发色瞳色与肤色的明度差
- `feature_size`：**最关键的一个字段**
  - `small` → 淡颜（五官占比小、留白多、对比低）
  - `medium` → 中等
  - `large` → 浓颜（五官大、对比强、留白少）
  它决定强度上限：淡颜 0.45 / 中等 0.65 / 浓颜 0.85
- `bone_vs_flesh`：0–1。0 = 皮相（肉感、轮廓柔和），1 = 骨相（颧骨下颌清晰）
- `visual_age`：`youthful` / `mature`

### hair
`color`: `black` / `dark_brown` / `cool_brown` / `warm_brown` / `light_brown` / `blonde` / `gray` / `colored`
`length`: `short` / `shoulder` / `long`
`texture`: `fine` / `medium` / `thick` / `curly`
`parting`: `center` / `side` / `none`

---

## 三、写之前先过"照片闸门"

四项有一项不达标，先请用户重拍，不要硬着头皮分析：

| 检查 | 不达标的表现 | 怎么说 |
|---|---|---|
| 角度 | 侧脸超过 15°、低头/仰头 | "这张有点侧，能不能正对镜头再来一张？侧脸会让我判断不准脸型。" |
| 光线 | 一侧脸过曝或全黑、顶光造成眼窝全黑 | "找个窗户正面站一下，让光均匀打在脸上，不然我看不清你的轮廓。" |
| 遮挡 | 头发盖住下颌、手挡脸、口罩、粗框眼镜压住眉 | "把头发别到耳后、摘掉眼镜再拍一张，我需要看到完整的脸型。" |
| 占比 | 人脸小于画面 20%、分辨率过低 | "离镜头近一点，让脸占画面一半左右，细节才够。" |

另外：不加滤镜、不化浓妆、不美颜。已经化了妆的照片要说明"这是带妆照"，否则分析会把妆效当成骨相。

---

## 四、三个常见错误

1. **用形容词代替枚举**。写"皮肤偏黄"没用，要写 `"undertone": "warm"`。
2. **把妆容当特征**。鼻影造成的"高鼻梁"、眼线造成的"上挑眼"都是假的，要看素颜结构。
3. **过度自信**。宁可 `confidence: 0.5` + 保守枚举，也不要猜一个极端值——猜错会把整套方案带偏（例如把方圆脸判成方脸，修容会重一倍）。

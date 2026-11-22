# 06 生成方案与技术选型

> **本 skill 的默认路线是「A：宿主自带图像编辑 + 强提示词」。**
> 不部署 ComfyUI、不训练、不需要 GPU。B/C 两条路线作为技术背景保留：
> 如果你把 skill 移植到有 GPU 的环境、想要写真级重妆，再按这里的方案升级。
> 操作层面看 `SKILL.md` 第 3 步；本节解释"为什么这么写提示词"。

分析得再准，最后交付的是一张图。这一层决定"像不像她"和"能不能用"。

---

## 1. 三条可行路线（按落地成本排序）

### 路线 A：通用图像编辑模型 + 强提示词（推荐起点）

```
自拍 → VLM 分析 → 规则引擎 → Makeup Spec → 提示词 → 图像编辑模型 → 质检
```

- **优点**：零训练、零 GPU 部署、可换模型、迭代以小时计。当前一代编辑模型（Nano Banana 系 / Seedream Edit 系 / FLUX Kontext 系 / GPT Image 编辑系）对"局部改色 + 保留身份"已经够用。
- **缺点**：精细度受模型能力限制；同一提示词在不同模型上漂移；重妆（烟熏、截断）容易糊。
- **适用**：日常妆、伪素颜、通勤、淡颜用户——也就是 80% 的使用场景。

### 路线 B：换妆专用模型

| 方向 | 代表工作 | 能力 | 限制 |
|---|---|---|---|
| GAN 局部迁移 | BeautyGAN (2018)、LADN (ICCV'19)、PSGAN / PSGAN++ (CVPR'20 / TPAMI'21)、SSAT (AAAI'22)、EleGANt (ECCV'22)、RamGAN (ECCV'22)、SOGAN (MM'21) | 把参考图的妆"贴"到目标脸，区域级可控（部分工作支持局部/插值） | 只能模仿参考图，不能执行"因为中庭长所以腮红横打"；需要 face parsing + landmark 前置；分辨率与自然度有限 |
| Diffusion 迁移 | Stable-Makeup (2024)、MAD (2024)、SHMT (NeurIPS'24)、CSD-MT (CVPR'24)、BeautyREC、Gorgeous (2025) | 真实感强，支持重妆，结构保持更好 | 需 GPU 部署；仍需一张"目标妆容图"作为条件 |
| 工程化封装 | `ComfyUI_Stable_Makeup`（节点：StableMakeup_LoadModel + StableMakeup_Sampler） | 两图进、一图出，无提示词；内建 SPIGA landmark + ControlNet | 依赖 SD1.5 底座，默认 512，cfg 1.6 左右；模型文件分散需手动放置 |

**关键认知**：B 类模型都缺"审美决策"。它们回答"怎么把这个妆搬过去"，不回答"她该化什么妆"。所以 B 必须挂在 A 的决策层后面。

### 路线 C：A + B 混合（高质量档）

```
Spec → 文生图生成"同风格妆容参考图"（同肤色、同脸型的模特） → Stable-Makeup 迁移 → 局部修
```

- 好处：重妆/复杂层次（截断、烟熏、亮片）质量显著高于纯提示词。
- 代价：两次生成，身份漂移风险叠加；延迟与成本翻倍；需要 GPU。
- 建议：只在用户选"浓妆/晚宴/写真"档时启用。

---

## 2. 开源资源清单（做 B/C 时直接用）

**数据集**

| 数据集 | 规模 | 用途 |
|---|---|---|
| Makeup Transfer (BeautyGAN) | 3,834 | 有妆/无妆配对，经典基准 |
| MWild (PSGAN) | 772 | 不同姿态与表情，真实场景 |
| LADN Makeup | 635 | 局部迁移评测 |
| Color-Pattern-Makeup (CPM) | 577–5,555（4 子集） | 色彩+图案，重妆 |
| FCC | 18,425 | 人脸补全，可做辅助训练 |

**前置工具**
- 人脸解析：`face-parsing.PyTorch`（BiSeNet，19 类语义，眼/眉/唇/皮肤分区）
- 关键点：dlib 68 点（快）、SPIGA 98 点（稳，Stable-Makeup 在用）、insightface（检测+识别+属性）
- 身份度量：ArcFace / InsightFace embedding，余弦相似度

---

## 3. 身份保持：把"别改脸"变成工程约束

### 3.1 六条硬规则

1. **明确锁清单**。提示词必须显式列出不许变的东西：脸型、眼距、鼻型、唇形、肤色、发际线、表情、脸的角度。
2. **一次只改一处**。想同时改底妆+眼妆+唇+发色，分两到三轮，每轮从**原图**出发，不要在原图的输出上继续叠（叠加会累积漂移）。
3. **不写美颜词**。`beautiful` `flawless` `perfect skin` `model-like` 会把脸推向模型的"平均美"模板，直接毁掉相似度。改写成 `natural skin texture, visible pores, same person`。
4. **人脸占画面够大**。人脸 <20% 画面时，模型没有足够像素可复制，会"脑补"一张脸。输入不足时先裁切/放大再编辑。
5. **光线与角度不换**。"换个角度拍"是身份漂移最大的触发器之一。
6. **兜底回贴**。若其余都对、只有脸不像，用 face swap 把原图的脸贴回编辑结果（仅限用户本人照片）。

### 3.2 结构性红线

以下改动一律禁止，且要在质检里显式检查：
- 改变脸型轮廓（瘦脸、改下颌线走向）
- 改变眼型（开眼角、改双眼皮宽度、改眼裂高度）
- 改变鼻梁高度/鼻翼宽度
- 改变唇厚与唇宽（超出 ±10%）
- 改变肤色深度（超出 ±1 阶）
- 改变年龄感（磨皮过度导致"逆龄"）

妆容允许改的只有四件事：**颜色、明暗对比、边缘形状、毛发质感**。

---

## 4. 提示词结构（模板）

```
[1 身份锚点]
Use the attached photo as the identity reference. Keep the exact same face shape,
eye spacing, nose shape, lip shape, skin tone, hairline, hairstyle and expression.
The person must remain clearly recognizable as the same individual.

[2 编辑边界]
Apply makeup only. Do not change the face structure, do not slim the face,
do not enlarge the eyes, do not change skin depth.

[3 分区指令]（由 Makeup Spec 逐项渲染）
Base: {finish}, {coverage} coverage, even skin tone, {conceal} concealed,
      natural skin texture with visible pores, no plastic sheen.
Brows: {shape}, {thickness}, {color}, soft feathered edges, lighter at the inner head.
Eyes: {shadow_palette} matte eyeshadow within the crease, deeper at the outer
      corner, {liner} eyeliner, {lash} lashes, subtle aegyo-sal.
Contour: {strength} cool gray-brown contour at the nose sides and jawline, fully blended.
Highlight: matte highlight under the eyes and nasolabial folds, tiny shimmer at the
      nose tip and inner corners only.
Blush: {color} blush, {placement}, sheer and blurred edges.
Lips: {color}, {finish} finish, {shape}, no harsh lip line.

[4 保留清单]（与 [1] 呼应，模型对重复更敏感）
Keep the same framing, the same lighting direction, the same background,
the same hair, the same clothing.

[5 画面]
Photorealistic portrait, 85mm lens, soft directional window light, neutral background,
natural color grading, sharp focus on the face, no beauty filter.

[6 负面]
negative: different person, changed face shape, slimmed jaw, enlarged eyes,
reshaped nose, different skin tone, over-smoothed skin, plastic skin, heavy filter,
extra makeup layers, glossy grease, harsh lines, smudged makeup, extra jewelry,
different hairstyle, different background
```

**三个写法要点**
- 保留清单要在开头和结尾各出现一次（模型对位置敏感）。
- 分区指令要带**位置词**（山根、眼尾三角、颧骨高点），不要只说"eye makeup"。
- 强度不写"淡/浓"，写具体量：`sheer` `a hint of` `soft wash` / `defined` `deep` `bold`，并在规则层映射到 0–1。

---

## 5. 质检（自动化，可落地）

| 指标 | 方法 | 阈值建议 |
|---|---|---|
| 身份相似度 | ArcFace embedding 余弦 | ≥ 0.75（低于 0.6 直接判失败） |
| 肤色漂移 | 额头/颈部 Lab ΔE00 | ≤ 3 |
| 妆容一致性 | 分区（眼/颊/唇）平均色 ΔE00 vs Spec 目标色 | ≤ 12 |
| 结构保真 | 关键点位移 / 脸宽 | ≤ 2% |
| 自然度 | 人评 1–5 或 FID 参考 | ≥ 3.5 |
| 无伪影 | 眼周/唇周/发际线区域清晰度检查 | 人工抽检 |

**失败处置树**：
1. 脸不像 → 降强度、删美颜词、加保留清单，重跑；仍不行 → face swap 回贴
2. 妆容太浓/太脏 → 强度 ×0.7，换更浅的色板
3. 妆没上上去 → 提升强度、把"apply makeup"改成具体部位指令
4. 肤色变了 → 加 `same skin tone, do not lighten`
5. 头发变了 → 加 `keep hairstyle and hair color exactly`

---

## 6. 合规与隐私（必须写进 skill）

- 仅接受用户本人照片，或明确获得授权的照片；上传即告知用途与保存期限。
- 输出图嵌入/标注"AI 生成"，不提供去除标识的选项。
- 不做人脸替换、不做"变成别人"、不做未成年人的美妆改造。
- 照片默认不长期留存；本地处理优先，云端处理需明示。
- 明确产品定位：**妆容预览，不是医美模拟**。这句话既是合规，也是产品边界。

# MinerU Backend 模式对比

## 总览

| 特性 | pipeline | vlm-http-client | hybrid-http-client |
|---|---|---|---|
| **一句话描述** | 传统多模型流水线 | 纯大模型端到端 | 小模型指路 + 大模型理解 |
| **VLM 依赖** | 不需要 | 远程 VLM Server | 远程 VLM Server |
| **本地模型** | 7 个专用小模型 | 无 | 3 个小模型（布局+OCR+标题） |
| **语言支持** | 多语言 | 仅中英文 | 多语言 |
| **图片/图表理解** | 不支持 | 支持 | 支持（effort=high 时） |
| **幻觉风险** | 无 | 有 | 低 |
| **速度** | 快 | 慢 | 中等 |
| **本地资源需求** | GPU（跑小模型） | 几乎无 | CPU/GPU（跑小模型） |
| **默认 backend** | 否 | 否 | **是** |

---

## 1. Pipeline（传统多模型流水线）

### 架构图

```
PDF → 图片（pdfium 渲染）
  │
  ▼
┌─────────────────────────────────────────────────────────┐
│  ① 布局检测 (PP-DocLayoutV2)                             │
│     输入：页面图片                                        │
│     输出：每个区域的位置和类型                              │
│     类型：文字、表格、图片、公式、标题、页眉页脚等            │
├─────────────────────────────────────────────────────────┤
│  ② 公式检测 + 识别 (MFR: UniMERNet)                      │
│     输入：布局检测到的公式区域                              │
│     输出：公式的 LaTeX 表达式                              │
├─────────────────────────────────────────────────────────┤
│  ③ 表格方向分类 (MineruTableOrientationClsModel)          │
│     输入：表格区域图片                                     │
│     输出：横排 / 竖排 / 旋转角度                           │
├─────────────────────────────────────────────────────────┤
│  ④ 表格分类 (PP-LCNet)                                   │
│     输入：表格区域图片                                     │
│     输出：有线表 / 无线表                                  │
├─────────────────────────────────────────────────────────┤
│  ⑤ 表格 OCR (PaddleOCR det+rec)                         │
│     输入：表格区域图片                                     │
│     输出：表格内每个单元格的文字                            │
├─────────────────────────────────────────────────────────┤
│  ⑥ 表格结构识别                                          │
│     无线表 → SlanetPlus 模型                              │
│     有线表 → UnetStructure 模型                           │
│     输入：表格图片 + OCR 结果                              │
│     输出：HTML 表格结构                                   │
├─────────────────────────────────────────────────────────┤
│  ⑦ 文字 OCR (PaddleOCR)                                  │
│     输入：文字区域图片                                     │
│     输出：识别的文字内容                                   │
└─────────────────────────────────────────────────────────┘
  │
  ▼
合并所有结果 → middle JSON → 输出 Markdown
```

### 处理流程详解

#### 第 1 步：PDF 渲染
```python
# pdfium 将每页 PDF 渲染为图片
# DPI: 200，最大边长: 3500px
image = page.render(scale=scale).to_pil()
```

#### 第 2 步：布局检测
```python
# PP-DocLayoutV2 识别页面上所有元素的位置和类型
layout_res = layout_model.batch_predict(images)
# 输出示例：
# [
#   {"label": "text", "bbox": [x0, y0, x1, y1]},
#   {"label": "table", "bbox": [x0, y0, x1, y1]},
#   {"label": "inline_formula", "bbox": [x0, y0, x1, y1]},
#   {"label": "image", "bbox": [x0, y0, x1, y1]},
#   {"label": "doc_title", "bbox": [x0, y0, x1, y1]},
# ]
```

#### 第 3 步：公式识别
```python
# MFR 模型对每个公式区域识别 LaTeX
formula_list = mfr_model.batch_predict(formula_regions, images)
# 输出示例：
# [{"bbox": [...], "latex": "E = mc^2", "score": 0.95}]
```

#### 第 4 步：表格处理
```python
# 1. 方向分类 → 决定是否旋转
rotate_label = table_orientation_cls_model.predict(table_img)

# 2. 分类 → 有线表 or 无线表
cls_label = table_cls_model.predict(table_img)

# 3. OCR 识别表格内文字
ocr_result = ocr_engine.ocr(table_img)

# 4. 结构识别 → 生成 HTML
if cls_label == "wireless":
    html = wireless_table_model.predict(table_img, ocr_result)
else:
    html = wired_table_model.predict(table_img, ocr_result)
```

#### 第 5 步：文字 OCR
```python
# PaddleOCR 识别文字区域
text_result = ocr_engine.ocr(text_region_image)
```

### 优点
- **无幻觉**：每个模型只做检测/识别，看到什么输出什么
- **速度快**：小模型推理快，无网络开销
- **多语言**：PaddleOCR 支持 80+ 语言
- **资源省**：每个模型只需几百 MB 显存

### 缺点
- **无语义理解**：不理解文字含义，只做字符识别
- **无图片理解**：图片只保留位置，不识别内容
- **级联误差**：前一步错了，后面全错（如布局检测错 → OCR 区域错 → 文字错）
- **模型多**：部署复杂，7 个模型要分别加载

---

## 2. VLM-HTTP-Client（纯大模型端到端）

### 架构图

```
PDF → 图片（pdfium 渲染）
  │
  ▼
┌─────────────────────────────────────────────────────────┐
│  远程 VLM Server (NPU/GPU)                               │
│                                                         │
│  MinerU2.5-Pro (1.2B 参数的视觉语言模型)                  │
│                                                         │
│  一次性完成所有任务：                                      │
│  ├── 布局检测（哪里是文字、表格、图片）                     │
│  ├── 文字识别（OCR）                                     │
│  ├── 公式识别（LaTeX）                                   │
│  ├── 表格结构识别（HTML）                                 │
│  ├── 图片/图表理解                                       │
│  └── 语义理解（标题层级、段落结构）                        │
│                                                         │
│  内部两步推理：                                           │
│  Step 1: batch_two_step_extract → 布局 + 内容提取        │
└─────────────────────────────────────────────────────────┘
  │
  ▼
合并结果 → middle JSON → 输出 Markdown
```

### 处理流程详解

#### 第 1 步：PDF 渲染
```python
# 与 pipeline 相同
image = page.render(scale=scale).to_pil()
```

#### 第 2 步：VLM 推理（一步到位）
```python
# 所有页面的图片一次性发给 VLM
results = predictor.batch_two_step_extract(
    images=images_pil_list,     # 64 页图片
    image_analysis=True,        # 是否分析图片/图表
)
# VLM 内部自动完成：
# - 布局检测
# - 文字识别
# - 公式识别
# - 表格识别
# - 图片理解
# 输出示例（每页）：
# [
#   {"type": "title", "bbox": [...], "text": "第一章 概述"},
#   {"type": "text", "bbox": [...], "text": "这是一段正文..."},
#   {"type": "table", "bbox": [...], "html": "<table>...</table>"},
#   {"type": "inline_formula", "bbox": [...], "latex": "E=mc^2"},
#   {"type": "image", "bbox": [...], "text": "图片描述..."},
# ]
```

### 优点
- **架构简单**：一个模型搞定所有事
- **全局理解**：VLM 理解文档整体语义
- **图片理解**：能识别图片/图表内容
- **无级联误差**：端到端训练，各任务联合优化

### 缺点
- **可能幻觉**：大语言模型可能编造内容
- **仅中英文**：模型训练数据限制
- **速度慢**：大模型推理 + 网络传输
- **资源重**：VLM 需要 8GB+ 显存

---

## 3. Hybrid-HTTP-Client（小模型 + 大模型协作）

### 架构图

```
PDF → 图片（pdfium 渲染）
  │
  ▼
┌─────────────────────────────────────────────────────────┐
│  本地 Pipeline 小模型                                     │
│                                                         │
│  ① 布局检测 (PP-DocLayoutV2)                             │
│     识别：文字、表格、图片、公式等区域                      │
│                                                         │
│  ② 表格方向分类                                          │
│     判断表格是横排还是竖排                                 │
│                                                         │
│  ③ 构建布局提示                                          │
│     把布局结果转成 VLM 能理解的 ContentBlock 格式          │
│     告诉 VLM："这个区域是文字，那个区域是表格"             │
└─────────────────────────────────────────────────────────┘
  │
  │  布局提示 + 图片
  ▼
┌─────────────────────────────────────────────────────────┐
│  远程 VLM Server (NPU/GPU)                               │
│                                                         │
│  VLM 在布局区域内提取内容：                                │
│  predictor.batch_extract_with_layout(                   │
│      images,           # 64 页图片                       │
│      vlm_blocks_list,  # 本地给的布局提示                 │
│  )                                                      │
│                                                         │
│  VLM 只需要在已知区域内做内容提取                          │
│  不需要自己做布局检测（已由本地小模型完成）                  │
└─────────────────────────────────────────────────────────┘
  │
  ▼
┌─────────────────────────────────────────────────────────┐
│  本地 Pipeline 小模型（补充）                              │
│                                                         │
│  ④ OCR det (PaddleOCR)                                  │
│     在文字区域提供位置 hint                               │
│     帮助 VLM 更准确定位文字                               │
│                                                         │
│  ⑤ 标题拆分                                              │
│     用布局检测的 doc_title 结果                           │
│     把 VLM 输出的 title 拆分为：                          │
│     - doc_title（文档标题）                               │
│     - paragraph_title（段落标题）                         │
└─────────────────────────────────────────────────────────┘
  │
  ▼
合并结果 → middle JSON → 输出 Markdown
```

### 处理流程详解

#### 第 1 步：PDF 渲染
```python
# 与 pipeline / vlm 相同
image = page.render(scale=scale).to_pil()
```

#### 第 2 步：本地布局检测
```python
# PP-DocLayoutV2 识别页面布局
images_layout_res = layout_model.batch_predict(images)
# 输出：每个区域的位置和类型
```

#### 第 3 步：构建 VLM 布局提示
```python
# 把布局结果转成 VLM 能理解的格式
vlm_blocks_list = [
    _build_medium_vlm_layout_blocks(page_layout_res, width, height)
    for page_layout_res in images_layout_res
]
# 输出示例：
# [
#   ContentBlock(type="text", bbox=[x0, y0, x1, y1]),
#   ContentBlock(type="table", bbox=[x0, y0, x1, y1]),
# ]
```

#### 第 4 步：VLM 在布局区域内提取内容
```python
# VLM 接收图片 + 布局提示，在已知区域内提取内容
window_model_list = predictor.batch_extract_with_layout(
    images_pil_list,    # 64 页图片
    vlm_blocks_list,    # 本地给的布局提示
)
# VLM 不需要自己做布局检测，只需要在给定区域内提取内容
# 输出：每页的文字、表格内容、图片描述等
```

#### 第 5 步：本地补充 OCR hint
```python
# PaddleOCR 在文字区域提供位置 hint
_apply_vlm_ocr_det_sidecars_for_window(
    images_pil_list,
    window_model_list,
    batch_ratio,
    images_layout_res=images_layout_res,
    hybrid_pipeline_model=hybrid_pipeline_model,
)
```

#### 第 6 步：标题拆分
```python
# 用布局检测的 doc_title 结果拆分 VLM 输出的标题
_apply_layout_title_split(
    window_model_list,     # VLM 输出的结果
    images_layout_res,     # 布局检测结果
    page_sizes,
)
```

### Effort 级别

| | medium（默认） | high |
|---|---|---|
| **布局检测** | 本地 Pipeline | VLM 自己做 |
| **VLM 方法** | `batch_extract_with_layout`（带布局提示） | `batch_two_step_extract`（全量推理） |
| **图片分析** | 关闭 | 开启 |
| **速度** | 快 | 慢 |
| **精度** | 高 | 最高 |

### 优点
- **各取所长**：小模型做精准检测，VLM 做内容理解
- **减少 VLM 负担**：VLM 不需要自己做布局检测，只需在已知区域内提取内容
- **多语言**：Pipeline 小模型支持多语言
- **低幻觉**：VLM 在已知区域内提取，编造空间小

### 缺点
- **级联误差**：布局检测错了 → VLM 在错误区域提取 → 结果错
- **部署复杂**：需要本地 GPU 跑 Pipeline 模型 + 远程 VLM
- **仍然有网络开销**：图片 + 布局提示要传到远程 VLM

---

## 4. 三者对比

### 工作流程对比

```
Pipeline（7 步）：
  布局 → 公式 → 表格方向 → 表格分类 → 表格OCR → 表格结构 → 文字OCR
  全部本地，串行执行

VLM（1 步）：
  图片 → VLM 一次性完成所有事
  全部远程

Hybrid（5 步）：
  布局 → 布局提示 → VLM提取 → OCR补充 → 标题拆分
  本地 + 远程协作
```

### 能力对比

| 能力 | pipeline | vlm-http-client | hybrid-http-client |
|---|---|---|---|
| 布局检测 | PP-DocLayoutV2 | VLM 内置 | PP-DocLayoutV2 + VLM |
| 文字识别 | PaddleOCR | VLM | VLM + PaddleOCR hint |
| 公式识别 | MFR (UniMERNet) | VLM | VLM |
| 表格结构 | SlanetPlus/Unet | VLM | VLM |
| 图片理解 | 不支持 | VLM | effort=high 时支持 |
| 语义理解 | 无 | VLM | VLM |
| 跨页合并 | 支持 | 支持 | 支持 |

### 资源消耗对比

| 资源 | pipeline | vlm-http-client | hybrid-http-client |
|---|---|---|---|
| 本地显存 | 2-4 GB | ~0 | 1-2 GB |
| 远程显存 | - | 8-64 GB (VLM) | 8-64 GB (VLM) |
| 本地 CPU | 中等 | 极低 | 中等 |
| 网络带宽 | 无 | 高（图片传输） | 高（图片+布局提示） |

### 适用场景

| 场景 | 推荐 backend | 原因 |
|---|---|---|
| 中英文文档，追求精度 | vlm-http-client | VLM 端到端，全局理解 |
| 多语言文档 | hybrid-http-client | Pipeline 支持多语言 |
| 扫描件 PDF | vlm-http-client | VLM 擅长图像理解 |
| 代码/公式密集文档 | hybrid-http-client | Pipeline 公式识别更准 |
| 表格密集文档 | pipeline | 专用表格模型更准 |
| 需要图片内容描述 | vlm-http-client | VLM 能理解图片 |
| 资源受限环境 | pipeline | 无需 VLM Server |
| 高并发批量处理 | pipeline | 速度快，资源省 |

---

## 5. 部署架构对比

### Pipeline

```
CPU/GPU 机器：
  └── mineru-api（本地跑 7 个模型）
```

### VLM-HTTP-Client

```
CPU 机器：                    NPU/GPU 机器：
  └── mineru-api（薄代理） ──→ └── VLM Server（推理）
```

### Hybrid-HTTP-Client

```
CPU/GPU 机器：                  NPU/GPU 机器：
  └── mineru-api               └── VLM Server（推理）
      （本地跑 3 个模型）
      （远程调 VLM）
```

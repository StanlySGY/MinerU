#!/usr/bin/env python3
"""Generate MinerU Backend Benchmark Report as .docx"""
import os
from docx import Document
from docx.shared import Pt, Cm, Inches, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.oxml.ns import qn

OUTPUT = os.path.join(os.path.dirname(__file__), "MinerU_Backend_Benchmark_Report.docx")

doc = Document()

# ── Page setup (A4) ──
for section in doc.sections:
    section.page_width = Cm(21.0)
    section.page_height = Cm(29.7)
    section.top_margin = Cm(2.54)
    section.bottom_margin = Cm(2.54)
    section.left_margin = Cm(3.18)
    section.right_margin = Cm(3.18)

style_normal = doc.styles['Normal']
style_normal.font.name = 'Calibri'
style_normal.font.size = Pt(11)
style_normal.paragraph_format.line_spacing = 1.15
style_normal.paragraph_format.space_after = Pt(6)

# ── Helper ──
def add_heading(text, level=1):
    h = doc.add_heading(text, level=level)
    for run in h.runs:
        run.font.color.rgb = RGBColor(0x1F, 0x3A, 0x5F)
    return h

def add_table(headers, rows):
    table = doc.add_table(rows=1 + len(rows), cols=len(headers))
    table.style = 'Light Grid Accent 1'
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    for i, h in enumerate(headers):
        cell = table.rows[0].cells[i]
        cell.text = h
        for p in cell.paragraphs:
            for run in p.runs:
                run.bold = True
                run.font.size = Pt(10)
    for r_idx, row in enumerate(rows):
        for c_idx, val in enumerate(row):
            cell = table.rows[r_idx + 1].cells[c_idx]
            cell.text = str(val)
            for p in cell.paragraphs:
                for run in p.runs:
                    run.font.size = Pt(10)
    return table

# ══════════════════════════════════════════════
# Title
# ══════════════════════════════════════════════
title = doc.add_heading('MinerU Backend 性能测试报告', level=0)
title.alignment = WD_ALIGN_PARAGRAPH.CENTER
for run in title.runs:
    run.font.color.rgb = RGBColor(0x1F, 0x3A, 0x5F)

doc.add_paragraph('')

meta_data = [
    ('测试时间', '2026-07-19 10:57 ~ 16:34'),
    ('报告日期', '2026-07-19'),
    ('测试人员', 'SGY'),
]
meta_table = doc.add_table(rows=len(meta_data), cols=2)
meta_table.alignment = WD_TABLE_ALIGNMENT.CENTER
for i, (k, v) in enumerate(meta_data):
    meta_table.rows[i].cells[0].text = k
    meta_table.rows[i].cells[1].text = v
    for cell in meta_table.rows[i].cells:
        for p in cell.paragraphs:
            for run in p.runs:
                run.font.size = Pt(10)

doc.add_page_break()

# ══════════════════════════════════════════════
# 1. 测试背景
# ══════════════════════════════════════════════
add_heading('1. 测试背景', level=1)
doc.add_paragraph(
    'MinerU 是一个文档解析工具，支持三种后端（Backend）模式。'
    '本报告对三种后端在相同测试集上的处理性能进行对比测试，'
    '为生产环境部署选型提供数据支撑。'
)

add_heading('1.1 三种 Backend 简介', level=2)
add_table(
    ['Backend', '说明', '计算位置'],
    [
        ['pipeline', '传统多模型流水线（布局+OCR+公式+表格）', '全部本地 CPU'],
        ['vlm-http-client', '纯大模型端到端', '远程 VLM Server（NPU）'],
        ['hybrid-http-client', '本地小模型布局 + 远程 VLM 内容提取', '本地 CPU + 远程 VLM'],
    ]
)

# ══════════════════════════════════════════════
# 2. 测试环境
# ══════════════════════════════════════════════
add_heading('2. 测试环境', level=1)
add_table(
    ['项目', '配置'],
    [
        ['API Server', 'CPU 服务器（ARM），端口 18000'],
        ['VLM Server', 'NPU 服务器（10.8.132.224:6002）'],
        ['VLM 模型', 'MinerU2.5-Pro-2604-1.2B（1.2B 参数）'],
        ['VLM 推理框架', 'vLLM'],
        ['最大并发请求数', '3'],
        ['处理窗口大小', '32 页/窗口'],
        ['请求超时', '4 小时'],
    ]
)

# ══════════════════════════════════════════════
# 3. 测试文件
# ══════════════════════════════════════════════
add_heading('3. 测试文件', level=1)
doc.add_paragraph('共 8 个 PDF 文件，总计约 682MB，均为军事历史类文档。')
add_table(
    ['文件名', '大小'],
    [
        ['回望历史 捍卫正义_世界反法西斯战争东方主战场的伟大贡献.pdf', '1.2 MB'],
        ['党的军事指导理论百年创新发展.pdf', '3.5 MB'],
        ['中国人民解放军战史教程.pdf', '8.1 MB'],
        ['人民军队为什么是不可战胜的力量.pdf', '62 MB'],
        ['战争制胜的智慧_毛泽东军事思想新论.pdf', '110 MB'],
        ['毛泽东和中国革命战争_毛泽东军事思想史论.pdf', '120 MB'],
        ['外国档案文献中的中共抗战.pdf', '163 MB'],
        ['抗日战争研究论集.pdf', '217 MB'],
    ]
)

# ══════════════════════════════════════════════
# 4. 测试方法
# ══════════════════════════════════════════════
add_heading('4. 测试方法', level=1)
doc.add_paragraph(
    '使用 curl 同时向 API Server 提交所有 PDF 文件（并发数 = 3），'
    '记录每个文件从提交到返回结果的耗时。每个 Backend 独立测试，'
    '测试间等待 5 秒让 VLM 释放资源。'
)
doc.add_paragraph(
    'pipeline 后端不需要 VLM Server，所有计算在本地 CPU 完成；'
    'vlm-http-client 和 hybrid-http-client 需要调用远程 VLM Server。'
)

# ══════════════════════════════════════════════
# 5. 测试结果
# ══════════════════════════════════════════════
add_heading('5. 测试结果', level=1)

add_heading('5.1 总体性能', level=2)
add_table(
    ['Backend', '总耗时', '平均每文件耗时', '相对速度'],
    [
        ['pipeline', '83.8 分钟', '10.5 分钟', '1.00x（基准）'],
        ['hybrid-http-client', '101.1 分钟', '12.6 分钟', '0.83x'],
        ['vlm-http-client', '151.7 分钟', '19.0 分钟', '0.55x'],
    ]
)

add_heading('5.2 逐文件耗时', level=2)
add_table(
    ['文件', '大小', 'pipeline', 'hybrid', 'vlm'],
    [
        ['回望历史...', '1.2 MB', '3.1 min', '4.4 min', '11.7 min'],
        ['党的军事...', '3.5 MB', '20.8 min', '16.2 min', '78.0 min'],
        ['中国人民...', '8.1 MB', '23.0 min', '39.1 min', '55.6 min'],
        ['人民军队...', '62 MB', '42.1 min', '64.1 min', '86.8 min'],
        ['战争制胜...', '110 MB', '53.5 min', '58.2 min', '108.1 min'],
        ['毛泽东和...', '120 MB', '56.0 min', '75.4 min', '116.0 min'],
        ['外国档案...', '163 MB', '78.0 min', '96.4 min', '147.9 min'],
        ['抗日战争...', '217 MB', '83.8 min', '101.1 min', '151.7 min'],
    ]
)

add_heading('5.3 VLM 倍数分析', level=2)
doc.add_paragraph('以 pipeline 为基准，计算 vlm-http-client 的耗时倍数：')
add_table(
    ['文件大小区间', 'vlm/pipeline 倍数', '说明'],
    [
        ['< 10 MB', '2.4x ~ 3.8x', '小文件固定开销（网络连接、请求排队）占比高'],
        ['50 ~ 120 MB', '1.7x ~ 2.0x', '大文件差异趋于稳定'],
        ['> 160 MB', '1.8x ~ 1.9x', '超大文件倍数基本稳定'],
    ]
)

# ══════════════════════════════════════════════
# 6. 分析与结论
# ══════════════════════════════════════════════
add_heading('6. 分析与结论', level=1)

add_heading('6.1 Pipeline 最快的原因', level=2)
doc.add_paragraph(
    '全部在本地 CPU 执行，无网络开销。专用模型（PP-DocLayoutV2、PaddleOCR、MFR）'
    '各自处理单一任务，效率高。无 VLM 推理的 GPU/NPU 资源竞争。'
)

add_heading('6.2 VLM 最慢的原因', level=2)
doc.add_paragraph(
    '所有任务都通过 HTTP 发到远程 VLM Server，网络延迟叠加。'
    'VLM 需要同时完成布局检测、文字识别、公式识别、表格识别，计算量大。'
    '3 个并发请求同时竞争同一个 VLM Server，显存和 KV Cache 资源不足导致处理速度退化。'
)

add_heading('6.3 Hybrid 居中的原因', level=2)
doc.add_paragraph(
    '布局检测在本地完成（快），减少了 VLM 的计算量。'
    'VLM 只需在已知区域内提取内容，比纯 VLM 的工作量少。'
    '但仍有网络开销，所以不如纯 Pipeline。'
)

add_heading('6.4 并发对 VLM 的影响', level=2)
doc.add_paragraph(
    '从日志观察到，VLM Server 在处理 3 个并发文档时，'
    '处理速度从 5s/it 退化到 45s/it（9 倍退化）。'
    '这是因为 vLLM 的 KV Cache 被多个请求瓜分，'
    '每个请求分到的显存减少，batch size 被迫降低，请求排队等待推理资源。'
)

# ══════════════════════════════════════════════
# 7. 部署建议
# ══════════════════════════════════════════════
add_heading('7. 部署建议', level=1)
add_table(
    ['场景', '推荐 Backend', '理由'],
    [
        ['追求处理速度', 'pipeline', '纯本地最快，无网络开销'],
        ['需要图片/图表理解', 'vlm-http-client', 'VLM 唯一支持的能力'],
        ['需要多语言支持', 'hybrid-http-client', 'Pipeline 小模型支持多语言'],
        ['平衡速度与精度', 'hybrid-http-client', '比纯 VLM 快 33%'],
        ['高并发批量处理', 'pipeline', '本地并行，无 VLM 瓶颈'],
    ]
)

# ══════════════════════════════════════════════
# 8. 性能优化建议
# ══════════════════════════════════════════════
add_heading('8. 性能优化建议', level=1)
suggestions = [
    '降低 VLM 并发：将 MINERU_API_MAX_CONCURRENT_REQUESTS 从 3 调为 1，避免 VLM 资源竞争。',
    '增加 VLM 实例：多起几个 VLM Server，用 Router 分散负载。',
    '增大处理窗口：将 MINERU_PROCESSING_WINDOW_SIZE 从 32 调为 64，减少 HTTP 请求次数。',
    '使用本地模型：如果不需要图片理解，用 pipeline 最快。',
]
for s in suggestions:
    doc.add_paragraph(s, style='List Number')

# ══════════════════════════════════════════════
# Save
# ══════════════════════════════════════════════
doc.save(OUTPUT)
print(f"Report saved to: {OUTPUT}")

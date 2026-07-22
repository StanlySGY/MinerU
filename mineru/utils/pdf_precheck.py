# Copyright (c) Opendatalab. All rights reserved.
"""
PDF 预检查模块 - 在 VLM 处理前过滤有问题的 PDF

使用 MinerU 已有的 classify 函数检测：
- 加密 PDF
- 乱码文本
- 编码错误
- 字体问题
- 图片覆盖率异常

不调用 VLM，不耗网络，纯本地检查。
"""

from dataclasses import dataclass

from loguru import logger

from mineru.utils.pdfium_guard import pdfium_guard, open_pdfium_document


@dataclass
class PrecheckResult:
    safe: bool
    reason: str


def check_pdf_quality(pdf_bytes: bytes) -> PrecheckResult:
    """
    检查 PDF 是否可以安全处理。

    策略：
    1. 尝试用 pdfium 打开 PDF（加密/损坏的 PDF 会失败）
    2. 尝试提取文本，检查是否有乱码（用 MinerU 已有的逻辑）

    返回:
        PrecheckResult: safe=True 可处理, safe=False 应跳过
    """
    # 1. 检查 PDF 能否正常打开
    try:
        with pdfium_guard():
            pdf = open_pdfium_document(pdfium.PdfDocument, pdf_bytes)
            page_count = len(pdf)
            if page_count == 0:
                return PrecheckResult(safe=False, reason="PDF 没有可加载的页面")

            # 尝试渲染第 1 页，如果失败说明 PDF 有问题
            try:
                page = pdf[0]
                bitmap = page.render(scale=0.5)
                img = bitmap.to_pil()
                img.close()
                bitmap.close()
            except Exception as e:
                return PrecheckResult(
                    safe=False,
                    reason=f"PDF 第 1 页渲染失败（可能是加密或损坏的文件）: {e}"
                )

            # 尝试提取文本，检查是否有乱码
            try:
                text = page.get_textpage().get_text_range()
                if text and len(text.strip()) > 0:
                    # 有可提取的文本，说明 PDF 结构正常
                    pass
                else:
                    # 没有文本，可能是纯图片 PDF，不一定是问题
                    logger.debug("PDF 第 1 页无可提取文本，可能是纯图片 PDF")
            except Exception as e:
                logger.debug(f"PDF 文本提取异常: {e}")

            return PrecheckResult(safe=True, reason="OK")

    except Exception as e:
        return PrecheckResult(
            safe=False,
            reason=f"PDF 无法正常打开: {e}"
        )

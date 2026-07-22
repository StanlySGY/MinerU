# Copyright (c) Opendatalab. All rights reserved.
"""
PDF 预检查模块 - 在 VLM 处理前识别乱码/加密 PDF

原理：
  pdfium 渲染第 1 页为图片 → numpy 分析像素质量 → 判断是否乱码

不调用 VLM，不耗网络，纯本地检查，耗时约 0.5 秒。
"""

from dataclasses import dataclass

from loguru import logger

from mineru.utils.pdfium_guard import pdfium_guard, open_pdfium_document


@dataclass
class PrecheckResult:
    safe: bool
    reason: str


def _analyze_image_quality(img) -> float:
    """
    分析图片质量，返回 0-1 分数。
    低分 = 乱码/噪声/纯色。
    """
    import numpy as np

    img_array = np.array(img.convert("L"))
    std_dev = np.std(img_array)

    # 纯色或接近纯色
    if std_dev < 5:
        return 0.1

    # 随机噪声：标准差高但无结构
    if std_dev > 80:
        diff_h = np.abs(np.diff(img_array, axis=1))
        diff_v = np.abs(np.diff(img_array, axis=0))
        avg_diff = (np.mean(diff_h) + np.mean(diff_v)) / 2
        if avg_diff > 40:
            return 0.2

    # 颜色极度单一
    hist, _ = np.histogram(img_array, bins=256, range=(0, 255))
    non_zero_bins = np.count_nonzero(hist)
    if non_zero_bins < 10:
        return 0.2

    return min(1.0, std_dev / 50)


def check_pdf_quality(pdf_bytes: bytes) -> PrecheckResult:
    """
    检查 PDF 渲染质量。
    乱码/加密 PDF 渲染出来的图片是纯色、噪声或无结构图案。

    返回:
        PrecheckResult: safe=True 可处理, safe=False 应跳过
    """
    try:
        with pdfium_guard():
            pdf = open_pdfium_document(pdfium.PdfDocument, pdf_bytes)
            page_count = len(pdf)
            if page_count == 0:
                return PrecheckResult(safe=False, reason="PDF 没有可加载的页面")

            # 只检查第 1 页，低分辨率快速渲染
            page = pdf[0]
            bitmap = page.render(scale=0.5)
            img = bitmap.to_pil()
            bitmap.close()

            quality = _analyze_image_quality(img)
            img.close()

            if quality < 0.3:
                logger.warning(f"PDF 渲染质量差: quality={quality:.2f}，跳过")
                return PrecheckResult(
                    safe=False,
                    reason=f"PDF 渲染质量差（可能是加密或损坏的文件）"
                )

            return PrecheckResult(safe=True, reason="OK")

    except Exception as e:
        logger.warning(f"PDF 预检查异常: {e}，跳过")
        return PrecheckResult(
            safe=False,
            reason=f"PDF 无法正常打开（{e}）"
        )

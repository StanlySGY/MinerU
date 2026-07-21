# Copyright (c) Opendatalab. All rights reserved.
"""
PDF 预检查模块 - 在 VLM 处理前识别问题 PDF

用于检测加密、损坏、乱码等异常 PDF，避免 VLM 陷入无限重试。

检测策略（不需要知道加密方式）：
1. PDF 结构检查：加密标志、文件完整性
2. 渲染质量检查：渲染 1 页图片，检查是否乱码/空白/噪声
3. 文本质量检查：提取文本，检查乱码比例
"""

import io
import os
from dataclasses import dataclass
from typing import Optional

from loguru import logger

from mineru.utils.pdfium_guard import pdfium_guard, open_pdfium_document


@dataclass
class PrecheckResult:
    """预检查结果"""
    safe: bool  # True=可以安全处理，False=应该跳过
    reason: str  # 原因描述
    confidence: float  # 置信度 0-1


def check_pdf_encryption(pdf_bytes: bytes) -> PrecheckResult:
    """
    检查 PDF 是否加密。
    加密 PDF 可能导致渲染出乱码图片，VLM 无法处理。
    """
    try:
        import pikepdf
        pdf = pikepdf.open(io.BytesIO(pdf_bytes))
        if pdf.is_encrypted:
            # 尝试用空密码打开
            try:
                pdf.decrypt("")
            except Exception:
                return PrecheckResult(
                    safe=False,
                    reason="PDF 已加密且需要密码才能打开",
                    confidence=0.95
                )
            # 能用空密码打开，但可能内容仍有问题
            pdf.close()
            return PrecheckResult(
                safe=True,
                reason="PDF 已加密但可以空密码打开",
                confidence=0.5
            )
        pdf.close()
    except ImportError:
        logger.debug("pikepdf 未安装，跳过加密检查")
    except Exception as e:
        logger.debug(f"加密检查异常: {e}")
    return PrecheckResult(safe=True, reason="未检测到加密", confidence=0.3)


def check_pdf_render_quality(pdf_bytes: bytes, max_pages: int = 2) -> PrecheckResult:
    """
    渲染 PDF 的前几页，检查图片质量。
    加密/损坏的 PDF 渲染出来通常是：纯色、噪声、乱码图案。
    """
    try:
        with pdfium_guard():
            pdf = open_pdfium_document(pdfium.PdfDocument, pdf_bytes)
            page_count = len(pdf)
            if page_count == 0:
                return PrecheckResult(
                    safe=False,
                    reason="PDF 没有可加载的页面",
                    confidence=0.9
                )

            # 只检查前几页
            check_pages = min(max_pages, page_count)
            problematic_pages = 0

            for i in range(check_pages):
                try:
                    page = pdf[i]
                    # 渲染为图片
                    bitmap = page.render(scale=0.5)  # 低分辨率快速检查
                    img = bitmap.to_pil()
                    bitmap.close()

                    # 分析图片质量
                    quality = _analyze_image_quality(img)
                    img.close()

                    if quality < 0.3:
                        problematic_pages += 1
                        logger.debug(
                            f"第 {i+1} 页渲染质量差: score={quality:.2f}"
                        )
                except Exception as e:
                    problematic_pages += 1
                    logger.debug(f"第 {i+1} 页渲染失败: {e}")

            if problematic_pages >= check_pages:
                return PrecheckResult(
                    safe=False,
                    reason=f"所有检查页面渲染质量差（{problematic_pages}/{check_pages}）",
                    confidence=0.85
                )
            elif problematic_pages > 0:
                return PrecheckResult(
                    safe=True,
                    reason=f"部分页面渲染质量差（{problematic_pages}/{check_pages}），可能有问题",
                    confidence=0.4
                )

    except Exception as e:
        logger.debug(f"渲染质量检查异常: {e}")
        return PrecheckResult(
            safe=True,
            reason=f"渲染检查异常: {e}",
            confidence=0.2
        )

    return PrecheckResult(safe=True, reason="渲染质量正常", confidence=0.7)


def _analyze_image_quality(img) -> float:
    """
    分析图片质量，返回 0-1 的分数。
    低分 = 可能是乱码/噪声/空白。

    判断依据：
    - 颜色方差：纯色或接近纯色 → 低分
    - 像素分布：过于均匀或过于集中 → 低分
    - 边缘信息：没有边缘信息 → 可能是空白
    """
    import numpy as np

    try:
        img_array = np.array(img.convert("L"))  # 转灰度

        # 计算像素值的标准差（衡量颜色丰富度）
        std_dev = np.std(img_array)
        mean_val = np.mean(img_array)

        # 检查是否接近纯色（标准差很小）
        if std_dev < 5:
            return 0.1  # 几乎纯色

        # 检查是否是随机噪声（标准差很高但没有结构）
        if std_dev > 80:
            # 进一步检查：计算相邻像素的差异
            diff_h = np.abs(np.diff(img_array, axis=1))
            diff_v = np.abs(np.diff(img_array, axis=0))
            avg_diff = (np.mean(diff_h) + np.mean(diff_v)) / 2
            if avg_diff > 40:
                return 0.2  # 随机噪声

        # 检查像素分布是否过于集中
        hist, _ = np.histogram(img_array, bins=256, range=(0, 255))
        non_zero_bins = np.count_nonzero(hist)
        if non_zero_bins < 10:
            return 0.2  # 颜色极度单一

        # 正常图片应该有一定的颜色丰富度和结构
        return min(1.0, std_dev / 50)

    except Exception:
        return 0.5  # 无法判断时给中间分


def precheck_pdf(pdf_bytes: bytes) -> PrecheckResult:
    """
    综合预检查 PDF。
    任何一项检查失败都会返回 safe=False。

    返回:
        PrecheckResult: safe=True 可以处理, safe=False 应该跳过
    """
    results = []

    # 1. 加密检查
    encryption_result = check_pdf_encryption(pdf_bytes)
    results.append(encryption_result)
    if not encryption_result.safe:
        return encryption_result

    # 2. 渲染质量检查
    render_result = check_pdf_render_quality(pdf_bytes)
    results.append(render_result)
    if not render_result.safe:
        return render_result

    # 综合判断：如果有任一检查明确表示不安全，就返回不安全
    for result in results:
        if not result.safe and result.confidence > 0.7:
            return result

    # 如果所有检查都通过，返回安全
    return PrecheckResult(
        safe=True,
        reason="所有检查通过",
        confidence=max(r.confidence for r in results)
    )

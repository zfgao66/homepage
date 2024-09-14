---
layout: post
title:  "Unlocking Data-free Low-bit Quantization with Matrix Decomposition for KV Cache Compression"
date:   2024-05-21 18:04:44 +00:00
image: images/2024/quantization.png
categories: 2024_research
author: Ze-Feng Gao
authors: "Peiyu Liu, <strong>Ze-Feng Gao</strong>, Wayne Xin Zhao, Yipeng Ma, Tao Wang, Ji-Rong Wen"
venue: "Annual Meeting of the Association for Computational Linguistics (ACL2024)"
paper: /pdfs/2024/quantization.pdf
arxiv: https://arxiv.org/abs/2405.12591
link: https://aclanthology.org/2024.acl-long.133/


---
In this paper, we introduce DecoQuant, a novel data-free low-bit quantization technique based on tensor decomposition methods, to effectively compress KV cache. Our core idea is to adjust the outlier distribution of the original matrix by performing tensor decomposition, so that the quantization difficulties are migrated from the matrix to decomposed local tensors. Specially, we find that outliers mainly concentrate on small local tensors, while large tensors tend to have a narrower value range.
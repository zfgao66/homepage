---
layout: post
title:  "Over-parameterized Student Model via Tensor Decomposition Boosted Knowledge Distillation"
date:   2024-10-31 18:04:44 +00:00
image: images/2024/MPO-distill.png
categories: 2024_research
author: Ze-Feng Gao
authors: "Yu-Liang Zhan, Zhong-Yi Lu, Hao Sun<sup>#</sup>, <strong>Ze-Feng Gao<sup>#</sup></strong>"
venue: "38th Conference on Neural Information Processing Systems (NeurIPS 2024)"
paper: /pdfs/2024/MPO-distill.pdf
arxiv: https://arxiv.org/abs/2411.06448
link: https://openreview.net/forum?id=fT1RkAgrC3
code: https://github.com/intell-sci-comput/OPDF

---
In this paper, we scale up the parameters of the student model during training, to benefit from over-parameterization without increasing the inference latency. In particular, we propose a tensor decomposition strategy that effectively over-parameterizes the relatively small student model through an efficient and nearly lossless decomposition of its parameter matrices into higher-dimensional tensors.
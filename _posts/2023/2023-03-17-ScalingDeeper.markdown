---
layout: post
title:  "Scaling Pre-trained Language Models to Deeper via Parameter-efficient Architecture"
date:   2023-03-17 18:04:44 +00:00
image: images/2023/ScalingDeeper.png
categories: 2023_research
author: Ze-Feng Gao
authors: "Peiyu Liu*, <strong>Ze-Feng Gao*</strong>, Yushuo Chen, Wayne Xin Zhao<sup>#</sup>, Ji-Rong Wen"
venue: "Arxiv"
paper: /pdfs/2023/ScalingDeeper.pdf
arxiv: https://arxiv.org/abs/2303.16753
---
In this paper, we propose a highly parameter-efficient approach to scaling pre-trained language models (PLMs) to a deeper model depth based on matrix product operator (MPO) decomposition, which shares the central tensor across all layers to reduce model size while keeping layer-specific auxiliary tensors and adapters for flexible adaptation.

---
layout: post
title:  "Enhancing Parameter-efficient Fine-tuning with Simple Calibration Based on Stable Rank"
date:   2024-01-09 18:04:44 +00:00
image: images/2024/stablerank.png
categories: 2024_research
author: Ze-Feng Gao
authors: "Peiyu Liu, <strong>Ze-Feng Gao</strong>, Xiao Zhang, Wayne Xin Zhao<sup>#</sup>, Ji-Rong Wen"
venue: "Proceedings of the 2024 Joint International Conference on Computational Linguistics, Language Resources and Evaluation (LREC-COLING 2024)"
paper: /pdfs/2024/stablerank.pdf
code: https://github.com/RUCAIBox/CaliSr
link: https://aclanthology.org/2024.lrec-main.534/

---
In this paper, we proposed both theoretical analyses
and experimental verification for the proposed calibration strategy. Considering efficiency, we further propose
time-aware and structure-aware strategies to determine the most crucial time to commence the fine-tuning procedure
and selectively apply parameter matrices for lightweight fine-tuning, respectively.
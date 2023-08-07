---
layout: post
title:  "Small Pre-trained Language Models Can be Fine-tuned as Large Models via Over-Parameterization"
date:   2023-05-08 18:04:44 +00:00
image: images/overparameter.png
categories: 2023_research
author: Ze-Feng Gao
authors: "<strong>Ze-Feng Gao</strong>, Kun Zhou,Peiyu Liu, Wayne Xin Zhao, Ji-Rong Wen"
venue: "Annual Meeting of the Association for Computational Linguistics (ACL2023), Oral (Nominated for Best Paper Reward)"
paper: /pdfs/acl2023.pdf
code: https://github.com/zfgao66/OPF
link: https://aclanthology.org/2023.acl-long.212/

---
In this paper, we focus on just scaling up the parameters of PLMs during fine-tuning, to benefit from the over-parameterization but not increasing the inference latency. Extensive experiments have demonstrated that our approach can significantly boost the fine-tuning performance of small PLMs and even help small PLMs outperform 3x parameterized larger ones.
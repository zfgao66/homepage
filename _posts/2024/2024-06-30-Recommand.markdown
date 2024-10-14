---
layout: post
title:  "基于矩阵乘积算符表示的序列化推荐模型"
date:   2024-06-30 18:04:44 +00:00
image: images/2024/recommand.png
categories: 2024_research
author: Ze-Feng Gao
authors: "刘沛羽，姚博文, <strong>高泽峰</strong>, 赵鑫"
venue: "山东大学学报(理学版)"
paper: /pdfs/2024/recommand.pdf
link: https://kns.cnki.net/kcms2/article/abstract?v=Dm4VI7mKrXNyjGjCsqaY-3Ds66BzrjaBpoObKbvUi_Bj7Mvqanj10tm8Nj9Igm09ZRpaRiiGya5ov6Rl10UtsBBgSppwsoD7Ak0U_K1zPpi3xCkIeRs39l8aM8y8VykkeEB-qgcX9Nm_fRYDxSXKbEhqsU4q27Q1YWH2pW9N13wDCSt1lh4f6pUh7mvX3f9C3Uzd_D1mZid_tvJayfKfBg==&uniplatform=NZKPT


---
推荐系统中的序列化推荐任务面临着高度复杂和多样性大的挑战，基于序列化数据的商品表示学习中广泛采用预训练和微调的方法，现有方法通常忽略了在新领域中模型微调可能会遇到的欠拟合和过拟合问题。为了应对这一问题，构建一种基于矩阵乘积算符(matrix product operator, MPO)表示的神经网络结构，并实现2种灵活的微调策略。
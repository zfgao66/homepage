---
layout: post
title:  "Compressing deep neural networks by matrix product operators"
date:   2020-06-19 18:04:44 +00:00
image: images/compressing_pic.png
categories: research
author: Ze-Feng Gao
authors: "<strong>Ze-Feng Gao</strong>,Song Cheng, Rong-Qiang He, Zhi-Yuan Xie, Hui-Hai Zhao, Zhong-Yi Lu, Tao Xiang"
venue: "Physical Review Research 2 (2), 023300"
URL: https://journals.aps.org/prresearch/abstract/10.1103/PhysRevResearch.2.023300
code: https://github.com/zfgao66/deeplearning-mpo
---
A deep neural network is a parametrization of a multilayer mapping of signals in terms of many alternatively arranged linear and nonlinear transformations. The linear transformations, which are generally used in the fully connected as well as convolutional layers, contain most of the variational parameters that are trained and stored. Compressing a deep neural network to reduce its number of variational parameters but not its prediction power is an important but challenging problem toward the establishment of an optimized scheme in training efficiently these parameters and in lowering the risk of overfitting. Here we show that this problem can be effectively solved by representing linear transformations with matrix product operators (MPOs), which is a tensor network originally proposed in physics to characterize the short-range entanglement in one-dimensional quantum states. We have tested this approach in five typical neural networks, including FC2, LeNet-5, VGG, ResNet, and DenseNet on two widely used data sets, namely, MNIST and CIFAR-10, and found that this MPO representation indeed sets up a faithful and efficient mapping between input and output signals, which can keep or even improve the prediction accuracy with a dramatically reduced number of parameters. Our method greatly simplifies the representations in deep learnin, and opens a possible route toward establishing a framework of modern neural networks which might be simpler and cheaper, but more efficient.
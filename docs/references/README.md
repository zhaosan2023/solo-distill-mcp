# 外部参考资料与理论基准 (External References)

本目录归档项目所依赖或借鉴的外部核心学术文献、技术标准与规范。

---

## 1. FastCode 论文

- **文件路径**: [FastCode-arXiv-2603.01012v2.pdf](FastCode-arXiv-2603.01012v2.pdf)
- **论文题目**: *FastCode: Fast and Cost-Efficient Code Understanding and Reasoning*
- **作者机构**: Zhonghang Li, Zongwei Li, Yuxuan Chen, Han Shi, Jiawei Li, Jierun Chen, Haoli Bai, Chao Huang (The University of Hong Kong, Huawei Noah's Ark Lab)
- **arXiv 链接**: [arXiv:2603.01012v2 [cs.SE]](https://arxiv.org/abs/2603.01012)
- **代码仓库**: [https://github.com/HKUDS/FastCode](https://github.com/HKUDS/FastCode)
- **归档日期**: 2026-10-29

### 核心贡献与对本项目的指导价值
1. **探索与消费解耦 (Decoupling Exploration from Consumption)**:
   - 彻底颠覆了“将全库代码暴力打包喂给大模型 (Naive Context Stuffing)”的传统低效做法。
   - 提出“无需读全文即具备全库可见性 (Visibility without Reading)”。
2. **轻量骨架双重表示 (Dual Representation)**:
   - 全库维护轻量元数据（函数签名、行范围、Docstrings）及三层拓扑图 $E = \{G_{dep}, G_{inh}, G_{call}\}$（导入依赖图、类继承图、调用流向图）。
   - 仅在最终确定的关键代码单元上加载函数体。
3. **成本感知动态预算控制 (Cost-Aware Context Management)**:
   - 动态预算与贪心优先级选择，确保进入 LLM Prompt 的有效代码切片严格受控在最小充分集（$\le 100\text{ KB}$）。
   - 解决本项目扫描 81 个文件生成 1.55 MB 提示词导致网关 384 KB 溢出的工程死锁问题。

### 关联分析文档
- 详细研读与算法映射报告参见：[_bmad-output/analysis/ANALYSIS-20261029-fastcode-scouting-architecture.md](../../_bmad-output/analysis/ANALYSIS-20261029-fastcode-scouting-architecture.md)

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

### 方法借鉴与证据限制

- 本工程借鉴探索与消费分离、轻量导航和问题相关正文选择；方法映射见[历史架构分析](../../_bmad-output/analysis/ANALYSIS-20261029-fastcode-scouting-architecture.md)，本轮未重新研读全文或核验出版元数据。
- [Schema 3.0 决策的理论边界](../../_bmad-output/adrs/ADR-20261030-harel-fastcode-schema-v3.md:222)明确双重表示及层次化抽象属于设计启发，不证明完整论文算法、运行时目标解析或状态图语义已经实现。
- 本工程的正文、完整请求、解码帧和累计传输限额属于项目契约，不是论文给出的统一 100 KB 保证；不同计量层不得混称。
- 历史容量失败不等于已证明的并发死锁。新表示、预算或期限是否解决具体失败，须关联对应版本的真实运行证据。
- 性能目标属于工程观测指标；两篇资料不能推出预检必须在固定秒数内完成。

## 2. Harel 层次化抽象资料

- [本地资料](statement_harl87.md)保留原始文件名，供相关决策按需引用。
- 聚类与视距缩放用于设计启发；函数聚合不是完整状态图语义实现，也不是耗时或并发安全保证。
- 本轮仅整理定位和措辞，不重新验证全文、作者信息、外部网址或实验结论。

### 关联分析文档
- 详细研读与算法映射报告参见：[_bmad-output/analysis/ANALYSIS-20261029-fastcode-scouting-architecture.md](../../_bmad-output/analysis/ANALYSIS-20261029-fastcode-scouting-architecture.md)

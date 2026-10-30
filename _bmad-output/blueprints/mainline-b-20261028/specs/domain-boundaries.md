# 领域边界候选（仅静态导航）

这些分区只按文件划分，不认定业务职责、架构所有权或运行时服务边界。

- [api.py](module-1ed02c3495a2cbdf-001.md)；导入 26；定义 36；职责与副作用语义待独立审核。
- [fastcode/__init__.py](module-83b551f56f191be7-001.md)；导入 12；定义 0；职责与副作用语义待独立审核。
- [fastcode/agent_tools.py](module-8ae8da3060650e5a-001.md)；导入 10；定义 10；职责与副作用语义待独立审核。
- [fastcode/answer_generator.py](module-b90ba517ca5fa32f-001.md)；导入 18；定义 17；职责与副作用语义待独立审核。
- [fastcode/cache.py](module-eda6d2c8efe4ce71-001.md)；导入 13；定义 21；职责与副作用语义待独立审核。

调用点、条件、未解析目标完整保存在[认证结构](../analysis/knowledge-package.json)。
调用方及写路径的跨文件业务对账尚未完成；范围外调用方不得登记为已覆盖。

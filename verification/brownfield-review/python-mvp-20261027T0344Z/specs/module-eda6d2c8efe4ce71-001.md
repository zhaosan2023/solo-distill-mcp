# Python 模块静态契约草案

状态：READY_FOR_REVIEW；尚未经人工接受。

来源：[fastcode/cache.py](../../../../../FastCode/fastcode/cache.py:1)
文件摘要：5ce18399d23a4ef19d71aedcec731ac0c558e673dd3421c5f7d57945b764b8c9
分页：1/1；完整名称、条件和来源记录见[溯源数据](../analysis/provenance.json)。

## 不变量与未知

- 本页只列语法声明；不证明运行时行为、业务职责或调用目标。
- P0 全部导入与 P1 全部定义（含方法、嵌套定义）保留，不按公开性删减。
- 参数、返回类型、导出闭包、动态绑定及业务不变量：本版本未推断。
- 验证命令未知；未执行来源项目测试，不生成猜测命令。

## 静态依赖与接口

- P0 [导入 os / *module*](../../../../../FastCode/fastcode/cache.py:5)；行 5—5；来源 a4994115747359ac36e61730。
- P0 [导入 pickle / *module*](../../../../../FastCode/fastcode/cache.py:6)；行 6—6；来源 fa57dc82612c1986bb3a0ff1。
- P0 [导入 hashlib / *module*](../../../../../FastCode/fastcode/cache.py:7)；行 7—7；来源 72cebf3ed90b7454daa085a5。
- P0 [导入 logging / *module*](../../../../../FastCode/fastcode/cache.py:8)；行 8—8；来源 81fb6261328a2aa77e6c9b26。
- P0 [导入 json / *module*](../../../../../FastCode/fastcode/cache.py:9)；行 9—9；来源 68670fde472f275837e4629c。
- P0 [导入 time / *module*](../../../../../FastCode/fastcode/cache.py:10)；行 10—10；来源 a8f8e255d0bd4d9cbac6929b。
- P0 [导入 typing / Any](../../../../../FastCode/fastcode/cache.py:11)；行 11—11；来源 67a53c9ac6003eec35816688。
- P0 [导入 typing / Optional](../../../../../FastCode/fastcode/cache.py:11)；行 11—11；来源 da3a78856e4444857ed1f3f2。
- P0 [导入 typing / List](../../../../../FastCode/fastcode/cache.py:11)；行 11—11；来源 f7c37c37881d66d59b9a4c46。
- P0 [导入 typing / Dict](../../../../../FastCode/fastcode/cache.py:11)；行 11—11；来源 f36eb3e38fe9fb8a82cebe2f。
- P0 [导入 pathlib / Path](../../../../../FastCode/fastcode/cache.py:12)；行 12—12；来源 08ed3a614305e8971433bacf。
- P0 [导入 diskcache / Cache](../../../../../FastCode/fastcode/cache.py:13)；行 13—13；来源 1da63e0e44ecc17fd1574cb3。
- P0 [导入 redis / *module*](../../../../../FastCode/fastcode/cache.py:54)；行 54—54；来源 816085090f648dd004d8ffed。
- P1 [class CacheManager](../../../../../FastCode/fastcode/cache.py:16)；行 16—463；来源 cb0bbe7231bae85186141acf。
- P1 [function CacheManager.__init__](../../../../../FastCode/fastcode/cache.py:19)；行 19—39；来源 348e27d5940b1175abffa019。
- P1 [function CacheManager._initialize_cache](../../../../../FastCode/fastcode/cache.py:41)；行 41—69；来源 351e92b5ba924278828eeba7。
- P1 [function CacheManager._generate_key](../../../../../FastCode/fastcode/cache.py:71)；行 71—76；来源 0ad3c5557eb8dcda0ce29e65。
- P1 [function CacheManager.get](../../../../../FastCode/fastcode/cache.py:78)；行 78—99；来源 6afa2bf60ba33216ff338564。
- P1 [function CacheManager.set](../../../../../FastCode/fastcode/cache.py:101)；行 101—120；来源 f8aa94dcd81107fa0a7d7d03。
- P1 [function CacheManager.delete](../../../../../FastCode/fastcode/cache.py:122)；行 122—134；来源 d3639342a99007ed7ba3a952。
- P1 [function CacheManager.clear](../../../../../FastCode/fastcode/cache.py:136)；行 136—152；来源 708658423e88767ef9418414。
- P1 [function CacheManager.get_embedding](../../../../../FastCode/fastcode/cache.py:154)；行 154—159；来源 e2dd549d3bcd85c54e87aa04。
- P1 [function CacheManager.set_embedding](../../../../../FastCode/fastcode/cache.py:161)；行 161—166；来源 39bf4338b548c6a624f82260。
- P1 [function CacheManager.get_query_result](../../../../../FastCode/fastcode/cache.py:168)；行 168—173；来源 4e87a7c7abb6b255dc9d90f8。
- P1 [function CacheManager.set_query_result](../../../../../FastCode/fastcode/cache.py:175)；行 175—180；来源 f950551e0e0ad0a574780c5d。
- P1 [function CacheManager.get_stats](../../../../../FastCode/fastcode/cache.py:182)；行 182—205；来源 d41b1a4258bd3a3590e1cebf。
- P1 [function CacheManager.save_dialogue_turn](../../../../../FastCode/fastcode/cache.py:209)；行 209—260；来源 c6d58f36ba24a5d3df73d301。
- P1 [function CacheManager.get_dialogue_turn](../../../../../FastCode/fastcode/cache.py:262)；行 262—277；来源 646cf32ad42107d4cddf3769。
- P1 [function CacheManager.get_dialogue_history](../../../../../FastCode/fastcode/cache.py:279)；行 279—320；来源 dd298246aeaf2af27ecdf2cf。
- P1 [function CacheManager.get_recent_summaries](../../../../../FastCode/fastcode/cache.py:322)；行 322—351；来源 76c1d05680ec896a4b34d54d。
- P1 [function CacheManager._update_session_index](../../../../../FastCode/fastcode/cache.py:353)；行 353—379；来源 3c0c040ce0559491fc51210e。
- P1 [function CacheManager._get_session_index](../../../../../FastCode/fastcode/cache.py:381)；行 381—384；来源 4f3f24f018c1c44bb0b1351d。
- P1 [function CacheManager.delete_session](../../../../../FastCode/fastcode/cache.py:386)；行 386—421；来源 a737d872a855e21d68c83acb。
- P1 [function CacheManager.list_sessions](../../../../../FastCode/fastcode/cache.py:423)；行 423—463；来源 9a4d80768178176c93552d3c。

## 三层阅读与剪枝

- 第一层：阅读[项目指引](../AGENTS.md)。
- 第二层：按需阅读本模块分页，不把所有模块一次性注入上下文。
- 第三层：阅读[溯源报告](../analysis/PROVENANCE.md)和完整静态记录。
- 可选调用仅在完整帧预算内交付；未交付数量和所有未完成事项保留在溯源中。

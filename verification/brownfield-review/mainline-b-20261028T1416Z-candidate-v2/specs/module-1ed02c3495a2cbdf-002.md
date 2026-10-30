# Python 模块静态契约草案

状态：READY_FOR_REVIEW；尚未经人工接受。

来源：[api.py](../../../../../FastCode/api.py:1)
文件摘要：e1e0dbda53075a723270db0f6f2709ee097b9054f5f0bb1ff4ecce10373663c0
分页：2/2；完整名称、条件和来源记录见[溯源数据](../analysis/provenance.json)。

## 不变量与未知

- 本页只列语法声明；不证明运行时行为、业务职责或调用目标。
- P0 全部导入与 P1 全部定义（含方法、嵌套定义）保留，不按公开性删减。
- 参数、返回类型、导出闭包、动态绑定及业务不变量：本版本未推断。
- 验证命令未知；未执行来源项目测试，不生成猜测命令。

## 静态依赖与接口

- P1 [async_function query_repository_stream](../../../../../FastCode/api.py:500)；行 500—571；来源 fde9093be67d9ec23a7f9e6d。
- P1 [async_function query_repository_stream.event_generator](../../../../../FastCode/api.py:515)；行 515—561；来源 bc1fd81314a17eba978478b9。
- P1 [async_function get_repository_summary](../../../../../FastCode/api.py:575)；行 575—595；来源 38f4b049bf276a7e43ba2fed。
- P1 [async_function new_session](../../../../../FastCode/api.py:599)；行 599—607；来源 b2f63425ec1df50df8b3c78f。
- P1 [async_function list_sessions](../../../../../FastCode/api.py:611)；行 611—631；来源 c9a4844dafa1147b31d42c7d。
- P1 [async_function get_session](../../../../../FastCode/api.py:635)；行 635—644；来源 fb948db17cc61d308056e2bb。
- P1 [async_function delete_session](../../../../../FastCode/api.py:648)；行 648—669；来源 902323b0ab3951e708bb3198。
- P1 [async_function delete_repositories](../../../../../FastCode/api.py:673)；行 673—700；来源 849250f385f49e713596f70e。
- P1 [async_function clear_cache](../../../../../FastCode/api.py:704)；行 704—713；来源 35f1502e4c3c6be44e804910。
- P1 [async_function get_cache_stats](../../../../../FastCode/api.py:717)；行 717—722；来源 3a2f9c604fb35cd57b80f8da。
- P1 [async_function refresh_index_cache](../../../../../FastCode/api.py:726)；行 726—741；来源 7d397889d7412d45bee85507。
- P1 [async_function unload_repository](../../../../../FastCode/api.py:745)；行 745—753；来源 16fff3d2ee9dedbe68dfe6e6。
- P1 [function _safe_jsonable](../../../../../FastCode/api.py:756)；行 756—780；来源 390f1d984aba183c71aaeb82。
- P1 [function start_api](../../../../../FastCode/api.py:783)；行 783—787；来源 fc013ac57a9743e15f70e35d。

## 三层阅读与剪枝

- 第一层：阅读[项目指引](../AGENTS.md)。
- 第二层：按需阅读本模块分页，不把所有模块一次性注入上下文。
- 第三层：阅读[溯源报告](../analysis/PROVENANCE.md)和完整静态记录。
- 本轮完整调用点与关系在认证分片中交付；未知目标和未完成事项仍保留在溯源与审核义务中。

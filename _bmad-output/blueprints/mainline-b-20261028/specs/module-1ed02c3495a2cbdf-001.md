# Python 模块静态契约草案

状态：READY_FOR_REVIEW；尚未经人工接受。

来源：[api.py](../../../../../FastCode/api.py:1)
文件摘要：e1e0dbda53075a723270db0f6f2709ee097b9054f5f0bb1ff4ecce10373663c0
分页：1/2；完整名称、条件和来源记录见[溯源数据](../analysis/provenance.json)。

## 不变量与未知

- 本页只列语法声明；不证明运行时行为、业务职责或调用目标。
- P0 全部导入与 P1 全部定义（含方法、嵌套定义）保留，不按公开性删减。
- 参数、返回类型、导出闭包、动态绑定及业务不变量：本版本未推断。
- 验证命令未知；未执行来源项目测试，不生成猜测命令。

## 静态依赖与接口

- P0 [导入 os / *module*](../../../../../FastCode/api.py:6)；行 6—6；来源 dae50e1d8a5abad91b08bb91。
- P0 [导入 platform / *module*](../../../../../FastCode/api.py:7)；行 7—7；来源 011cfdc02fe0fbefe5e63752。
- P0 [导入 fastapi / FastAPI](../../../../../FastCode/api.py:15)；行 15—15；来源 79633c2bca6fa45709090a88。
- P0 [导入 fastapi / HTTPException](../../../../../FastCode/api.py:15)；行 15—15；来源 4d22d297fcf4aa9709f77d23。
- P0 [导入 fastapi / File](../../../../../FastCode/api.py:15)；行 15—15；来源 35f17927d94c5fece5320e34。
- P0 [导入 fastapi / UploadFile](../../../../../FastCode/api.py:15)；行 15—15；来源 9f4e9aed7fb6b9e7c9b166af。
- P0 [导入 fastapi.responses / StreamingResponse](../../../../../FastCode/api.py:16)；行 16—16；来源 b336a3b186209c161aeb84b0。
- P0 [导入 fastapi.middleware.cors / CORSMiddleware](../../../../../FastCode/api.py:17)；行 17—17；来源 df4f6c34a577e821e8fb3851。
- P0 [导入 contextlib / asynccontextmanager](../../../../../FastCode/api.py:18)；行 18—18；来源 b10c3fdd5c8e29c918e7a6fd。
- P0 [导入 pydantic / BaseModel](../../../../../FastCode/api.py:19)；行 19—19；来源 a5aa723fb689dabda348b869。
- P0 [导入 pydantic / Field](../../../../../FastCode/api.py:19)；行 19—19；来源 b287e6f8d63333848537c5ca。
- P0 [导入 typing / Optional](../../../../../FastCode/api.py:20)；行 20—20；来源 1c282ce33804caf6d823bc9a。
- P0 [导入 typing / Dict](../../../../../FastCode/api.py:20)；行 20—20；来源 31156d19fd283bbd05a35b47。
- P0 [导入 typing / Any](../../../../../FastCode/api.py:20)；行 20—20；来源 604fef111e2f3debebcef65a。
- P0 [导入 typing / List](../../../../../FastCode/api.py:20)；行 20—20；来源 971edf86aae12e489e2aca69。
- P0 [导入 uvicorn / *module*](../../../../../FastCode/api.py:21)；行 21—21；来源 270e844f615f1745848e6f93。
- P0 [导入 logging / *module*](../../../../../FastCode/api.py:22)；行 22—22；来源 9bb41437c183e42329f40ea1。
- P0 [导入 pathlib / Path](../../../../../FastCode/api.py:23)；行 23—23；来源 47f33c07c03bc29936b6de6e。
- P0 [导入 tempfile / *module*](../../../../../FastCode/api.py:24)；行 24—24；来源 58780b327b4b74cd38362576。
- P0 [导入 zipfile / *module*](../../../../../FastCode/api.py:25)；行 25—25；来源 765e30b801f9c47e5a97bf08。
- P0 [导入 shutil / *module*](../../../../../FastCode/api.py:26)；行 26—26；来源 3e22dd46582540f94a497496。
- P0 [导入 uuid / *module*](../../../../../FastCode/api.py:27)；行 27—27；来源 98746d218258cac28843ccc2。
- P0 [导入 json / *module*](../../../../../FastCode/api.py:28)；行 28—28；来源 170b1ee02399989c629c4f2f。
- P0 [导入 asyncio / *module*](../../../../../FastCode/api.py:29)；行 29—29；来源 28a2f30cb5079f8430aa6de2。
- P0 [导入 fastcode / FastCode](../../../../../FastCode/api.py:31)；行 31—31；来源 153df3b0550be481a4fbf365。
- P0 [导入 argparse / *module*](../../../../../FastCode/api.py:791)；行 791—791；来源 a0e390e13a6725fe892fdbd3。
- P1 [class LoadRepositoryRequest](../../../../../FastCode/api.py:35)；行 35—40；来源 1502b1cc3087dfc0705e9d8c。
- P1 [class QueryRequest](../../../../../FastCode/api.py:43)；行 43—48；来源 e873b1e22d4b03a4064f13dd。
- P1 [class QueryResponse](../../../../../FastCode/api.py:51)；行 51—59；来源 8737e76b5f74c9bc02074a81。
- P1 [class LoadRepositoriesRequest](../../../../../FastCode/api.py:62)；行 62—63；来源 7a6469c88efe0f64227983b6。
- P1 [class IndexMultipleRequest](../../../../../FastCode/api.py:66)；行 66—67；来源 92b73eb8c27f7f6ce30defc4。
- P1 [class NewSessionResponse](../../../../../FastCode/api.py:70)；行 70—71；来源 dcf95b23ec340bffa327658b。
- P1 [class DeleteReposRequest](../../../../../FastCode/api.py:74)；行 74—76；来源 1a5587d4f027c616125abca2。
- P1 [class StatusResponse](../../../../../FastCode/api.py:79)；行 79—85；来源 82393b921a87da8ad5081e4a。
- P1 [async_function lifespan](../../../../../FastCode/api.py:91)；行 91—97；来源 b7cb8cd4de4e7df94a35ee9a。
- P1 [function _ensure_fastcode_initialized](../../../../../FastCode/api.py:134)；行 134—140；来源 ee1d757549055361555462dc。
- P1 [async_function root](../../../../../FastCode/api.py:145)；行 145—152；来源 8662a2b2f6e06e27f8515448。
- P1 [async_function health_check](../../../../../FastCode/api.py:156)；行 156—171；来源 daeee5d3f4bb773d0df92ded。
- P1 [async_function get_status](../../../../../FastCode/api.py:175)；行 175—194；来源 dbf2b31f1d41078c280ad155。
- P1 [async_function list_repositories](../../../../../FastCode/api.py:198)；行 198—218；来源 f3b3d46571739b2deb421676。
- P1 [async_function load_repository](../../../../../FastCode/api.py:222)；行 222—238；来源 7f7a7f3e817950ccf4d9debe。
- P1 [async_function index_repository](../../../../../FastCode/api.py:242)；行 242—263；来源 4fec8290b5c33a5fcf08c04d。
- P1 [async_function load_and_index](../../../../../FastCode/api.py:267)；行 267—288；来源 350ed98e217e40f110c7e7b3。
- P1 [async_function load_repositories](../../../../../FastCode/api.py:292)；行 292—315；来源 10f57577ed4ffa34d1bac4b2。
- P1 [async_function index_multiple](../../../../../FastCode/api.py:319)；行 319—339；来源 041c99a4db72e332602c8d45。
- P1 [async_function upload_repository_zip](../../../../../FastCode/api.py:343)；行 343—418；来源 86e9425697a7990ec3a51a55。
- P1 [async_function upload_and_index](../../../../../FastCode/api.py:422)；行 422—445；来源 86c654f4521aee91893f97a9。
- P1 [async_function query_repository](../../../../../FastCode/api.py:449)；行 449—496；来源 ce0369a0db3ebb59db874247。

## 三层阅读与剪枝

- 第一层：阅读[项目指引](../AGENTS.md)。
- 第二层：按需阅读本模块分页，不把所有模块一次性注入上下文。
- 第三层：阅读[溯源报告](../analysis/PROVENANCE.md)和完整静态记录。
- 本轮完整调用点与关系在认证分片中交付；未知目标和未完成事项仍保留在溯源与审核义务中。

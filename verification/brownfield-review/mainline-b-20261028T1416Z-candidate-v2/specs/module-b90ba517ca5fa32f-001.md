# Python 模块静态契约草案

状态：READY_FOR_REVIEW；尚未经人工接受。

来源：[fastcode/answer_generator.py](../../../../../FastCode/fastcode/answer_generator.py:1)
文件摘要：b48036851a782e3a59eff3e9205af7cd987bfb40aa7e37ced319553eb2bef4e7
分页：1/1；完整名称、条件和来源记录见[溯源数据](../analysis/provenance.json)。

## 不变量与未知

- 本页只列语法声明；不证明运行时行为、业务职责或调用目标。
- P0 全部导入与 P1 全部定义（含方法、嵌套定义）保留，不按公开性删减。
- 参数、返回类型、导出闭包、动态绑定及业务不变量：本版本未推断。
- 验证命令未知；未执行来源项目测试，不生成猜测命令。

## 静态依赖与接口

- P0 [导入 logging / *module*](../../../../../FastCode/fastcode/answer_generator.py:5)；行 5—5；来源 5f15c753c9398cf871f94cc7。
- P0 [导入 re / *module*](../../../../../FastCode/fastcode/answer_generator.py:6)；行 6—6；来源 80a7c36e27fbd25f6ad628c0。
- P0 [导入 json / *module*](../../../../../FastCode/fastcode/answer_generator.py:7)；行 7—7；来源 a7fca85eed9a9de1063d5d31。
- P0 [导入 typing / List](../../../../../FastCode/fastcode/answer_generator.py:8)；行 8—8；来源 d44af47a640fc5233ac6b26f。
- P0 [导入 typing / Dict](../../../../../FastCode/fastcode/answer_generator.py:8)；行 8—8；来源 6d77447cb38aa8f39ea1c57d。
- P0 [导入 typing / Any](../../../../../FastCode/fastcode/answer_generator.py:8)；行 8—8；来源 457c87f6cee26d2c8005380e。
- P0 [导入 typing / Optional](../../../../../FastCode/fastcode/answer_generator.py:8)；行 8—8；来源 a476baf22e8893d62c6afb44。
- P0 [导入 typing / Tuple](../../../../../FastCode/fastcode/answer_generator.py:8)；行 8—8；来源 b5661212a4bbac59f547b197。
- P0 [导入 typing / Callable](../../../../../FastCode/fastcode/answer_generator.py:8)；行 8—8；来源 c30cb2831621d9a2cb461a3a。
- P0 [导入 os / *module*](../../../../../FastCode/fastcode/answer_generator.py:9)；行 9—9；来源 6b1a3d9f70ad60b813184578。
- P0 [导入 openai / OpenAI](../../../../../FastCode/fastcode/answer_generator.py:10)；行 10—10；来源 c912325bcfcc1f5e9b971e69。
- P0 [导入 anthropic / Anthropic](../../../../../FastCode/fastcode/answer_generator.py:11)；行 11—11；来源 039158167e015c392bfdd867。
- P0 [导入 dotenv / load_dotenv](../../../../../FastCode/fastcode/answer_generator.py:12)；行 12—12；来源 a6e8552a255471a000d33a0a。
- P0 [导入 llm_utils / openai_chat_completion](../../../../../FastCode/fastcode/answer_generator.py:14)；行 14—14；来源 09d1eb6aba5178afcb392c72。
- P0 [导入 utils / count_tokens](../../../../../FastCode/fastcode/answer_generator.py:15)；行 15—15；来源 aa0485f712831ac22199a41b。
- P0 [导入 utils / truncate_to_tokens](../../../../../FastCode/fastcode/answer_generator.py:15)；行 15—15；来源 902fcbdd30d165400431fbae。
- P0 [导入 traceback / *module*](../../../../../FastCode/fastcode/answer_generator.py:186)；行 186—186；来源 3e314cd8cc4a390719d79acb。
- P0 [导入 traceback / *module*](../../../../../FastCode/fastcode/answer_generator.py:315)；行 315—315；来源 5ad151255f94a589d53e0915。
- P1 [class AnswerGenerator](../../../../../FastCode/fastcode/answer_generator.py:18)；行 18—888；来源 9c5df3c1367989e4e33fc958。
- P1 [function AnswerGenerator.__init__](../../../../../FastCode/fastcode/answer_generator.py:21)；行 21—51；来源 02b636943e2ac13ddf3d2d54。
- P1 [function AnswerGenerator._initialize_client](../../../../../FastCode/fastcode/answer_generator.py:53)；行 53—69；来源 da017ee276f3f3be458800ce。
- P1 [function AnswerGenerator.generate](../../../../../FastCode/fastcode/answer_generator.py:71)；行 71—194；来源 7f8545c47f9fc125a34b91bf。
- P1 [function AnswerGenerator.generate_stream](../../../../../FastCode/fastcode/answer_generator.py:196)；行 196—319；来源 04833765ecdae7e9a62e4390。
- P1 [function AnswerGenerator._stream_with_summary_filter](../../../../../FastCode/fastcode/answer_generator.py:321)；行 321—445；来源 76fa9a9cea14eec1a56db90f。
- P1 [function AnswerGenerator._prepare_context](../../../../../FastCode/fastcode/answer_generator.py:447)；行 447—515；来源 0557722e7d949fad7bc10bdd。
- P1 [function AnswerGenerator._build_prompt](../../../../../FastCode/fastcode/answer_generator.py:517)；行 517—629；来源 d2e2713177e6901ed6f9d388。
- P1 [function AnswerGenerator._truncate_context](../../../../../FastCode/fastcode/answer_generator.py:631)；行 631—633；来源 2e190552a9ab124acd3f8997。
- P1 [function AnswerGenerator._generate_openai](../../../../../FastCode/fastcode/answer_generator.py:635)；行 635—663；来源 064463d9c2a04304341b3642。
- P1 [function AnswerGenerator._generate_openai_stream](../../../../../FastCode/fastcode/answer_generator.py:665)；行 665—689；来源 75aa1c12179cc35f4f6a3367。
- P1 [function AnswerGenerator._generate_anthropic](../../../../../FastCode/fastcode/answer_generator.py:691)；行 691—715；来源 1c7f28873d137029f34a7476。
- P1 [function AnswerGenerator._generate_anthropic_stream](../../../../../FastCode/fastcode/answer_generator.py:717)；行 717—735；来源 9d055111e78f90ea0bb6fc5c。
- P1 [function AnswerGenerator._parse_response_with_summary](../../../../../FastCode/fastcode/answer_generator.py:737)；行 737—768；来源 ccfeaaba2031b8910b9b0b28。
- P1 [function AnswerGenerator._generate_fallback_summary](../../../../../FastCode/fastcode/answer_generator.py:770)；行 770—843；来源 e7caa1051c63d3c52b12ef22。
- P1 [function AnswerGenerator._extract_sources](../../../../../FastCode/fastcode/answer_generator.py:845)；行 845—861；来源 add509d186fd38676f480657。
- P1 [function AnswerGenerator.format_answer_with_sources](../../../../../FastCode/fastcode/answer_generator.py:863)；行 863—888；来源 ac6f7f373536334c6541d371。

## 三层阅读与剪枝

- 第一层：阅读[项目指引](../AGENTS.md)。
- 第二层：按需阅读本模块分页，不把所有模块一次性注入上下文。
- 第三层：阅读[溯源报告](../analysis/PROVENANCE.md)和完整静态记录。
- 本轮完整调用点与关系在认证分片中交付；未知目标和未完成事项仍保留在溯源与审核义务中。

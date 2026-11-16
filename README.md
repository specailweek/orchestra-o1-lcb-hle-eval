# Orchestra-o1 测评代码

分为两个独立文件夹，按需取用：

| 文件夹 | 内容 | 运行说明 |
|---|---|---|
| [livecodebench/](livecodebench/) | LiveCodeBench 测评、代码工具、隔离判题器和 O1 框架 | [查看说明](livecodebench/README.md) |
| [hle/](hle/) | HLE 分层抽样、解题工具、独立评分脚本和 O1 框架 | [查看说明](hle/README.md) |

每个文件夹均自带 `framework/`、`shared/`、`.env.example`、`requirements.txt`、`Dockerfile.tools` 和 `run.sh`，可单独复制到其他服务器。进入对应文件夹后，按其 README 准备环境与数据。

**不包含数据集、题目、答案、私有测试、模型权重、API 密钥或实验输出。** 数据和权重需自行合法获取；评分默认不调用付费 API。

## 来源与验证范围

本仓库整理自历史测评源码副本，修改了部署路径、端点和凭证配置，未在新服务器上重跑测评。LiveCodeBench 原远程测评目录已清理，不能逐字节核验最终运行版本。上游源码权利归原作者。

原历史成绩与本次目录整理无关。文件哈希见 `SOURCE_MANIFEST.json`。

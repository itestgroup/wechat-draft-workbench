# 第三方依赖核查（2026-11-07）

实际Linux Python3.10运行包构建、pip check及本轮OSV查询通过，见[依赖核查](DEPENDENCY_AUDIT.md)。代码MIT不替代第三方许可；当前候选不直接分发第三方二进制、字体或图片。

- 运行SDK：cos-python-sdk-v5 1.9.44，MIT。
- 部署SDK：tencentcloud-sdk-python-scf 3.1.100，Apache-2.0；不加入函数运行包。

Python3 COS wheel实际依赖为requests>=2.8、xmltodict、six、crcmod、pycryptodome。此前PyPI汇总元数据中的requests/certifi旧版上限已被目标wheel检查和真实构建否定，不能据此限制构建。

部分传递依赖声明：certifi为MPL-2.0、requests为Apache-2.0、idna为BSD-3-Clause、pycryptodome涉及多个许可。发布运行包需保留各许可文件，不能统一替换为项目MIT。部署SDK及common wheel未找到独立许可文件，需核对上游许可文件。

部署SDK的补充许可已从其元数据指向的腾讯云官方仓库固定提交83bb3ce37bd53e1639ec6a294f056ea7a3fd4bb4归档：third_party/licenses/tencentcloud-sdk-python/LICENSE。来源与哈希见third_party/deploy-license.json。它是上游Apache-2.0许可补充，不声称原wheel自带该文件，也不将部署SDK加入运行包。自动构建会将项目及归档许可放入最终ZIP并验证原文字节。

运行依赖的10个目标发行文件已按SHA256锁定，许可原文归档在third_party/licenses/，来源及哈希记录于third_party/runtime-artifacts.json；已在脱敏候选的Linux构建中以 --require-hashes 安装并验证通过。部署SDK许可和从零部署复现仍待补齐。项目MIT不改变归档许可的适用条件。

0条漏洞查询记录不保证不存在漏洞。Windows缺少crcmod wheel不代表Linux构建失败，后者已构建和原生导入成功。锁定发行文件不锁定源码构建工具链及基础镜像，不声称构建完全可复现。

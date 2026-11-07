# Linux Python3.10 依赖核查

日期：2026-11-07。结论：实际云端构建和本轮运行依赖漏洞查询通过。没有部署或修改生产配置，这不是对线上实际版本或未来版本的安全保证。

## 脱敏候选与锁定依赖的复核

后续运行 https://github.com/itestgroup/LabVIEW-Jobs/actions/runs/42350397723 全部success。分支codex/verify-open-source-candidate，提交d5b271984bbdb65c6025393f60c03eade23263aa。远端71个文件（70项候选文件及来源清单）逐一Git blob哈希与本地候选一致；该分支的文件树只含脱敏候选，不含原生产工作流，但仍属于原私有仓库，首次公开须另建干净历史，不能直接公开此分支历史。

真实Linux Python3.10使用 --require-hashes 安装scripts/requirements-runtime.lock成功，10个运行依赖已锁定版本及目标发行文件SHA256。许可原文归档在third_party/licenses/，来源及文件哈希见third_party/runtime-artifacts.json。最终ZIP离线检查、Python92项、前端7项、pip check及OSV查询通过，0条未撤销漏洞记录。未部署、未引用生产Secrets、未调用真实微信接口。

本阶段已完成下文原列为待办的运行依赖锁定、运行依赖许可归档和脱敏候选CI；部署SDK许可及从零云账号部署仍未完成。

## 可核实证据

独立分支 codex/cloud-dependency-check，远端提交 cbb77f260ac253bf092b0c702d2f22993d2c65e2。
运行：https://github.com/itestgroup/LabVIEW-Jobs/actions/runs/42349466789 ，全部步骤 success。

- manylinux2014_x86_64 / CPython3.10实际安装成功，Crypto、crcmod、qcloud_cos原生导入成功。
- 最终ZIP离线检查通过：COS SDK来自ZIP、禁止覆盖请求头映射、原生加密、RS256验签及业务导入。
- 构建依赖下Python92项、前端7项测试通过；pip check无依赖冲突。
- 向OSV逐一查询实际10个运行包，0条未撤销记录；只发送公开包名及版本，没有源码或凭据。
- 工件isolated-dependency-review保留3天，包含linux-runtime.json和osv-findings.json。

## 实际运行包快照

cos-python-sdk-v5==1.9.44；requests==2.34.2；certifi==2026.10.23；urllib3==2.8.0；charset-normalizer==3.5.2；xmltodict==1.0.4；six==1.17.0；crcmod==1.7；idna==3.20；pycryptodome==4.3.0。

这里只记录已测试快照，不是带hash的可复现锁文件。部署SDK没有加入运行包，其许可与依赖需单独核查。

## 上一轮判断的纠正

上一轮依据PyPI版本汇总JSON的requires_dist，误认为certifi<=2021.10.8、requests<=2.27.1适用于Python3实际构建，并据此手动下载旧版组合。该组合确有OSV匹配，但不证明SDK强制使用旧版。

直接检查cos_python_sdk_v5-1.9.44-py3-none-any.whl的METADATA，实际依赖只有requests>=2.8、xmltodict、six、crcmod、pycryptodome，没有上述两个上限。实际Linux安装采用新版依赖且pip check通过，无需为此修改SDK或覆盖依赖。

旧组合报告留在忽略的.build/dependency-review.json，仅作调查记录，不能代表生产安装结果。多发行文件的项目需核实目标wheel元数据和实际构建，不能只依赖版本汇总元数据。

## 隔离与后续

核查工作流无部署步骤、不引用生产Secrets、不调用真实微信接口，未合并main。主工作区保持main，未提交文件保留。两次本机推送失败后由GitHub API创建远端分支和文件，本地同名分支提交不同，不应强推覆盖。

运行依赖hash锁定、许可归档、候选及新仓库独立CI均已完成；仍待从零部署复现及更完整构建工具链锁定。线上安装版本未盘点，本次结果不代表生产依赖已更新。

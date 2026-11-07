# 部署路线与配置

运行依赖锁定清单位于scripts/requirements-runtime.lock，目标为Linux x86_64 / Python3.10。构建时使用 `pip install --require-hashes --target .build/scf-runtime -r scripts/requirements-runtime.lock`，不要用Windows替代目标构建，也不要为消除hash错误关闭校验。锁定文件选择了目标平台发行文件，不适用于任意平台；crcmod从锁定源码构建。

更新依赖需要重建、测试、漏洞查询并同步third_party/runtime-artifacts.json与许可文件。锁定下载文件不等于锁定Docker基础镜像及源码构建工具链，当前构建仍非完全hermetic。

本指南为发布准备稿，尚未在全新云账号完成从零复现。仅支持当前已实现的腾讯云广州 SCF、私有 COS 与 Cloudflare Pages 路线。费用及公众号接口资格需部署者自行核对。

## 顺序

1. 确认自己的公众号拥有所需草稿及素材接口权限。
2. 创建独立 Python3.10 SCF 事件函数，入口 `scf_probe.main_handler`，参考初始128MiB、90秒超时。开启固定公网出口并加入微信 IP 白名单，保留原白名单项。
3. 在 SCF 后台配置微信凭据，只用 `{"action":"labview.check"}` 做只读检查。禁止打印事件、凭据、响应正文或令牌；OAuth 启用前关闭请求/响应正文收集。
4. 创建广州私有 COS，保持从未开启版本控制。当前实现依赖禁止覆盖写入；暂停版本控制不等于从未开启。替换 `deploy/cos-role-policy.json` 中占位账号、桶，授予函数执行角色，不给前端凭据。
5. 配置独立管理密钥及 CORS 来源，建立 SCF 函数 URL，绑定 `$LATEST`，启用参数兼容。当前代码只接受腾讯 SCF HTTPS 域名，不支持任意自定义 API 域名。
6. 部署 Pages：Node22+，构建 `node scripts/build_pages.mjs`，输出 `dist/web`。不要向静态构建注入任何秘密。打开网页自行填写 SCF 根地址，将实际 Pages origin 精确配置到后端。
7. 验证鉴权、预览、保存、刷新后重新读取及同文防重复；最后再开启微信同步，明确确认一篇测试稿，创建一次并读回。
8. MCP 为可选接入：创建自己的 App，使用自己的 `/mcp` 地址，核对精确回调和实际 scope，再启用 OAuth。示例 App 文件不能直接安装，禁止绑定原作者私有 App。

## 环境变量

| 变量 | 是否秘密 | 配置要求 |
| --- | --- | --- |
| WECHAT_APP_ID / WECHAT_APP_SECRET | 凭据，均不公开 | SCF 后台配置，不写入源码或前端 |
| COS_BUCKET | 否 | 自己的私有广州桶 |
| PLATFORM_API_KEY | 是 | 至少32字符随机值，仅用于维护者工作台 |
| AI_SUBMIT_KEY | 是 | 独立至少32字符，不能与管理密钥相同 |
| PLATFORM_ALLOWED_ORIGIN | 否 | 自己的 Pages HTTPS origin，无末尾斜线 |
| ENABLE_DRAFT_SYNC | 否 | 初始不设置；完成只读与存储验证后才设 true |
| MCP_PUBLIC_BASE_URL | 否 | 自己的 SCF HTTPS 根地址，必须显式填写 |
| MCP_ALLOWED_REDIRECT_URIS | 否 | 从自己连接界面获取的精确回调，不用通配符 |
| MCP_OAUTH_ENABLED | 否 | 日志、回调和凭据隔离核对完成后才设 true |

这不是 `.env` 自动加载机制，请在函数运行配置中设置。COS 运行时使用执行角色临时凭据，部署凭据应另行最小授权。

## 云端打包尚需补齐的步骤

当前候选没有生产自动部署工作流，仅有独立构建测试CI。使用scripts/requirements-runtime.lock和 --require-hashes 在兼容Linux Python3.10环境安装到 `.build/scf-runtime`，先运行scripts/fetch_chatgpt_oauth.py，再执行build_scf_probe.py及scripts/check_scf_package.py。具体构建流程见.github/workflows/check.yml，已在脱敏候选上云端执行通过。Windows原生依赖不能直接上传Linux。

OAuth在原部署网络不能直接获取官方公开客户端元数据时，使用scripts/fetch_chatgpt_oauth.py生成最长72小时的公开快照。部署者必须安排刷新部署并观察失败，不能把一次打包当作长期可用。独立构建CI已补齐；自动部署、权限模板完善及逐步截图仍待补充。

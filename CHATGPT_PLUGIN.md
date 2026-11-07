# 部署 ChatGPT 插件：MCP、OAuth 与创作指引

本教程接在[云端工作台部署](DEPLOYMENT.md)之后。工作台能打开、服务端能列出工具、ChatGPT完成授权、聊天实际调用工具，是四个不同验收点。这里不承诺所有账号都开放相同入口；本项目不是ChatGPT旧式ChatGPT Plugin商店协议，也不是需要OPENAI_API_KEY的API应用。

## 1. 先准备自己的后端

工作台已完成鉴权、COS保存与重开验收。下面以 `https://your-function.ap-guangzhou.tencentscf.com` 代表自己的SCF根地址（替换占位值，不是可用演示服务）。

| 项目 | 自己的配置 |
| --- | --- |
| MCP Server URL | SCF根地址 + `/mcp` |
| issuer / MCP_PUBLIC_BASE_URL | SCF根地址，无末尾斜线 |
| resource | SCF根地址 + `/mcp` |
| 授权发现 | 根地址 + `/.well-known/oauth-authorization-server` |
| 资源发现 | 根地址 + `/.well-known/oauth-protected-resource/mcp` |
| PLATFORM_ALLOWED_ORIGIN | 自己Pages的精确HTTPS origin，不填ChatGPT域名 |
| PLATFORM_API_KEY | 维护者管理密钥，至少32字符，只在自己的工作台输入 |
| AI_SUBMIT_KEY | 独立随机至少32字符，与管理密钥不同，只在服务端配置 |
| MCP_ALLOWED_REDIRECT_URIS | 从自己的ChatGPT连接表单核对的精确回调，多个已核实值用空格分隔 |
| MCP_OAUTH_ENABLED | 初始不设置；关闭正文采集并核实回调后才设true |

保留微信凭据及COS执行角色。不要将管理密钥、固定投稿密钥或微信凭据粘贴进聊天、插件描述、回调URL或静态构建。示例App文件没有真实绑定，不能直接当成已连接插件。

OAuth响应包含短期凭据。在SCF这一独立函数关闭请求/响应正文采集，核对没有其他代理或日志配置采集Authorization、授权码和令牌；删除源码print不等于关闭平台日志。不要用控制台测试事件调试真实OAuth令牌兑换。

## 2. 无密钥检查端点

两份发现文档应返回JSON。MCP采用Streamable HTTP：POST处理JSON-RPC，直接浏览器GET `/mcp` 返回405是预期，不应为此切换成无认证。

可以使用官方推荐的MCP Inspector连接自己的完整 `/mcp` 地址，先检查initialize和tools/list，不调用写入工具。没有访问令牌的业务工具调用应被拒绝。预期服务端五项如下：

| 工具 | OAuth scope | 行为 |
| --- | --- | --- |
| submitArticleForReview | labview:submit | 新增平台待审稿，不同步微信 |
| getSubmissionStatus | labview:status | 查询原投稿状态 |
| listPublishedArticleIndex | labview:index | 查询已登记公开文章索引 |
| getWeChatDraftStatus | labview:status | 读原投稿全文/封面说明及微信草稿状态 |
| createWeChatDraft | labview:wechat-draft | 确认当前版本后仅新建微信草稿 |

scope沿用历史命名，不需要改成新仓库名；擅改会使现有工具定义与令牌不匹配。服务端五项不代表ChatGPT应用目录已经有五项。

## 3. 在 ChatGPT 登记服务器

按[官方连接教程](https://developers.openai.com/plugins/deploy/connect-chatgpt)：在网页版[Plugins](https://chatgpt.com/plugins)选择加号 → Add custom MCP server。填写自己的名称、含 `/mcp` 的Server URL，认证选择OAuth，核对风险提示后创建。账号与工作区策略可能影响入口；找不到时先核实当前账号能力，不照搬工作区管理员页面，不要求购买套餐或重建App来猜测解决。

优先选客户端标识符元数据文档CIMD。当前服务支持官方ChatGPT客户端元数据，token认证方法支持none及private_key_jwt；不要手动填写管理密钥作为client secret。DCR注册端点未实现，注册URL留空，不填example.com。

创建表单如果提供高级OAuth项：先保留自动发现的issuer、authorize、token和resource值。核对实际回调后填入服务端精确白名单，再启用MCP_OAUTH_ENABLED。缺少发现项时先检查端点，不能根据猜测填写任意URL。

如账号只有自定义OAuth客户端方式，本代码已有公共客户端ID `labview-private-plugin`，client secret留空，token认证方法none；必须仍核对自己的实际回调。它不是管理凭据，也不是推荐绕过CIMD的修复方式。更详细的客户端身份及回调规则见[官方认证说明](https://developers.openai.com/plugins/build/auth)。代码另保留了已核实的ChatGPT稳定回调，不支持任意客户端域名或通配跳转。

首次纯平台投稿只需labview:submit、labview:status、labview:index；只有需要聊天新建微信草稿时才授权labview:wechat-draft，并先完成微信权限与工作台验证。OIDC未实现，保持关闭，不为完成表单编造端点或scope。

## 4. 完成授权

点击连接 → 跳到自己的Pages工作台 → 输入自己的SCF地址与管理密钥连接 → 核对客户端、回调和权限 → 批准 → 返回ChatGPT。管理密钥只在工作台使用，不交给ChatGPT。

不要在跳转过程中刷新、复制分享或保留完整授权链接。请求/授权码有效10分钟且单次消费；兑换结果未知时重新发起授权，不重用原码。访问令牌有效1小时，目前没有refresh token，过期后需重新授权。工作台地址被打开不代表授权完成，必须再做实际工具调用验证。

## 5. 验收聊天工具

安装创建的插件，新建聊天，用@选中自己的插件。先发只读请求：

> 请调用listPublishedArticleIndex读取已发表文章索引。只查询，不投稿、不修改、不创建微信草稿。

真实调用成功、即使返回0条，也算这一环节通过；模型仅口头说能调用不算。之后由维护者审阅一篇无私人信息的测试稿、确认仅存平台待审，再调用submitArticleForReview并按同一submission_id查询。超时先查原编号，not_found也不自动重投。

微信草稿另行验收：工具详情实际有两个新增工具、具备新增scope、工作台已启用写入后，读取原投稿当前全文/封面与review_hash，维护者确认该版本及封面后才新建一次，再只读核对。默认封面须明确说明，不能把AI配图建议当成已上传图片。不发表、不群发、不改删旧稿。

## 6. 可选：在同一插件增加创作指引

MCP连接提供工具，不会自动加载仓库里的SKILL.md。示例指引见[plugin/skills/article-workflow/SKILL.md](plugin/skills/article-workflow/SKILL.md)，不含作者私有App绑定。

先验证工具，再使用账号实际支持的App-backed技能编辑方式给刚创建的同一插件加指引。例如已有Plugin Creator时，提供自己的插件链接和该文件，明确要求“只增加技能，保留现有App ID、MCP连接、OAuth、名称和权限”。完成后查看插件技能列表，并在新聊天检查指引是否真正加载。可编辑资格由平台决定；没有支持的编辑入口时保留工具连接并先用明确聊天提示，不把文件存在等同于技能已安装。

`plugin/.app.json.example`只用于解释绑定格式，必须替换成自己实际创建的App身份，不能猜造ID。不要为网页接入直接照搬仅声明mcp.json的桌面包，也不要另建第二个连接来拼接技能。此仓库没有“一键生成已授权ChatGPT应用”的包；代码公开、插件安装、账号授权是分别进行的步骤。[官方技能说明](https://developers.openai.com/plugins/build/skills)

## 7. 已踩过的坑与更新

| 现象 | 检查与边界 |
| --- | --- |
| Authentication succeeded, action discovery failed | 区分授权和工具发现；用标准客户端检查初始化、响应头和tools/list，不能把已连接当作全部可用 |
| 服务端五项、ChatGPT只有三项 | 比较应用工具详情。官方要求对直接连接执行Refresh；重新授权、上传技能版本或新聊天不能替代目录刷新 |
| 没有Refresh | 记录网页版工具列表与管理页截图，提供自己的App ID及缺少的工具名给支持；保留现有连接，不宣称个人账号必需新App ID |
| 只有工作区管理员说明 | 先说明自己是个人账号还是工作区owner/admin，不在个人账号反复找管理员入口 |
| 重新连接成功但新工具仍缺失 | OAuth状态与目录状态分别验收；提高“允许所有工具”的审批策略不会增加目录项 |
| 授权503、invalid_client或过期 | 检查服务端开关、精确回调、官方公开元数据快照和有效期；不重置微信AppSecret |

部署新工具后按[官方元数据刷新说明](https://developers.openai.com/plugins/deploy/connect-chatgpt)更新目录并重测。原个人应用缺少可见刷新入口的问题仍由平台支持跟进，尚未解决；本教程不把该入口承诺给所有账号。问题反馈只提交脱敏提示、截图、版本和工具名，不提交完整OAuth链接或token。

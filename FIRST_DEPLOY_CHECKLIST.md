# 首次部署验收表

构建CI已验证；本表尚未在新的腾讯云账号、公众号和Pages项目从零完成。应以实际结果勾选，不能凭文档或CI代填。不开资源也能先用本地预览。

## 本地与构建包

- [ ] Python3.10启动 `python platform_preview.py`，打开127.0.0.1:18765，无凭据可预览、保存、重开。
- [ ] 已备份.local/数据，不提交数据库。
- [ ] 自己仓库的Actions检查全部通过，下载checked-workbench-packages并解压外层artifact ZIP。
- [ ] 上传SCF的是内层scf-probe.zip，而不是外层artifact ZIP。
- [ ] 用 `Get-FileHash .\scf-probe.zip -Algorithm SHA256`（PowerShell）或 `sha256sum scf-probe.zip`（Linux）核对scf-probe-manifest.json。
- [ ] 包含scf_probe.py、qcloud_cos、Crypto、LICENSE和third_party。
- [ ] OAuth公开快照最长72小时，旧包过期必须重新构建，不手动延长有效期。

## 云端只读与存储

- [ ] 新建独立广州Python3.10事件函数，入口scf_probe.main_handler，初始参考128MiB/90秒，不覆盖旧项目。
- [ ] 后台设置微信凭据，关闭请求/响应正文收集；不分享环境截图或导出配置。
- [ ] 固定出口追加微信白名单，用labview.check事件只读验证，不重置原密钥。
- [ ] COS私有桶从未开启版本控制；函数执行角色仅有自己桶的模板权限，无删除权限。
- [ ] 设置COS_BUCKET，用labview.storage.check验证读权限；该检查不证明写权限通过。
- [ ] 管理密钥、精确Pages来源、函数URL已配置；URL目标与更新目标一致，微信写入仍关闭。

## 网页与草稿

- [ ] Pages只部署dist/web，不注入微信、腾讯云或平台密钥。
- [ ] 用自己的SCF根地址与管理密钥连接；无授权请求被拒绝。
- [ ] 保存测试稿、刷新重连后重开；同文重复保存不产生重复记录。
- [ ] 图片与文章关联正常；示例公众号品牌和水印使用前已自行确认。
- [ ] 仅在需要时开启ENABLE_DRAFT_SYNC，确认具体测试稿和封面后创建一次，公众号后台核对。
- [ ] 写入未知只读核对，不重复提交，不删状态解锁。

## 可选MCP

- [ ] 创建自己的App，核对根URL、/mcp资源和精确回调，不使用作者私有绑定。
- [ ] 管理密钥只在工作台输入，AI_SUBMIT_KEY独立随机且与之不同。
- [ ] 先做索引只读查询；空索引正常，不代表公众号没有历史文章。
- [ ] 应用详情核对工具数量；重新授权不等于刷新目录。个人账号缺刷新入口问题仍待平台处理。
- [ ] 微信草稿工具只有实际出现并具备对应scope才能验收，服务端tools/list五项不代表聊天可用。

验收记录只记版本、日期、步骤、脱敏错误码和是否写入，不记密钥、token、完整OAuth链接或私人正文。

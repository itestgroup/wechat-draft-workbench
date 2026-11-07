# WeChat Draft Workbench v0.1.0-preview

单维护者使用的微信公众号 Markdown 内容工作台：创作、人工审阅、保存，再明确确认同步到微信草稿箱。最终发表由维护者在公众号端完成。

这是MIT开源的早期预览版，已完成当前候选的秘密扫描及构建检查，但尚未完成独立账号部署复现；不是稳定生产版。LabVIEW 伯乐是原使用场景，部分界面和水印仍保留该名称。

实际Linux Python3.10构建、哈希锁定安装、许可归档及本轮OSV查询已通过，见[依赖核查](DEPENDENCY_AUDIT.md)。仍需完成独立账号部署复现，不把一次检查作为长期安全保证。当前发布为v0.1.0-preview。

## 能做什么

- 本地预览 Markdown、保存及重新打开文章。
- 自定义封面、正文图片和浏览器端水印处理。
- 云端保存文章，人工确认后创建微信草稿。
- 写入超时后只读核对，避免因重试产生重复草稿。
- 可选 MCP 接入：平台投稿、状态查询和公开文章索引。

已知限制：AI 自动配图未实现；聊天直接创建微信草稿的后端已有代码，但原 ChatGPT 应用新增工具目录尚未更新，不能承诺该链路可用。当前为单维护者模型，不是多租户系统。不提供发表、群发、修改或删除现有微信内容。

## 先在本地使用

安装Python3.10和Git。本地预览无需云资源或微信凭据：

```powershell
git clone https://github.com/itestgroup/wechat-draft-workbench.git
cd wechat-draft-workbench
python platform_preview.py
```

打开 http://127.0.0.1:18765 。默认模式不需要微信凭据、不调用微信。先预览、保存，再重新打开核对。数据在 `.local/`，请自行备份。不要将本地 HTTP 服务直接暴露公网。

需要本地同步微信草稿时，以 `python platform_preview.py --wechat` 启动，按终端提示隐藏输入自己的凭据。不要把凭据写在命令参数中。首次应使用测试文章，明确确认创建后只读检查草稿箱。

## 开发验证

```powershell
python -m unittest discover -p "test_*.py" -v
node --test test_frontend.mjs
node scripts/build_pages.mjs
```

Node.js 使用22或更高版本。模拟测试不需要生产凭据，不能替代真实云端权限及微信验收。云端运行包仍需兼容 Linux Python 3.10 的依赖。

云端部署见 [DEPLOYMENT.md](DEPLOYMENT.md)，更新与踩坑记录见 [MAINTENANCE.md](MAINTENANCE.md)。

ChatGPT接入请继续阅读[插件部署教程](CHATGPT_PLUGIN.md)：包含MCP端点、OAuth/CIMD、授权、五项工具验收、可选创作技能和目录刷新问题。工作台部署不会自动创建或授权ChatGPT插件。

首次操作按[FIRST_DEPLOY_CHECKLIST.md](FIRST_DEPLOY_CHECKLIST.md)逐项验收；维护者发布与跨仓库更新规则见[PUBLISHING.md](PUBLISHING.md)。

## 许可状态

代码采用 [MIT License](LICENSE)。公众号品牌、文章与图片不自动纳入代码许可证；候选包不含私人文章和图片。

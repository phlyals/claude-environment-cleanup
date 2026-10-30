---
name: claude-environment-cleanup
description: Use when a user asks to clean or reset a local macOS Claude Code environment, remove old login or API configuration residue, or prepare for a fresh CLI login while preserving conversation history, skills and MCP. Also applies to 清理 Claude 环境、重置 Claude 登录环境. Excludes cloud resources, account deletion and general disk cleanup.
---

# Claude Code 环境清理

将本地 Claude Code 恢复为未登录、未完成首次引导的状态。保留历史会话、技能、插件、MCP、项目内容、machineID、其他应用配置和现有网络线路。

支持 macOS 默认配置 `~/.claude` 与 `~/.claude.json`，Python 3.9+。自定义配置目录、Windows、Linux 不运行附带的清理脚本；先报告适用范围。脚本源于 Claude Code 2.1.312 上验证过的流程，内部字段与钥匙串名称可能随版本变化。

## 授权与范围

“先看、先说方案、不要改”只做预览。“清理一下、按这个方案执行”可直接在该范围执行，不重复索要已给出的授权。安装这个 Skill 本身不授权运行清理。

| 处理 | 保留或单独处理 |
| --- | --- |
| 官方退出登录；重置首次启动字段 | 不删除整个 `.claude`，不改 machineID |
| 清除确认为旧账号的缓存字段 | 不更换账号、不发起登录、不保证解封 |
| 将白名单缓存和旧恢复副本移到私有备份 | 不动 Codex、Gemini、浏览器、Claude Desktop 的独立数据 |
| 对已授权的 API 残留逐项清除 | 不改系统代理、DNS、节点、时区；不自动重装 CLI |

## 执行顺序

1. **确认目标与进程。** 核对当前用户、平台、实际 CLI 路径、配置目录。识别 CLI/IDE 的 Claude 子进程；授权范围内正常结束准确的目标进程，保留编辑器和其他工作。如果当前 Agent 就运行在 Claude Code 中，从独立的 Codex 或用户终端执行；不要杀掉自己继续清理。脚本遇到正在运行的 Claude 会停止。
2. **预览。** 将下面命令中的 `<skill-dir>` 替换为本 Skill 的绝对路径：
   ```bash
   python3 "<skill-dir>/scripts/cleanup.py"
   ```
   预览不写文件、不调用 Claude；会解析可能包含凭据的配置，但只展示字段名、目标路径和阻塞原因，不输出值。
3. **核对外部注入。** 脚本只扫描常见用户设置、当前进程环境和常见 shell 文件。按 [范围与异常处理](references/scope-and-recovery.md) 检查项目/IDE/launchd/托管配置及已安装切换器。不能把“预览无阻塞”当成全机无注入。API 残留先备份，再按用户选择逐项处理；不要直接运行来源不明的旧清理脚本。
4. **清理。** 已有执行授权且阻塞已解决时运行：
   ```bash
   python3 "<skill-dir>/scripts/cleanup.py" --apply
   ```
   脚本先完整备份并校验，再执行 `claude auth logout` 和 `claude auth status`，确认未登录与钥匙串凭据不存在，然后清除白名单字段、设置 `numStartups=0` / `hasCompletedOnboarding=false`，隔离缓存与 `.claude.json.backup.*`。
5. **验收。** 必须退出码为 0 且 `verified=true`；读本次 `result.json`。检查备份、保留文件哈希、machineID/MCP、未登录状态和隔离清单。有异常就报告阶段和备份位置，不继续试删。脚本失败后的恢复见引用文档。
6. **交付。** 给出完成项、备份位置、保留项、未验收项。保持 Claude 关闭且未登录；用户需要使用时自行启动。不要为了截图验收重新启动 CLI，因为它会重新生成 userID、缓存或迁移首次启动字段。

## 容易误判的地方

- `auth status` 未登录的正常退出码是 **1**；其他错误不能视为未登录。
- 钥匙串仅检查 `Claude Code-credentials`，不用 `security ... -w` 输出密码；查询错误不等于凭据不存在。
- JSON 损坏时保留原件并停止，不能用空对象覆盖。
- 旧账号恢复副本要移出活动扫描目录，不能清理后又自动还原旧身份。
- 网络检查只在用户要求时做；保留既定线路。第三方评分或出口相同不等于账号可用，更不等于不会封禁。

官方命令依据：[CLI reference](https://code.claude.com/docs/en/cli-reference)。配置来源与优先级：[Settings](https://code.claude.com/docs/en/settings)。内部缓存字段属于本地实践，不能冒充官方保证。

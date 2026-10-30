# Claude Environment Cleanup

一个可安装到 Codex 或其他支持 Agent Skills 的工具中的中文 Skill，用于清理 **macOS Claude Code 本地登录环境**。

先预览、完整备份与哈希校验，再官方退出登录、重置首次启动状态、隔离账号缓存和旧恢复副本。保留会话、技能、插件、MCP、machineID、项目、Codex 和网络设置。

**不会解封账号、绕过平台限制、自动登录或删除整个 `.claude`。** Claude Desktop、浏览器、云端资源、Windows/Linux 和自定义配置目录不在自动脚本范围内。

## 安装 Skill

下载或克隆本仓库：

```bash
git clone https://github.com/phlyals/claude-environment-cleanup.git
```

将仓库中的 `skills/claude-environment-cleanup` 整个文件夹复制到以下任一 Skill 目录；若已有同名目录，先比较版本，不直接覆盖：

| Agent | 用户级目录 |
| --- | --- |
| Codex | `~/.codex/skills/` |
| Claude Code | `~/.claude/skills/` |
| 其他兼容工具 | 该工具指定的 Skills 目录 |

重新打开会话后使用：

> 使用 $claude-environment-cleanup，先检查本机 Claude 环境并给出清理范围。

或在范围已确认后：

> 使用 $claude-environment-cleanup 执行清理，保留历史会话、技能、MCP、Codex 和现有网络线路。

如果当前 Agent 本身运行在 Claude Code 中，要从独立的 Codex 或终端执行，避免正在工作的进程把旧状态写回。脚本不会杀进程。

## 直接使用脚本

依赖：macOS、Python 3.9+、支持 `auth logout` / JSON `auth status` 的 Claude Code CLI。清理逻辑源于 2.1.312 上的实际流程；内部字段不是稳定公共 API，新版本行为变化时应先检查。

从仓库根目录运行，只读预览：

```bash
python3 skills/claude-environment-cleanup/scripts/cleanup.py
```

关闭 Claude Code 的终端与 IDE 子进程、处理预览中的阻塞项，并确认清理范围后执行：

```bash
python3 skills/claude-environment-cleanup/scripts/cleanup.py --apply
```

默认无写入。执行模式在 `~/.claude-cleanup-backups/` 创建私有备份，退出登录后将白名单缓存和恢复副本移到隔离区，不永久删除。JSON 损坏、备份失败、符号链接、活动进程或认证检查异常会停止。详见 [Skill](skills/claude-environment-cleanup/SKILL.md) 和 [范围与恢复说明](skills/claude-environment-cleanup/references/scope-and-recovery.md)。

**备份可能包含凭据和个人历史，请勿上传备份或真实执行记录。** 钥匙串凭据不在文件备份中；退出登录后的恢复需要正常重新登录。

## 验证

```bash
python3 -m unittest discover -s tests -v
```

测试使用临时目录和虚构数据；系统进程、CLI、钥匙串调用全部替换为受控测试实现，不会注销真实账号。脚本测试通过不等于真实账号、登录或网络可用。

官方依据：[CLI 命令](https://code.claude.com/docs/en/cli-reference)、[配置来源](https://code.claude.com/docs/en/settings)。这是社区工具，与 Anthropic 无官方关联。

MIT License.

# 开发规范

## 语言

- 主要语言: Python、TypeScript、Go、C、PowerShell
- 包管理器: uv、Bun、pnpm
- 自然语言: 简体中文 (zh-CN)

## 流程

1. 用 `update_todo_list` 跟踪任务
2. 逐个确认工具调用结果
3. 完成后用 `attempt_completion`

## Python代码风格

- argparse 命令行参数
- configparser 配置管理
- logging 日志记录
- try-except 异常处理
- PascalCase 类名, snake_case 函数名
- 公开函数加 docstring

## Git Commit

```
<类型>(<范围>): <描述>
```

类型: feat, fix, docs, style, refactor, perf, test, chore
Commit message: 英文
版本号作为每次 commit 后的 tag

## 版本号

```
MAJOR.MINOR.PATCH.BUILD
```

- 每次提交: BUILD自动+1
- 其余部分根据指令
- feat: 建议PATCH+1

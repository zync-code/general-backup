# Project: general-backup

## Overview
Full-server snapshot and restore toolkit (Ubuntu 24.04)

## GitHub
- Repository: https://github.com/zync-code/general-backup
- Remote: https://github.com/zync-code/general-backup.git
- Default branch: main
- Merge strategy: squash

## Linear (reference only)
- Project ID: 457306f3-c9ec-4ee6-b916-1fc827d1b9dc
- Team: ZYN (Zync-code)
- Note: Tasks are managed via GitHub Issues. Linear is for reference only.

## Deployment
- Type: **nginx**
- nginx path-based routing on the shared server
- Each app gets a port and a location block under /general-backup/

### After building apps, run deploy-setup:
```bash
deploy-setup --project general-backup
```
This auto-discovers apps from `apps/` and sets up nginx deployment with nginx routing, env files, and PM2 config.

## Language
- ALL output (code, comments, commits, PRs, task descriptions) MUST be in English

## Task Management

Task backend: **github**
All task operations go through the `task-*` wrapper commands — do NOT use Linear MCP tools or direct linear-* commands.

### Task bash commands

| Operation | Command |
|-----------|---------|
| Get open tasks | `task-get-todo general-backup` → JSON array |
| Update task status | `task-update general-backup <task_id> <status>` |
| Add comment | `task-comment general-backup <task_id> "<body>"` |
| Create task | `task-create general-backup "<title>" "[body]" "[label]"` → task_id |

Status values: `In Progress`, `In Review`, `Done`

### GitHub MCP tools (exact names)

| Operation | Tool | Required params |
|-----------|------|-----------------|
| Create PR | `mcp__github__create_pull_request` | `owner`, `repo`, `title`, `body`, `head`, `base` |
| Get PR | `mcp__github__get_pull_request` | `owner`, `repo`, `pull_number` |
| List PRs | `mcp__github__list_pull_requests` | `owner`, `repo`, `state` |
| Merge PR | `mcp__github__merge_pull_request` | `owner`, `repo`, `pull_number`, `merge_method` |
| Review PR | `mcp__github__create_pull_request_review` | `owner`, `repo`, `pull_number`, `body`, `event` |

### Git (shell commands)

```bash
git checkout -b feature/TASK-123-slug
git add .
git commit -m "type(scope): subject"
git push -u origin $(git branch --show-current)
gh pr merge NUMBER --squash --delete-branch
git checkout main && git pull
```

## Task Execution Workflow

### 0. Find Next Task
```bash
task-get-todo general-backup
```
Parse JSON — take first task (id, identifier, title, description). If empty array → all done, stop.

### 1. Start Task
```bash
task-update general-backup <task_id> "In Progress"
```

### 2. Create Branch
```bash
git checkout -b feature/<identifier>-slug
task-comment general-backup <task_id> "Branch: \`feature/<identifier>-slug\`"
```

### 3. Implement & Commit
- Format: `type(scope): subject` (Conventional Commits)
- NO emojis, NO Co-authored-by, NO Cursor attribution

### 4. Push & PR
```bash
git push -u origin feature/<identifier>-slug
```
```
mcp__github__create_pull_request(owner: "zync-code", repo: "general-backup",
  title: "feat(scope): description",
  body: "## Description\n\n...\n\nCloses <identifier>",
  head: "feature/<identifier>-slug", base: "main")
task-update general-backup <task_id> "In Review"
task-comment general-backup <task_id> "PR created: #N"
```

### 5. Merge & Close
```bash
gh pr merge N --squash --delete-branch
git checkout main && git pull
```
```bash
task-update general-backup <task_id> "Done"
```

## Task Hierarchy
- **Epic**: Large body of work (e.g., "User Authentication System")
- **Feature**: Discrete functionality within an epic (e.g., "Login Form")
- **Issue**: Specific implementation task (e.g., "Add email validation")

## Guidelines
- Read and follow the PRD if one exists in the project root
- Keep commits atomic and descriptive
- Update task status at every stage transition
- Add meaningful comments on tasks for significant actions
- Close tasks only when PR is merged

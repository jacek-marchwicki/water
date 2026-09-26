# Listing Worktrees

## Basic list

```bash
git worktree list
```

Shows all worktrees with their paths, commit hashes, and branch names:

```
/path/to/repo                       abc1234 [main]
/path/to/repo/.worktrees/feature-x  def5678 [feature/x]
```

## Porcelain format (for scripting)

```bash
git worktree list --porcelain
```

Returns machine-readable output with one attribute per line:

```
worktree /path/to/repo
HEAD abc1234abc1234abc1234abc1234abc1234abc1234
branch refs/heads/main

worktree /path/to/repo/.worktrees/feature-x
HEAD def5678def5678def5678def5678def5678def5678
branch refs/heads/feature/x
```

## Step-by-step process for agents

1. Run `git worktree list`.
2. Present the output to the user in a readable format.
3. If no worktrees exist (only the main worktree is shown), let the user know.

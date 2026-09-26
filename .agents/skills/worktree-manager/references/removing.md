# Removing Worktrees

## Basic removal

```bash
git worktree remove <path>
```

Removes the worktree directory and unregisters it from git.

## Force removal (recommended default)

```bash
git worktree remove --force <path>
```

Always use `--force` — repos with submodules will fail without it. The flag is safe; it just bypasses the submodule check.

## Pruning stale references

```bash
git worktree prune
```

Cleans up worktree metadata for directories that were deleted manually (e.g., via `rm -rf` instead of `git worktree remove`). This is a no-op if there's nothing stale. Always run after removing a worktree.

## Step-by-step process for agents

1. Run `git worktree list` to show existing worktrees.
2. Ask the user which worktree to remove.
3. Remove using the path from the list:
   ```bash
   git worktree remove --force <worktree-path>
   ```
4. Prune stale references:
   ```bash
   git worktree prune
   ```
5. If the worktree was inside `.worktrees/` and that directory is now empty, clean it up:
   ```bash
   rmdir .worktrees 2>/dev/null
   ```
6. Confirm removal.

## Notes

- The branch itself is not deleted when you remove a worktree. Delete it separately with `git branch -d <branch>` if no longer needed.
- Never hardcode paths — always read from `git worktree list`.

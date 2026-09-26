# Troubleshooting

## "working trees containing submodules cannot be moved or removed"

**Cause**: The repo has git submodules. Standard `git worktree remove` refuses to proceed.

**Fix**: Use `--force`:
```bash
git worktree remove --force <path>
```

This is safe — it only bypasses the submodule safety check, not data protection.

## "fatal: '<branch>' is already checked out at '<path>'"

**Cause**: Git doesn't allow the same branch to be checked out in two worktrees simultaneously.

**Fix**: Either:
- Remove the existing worktree that has that branch checked out
- Create a new branch instead: `git worktree add -b <new-branch> <path> <start-point>`
- Detach HEAD in the other worktree: `git checkout --detach` (from within that worktree)

## Stale worktree entries in `git worktree list`

**Cause**: A worktree directory was deleted manually (e.g., `rm -rf`) instead of using `git worktree remove`.

**Fix**:
```bash
git worktree prune
```

## `.worktrees/` showing up in `git status`

**Cause**: The `.worktrees/` directory isn't in `.gitignore`.

**Fix**:
```bash
echo '.worktrees/' >> .gitignore
```

## Worktree not picking up changes from main repo

**Cause**: Worktrees share the same `.git` directory. Branches and commits are shared, but the working directory is independent.

**Note**: This is expected behavior. Each worktree has its own working tree and index. To get changes from another branch, use `git merge` or `git rebase` from within the worktree.

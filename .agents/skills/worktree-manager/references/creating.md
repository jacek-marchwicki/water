# Creating Worktrees

## With a new branch (most common)

```bash
git worktree add -b <branch-name> <path> <start-point>
```

- `-b <branch-name>`: the new branch to create (e.g., `feature/my-thing`)
- `<path>`: where the worktree lives on disk (e.g., `.worktrees/my-thing`)
- `<start-point>`: which branch/commit to base it on (e.g., `develop`, `main`)

If `<start-point>` is omitted, it defaults to `HEAD` (the current commit).

### Example

```bash
git worktree add -b feature/auth .worktrees/auth develop
```

Creates branch `feature/auth` off `develop`, checked out at `.worktrees/auth`.

## With an existing branch

```bash
git worktree add <path> <branch>
```

- `<path>`: where the worktree lives on disk
- `<branch>`: an existing branch to check out

### Example

```bash
git worktree add .worktrees/bugfix feature/existing-branch
```

## Step-by-step process for agents

1. Ask the user for:
   - **Branch name** — the new branch to create (e.g., `feature/my-thing`)
   - **Worktree folder name** — defaults to the last segment of the branch name (e.g., `my-thing`)
   - **Start point** — which branch to base it on (default: the main/default branch)
   - **Location** — subfolder or parent folder (see `placement.md`)

2. If subfolder location, ensure `.worktrees/` is in `.gitignore`:
   ```bash
   grep -q '\.worktrees/' .gitignore 2>/dev/null || echo '.worktrees/' >> .gitignore
   ```

3. Create the worktree:
   - **Subfolder**: `git worktree add -b <branch-name> .worktrees/<folder-name> <start-point>`
   - **Parent folder**: `git worktree add -b <branch-name> ../<repo-name>-<folder-name> <start-point>`

4. Confirm success and show the full path.

## Notes

- `git worktree add` creates intermediate directories automatically — no need to `mkdir` first.
- You cannot check out the same branch in two worktrees simultaneously.
- Being explicit with the start point (e.g., `develop`) is safer than relying on `HEAD`.

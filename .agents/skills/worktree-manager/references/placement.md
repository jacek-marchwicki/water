# Worktree Placement

Two strategies for where worktrees live on disk.

## Option 1: Subfolder (default)

Place worktrees inside a `.worktrees/` directory within the repo:

```
my-repo/
├── .worktrees/
│   ├── feature-a/
│   └── bugfix-b/
├── src/
└── ...
```

**Path**: `.worktrees/<folder-name>`

**Pros**:
- Everything in one place
- Easy to find and manage

**Cons**:
- Some IDEs may index the subfolder (can usually be excluded)
- Must be added to `.gitignore`

### .gitignore setup

Before creating the first subfolder worktree, ensure `.worktrees/` is ignored:

```bash
grep -q '\.worktrees/' .gitignore 2>/dev/null || echo '.worktrees/' >> .gitignore
```

## Option 2: Parent folder (sibling directory)

Place worktrees alongside the repo in the parent directory:

```
Developer/
├── my-repo/                    (main worktree)
├── my-repo-feature-a/          (worktree)
└── my-repo-bugfix-b/           (worktree)
```

**Path**: `../<repo-name>-<folder-name>`

To get the repo name programmatically:
```bash
basename "$(git rev-parse --show-toplevel)"
```

**Pros**:
- Full IDE isolation — each worktree is a completely separate project folder
- No `.gitignore` changes needed

**Cons**:
- Worktrees scattered in the parent directory
- Harder to tell which folders are worktrees vs standalone repos

## Naming convention

Default the folder name to the **last segment** of the branch name:
- `feature/auth` → `auth`
- `feature/new-thing` → `new-thing`
- `bugfix/login-crash` → `login-crash`

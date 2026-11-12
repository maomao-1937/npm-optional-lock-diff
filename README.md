# npm-optional-lock-diff

Compare two saved npm lockfiles and report direct optional dependencies that lost a recorded package path. Run with Python 3.11 or later. The CLI uses the standard library and reads two local files offline.

Locally tested on Windows with CPython 3.11.15. CI runs the tests and examples on Ubuntu and Windows.

## Quick start

Run these commands from the repository root.

```sh
python npm_optional_lock_diff.py examples/before-lock.json examples/retained-lock.json
python npm_optional_lock_diff.py examples/before-lock.json examples/lost-lock.json
python npm_optional_lock_diff.py examples/before-lock.json examples/linked-lock.json
python -I examples/run_examples.py
```

The first command exits `0` because the scoped package moved to another location reachable from its importer. The second exits `1` and prints this report.

```json
{"status":"regressions","regressions":[{"importer":"","dependency":"@native/root","previous_target":"node_modules/@native/root"},{"importer":"node_modules/@tools/worker","dependency":"@native/engine","previous_target":"node_modules/@native/engine"}]}
```

The nested worker keeps its own reachable copy of `@native/engine`. Declarations already missing before the change and newly added declarations do not appear. The third command exits `2` with `unsupported_link` for the after file.

## Inputs and comparison

- Pass the before file first and the after file second. Use `--` before the filenames if a filename begins with a hyphen. There are no configuration files, environment variables or package-manager commands to set up.
- Use trusted local regular file paths. On Windows, explicit UNC paths, device paths, reserved names and alternate data streams return `not_regular_file` before filesystem access. Links and mounted drives can lead to remote files, so use paths you know resolve locally. Each file must contain UTF-8 JSON without a BOM, duplicate object keys or non-finite literals. The format requires integer `lockfileVersion` 2 or 3 and a `packages` object with a root entry at `""`.
- Per-file limits are 8 MiB, 100,000 package entries including the root and 50,000 direct optional declarations. Package locations use forward slashes, start with `node_modules/` apart from the root and end with an unscoped or scoped package name after a `node_modules` segment. They use ASCII letters, digits, underscores, tildes and hyphens, with dots after the first character of a segment and an optional scope marker. Each path is limited to 1,024 characters and 64 segments. Package names are limited to 214 characters.
- Compare entries at identical importer paths and declarations with identical names and exact spec strings. The CLI searches the importer's `node_modules` and then its ancestors up to the lockfile root. It skips an ancestor that is itself named `node_modules` when constructing search directories. The first matching package record, including a versionless placeholder, supplies the recorded path.
- A recorded path indicates that a package row exists in this lookup. Installability, version compatibility, registry availability, platform eligibility, package exports, peer edges and full npm resolution are outside the comparison. Root workspaces, package rows marked `link: true`, explicit `workspace:` or `link:` optional specs and locations outside the supported layout stop the comparison with exit `2`.

| Exit | JSON status | Meaning |
| --- | --- | --- |
| `0` | `pass` | No matching optional edge lost a recorded path |
| `1` | `regressions` | One or more matching optional edges lost a recorded path |
| `2` | `error` | Invalid arguments, unreadable, malformed or unsupported input |

The CLI writes one JSON line to stdout. Regressions are sorted by importer path and then dependency name. An empty importer means the root. Input errors include a fixed code and the role `before` or `after`. Reports omit dependency specs, registry URLs and local input filenames. A failed stdout write also exits `2`. The `--help` option prints usage and exits `0`.

## 中文说明

这个命令比较修改前后的两份 npm 锁文件，找出仍由同一路径下的包直接声明、依赖规格字符串也完全相同，但在修改后失去可查找锁文件条目的可选依赖。需要 Python 3.11 或更高版本，仅用标准库，离线运行。

```sh
python npm_optional_lock_diff.py examples/before-lock.json examples/retained-lock.json
python npm_optional_lock_diff.py examples/before-lock.json examples/lost-lock.json
python npm_optional_lock_diff.py examples/before-lock.json examples/linked-lock.json
python -I examples/run_examples.py
```

- 把修改前的文件放在第一个参数，修改后的文件放在第二个参数。文件名以连字符开头时，在两个文件名前加 `--`。运行无需配置文件、环境变量或 npm 命令。
- 支持锁文件 v2、v3，仅用 `packages` 对象比较依赖。根项目的路径是空字符串 `""`。工具从依赖声明方逐级向上查找 `node_modules`，遇到本身叫 `node_modules` 的目录时跳过该候选层，到锁文件根目录为止。支持带作用域的名称和嵌套目录中的提升安装。
- 输入须为可信的本地普通文件，使用无 BOM 的 UTF-8 JSON，且无重复键或非有限数字字面量。Windows 下会在访问文件系统前拒绝显式 UNC 路径、设备路径、保留名称和备用数据流。文件链接和挂载盘的实际位置由文件系统决定，请使用已知指向本地文件的路径。每份文件上限是 8 MiB、10 万条包记录和 5 万条直接可选依赖声明。包路径上限为 1,024 字符、64 段，包名上限为 214 字符。具体路径格式见上文。
- 退出码 `0` 表示未发现符合规则的条目丢失，`1` 表示发现丢失，`2` 表示输入无效、无法读取或超出支持范围。涉及 workspace、链接安装或不受支持的包路径时，工具直接返回 `2`。
- 第二条例子报告根项目的 `@native/root` 和顶层 `@tools/worker` 的 `@native/engine`。嵌套的 worker 仍然能找到它所在子树中的同名包。无版本号的占位记录也计入。报告只说明锁文件中的路径变化，版本兼容性、实际安装结果和平台支持需另行验证。

See [TESTING.md](TESTING.md) for local results and [RESEARCH.md](RESEARCH.md) for upstream evidence. Licensed under the [MIT License](LICENSE).

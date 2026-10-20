# Security and GitHub publication checklist / 安全与发布检查

## Audit scope / 检查范围

Static review of the supplied `MCP(5).zip` snapshot and this prepared release. The original archive contained 59 entries in six directories. No `.git` object database or prior commit history was included; therefore historical commits and separate local working folders were **not inspected**.

静态检查对象为用户提供的 `MCP(5).zip` 和本次整理的发布包。原包包含 6 个目录、59 个条目，未包含 `.git` 历史，因此**无法据此保证过去的提交和其他本地目录中不存在泄露**。

## Findings / 检查结果

- No embedded private key, API token file, personal browser profile, Cookie database, SQLite memory database, logs, downloaded images, personal absolute paths, or plaintext passwords were found in the original archive. Automated secret-pattern scanning alone is not a guarantee against every possible secret format.
- The original `WebSearch/src/webmcp/cli.py` displayed the complete saved configuration, including `clash_controller_secret` if configured. The release masks that field and removes credential-bearing URL details from configuration/status display. Network probe exception messages in status were also generalized.
- The original ZIP was missing `WebSearch/webmcp_config.example.json`, `Meme/meme_config.example.json`, and component README files required by `MemoryHub/pyproject.toml` and `WebSearch/pyproject.toml`. Safe examples and the missing documentation files have been added. `JMComic/requirements.txt` now declares its direct Pillow dependency.
- Runtime tools can still intentionally reveal local paths or remote website responses. Tool results may be transmitted to the host model. Any browser screenshot, diagnostic file, HTTP archive, database dump, or command output must be reviewed before publication.
- `gallery_mcp` creates a Pixiv login profile **outside the repo** under the OS's app data location. `MemoryHub` and `SemanticSearch` persist user data in local SQLite files; `JMComic` and Pixiv may write downloaded media. These must remain private.

## Before pushing / 推送前检查

1. **Create the repository from the prepared release tree**, not by copying over an existing working tree full of private files. 不要在包含历史凭据的旧目录中直接 `git add .`。
2. Verify the working tree and staged files:

   ```bash
   git status --short
   git diff --cached --name-only
   git ls-files
   ```

3. Check that there are no committed or staged copies of `webmcp_config.json`, `meme_config.json`, `.env`, SQLite databases, local profiles, cookies, downloads, screenshots, logs, or real user datasets.
4. If you are publishing an existing Git repository, **inspect its prior commits and Git tags**. `.gitignore` does not erase previously committed secrets. If a real credential was exposed, revoke/rotate it and clean up history under the provider's published procedures.
5. Verify any screenshots and sample tool outputs do not show filesystem user names, proxy credentials, IP addresses you do not wish to share, account identifiers, personal conversations, or copyrighted/unauthorized media.
6. Decide and add a suitable `LICENSE` after reviewing third-party dependencies and any third-party assets. No license has been selected on the user's behalf.

## Deployment responsibility / 部署责任

The author reports local success using five specified MCP providers with DeepSeek Harness; cross-machine behavior was not tested during packaging. The user of this repository is responsible for environment setup, authorization, network routing, browser login, model/tool permissions, and ongoing maintenance. No guarantee is made for installations outside the author's tested setup.

作者在本人设备上验证了指定的五项 DeepSeek Harness 插件接入能力；此次打包未做跨设备验证。其他用户应自行完成部署、账号授权、网络配置、模型和工具权限设置及维护。

For vulnerabilities discovered after publication, report privately to the repository owner. Avoid publishing real credentials or account data in public issues. / 若后续发现安全漏洞，请尽量私下联系仓库维护者，不要在公开 Issue 中粘贴真实令牌、Cookie 和个人数据。

# MCP Tool Collection

**A collection of six independent, locally hosted Model Context Protocol (MCP) servers for AI assistants.** It covers browser-based research, Pixiv access, JMComic metadata and downloads, evidence-aware memory, hybrid semantic retrieval, and local reaction images.

[简体中文](README_zh-CN.md) · [Security and publication notes](SECURITY.md)

> **DeepSeek Harness compatibility:** The author has confirmed on their own computer that **`gallery_mcp`, `JMComic`, `MemoryHub`, `SemanticSearch`, and `WebSearch`** can be discovered/converted into plugins by DeepSeek Harness and can return results. This is a report of one local deployment, **not a cross-platform compatibility guarantee**. Whether this works elsewhere depends on each user's host, Python environment, MCP/plugin integration, configuration, models, browser, permissions, and network access. **`Meme` is not included in that verification claim.**

## 1. Included servers

| Directory | Purpose | MCP tools |
| --- | --- | --- |
| `gallery_mcp/` | Pixiv browsing and artwork information through a dedicated Chrome profile | `pixiv_open_login`, `pixiv_session_status`, `pixiv_close_browser`, `pixiv_search`, `pixiv_artwork`, `pixiv_user`, `pixiv_user_works`, `pixiv_download` |
| `JMComic/` | Album search, information, cover retrieval, and authorized downloads | `jmcomic_search`, `jmcomic_album_detail`, `jmcomic_cover`, `jmcomic_download_album` |
| `MemoryHub/` | Evidence-backed, persistent, temporal personal memory using SQLite | `memory_remember`, `memory_recall`, `memory_manage`, `memory_status` |
| `SemanticSearch/` | Hybrid retrieval with EmbeddingGemma 2, SQLite FTS5, and FAISS | `semantic_status`, `semantic_index_files`, `semantic_sync_memoryhub`, `semantic_search` |
| `WebSearch/` | Rendered webpage search, extraction, and persistent browser actions | `web_status`, `web_search`, `web_fetch`, `browser_open`, `browser_pages`, `browser_click`, `browser_type`, `browser_scroll`, `browser_back`, `browser_extract`, `browser_screenshot`, `browser_close` |
| `Meme/` | Local meme library retrieval and configurable occasional image responses | `list_meme_library`, `get_meme`, `maybe_send_meme` |

Each component is a separate server; **the collection has no single all-in-one launcher**. Most providers use stdio, so the MCP host launches each process individually.

## 2. Requirements

- **Python 3.10 or later**. Use the same Python interpreter for installation and MCP host launch. A separate virtual environment per server is recommended because dependency constraints differ.
- A client/host capable of launching **stdio MCP servers**.
- For browser tools: a graphical environment and a supported browser. `gallery_mcp` specifically uses an installed **Google Chrome**, a **separate application-owned browser profile**, and Playwright.
- For `SemanticSearch`: the EmbeddingGemma 2 model and its Python dependencies. A first model load may require model downloads and additional RAM/VRAM. No GPU configuration is guaranteed here.
- Valid internet connectivity and any required proxy, account authorization, or external-site access for online providers.

Installation examples below use `python`. Substitute the exact interpreter path selected for your MCP host; do not assume your terminal and host use the same environment.

## 3. Installation and launch

Run the commands from the **repository root**. Do not use `--setup` commands blindly: some create machine-specific local files or perform host integration.

### WebSearch

```bash
python -m pip install -e ./WebSearch
python -m playwright install chromium
```

Copy `WebSearch/webmcp_config.example.json` to `WebSearch/webmcp_config.json`, then edit your actual proxy/browser settings. Alternatively, run the configuration command:

```bash
python WebSearch/run_webmcp.py configure
python WebSearch/run_webmcp.py status
python -I WebSearch/run_webmcp.py serve
```

The source's default `require_proxy` is `true` and its `proxy_url` is empty; therefore online search is disabled until the required network route works or the user explicitly changes the configuration. `run_webmcp.py --setup` provides an optional installation/browser-detection flow. **Never commit `webmcp_config.json`: it may contain a Clash controller secret, authenticated proxy URL, browser paths, or a remote debugging address.**

### MemoryHub

```bash
python -m pip install -e ./MemoryHub
python MemoryHub/run_memoryhub.py test
python -I MemoryHub/run_memoryhub.py serve
```

By default, MemoryHub writes a SQLite database under `MemoryHub/data/`. Set `MEMORYHUB_DB_PATH` to an **absolute path** to relocate it. The `connect` command is intended for Jan integration and is **not needed** to run the stdio provider in other MCP hosts. MemoryHub's `forget` operation is a logical/soft deletion; historical evidence and audit records remain in local storage.

### SemanticSearch

```bash
python -m pip install -e './SemanticSearch[model]'
python -I SemanticSearch/run_semantic_search.py serve-mcp
```

On Windows PowerShell, the `pip install` command can also be run with double quotes around `./SemanticSearch[model]`.

Before indexing local files, set `SEMANTIC_SEARCH_ALLOWED_ROOTS` to the **specific directories that the service may read**. Multiple paths are separated by your platform's `os.pathsep` (semicolon on Windows, colon on Unix). The database defaults to `SemanticSearch/data/semantic_search.db` and can be redirected using `SEMANTIC_SEARCH_DB_PATH`. Optional `SEMANTIC_SEARCH_MODEL_PATH` selects a local model directory. See `SemanticSearch/src/semantic_search/config.py` for the actual environment variables and accepted dimensions.

The separate HTTP API mode (`serve-api`) requires `SEMANTIC_SEARCH_API_TOKEN` and binds to `127.0.0.1`; the stdio MCP mode does **not** require that HTTP token. Do not expose the HTTP API publicly without an independent security review.

### gallery_mcp (Pixiv)

```bash
python -m pip install -r gallery_mcp/requirements.txt
python gallery_mcp/server.py
```

The `pixiv_open_login` tool opens a separate, application-owned Google Chrome profile for interactive login. It does **not** need the user's regular Chrome profile or copied browser cookies. The session profile is stored locally and may contain authentication cookies: keep it private. Optional `PIXIV_PROXY` selects the network route; `PIXIV_DOWNLOAD_DIR` changes the download folder. Only retrieve content that you are permitted to access and save.

### JMComic

```bash
python -m pip install -r JMComic/requirements.txt
python JMComic/server.py
```

Online operations are **explicitly disabled by default** in the source. To enable them after reviewing applicable site rules and obtaining authorization, set `JMCOMIC_ONLINE_ENABLED=1` in the server process's environment. Optional `JMCOMIC_PROXY` and `JMCOMIC_CONFIG_FILE` control the proxy and local jmcomic configuration. Output goes under the component's private `data/` directory. Do not publish retrieved content, metadata containing private information, or downloads without authorization. `JMComic/requirements.lock.txt` is an environment-specific pinned package snapshot; the short `requirements.txt` is the primary basic installation list.

### Meme

```bash
python -m pip install -r Meme/requirements.txt
python Meme/meme_mcp.py
```

Copy `Meme/meme_config.example.json` to `Meme/meme_config.json`, then set `meme_dir` to an existing folder of images. The example disables random emissions (`append_probability=0.0` and `image_only_probability=0.0`); change these only if you want autonomous reaction images. Image data, local paths, and private memes must never be published accidentally. **No DeepSeek Harness-specific result is claimed for this module.**

## 4. Connect to an MCP host

These are stdio processes, not web endpoints. A generic host registration consists of an exact Python executable, the script path, command arguments, and any required environment variables. For example, the following shows **only the MemoryHub entry** in a common MCP configuration format:

```json
{
  "mcpServers": {
    "MemoryHub": {
      "command": "/absolute/path/to/python",
      "args": [
        "-I",
        "/absolute/path/to/MCP/MemoryHub/run_memoryhub.py",
        "serve"
      ]
    }
  }
}
```

Replace both absolute paths with actual paths on your machine. The example illustrates generic stdio MCP configuration; **it is not asserted to be the exact DeepSeek Harness configuration schema**. The host may have a plugin conversion/discovery flow instead. Do not invent plugin identifiers or configuration keys; use the host's documented interface and the server's actual registration output.

| Server | Process entry point and required argument |
| --- | --- |
| `gallery_mcp` | `python gallery_mcp/server.py` |
| `JMComic` | `python JMComic/server.py` |
| `MemoryHub` | `python -I MemoryHub/run_memoryhub.py serve` |
| `SemanticSearch` | `python -I SemanticSearch/run_semantic_search.py serve-mcp` |
| `WebSearch` | `python -I WebSearch/run_webmcp.py serve` |
| `Meme` | `python Meme/meme_mcp.py` |

### DeepSeek Harness: scope of the author's verification

The author has observed successful plugin conversion and tool-result output for the **five** servers named at the top of this README on the author's own host. That does not establish that a new user's DeepSeek Harness version, plugin registry, model, operating system, Python environment, browser installation, access credentials, proxy, or network will behave the same way. **Every user is responsible for deploying, configuring, securing, testing, and maintaining their own installation.** The author does not promise deployment support or successful operation in environments not tested by the author, and is not responsible for failures caused by a user's setup or external dependencies. These statements describe technical scope, not a substitute for applicable law.

## 5. Privacy and security

This release intentionally contains **no actual account cookies, model conversations, personal memory databases, user-specific browser profiles, downloaded media, populated configuration, or embedded credentials** found in the submitted source archive. It includes a `.gitignore` and safe sample configuration files, but `.gitignore` does **not** protect files already tracked by Git and does not sanitize prior commits.

- Keep `WebSearch/webmcp_config.json`, `Meme/meme_config.json`, local profiles, cookies, database files, exported evidence, logs, screenshots, and downloads **out of the repository**.
- The CLI masks a saved Clash controller secret and hides URL credentials from the displayed WebSearch configuration/status output. Nevertheless, **raw logs, debug dumps, exceptions, browser output, and third-party libraries can still reveal local paths or account data**. Inspect everything before sharing it.
- `memory_status` and several browser/file tools can expose local file paths by design. The use of a remote LLM may transmit tool outputs to a remote service. Set narrow file-allowlist roots and review MCP permissions.
- Do not provide tools with private browser sessions or unrestricted filesystem access unless needed. Approve downloads and external-site access under your own policies.
- Comply with each content provider's terms, applicable law, copyright, account restrictions, and age/access requirements. Do not attempt to bypass CAPTCHA or access controls.

See [SECURITY.md](SECURITY.md) for the publication checklist and the exact remaining limitations.

## 6. Repository layout

```text
MCP/
├── gallery_mcp/      # Pixiv MCP server
├── JMComic/          # JMComic MCP server
├── MemoryHub/        # Evidence-backed temporal memory
├── SemanticSearch/   # Embedding + FTS5 + FAISS retrieval
├── WebSearch/        # Rendered search and browser automation
├── Meme/             # Local reaction image service
├── README.md         # English documentation
├── README_zh-CN.md   # 简体中文文档
├── SECURITY.md       # Privacy and publication guidance
└── .gitignore        # Runtime data and secret exclusion
```

## 7. Status, support and licensing

The source code and CLI entry points were checked for this packaging task, but **the DeepSeek Harness runtime was not retested**. The five-server success report above is the author's own observation. Model downloads, site availability, authentication, Chrome integration, network routes and version-specific plugin behavior remain deployment-dependent.

**Provided as-is; no availability, accuracy, compatibility, or successful deployment warranty.** The author is not responsible for each user's individual deployment choices, local misconfiguration, external provider interruptions, or misuse, subject to applicable law.

**License:** The supplied archive did not include a `LICENSE` file. Publishing code on GitHub does not itself grant an open-source license. The repository owner should explicitly select and add a license before inviting reuse or redistribution.

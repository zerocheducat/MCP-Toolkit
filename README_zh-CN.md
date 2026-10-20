# MCP 工具集（MCP Tool Collection）

**面向本地 AI 助手的六个独立 Model Context Protocol（MCP）服务。** 功能涵盖网页检索与自动化、Pixiv 作品查询、JMComic 作品信息及下载、长期记忆、混合语义检索和本地表情包调用。

[English README](README.md) · [隐私与发布说明](SECURITY.md)

> **DeepSeek Harness 兼容性说明：** 作者已在自己的计算机上验证，**`gallery_mcp`、`JMComic`、`MemoryHub`、`SemanticSearch`、`WebSearch`** 可以由 DeepSeek Harness 自主发现／转换为插件并正常返回工具结果。这是**作者个人部署环境中的实测结论**，不代表在其他设备和版本上保证可用。实际结果取决于使用者的部署方式、Python 环境、Harness 版本、模型、插件管理方式、浏览器、权限和网络条件。**`Meme` 不包含在上述五项验证范围内。**

## 一、工具介绍

| 目录 | 主要功能 | 提供的 MCP 工具 |
| --- | --- | --- |
| `gallery_mcp/` | 使用独立 Chrome Profile 访问 Pixiv 作品与作者信息 | `pixiv_open_login`、`pixiv_session_status`、`pixiv_close_browser`、`pixiv_search`、`pixiv_artwork`、`pixiv_user`、`pixiv_user_works`、`pixiv_download` |
| `JMComic/` | 作品搜索、详情、封面、授权下载 | `jmcomic_search`、`jmcomic_album_detail`、`jmcomic_cover`、`jmcomic_download_album` |
| `MemoryHub/` | 基于 SQLite 的可溯源、具有时间维度的长期记忆 | `memory_remember`、`memory_recall`、`memory_manage`、`memory_status` |
| `SemanticSearch/` | EmbeddingGemma 2 + SQLite FTS5 + FAISS 混合检索 | `semantic_status`、`semantic_index_files`、`semantic_sync_memoryhub`、`semantic_search` |
| `WebSearch/` | 浏览器网页搜索、内容提取和持久化页面操作 | `web_status`、`web_search`、`web_fetch`、`browser_open`、`browser_pages`、`browser_click`、`browser_type`、`browser_scroll`、`browser_back`、`browser_extract`、`browser_screenshot`、`browser_close` |
| `Meme/` | 本地表情包索引、查询和概率式自动发送 | `list_meme_library`、`get_meme`、`maybe_send_meme` |

各工具是**相互独立的 MCP 服务**，本仓库**没有统一启动全部服务的入口**。主要通过标准输入／输出（stdio）与上层 AI 客户端通信。

## 二、环境要求

- **Python 3.10 及以上**。安装依赖与 MCP 客户端启动时应使用同一个 Python 解释器。由于各服务的依赖约束不完全相同，建议分别使用独立的虚拟环境。
- 支持 **stdio MCP** 的客户端或宿主程序。
- 浏览器工具需要可用的图形桌面与浏览器。`gallery_mcp` 明确使用系统已安装的 **Google Chrome**、独立的应用专用 Profile 和 Playwright。
- `SemanticSearch` 需要 EmbeddingGemma 2 模型及其依赖；首次加载模型可能需要下载权重及较多系统内存／显存，不承诺适用于所有硬件配置。
- 使用在线服务需要满足相应的网络、代理、账号授权及第三方站点访问条件。

以下命令默认在**仓库根目录**执行，`python` 应替换为实际提供给 MCP 客户端的 Python 解释器。不要盲目执行 `--setup`，部分安装器会创建本地配置或注册外部客户端。

## 三、安装与运行

### 1. WebSearch

```bash
python -m pip install -e ./WebSearch
python -m playwright install chromium
```

将 `WebSearch/webmcp_config.example.json` 复制为 `WebSearch/webmcp_config.json`，填写真实的网络、代理和浏览器参数；也可执行交互式配置：

```bash
python WebSearch/run_webmcp.py configure
python WebSearch/run_webmcp.py status
python -I WebSearch/run_webmcp.py serve
```

源码默认 `require_proxy=true` 且 `proxy_url` 为空，因此需要设置可用的代理链路，或者由用户明确调整配置，否则在线检索不能按预期运行。`run_webmcp.py --setup` 是可选的安装和浏览器检测流程。**不要提交 `webmcp_config.json`**，其中可能包含 Clash 控制器密钥、代理账号信息、浏览器路径或远程调试地址。

### 2. MemoryHub

```bash
python -m pip install -e ./MemoryHub
python MemoryHub/run_memoryhub.py test
python -I MemoryHub/run_memoryhub.py serve
```

数据库默认保存在 `MemoryHub/data/` 下。通过 `MEMORYHUB_DB_PATH` 环境变量可以指定**绝对路径**。`connect` 是面向 Jan 的集成指令，使用其他 MCP 客户端时不需要执行。需要特别说明：`memory_manage` 的 `forget` 是**逻辑遗忘／软删除**，原有证据和审计事件仍可能保存在本地数据库中，不等于物理擦除。

### 3. SemanticSearch

```bash
python -m pip install -e './SemanticSearch[model]'
python -I SemanticSearch/run_semantic_search.py serve-mcp
```

在 Windows PowerShell 中，也可以使用双引号包裹 `./SemanticSearch[model]`。

索引个人文件之前，必须通过 `SEMANTIC_SEARCH_ALLOWED_ROOTS` 配置允许读取的具体目录。多目录分隔符由操作系统的 `os.pathsep` 决定（Windows 使用分号，Linux/macOS 使用冒号）。默认数据库位置为 `SemanticSearch/data/semantic_search.db`；可通过 `SEMANTIC_SEARCH_DB_PATH` 指定其他位置。`SEMANTIC_SEARCH_MODEL_PATH` 可指定本地模型文件目录。具体环境变量和支持的维度以 `SemanticSearch/src/semantic_search/config.py` 为准。

独立 HTTP 接口模式 `serve-api` 需要设置 `SEMANTIC_SEARCH_API_TOKEN`，默认监听 `127.0.0.1`；普通的 stdio MCP 模式**不需要**这个 HTTP Token。未经额外安全审查，不建议将 HTTP 接口直接开放至公网。

### 4. gallery_mcp（Pixiv）

```bash
python -m pip install -r gallery_mcp/requirements.txt
python gallery_mcp/server.py
```

`pixiv_open_login` 会打开独立的 Google Chrome 用户配置目录，由使用者自行登录；工具不需要访问个人日常使用的 Chrome Profile，也不要求复制 Cookie。专用 Profile 仍可能保存登录凭据，不能上传到 GitHub。`PIXIV_PROXY` 可以配置代理；`PIXIV_DOWNLOAD_DIR` 可以指定下载目录。仅访问、保存有权使用的内容。

### 5. JMComic

```bash
python -m pip install -r JMComic/requirements.txt
python JMComic/server.py
```

**源码默认关闭在线功能。** 使用者在确认访问权限并遵守相关站点规定后，需要为服务进程配置环境变量 `JMCOMIC_ONLINE_ENABLED=1` 才能允许在线请求。`JMCOMIC_PROXY` 用于指定代理，`JMCOMIC_CONFIG_FILE` 用于指定本地 jmcomic 配置。输出文件保存在组件自身的 `data/` 目录内。不得擅自传播涉及版权或隐私的下载内容。`JMComic/requirements.lock.txt` 是特定环境的依赖快照，基础安装优先使用 `requirements.txt`。

### 6. Meme

```bash
python -m pip install -r Meme/requirements.txt
python Meme/meme_mcp.py
```

将 `Meme/meme_config.example.json` 复制为 `Meme/meme_config.json`，并修改 `meme_dir`，指向实际存在的图片文件夹。示例模板将 `append_probability` 和 `image_only_probability` 均设为 `0.0`，因此不会主动随机发送图片；如需启用应自行调整。本地表情包、图片路径和素材版权由使用者自行管理。**此组件不包含在已确认的 DeepSeek Harness 五项测试结论中。**

## 四、接入 MCP 客户端和 DeepSeek Harness

上述服务使用 stdio 进程，不是直接暴露在公网的 API。普通 MCP 客户端通常需要设置 Python 的**真实路径**、启动脚本的**真实路径**、启动参数和必要环境变量。下面仅示范常见 MCP 注册形式中的 MemoryHub：

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

注意：这里是**通用 MCP 配置示例**，**不是对 DeepSeek Harness 专用配置结构的声明**。实际部署应以所使用的 Harness 版本、插件发现机制及真实的本地路径为准，不应盲目复制示例路径或推测插件 ID。

| 工具 | 启动入口 |
| --- | --- |
| gallery_mcp | `python gallery_mcp/server.py` |
| JMComic | `python JMComic/server.py` |
| MemoryHub | `python -I MemoryHub/run_memoryhub.py serve` |
| SemanticSearch | `python -I SemanticSearch/run_semantic_search.py serve-mcp` |
| WebSearch | `python -I WebSearch/run_webmcp.py serve` |
| Meme | `python Meme/meme_mcp.py` |

### DeepSeek Harness 兼容性及责任范围

作者仅确认前述 **5 个工具**在**作者本人主机**上能够被 DeepSeek Harness 自主发现／转换为插件，并正常输出工具返回内容。这不意味着所有版本、模型、操作系统或环境均可自动完成插件化。**用户需自行负责安装、配置、权限控制、模型与工具兼容性验证、网络可达性及后续维护。** 作者不承诺对未经测试的用户环境提供部署支持，也不对因用户配置、第三方服务变更、网络或依赖问题导致的运行失败作出可用性保证。上述说明用于限定技术验证范围，不替代法律规定。

## 五、隐私保护与使用边界

本次发布版本基于所提供压缩包，经静态审查**未发现已经打包进去的真实登录 Cookie、私人聊天记录、MemoryHub 数据库、模型对话存档、浏览器 Profile、用户下载资源或明文访问令牌文件**。项目新增了 `.gitignore` 和示例配置，但忽略规则**无法自动删除已经被 Git 跟踪的文件，更不能清理旧提交历史**。

- **禁止上传** `WebSearch/webmcp_config.json`、`Meme/meme_config.json`、浏览器 Profile、Cookie、SQLite 数据库、记忆证据、截图、日志、下载文件及其他个人素材。
- 已对 WebSearch 的配置显示做了保护性调整：不再原样打印 Clash 控制器密钥，相关 URL 显示时隐藏认证信息与路径参数。但原始日志、异常、浏览器输出及第三方依赖仍可能暴露个人路径、账号信息；分享之前仍需检查。
- `memory_status`、部分浏览器／文件工具会按设计返回本地路径。若 MCP 宿主调用远程语言模型，工具结果也可能传递给远端模型服务。对个人文件应设置尽可能严格的目录白名单，并限制工具权限。
- 浏览器自动化、在线检索和资源下载需要符合访问授权、平台服务条款、著作权及当地适用法律；不得以此绕过验证码、登录限制或访问控制。

详见 [SECURITY.md](SECURITY.md)。

## 六、目录结构

```text
MCP/
├── gallery_mcp/      # Pixiv MCP
├── JMComic/          # JMComic MCP
├── MemoryHub/        # 长期记忆与证据管理
├── SemanticSearch/   # 向量 / FTS5 / FAISS 混合检索
├── WebSearch/        # 浏览器搜索及自动化
├── Meme/             # 本地表情包服务
├── README.md         # 英文文档
├── README_zh-CN.md   # 中文文档
├── SECURITY.md       # 隐私与发布指南
└── .gitignore        # 屏蔽敏感和运行时文件
```

## 七、状态、免责声明和开源协议

本轮针对源代码、启动入口、发布文件和私密信息进行了静态检查，**未重新执行 DeepSeek Harness 的运行测试**。上述五个插件在 Harness 中能够输出内容的结论来自作者自己的主机测试。模型下载、第三方服务可用性、账号授权、代理网络和环境兼容性均依赖实际部署。

**本工具集按现状提供，不保证所有环境的兼容性、可用性、结果准确性或部署成功。** 由用户自行部署、配置错误、第三方平台故障以及不当使用产生的问题，作者不作技术可用性承诺；具体责任仍以适用法律为准。

**许可证说明：** 原始压缩包未包含 `LICENSE` 文件。GitHub 公开展示不自动等于授予开源使用和再分发许可。若准备将本仓库正式作为开源项目发布，请由仓库所有者决定并添加对应许可证。

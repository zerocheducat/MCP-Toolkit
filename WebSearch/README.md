# WebSearch

MCP stdio provider for rendered browser web search, page extraction, and browser automation. Its default configuration requires a configured proxy and optionally supports a Clash controller.

## Install and run

```bash
python -m pip install -e ./WebSearch
```

Copy `webmcp_config.example.json` to `webmcp_config.json`, then supply your own network/browser settings, or use `python WebSearch/run_webmcp.py configure` from the repository root. To run the MCP provider:

```bash
python -I WebSearch/run_webmcp.py serve
```

The `--setup` launcher can also install packages, detect a browser and generate the local config/connection files. Browsers and network routes must be configured by each user. Configuration can contain proxy and controller credentials: never commit `webmcp_config.json`. The CLI masks the controller secret and URL credentials in configuration/status output; do not share raw exception logs, local configs or browser screenshots without checking them.

See [the main README](../README.md) for details.

## How to Run Mcp Server
1. Clone the repository locally.
2. Create/activate a virtual environment.
3. Install MCP deps:
```bash
pip install -r mcp_server/requirements-mcp.txt
```
4. Run server:
```bash
python mcp_server/src/server.py
```

## Claude Desktop Config Example
Replace `<ABSOLUTE_PATH_TO_REPO>` with your local repo path.

```json
{
  "RespectASO": {
    "command": "<ABSOLUTE_PATH_TO_REPO>/.venv/bin/python",
    "args": [
      "<ABSOLUTE_PATH_TO_REPO>/mcp_server/src/server.py"
    ],
    "env": {},
    "transport": "stdio",
    "type": null,
    "cwd": null,
    "timeout": null,
    "keep_alive": null,
    "description": null,
    "icon": null,
    "authentication": null
  }
}
```

Example path format:
`/Users/your-user/path/to/respectaso`
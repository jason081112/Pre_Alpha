# Codespaces 配置

`forwardPorts: [8000]` + `visibility: public` —— 服务一启动，`https://<codespace>-8000.app.github.dev`
就能直接打开，不需要 cloudflared / ngrok 之类的隧道。

启动：

```bash
cd /workspaces/Pre_Alpha
python3 -m shell.app
```

如果端口面板显示 8000 是 Private，点一下改成 Public（或 `gh codespace ports visibility 8000:public`）。

密钥不入库。需要真跑模型时：

```bash
export OPENAI_API_KEY=<key>   # ollama cloud 也用这个名字
```

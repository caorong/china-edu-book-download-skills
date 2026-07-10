# 故障排查

## `selection_required`

含义：筛选条件匹配多本教材，但没有显式 `--all`。

处理：

1. 先执行同条件的 `search --json`。
2. 展示标题、版本、册次和 ID。
3. 用用户选定的 `--id` 下载。
4. 仅当用户明确说“全部/全套/都要”时使用 `--all`。

## `no_match`

按下面顺序放宽条件，每次只放宽一个：

1. 去掉 `--volume`。
2. 把完整版本名缩短为出版社关键词，如 `人教版（PEP）` 改为 `人教`。
3. 去掉 `--version`，保留学段、年级和学科。
4. 使用 `--query` 搜标题或出版社。
5. `--refresh` 重新同步索引。

不要自动切换成用户没有要求的版本。

## `auth_required`

含义：详情或 PDF 要求用户自己的有效登录态，或者服务器返回了登录 HTML 而不是 PDF。

处理：

```bash
export SMARTEDU_ACCESS_TOKEN='...'
export SMARTEDU_COOKIE='...'
python3 scripts/china_edu_book.py download --id RESOURCE_ID --json
```

或：

```bash
python3 scripts/china_edu_book.py \
  --browser-state /secure/path/smartedu-state.json \
  doctor --id RESOURCE_ID --probe --json
```

注意：

- 不要把 token/Cookie 写进 README、SKILL.md、脚本、Git 提交或公开对话。
- storage-state 通常含完整会话 Cookie，应设置严格文件权限并在使用后妥善删除。
- 不绕过验证码、登录、限流、访问授权或其他技术措施。

## `not_pdf` / `invalid_pdf`

可能原因：

- 登录态已过期，服务器返回登录页。
- 平台资源正在转码或暂时不可用。
- 详情 JSON 仍指向旧 storage。
- 代理/WAF 返回 HTML 错误页。

建议：

1. 运行 `doctor --id RESOURCE_ID --probe --json`。
2. 重新登录并更新 token/storage-state。
3. 稍后重试，不要高并发轰炸。
4. 打开输出中的 `detail_page` 确认该资源在浏览器内是否可访问。

## `index_missing`

联网同步：

```bash
python3 scripts/china_edu_book.py sync --json
```

若使用 `--offline`，必须先在同一个 `--cache-dir` 中生成索引。

## 索引刷新失败但仍有旧缓存

脚本会在 stderr 输出 warning 并继续使用旧索引。JSON 中的 `synced_at` 可用于判断缓存时间。需要强一致时，单独运行 `sync` 并检查退出码。

## 企业代理或证书错误

本脚本使用系统 Python 的 TLS/代理配置，不提供 `--insecure`。正确做法是配置系统信任链或组织代理证书，不要关闭证书校验。

## Windows 文件名问题

脚本会替换 `<>:"/\\|?*`、控制字符和 `CON/PRN/AUX/NUL/COM*/LPT*` 等保留名。路径过长时可使用：

```bash
python3 scripts/china_edu_book.py download --id RESOURCE_ID --flat -o C:\教材 --json
```

## 下载数量被拒绝

`too_many_matches` 是防误操作机制。先 `--dry-run` 查看范围。只有用户明确同意后再提高：

```bash
python3 scripts/china_edu_book.py download \
  --stage 小学 --subject 数学 \
  --all --max-books 50 --dry-run --json
```

确认后去掉 `--dry-run`。

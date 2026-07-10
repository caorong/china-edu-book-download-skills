# China Edu Book Download Skill

一个面向 AI Agent 的中国中小学教材搜索与下载 skill，数据源为国家中小学智慧教育平台。

它受到 [MrWillCom/textbooksDownloader](https://github.com/MrWillCom/textbooksDownloader) 的启发：原项目通过 Playwright 选择学段、学科和版本，从教材详情页的 `contentId` 推导 PDF 地址。本项目保留“官方书目 -> 精确资源 ID -> PDF”的核心思路，但改为使用官方公开书目索引和资源详情 JSON，避免依赖易变化的页面选择器；浏览器状态仅在资源需要用户登录态时作为可选鉴权输入。

## 特性

- 通用 `SKILL.md` 目录结构，可放入支持 Agent Skills 的运行环境。
- 纯 Python 标准库，Python 3.10+，无需 `pip install`。
- 按学段、年级、学科、版本、册次和关键词筛选。
- 支持资源 ID、SmartEdu 详情页 URL、官方 PDF URL。
- 多匹配时拒绝含糊下载，批量下载必须显式 `--all`。
- 支持 SmartEdu access token、Cookie、Playwright storage-state（含当前 `ND_UC_AUTH*` 嵌套格式），并自动生成私有 NDR 所需的认证头。
- 官方域名白名单、HTTPS 限制、秘密脱敏。
- PDF 文件头验证、临时文件原子落盘、SHA-256 摘要。
- 默认低并发和单次数量上限。
- JSON 输出，方便 Agent 编排。

## 目录

```text
.
├── SKILL.md
├── README.md
├── LICENSE
├── THIRD_PARTY_NOTICES.md
├── manifest.txt
├── references/
│   ├── platform.md
│   └── troubleshooting.md
├── scripts/
│   ├── __init__.py
│   └── china_edu_book.py
└── tests/
    └── test_china_edu_book.py
```

## 快速开始

查看帮助：

```bash
python3 scripts/china_edu_book.py --help
```

同步书目：

```bash
python3 scripts/china_edu_book.py sync --json
```

搜索人教版小学三年级数学上册：

```bash
python3 scripts/china_edu_book.py search \
  --stage 小学 \
  --grade 三年级 \
  --subject 数学 \
  --version 人教版 \
  --volume 上册 \
  --json
```

按精确 ID 下载：

```bash
python3 scripts/china_edu_book.py download \
  --id RESOURCE_ID \
  --output-dir ./教材 \
  --json
```

在部分资源要求登录态时：

```bash
export SMARTEDU_ACCESS_TOKEN='your-own-token'
python3 scripts/china_edu_book.py download --id RESOURCE_ID --json
```

或使用用户自己的 Playwright storage-state：

```bash
python3 scripts/china_edu_book.py \
  --browser-state /secure/path/smartedu-state.json \
  download --id RESOURCE_ID --json
```

## Agent 使用方式

把整个仓库目录放进 Agent 的 skills 搜索路径。Agent 应读取根目录 `SKILL.md`，按“先搜索、再消歧、最后按 ID 下载”的流程执行。

不建议让 Agent 直接运行模糊的全量命令。下面的命令会因为匹配不唯一而返回 `selection_required`，这是刻意的安全设计：

```bash
python3 scripts/china_edu_book.py download --subject 数学 --json
```

只有用户明确要求全部时才使用：

```bash
python3 scripts/china_edu_book.py download \
  --stage 初中 --grade 七年级 --subject 数学 \
  --all --max-books 10 --json
```

## 命令

### `sync`

下载并缓存官方教材元数据。

```bash
python3 scripts/china_edu_book.py sync [--keep-raw] [--json]
```

### `search`

```bash
python3 scripts/china_edu_book.py search \
  [--stage 小学] [--grade 三年级] [--subject 数学] \
  [--version 人教版] [--volume 上册] [--query 关键词] \
  [--refresh] [--offline] [--limit 20] [--json]
```

### `download`

```bash
python3 scripts/china_edu_book.py download \
  [--id RESOURCE_ID ...] [--url OFFICIAL_URL ...] \
  [筛选条件] [--all] [--max-books 20] \
  [--output-dir 教材] [--flat] [--workers 1] \
  [--overwrite] [--dry-run] [--json]
```

### `doctor`

```bash
python3 scripts/china_edu_book.py doctor [--id RESOURCE_ID | --url URL] [--probe] [--json]
```

## 环境变量

| 变量 | 用途 |
|---|---|
| `SMARTEDU_ACCESS_TOKEN` | 用户自己的 SmartEdu access token |
| `SMARTEDU_COOKIE` | 用户自己的 Cookie 字符串，可选 |
| `SMARTEDU_BROWSER_STATE` | Playwright storage-state JSON 路径 |
| `SMARTEDU_EXTRA_HEADERS_JSON` | 可选附加请求头 JSON，不要提交到仓库 |
| `CHINA_EDU_BOOK_CACHE` | 自定义索引缓存目录 |

脚本不会把上述秘密写入索引、下载清单或 JSON 输出。仍应避免在共享终端、CI 日志或 shell history 中暴露秘密。

## 下载输出

默认目录：

```text
教材/学段/年级/学科/版本/册次/标题_资源ID前8位.pdf
```

每条成功结果含本地路径、文件大小、SHA-256 和官方详情页。已有且通过 PDF 头校验的文件默认跳过；使用 `--overwrite` 强制覆盖。

## 测试

所有测试均为离线测试，不请求 SmartEdu：

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall -q scripts tests
```

当前包含 18 项单元测试，覆盖索引归一化、筛选别名、候选优先级、鉴权头、输出路径和批量下载保护等关键逻辑。

## 设计说明

技术细节见 [`references/platform.md`](references/platform.md)，常见问题见 [`references/troubleshooting.md`](references/troubleshooting.md)。

## 合规说明

本仓库只提供访问用户有权访问的官方资源的自动化工具，不包含教材文件，也不提供登录绕过、验证码规避、DRM 破解或公开镜像功能。请遵守平台服务条款、版权规则、学校/机构政策和所在地法律。

## License

MIT。原项目归属和许可说明见 [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md)。

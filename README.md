# China Edu Book Download Skill

一个可供智能体调用、也可直接在命令行使用的中国中小学电子教材下载 Skill。它从[国家中小学智慧教育平台](https://basic.smartedu.cn/elecEdu)检索教材，提取教材详情页中的 `contentId`，查询当前资源信息 JSON，并下载用户有权访问的 PDF。

本项目基于 [MrWillCom/textbooksDownloader](https://github.com/MrWillCom/textbooksDownloader) 的思路重写。原项目证明了“Playwright 筛选目录 → 从详情页读取 `contentId` → 拼接 PDF 地址”的可行路径；本仓库把它整理成了适合 Agent Skill 使用的非交互式 CLI，并增加了清单、精确选择、重试、PDF 校验、测试和 CI。

## 特性

- 按学段、学科、教材版本检索
- 按年级、册次等标题关键词过滤
- 先保存 JSON 清单，再按序号或标题下载
- 支持教材详情页 URL / `contentId` 直接下载
- 默认无头浏览器，可用 `--headful` 排查页面变化
- 优先解析当前 `tch_material/details/{contentId}.json` 资源信息
- 自动尝试资源信息中的源文件地址和多个官方 CDN 回退地址
- 写入前检查 PDF 响应，使用临时文件避免留下半截文件
- 并发限制、超时、重试和覆盖策略可配置
- 多结果时必须显式选择，避免误下载整批教材

## 环境要求

- Node.js 20+
- 可访问国家中小学智慧教育平台及其教材 CDN

## 安装

```bash
git clone https://github.com/caorong/china-edu-book-download-skills.git
cd china-edu-book-download-skills
npm install
npx playwright install chromium
```

作为 Agent Skill 使用时，将整个仓库目录放入支持 `SKILL.md` 的技能目录即可；`SKILL.md` 是智能体入口，`scripts/china-textbook-downloader.mjs` 是执行入口。

## 登录授权（可选）

平台上的部分非最新版教材可能要求已登录账号。CLI 不绕过登录，只支持使用用户本人已有的 Access Token：

```bash
export SMARTEDU_ACCESS_TOKEN='你的 Access Token'
node scripts/china-textbook-downloader.mjs from-url '<教材详情页 URL>' --out books
```

也可以从权限受限的本地文件读取：

```bash
node scripts/china-textbook-downloader.mjs from-url '<教材详情页 URL>' \
  --access-token-file ~/.config/smartedu/token \
  --out books
```

Access Token 属于敏感凭据：不要贴进 issue、聊天、命令历史截图或仓库。程序不会把 token 写入 manifest 或 JSON 输出，也会对输出中的相关查询参数做脱敏。

## 快速开始

### 先查询

```bash
node scripts/china-textbook-downloader.mjs search \
  --period 小学 \
  --subject 数学 \
  --version 人教版 \
  --title 五年级 \
  --save books.json
```

输出类似：

```text
  1. 义务教育教科书 数学 五年级上册
     contentId: ...
     https://basic.smartedu.cn/tchMaterial/detail?...
```

### 再下载

```bash
node scripts/china-textbook-downloader.mjs download \
  --manifest books.json \
  --index 1 \
  --out books
```

序号支持逗号和范围：

```bash
node scripts/china-textbook-downloader.mjs download \
  --manifest books.json \
  --index 1,3-5 \
  --out books
```

### 直接通过教材链接下载

```bash
node scripts/china-textbook-downloader.mjs from-url \
  'https://basic.smartedu.cn/tchMaterial/detail?contentType=assets_document&contentId=b8e9a3fe-dae7-49c0-86cb-d146f883fd8e&catalogType=tchMaterial' \
  --title '教材名称' \
  --out books
```

### 给智能体使用 JSON

```bash
node scripts/china-textbook-downloader.mjs search \
  --period 高中 --subject 数学 --version 沪教版 \
  --save /tmp/books.json --json

node scripts/china-textbook-downloader.mjs download \
  --manifest /tmp/books.json --index 2 --out ./books --json
```

stdout 只输出 JSON；页面和下载进度输出到 stderr。

## 命令概览

```text
search      检索并输出教材清单
download    从实时检索结果或 manifest 中选择并下载
from-url    通过详情页 URL 或 contentId 直接下载
```

完整参数：

```bash
node scripts/china-textbook-downloader.mjs --help
```

## 与原项目的主要差异

| 项目 | 原项目 | 本项目 |
|---|---|---|
| 筛选配置 | 修改源码中的常量 | CLI 参数 |
| 运行模式 | 固定显示浏览器 | 默认无头，可选 `--headful` |
| 选择范围 | 下载当前筛选下全部卡片 | 先清单，再按标题/序号选择 |
| 下载完成 | Promise 未统一等待 | 等待全部任务并返回结构化结果 |
| 资源解析 | 固定拼接旧版 PDF 地址 | 当前资源信息 API + 源文件地址 + 旧版 CDN 回退 |
| 文件安全 | 直接写入 | `.part` 临时文件 + PDF 类型校验 |
| 智能体入口 | 无 | `SKILL.md` |
| 测试/CI | 无 | Node 单元测试 + GitHub Actions |

## 开发

```bash
npm test
npm run check
```

测试不启动浏览器，也不下载教材；它只验证 URL、清单、选择和文件名等纯逻辑。平台页面属于外部动态依赖，真实检索建议在本机按需进行。

## 限制

国家中小学智慧教育平台可能调整页面结构、筛选名称、资源信息字段或授权规则。CLI 对旧版“点击封面打开详情页”和新版“详情链接”都做了兼容，并同时支持当前资源信息 API 与旧版 PDF 地址；页面或接口大改后仍可能需要更新。遇到问题请先运行 `--headful`，并参考 [`references/troubleshooting.md`](references/troubleshooting.md)。

## 版权与使用说明

程序只访问平台公开提供或用户本人账号获权访问的资源，不绕过登录、付费墙或技术保护。教材内容的著作权及相关权利归原权利人所有；能够通过公开 URL 下载并不等于获得再分发、商用或建立镜像的授权。请遵守平台条款和当地法律，仅在有权使用的范围内下载。

## License

MIT。原项目的版权声明和本项目的修改版权声明均保留在 [`LICENSE`](LICENSE) 与 [`NOTICE`](NOTICE) 中。

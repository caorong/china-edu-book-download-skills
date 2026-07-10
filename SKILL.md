---
name: china-edu-book-download
description: 搜索、筛选并下载国家中小学智慧教育平台上的中国中小学电子教材 PDF。适用于用户要求查找、列出或下载小学、初中、高中教材，并按学段、年级、学科、教材版本、册次、资源 ID 或详情页 URL 精确定位；优先使用官方公开书目索引，下载受限文件时只使用用户自己的 SmartEdu 登录态。
---

# 中国教材下载 Skill

使用 `scripts/china_edu_book.py` 搜索和下载国家中小学智慧教育平台的电子教材。脚本只访问预设的 SmartEdu/YKT 官方域名，使用 Python 标准库，无需安装第三方包。

## 核心原则

1. **先搜索，后下载。** 除非用户给出精确资源 ID 或详情页 URL，否则先列出候选。
2. **不要猜版本或册次。** “人教版三年级数学”通常仍可能匹配上下册或多个版本，先让用户选定。
3. **不要擅自批量下载。** 多结果下载必须由用户明确说“全部/全套/都要”，并显式使用 `--all`。
4. **只用用户自己的登录态。** 遇到 `auth_required` 时，使用用户提供的环境变量或浏览器 storage-state；绝不绕过登录、验证码、访问控制或平台限制。
5. **不泄露秘密。** 不在回复、日志、命令历史、仓库文件或下载清单中打印 access token、Cookie、Authorization 头。
6. **只交付验证过的 PDF。** 脚本会检查 PDF 文件头、采用 `.part` 临时文件原子落盘，并计算 SHA-256。

## 入口

在本 skill 根目录运行：

```bash
python3 scripts/china_edu_book.py --help
```

Agent 调用时优先加 `--json`，便于稳定解析结果。

## 标准工作流

### 1. 从用户请求提取条件

尽量识别以下槽位：

- 学段：小学、初中、高中
- 年级：一年级至九年级、高一、高二、高三
- 学科：语文、数学、英语、物理、化学、历史等
- 版本/出版社：人教版、北师大版、苏教版、沪教版等
- 册次：上册、下册、全一册、必修一、选择性必修等
- 额外关键词：书名、主编、出版社或其他标签

脚本支持常用年级别名，如 `初一 -> 七年级`、`高一 -> 高一年级`。

### 2. 搜索候选

```bash
python3 scripts/china_edu_book.py search \
  --stage 小学 \
  --grade 三年级 \
  --subject 数学 \
  --version 人教版 \
  --volume 上册 \
  --limit 20 \
  --json
```

只知道一部分条件时也可以搜索：

```bash
python3 scripts/china_edu_book.py search \
  --stage 高中 \
  --subject 物理 \
  --query 必修 \
  --json
```

输出中的关键字段：

- `matched`：全部匹配数
- `returned`：本次返回数
- `books[].id`：后续下载使用的精确资源 ID
- `books[].detail_page`：可向用户展示的官方详情页
- `books[].stage/grade/subject/version/volume`：用于消歧

### 3. 决定是否需要用户选择

- 只有一个明确匹配：可以继续下载。
- 有多个匹配：展示精简候选，要求用户按标题、册次或 ID 选择。
- 用户明确要求全部：先告知匹配数量，再使用 `--all`。
- 匹配数量很大：不要直接下载；继续缩小条件，或让用户明确提高批量上限。

脚本自身也会拦截模糊下载：多结果但没有 `--all` 时返回 `selection_required`。

### 4. 用精确 ID 下载

```bash
python3 scripts/china_edu_book.py download \
  --id 5cd7e623-5c38-4602-871a-3fba8a551db2 \
  --output-dir ./教材 \
  --json
```

也可以直接传官方详情页：

```bash
python3 scripts/china_edu_book.py download \
  --url 'https://basic.smartedu.cn/tchMaterial/detail?contentType=assets_document&contentId=RESOURCE_ID&catalogType=tchMaterial&subCatalog=tchMaterial' \
  --output-dir ./教材 \
  --json
```

用户明确要求下载所有匹配结果时：

```bash
python3 scripts/china_edu_book.py download \
  --stage 小学 \
  --grade 三年级 \
  --subject 数学 \
  --version 人教版 \
  --all \
  --max-books 10 \
  --output-dir ./教材 \
  --json
```

先预演而不下载：

```bash
python3 scripts/china_edu_book.py download \
  --stage 小学 --grade 三年级 --subject 数学 \
  --all --dry-run --json
```

### 5. 检查下载结果

读取 JSON 摘要：

- `downloaded`：新下载数量
- `skipped_existing`：已有且通过 PDF 校验的数量
- `failed`：失败数量
- `results[].path`：本地 PDF 路径
- `results[].sha256`：文件摘要
- `results[].detail_page`：官方详情页
- `results[].error_code`：失败类型

最终回复应报告成功/失败数量、本地路径和必要的失败原因。不要暴露内部私有下载 URL 或鉴权信息。

## 鉴权

公开书目索引通常不需要登录。部分完整 PDF 会要求当前用户自己的 SmartEdu 登录态。

优先使用环境变量：

```bash
export SMARTEDU_ACCESS_TOKEN='用户自己的令牌'
export SMARTEDU_COOKIE='用户自己的 Cookie；可选'
python3 scripts/china_edu_book.py download --id RESOURCE_ID --json
```

不要把令牌直接写进命令参数。需要复用用户已登录的 Playwright storage-state 时，把全局参数放在子命令之前：

```bash
python3 scripts/china_edu_book.py \
  --browser-state /secure/path/smartedu-state.json \
  download --id RESOURCE_ID --json
```

也可通过 `SMARTEDU_BROWSER_STATE` 指向该文件。脚本能识别直接 `accessToken` 和当前 `ND_UC_AUTH*` 嵌套结构。storage-state 含敏感 Cookie，应限制文件权限，不要提交 Git。

脚本会根据用户自己的 access token 自动发送 `Authorization`、`accessToken` 与 SmartEdu 私有 NDR 使用的 `X-ND-AUTH` 头；Agent 不应自行拼接、展示或记录这些头。

遇到 `auth_required`：

1. 说明该资源要求用户自己的有效登录态。
2. 建议用户在本机设置环境变量或提供本地 storage-state 路径。
3. 不要求用户把 token 粘贴进公开对话；不尝试绕过限制。

## 诊断

检查公开索引：

```bash
python3 scripts/china_edu_book.py doctor --json
```

检查某本教材的详情和 PDF 前几 KB：

```bash
python3 scripts/china_edu_book.py doctor \
  --id RESOURCE_ID \
  --probe \
  --json
```

## 索引缓存

默认索引位于：

- Linux/macOS：`~/.cache/china-edu-book-download/index.json`
- Windows：`%LOCALAPPDATA%/china-edu-book-download/index.json`

强制刷新：

```bash
python3 scripts/china_edu_book.py search --refresh --subject 数学 --json
```

仅使用已有索引：

```bash
python3 scripts/china_edu_book.py search --offline --subject 数学 --json
```

可通过全局 `--cache-dir` 或环境变量 `CHINA_EDU_BOOK_CACHE` 修改缓存目录。

## 输出目录

默认按以下结构保存：

```text
教材/
  学段/
    年级/
      学科/
        版本/
          册次/
            标题_资源ID前8位.pdf
```

用户要求单一目录时使用 `--flat`。

## 错误处理

- `selection_required`：结果不唯一，列出候选让用户选择，或用户明确要求全部后使用 `--all`。
- `no_match`：放宽一个筛选条件后重新搜索；不要自行换成不相关版本。
- `too_many_matches`：继续缩小条件，或仅在用户明确同意时提高 `--max-books`。
- `auth_required`：需要用户自己的登录态；不要绕过。
- `not_pdf` / `invalid_pdf`：响应不是有效 PDF，保留官方详情页并报告失败。
- `index_missing`：联网执行 `sync`；若用户要求离线，则说明本地尚无索引。

更多细节见 `references/troubleshooting.md`。

## 访问与版权边界

- 仅用于用户有权访问的官方教材资源。
- 尊重平台服务条款、版权、学校或机构政策及所在地法律。
- 不破解 DRM，不规避登录、验证码、限流或其他访问控制。
- 不把下载到的教材自动上传、公开镜像或重新分发。
- 默认并发为 1，最大限制为 4，避免给官方服务造成不必要负载。

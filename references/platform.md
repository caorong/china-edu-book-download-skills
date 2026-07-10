# SmartEdu 教材数据链路

本文记录 skill 当前依赖的数据形态，方便平台改版时维护。

## 1. 原项目思路

`MrWillCom/textbooksDownloader` 的 2022 年实现使用 Playwright 打开 `https://www.zxx.edu.cn/elecEdu`，按学段、学科、版本点击页面选项，再点击教材封面。详情页 URL 中带有 `contentId`，程序据此构造：

```text
https://r1-ndr.ykt.cbern.com.cn/edu_product/esp/assets_document/{contentId}.pkg/pdf.pdf
```

这条思路很直接，但依赖页面结构、选择器、弹出页数量和旧入口地址。

## 2. 本 skill 的实现

本 skill 把流程拆成四层：

```text
公开版本清单
  -> 教材元数据分片
    -> 精确资源 ID
      -> 资源详情 JSON / 旧式 ID 地址回退
        -> 用户有权访问的 PDF
```

### 公开版本清单

```text
https://s-file-2.ykt.cbern.com.cn/zxx/ndrs/resources/tch_material/version/data_version.json
```

脚本也会尝试 `s-file-1` 镜像。返回值中的 `urls` 指向一个或多个教材元数据分片。

### 标签树

```text
https://s-file-1.ykt.cbern.com.cn/zxx/ndrs/tags/tch_material_tag.json
```

标签树目前仅作同步诊断和可选原始数据留存；筛选字段主要从每条教材记录的 `tag_list` 提取。

### 详情 JSON

```text
https://s-file-1.ykt.cbern.com.cn/zxx/ndrv2/resources/tch_material/details/{id}.json
https://s-file-2.ykt.cbern.com.cn/zxx/ndrv2/resources/tch_material/details/{id}.json
```

脚本优先读取详情中的 PDF storage 信息；当详情含多个 PDF 项时，优先选择 `ti_is_source_file=true` 或 `ti_file_flag=source` 的原始文件。当前兼容：

- `ti_items[].ti_storages`
- `ti_items[].ti_storage`
- `cs_path:${ref-path}...`
- 私有与公开 `r1/r2/r3-ndr` 域名
- 详情 JSON 中其他明确的 `.pdf` / `assets_document` 字符串

如果详情暂不可用，脚本会把原项目的 `{resource_id}.pkg/pdf.pdf` 规则作为最后回退。

## 3. 鉴权变化

书目索引通常是公开静态 JSON。完整教材 PDF 可能由私有 NDR 地址提供，并要求当前用户自己的账号态。脚本支持：

- `Authorization: Bearer ...`
- `accessToken: ...`
- `X-ND-AUTH: MAC id="...",nonce="0",mac="0"`
- `accessToken` 查询参数回退
- 用户自己的 Cookie
- Playwright storage-state 中属于 SmartEdu/YKT 的 Cookie、直接 `accessToken`，以及当前 `ND_UC_AUTH* -> value -> access_token` 嵌套结构

脚本不负责创建、破解或伪造登录态。

## 4. 字段归一化

常用 tag dimension：

| dimension | 归一化字段 |
|---|---|
| `zxxxd` | `stage` 学段 |
| `zxxxk` | `subject` 学科 |
| `zxxbb` | `version` 版本 |
| `zxxnj` | `grade` 年级 |
| `zxxcc` | `volume` 册次 |

平台字段缺失时，脚本会根据常见标签名称做保守推断。原始标签仍保留在索引的 `tags` 字段中。

## 5. 安全边界

脚本把不同 URL 分成两类白名单：

- 元数据：`s-file-*.ykt.cbern.com.cn`
- PDF 资源：`r1/r2/r3-ndr.ykt.cbern.com.cn` 与 `r1/r2/r3-ndr-private.ykt.cbern.com.cn`

只允许 HTTPS。元数据或详情中即便出现第三方 URL，也不会被脚本下载。这可以降低上游数据异常或提示注入导致任意网络访问的风险。

## 6. 平台改版时的维护顺序

1. 运行 `doctor --json`，确认版本清单是否可访问。
2. 检查版本清单的 `urls` 字段是否仍为字符串或数组。
3. 用一个已知资源 ID 运行 `doctor --id ... --probe --json`。
4. 若详情结构变化，更新 `extract_pdf_candidates()`，保留官方域名白名单。
5. 更新离线 fixture/单元测试，再做受控在线验证。
6. 不要以关闭 TLS 校验、放宽到任意域名或绕过账号态作为“修复”。

# 油脂油料期货价格季节性看板（可每日自动更新）

> ⚠️ **这不是一个"死"的静态网页。** 本项目是一个**可每日自动刷新的工程**：推到 GitHub 后，由 GitHub Actions 在云端每天 15:25（北京时间）自动抓取最新收盘数据、重新生成 `油脂油料期货季节性看板.html` 并写回仓库。任何人打开仓库里的 HTML（或 GitHub Pages）看到的都是当天最新数据，无需手动重跑。

## 项目内容
| 文件 | 说明 |
|------|------|
| `油脂油料期货季节性看板.html` | 看板主页（双击即可离线打开；ECharts 已内嵌，无需联网） |
| `build_dashboard.py` | 构建脚本：抓取新浪期货日线 → 计算 → 生成 HTML。纯 Python 标准库，**无需 pip 安装任何依赖** |
| `echarts.min.js` | 图表库（脚本优先用本地这份，离线可用） |
| `.github/workflows/daily.yml` | GitHub Actions：每日 15:25 自动更新（核心） |
| `cache/futures_raw.json` | 原始合约收盘价缓存（每日增量更新，避免重复全量抓取） |
| `油脂油料期货季节性看板-项目交接与对话记录.html` | 历次修改意见与验收要点汇总（离线可读） |

## 品种与界面
- 品种：豆粕(m) / 豆油(y) / 豆二(b) / 棕榈油(p) / 菜油(OI) / 菜粕(RM)
- 三个一级界面（点击才展开二级标签）：
  1. **绝对价格季节性** — 各品种全部有数据的月份合约（01–12，剔除无交易的空合约）
  2. **月差季节性** — 各品种指定配对（如豆粕 1-3 / 7-9 / 9-1 等，详见脚本 `SPREAD_PAIRS`）
  3. **品种间价差季节性** — 豆粕−菜粕、豆油−菜油、菜油−棕榈油、豆油−棕榈油、内盘大豆榨利(78.5豆粕+18.5豆油−100豆二)
- 横轴为**公历**，按各届合约**实际交易区间**对齐（非强制 1–12 月），跨年连续
- 图例：每届合约单独可点选，命名为合约代码（如 `M2701`、`Y2609-RM2609`）
- 无历届均值线；当前在市一届为红色粗线

## 怎么部署到 GitHub（交给另一台 WorkBuddy 操作）
把本目录（含隐藏的 `.git`）解压后，在终端执行：

```bash
# 若解压目录里已有 .git（本包已初始化好），跳过 git init
git remote add origin https://github.com/<你的用户名>/<仓库名>.git
git push -u origin main
```

推送成功后：
- 进入仓库 **Settings → Actions → General**，确认 `Workflow permissions` 为 **Read and write**（否则无法写回仓库）。
- 工作流首次会在每天 15:25 自动触发；也可在仓库 **Actions** 页点 **Run workflow** 立即试跑一次验证。
- 若想用 GitHub Pages 在线看：仓库 **Settings → Pages → Source** 选 `main` 分支根目录，稍等即可获得一个网址。

## ⚠️ 重要风险与备选
1. **GitHub 美国服务器能否访问新浪接口**：`stock2.finance.sina.com.cn` 在国内可用；GitHub 的 ubuntu 运行器在境外，个别情况下可能被限或偶发超时。脚本已对单合约重试 3 次。若某天 Actions 标红（抓取失败），看板不会损坏（只是当天未更新）；解决：**改用 self-hosted runner（一台国内常开机器）**，或在本机用 WorkBuddy 自动化 15:25 生成后 push。
2. **定时精度**：GitHub 说明 scheduled workflow 可能延迟（通常几分钟内，高负载时偶发更久）。若严格要求 15:30 前完成，建议叠加一台国内机器的 WorkBuddy 自动化（见下）兜底。
3. **本地/其他机器手动更新**：装好 Python 3.11+，在目录里直接 `python build_dashboard.py` 即可，无需任何第三方包。

## 想继续修改规则
所有配对、品种、界面都在 `build_dashboard.py` 顶部常量里（`SPREAD_PAIRS`、`INTER_GROUPS`、`PRODUCTS` 等）。改完重跑脚本，HTML 即更新；GitHub Actions 次日也会自动带上新规则。

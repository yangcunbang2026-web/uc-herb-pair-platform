# 中药药对协同潜力筛选平台

面向科研人员的网络药理学证据看板。首页可输入疾病和候选中药，系统按“疾病靶点 → 成分靶点 → 交集 → 药对初筛 → STRING/Cytoscape → DAVID → Top10 → Top3分子对接”运行，并保存每一步的原始文件、参数、日志和失败原因。

本地网页：<http://localhost:8510>

已部署的历史公开版：<https://uc-herb-pair-platform.streamlit.app>（重新发布前可能落后于本地版本）

## 已验证基准任务（2026-09-27）

- 疾病：溃疡性结肠炎（UC）。
- 候选：24味中药，不限制药食同源。
- 药对：276组；153组通过双方独立贡献门槛。
- 疾病交集靶点：93个。
- 全部合格药对与全部单药基线已完成PPI和DAVID分析。
- 已输出Top10协同潜力顺序。
- Top3真实AutoDock Vina对接已完成，结合能为−8.142～−5.935 kcal/mol。
- PTGS2/5IKR共晶配体回对接RMSD为1.7969 Å，通过≤2.0 Å质控。

网页的分子对接详情可查看：原始排名、最终成功药对、实际采用的成分和靶点、PDB与PubChem编号、回对接RMSD、全部尝试、失败原因、后备靶点队列和原始文件。

## 通用任务入口

首页输入疾病中文名、英文标准名和候选中药后，可创建隔离任务：

- Open Targets官方API：全自动获取疾病靶点，不需要登录。
- GeneCards：官方没有公开OAuth且禁止自动抓取，因此采用“人工登录导出后上传”的合规模式。
- TCMSP：自动抓取成分和靶点；缺失药材不会被伪装成“没有成分”，而是明确报出证据缺口。
- HERB 2.0 / ETCM 2.0：读取完整活性靶点后，重新与本次疾病取交集，不复用UC专属交集。
- DAVID、STRING、Cytoscape：按任务目录保存输入、响应和结果。
- 分子对接：优先使用已验证结构；陌生靶点自动通过UniProt和RCSB寻找实验结构、共晶配体和Grid Box，并先做回对接。

通用结构发现已用注册表外的TNF实测：系统自动尝试多个PDB，前两项受体准备失败、6X82回对接RMSD 3.6635 Å未通过，随后切换到6X81并以RMSD 1.1185 Å通过。失败记录被保留，没有强行判定为成功。

## 结果边界

- 系统输出的是实验前“协同潜力排名”，不是临床药方。
- 靶点、PPI、富集和分子对接不能单独证明“1+1＞2”。
- 真正协同仍需单药A、单药B、联合AB及正式协同模型实验确认。
- 任意新任务都可能因数据库未收录、官方接口暂时不可用、缺少合格PDB结构或回对接不通过而停止；系统应报告明确终态，不伪造Top3。

## 项目结构

```text
app.py / src/                    网页、任务创建和证据展示
config/tasks/                    每个任务的冻结输入与流水线配置
data/formal_inputs/              数据库导出和标准化输入
data/formal_analysis/            交集、PPI、DAVID、排名和对接结果
data/channel_tests/              数据渠道与通用结构发现测试证据
runs/                            步骤状态、日志、报告、快照和哈希
scripts/                         可复现科研流水线
tools/vina/                      本地AutoDock Vina执行文件
```

## 本地启动

```powershell
.\.venv\Scripts\python.exe -m streamlit run app.py --server.address 127.0.0.1 --server.port 8510
```

## 测试

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

当前共38项自动测试，覆盖任务隔离、疾病文件参数化、评分边界、漏斗、网页章节、Top3对接结果和证据文件。

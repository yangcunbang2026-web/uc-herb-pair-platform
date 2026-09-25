# UC 药食同源药对筛选平台

面向团队内部科研使用的 UC 药食同源药对协同潜力筛选原型。

## 第一版范围

- 疾病范围：仅溃疡性结肠炎（UC）
- 药材范围：国家正式药食同源目录内药材
- 组合范围：两味药组合
- 评分权重：靶点互补度 40%、通路协同度 30%、安全性 15%、配伍依据 15%
- 输出：协同潜力排名前 10 名及 CSV 下载
- 边界：结果属于科研预测，不代表临床处方或已经证实的协同疗效

## 项目结构

```text
app.py                  结果展示网页
config/scoring.json     已确认的评分权重
data/raw/               外部数据库原始文件，只追加、不手改
data/processed/         标准化后的中间数据
data/uc_herbs.sqlite    本地结构化数据库（运行初始化脚本后生成）
exports/                排名结果导出目录
scripts/init_database.py 数据库初始化脚本
src/database.py         数据库连接和查询
```

## 本地启动

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe scripts/init_database.py
.\.venv\Scripts\python.exe -m streamlit run app.py --server.port 8511
```

打开 <http://localhost:8511>。

线下重新执行生信、分子对接等研究脚本时，安装完整依赖：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-research.txt
```

## 当前真实数据状态（2026-09-25）

- 已接入 NCBI GEO GSE75214：活动期 UC 结肠 74 例、健康结肠 11 例
- 已完成 19,036 个基因的差异分析
- 已写入 1,150 个显著差异基因（FDR < 0.05 且 |log2FC| > 1）
- 当前数据库共有 1,598 个标准靶点，其中 1,539 个映射到已审阅人类 UniProt 蛋白
- 已写入 7,008 条 GO/Reactome 通路与功能术语及 25,850 条靶点—通路关系
- 已写入 5,403 个 GO 术语及 19,562 条靶点—GO 关系
- 已核验13味国家药食同源药材，其中9味可在 TCMSP 准确匹配
- 已导入947个成分、1,071条药材—成分关系和5,206条TCMSP原始成分—靶点关系
- 145个活性成分中，84个已通过 PubChem 名称与分子量交叉核对
- 已用国家卫健委公告中的正式植物学名称扩展 PubMed 检索；6篇候选中人工规则核验纳入1篇直接UC药对动物研究
- 已生成36个两味组合，并输出最新批次协同潜力前10名
- 已完成5,000次权重扰动敏感性检查，并在结果页显示前10稳定率和去掉配伍证据后的对照名次
- 已增加数据覆盖偏差审计，显示靶点标准化和PubChem结构核对的综合完整度，但不参与药效评分
- 已接入第二个UC患者结肠数据集GSE87466；与GSE75214共有555个同方向差异基因，三种数据集场景第一名一致
- 已通过官方GraphQL API接入Open Targets的UC—靶点关联；完整原始证据独立存储，在纳入门槛确认前不进入评分
- 已通过官方REST API接入NHGRI-EBI GWAS Catalog的UC遗传关联；作者报告基因与变异位置映射基因分开存储，暂不进入评分
- 已通过官方API接入STRING高置信人类蛋白互作，范围限定为两个UC GEO数据集的共识差异基因，暂不进入评分
- ChEMBL官方API当前返回HTTP 500，按“有问题先不接”原则暂缓，不使用第三方镜像
- 已完成党参/黄芪核心成分对PLAU的AutoDock Vina对接；1C5X共晶配体ESI回对接RMSD为0.469埃（合格线2.0埃），方法学验证通过，结果仍暂不进入评分

数据库接入进度见：[数据库接入调查](docs/数据库接入调查.md)。

## 数据更新顺序

```powershell
.\.venv\Scripts\python.exe scripts/init_database.py
.\.venv\Scripts\python.exe scripts/prepare_geo.py
.\.venv\Scripts\python.exe scripts/sync_open_sources.py
.\.venv\Scripts\python.exe scripts/analyze_geo.py
.\.venv\Scripts\python.exe scripts/import_uniprot_reactome.py
.\.venv\Scripts\python.exe scripts/import_go_annotations.py
.\.venv\Scripts\python.exe scripts/import_nhc_botanical_aliases.py
.\.venv\Scripts\python.exe scripts\sync_tcmsp_catalog.py
.\.venv\Scripts\python.exe scripts\sync_tcmsp_details.py
.\.venv\Scripts\python.exe scripts\sync_uniprot_names.py
.\.venv\Scripts\python.exe scripts\import_tcmsp_details.py
.\.venv\Scripts\python.exe scripts\import_uniprot_reactome.py
.\.venv\Scripts\python.exe scripts\sync_pubchem_active.py
.\.venv\Scripts\python.exe scripts\sync_pubmed_pair_candidates.py
.\.venv\Scripts\python.exe scripts\review_pubmed_pair_candidates.py
.\.venv\Scripts\python.exe scripts\score_pairs.py
.\.venv\Scripts\python.exe scripts\analyze_score_sensitivity.py
.\.venv\Scripts\python.exe scripts\audit_data_coverage.py
.\.venv\Scripts\python.exe scripts\sync_open_targets_uc.py
.\.venv\Scripts\python.exe scripts\sync_gwas_catalog_uc.py
.\.venv\Scripts\python.exe scripts\sync_string_uc_consensus.py
.\.venv\Scripts\python.exe scripts\prepare_plau_docking.py
.\.venv\Scripts\python.exe scripts\run_plau_docking.py
.\.venv\Scripts\python.exe scripts\validate_plau_redocking.py
```

日常只重新分析和导出最新结果时，可以直接运行：

```powershell
.\.venv\Scripts\python.exe scripts\run_analysis.py
```

该命令会依次生成排名、权重敏感性、数据完整度，并把带批次号的前10名 CSV 和元数据 JSON 保存到 `exports/`。

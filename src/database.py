from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATABASE_PATH = PROJECT_ROOT / "data" / "uc_herbs.sqlite"


def connect() -> sqlite3.Connection:
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DATABASE_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def load_top_pairs(limit: int = 10) -> pd.DataFrame:
    if not DATABASE_PATH.exists():
        return pd.DataFrame()

    query = """
        SELECT
            ps.rank_position AS 排名,
            h1.standard_name || '＋' || h2.standard_name AS 药对,
            ROUND(ps.total_score, 2) AS 综合分,
            ROUND(ps.target_complementarity_score, 2) AS 靶点互补,
            ROUND(ps.pathway_synergy_score, 2) AS 通路协同,
            ROUND(ps.safety_score, 2) AS 安全基础分,
            ROUND(ps.pair_evidence_score, 2) AS 配伍依据,
            ps.confidence_level AS 证据可信度,
            ROUND(COALESCE(dc.coverage_score, 0), 1) || '%' AS 数据完整度,
            ROUND(COALESCE(st.top10_stability_rate, 0) * 100, 1) || '%' AS 权重扰动下前10稳定率,
            st.rank_without_pair_evidence AS 去掉配伍证据后名次,
            ps.run_id AS 分析批次
        FROM pair_scores ps
        JOIN herbs h1 ON h1.id = ps.herb_a_id
        JOIN herbs h2 ON h2.id = ps.herb_b_id
        LEFT JOIN pair_score_stability st
          ON st.run_id = ps.run_id
         AND st.herb_a_id = ps.herb_a_id
         AND st.herb_b_id = ps.herb_b_id
        LEFT JOIN pair_data_coverage dc
          ON dc.run_id = ps.run_id
         AND dc.herb_a_id = ps.herb_a_id
         AND dc.herb_b_id = ps.herb_b_id
        ORDER BY ps.run_id DESC, ps.rank_position ASC
        LIMIT ?
    """
    with connect() as connection:
        try:
            return pd.read_sql_query(query, connection, params=(limit,))
        except (sqlite3.OperationalError, pd.errors.DatabaseError):
            return pd.DataFrame()


def load_database_status() -> dict[str, int | str]:
    if not DATABASE_PATH.exists():
        return {"status": "尚未初始化", "herbs": 0, "targets": 0, "pairs": 0}

    with connect() as connection:
        values: dict[str, int | str] = {"status": "已初始化"}
        for label, table in (
            ("herbs", "herbs"),
            ("ingredients", "ingredients"),
            ("targets", "targets"),
            ("pairs", "pair_scores"),
        ):
            try:
                values[label] = connection.execute(
                    f"SELECT COUNT(*) FROM {table}"
                ).fetchone()[0]
            except sqlite3.OperationalError:
                values[label] = 0
        try:
            values["eligible_herbs"] = connection.execute(
                """
                SELECT COUNT(DISTINCT h.id)
                FROM herbs h
                JOIN herb_ingredients hi ON hi.herb_id = h.id
                WHERE h.food_medicine_homology = 1
                """
            ).fetchone()[0]
            values["pairs"] = connection.execute(
                "SELECT COUNT(*) FROM pair_scores WHERE run_id = (SELECT MAX(id) FROM analysis_runs)"
            ).fetchone()[0]
        except sqlite3.OperationalError:
            values["eligible_herbs"] = 0
        return values


def load_dataset_validation() -> pd.DataFrame:
    query = """
        SELECT scenario_name AS 验证场景,
               disease_target_count AS UC差异靶点数,
               h1.standard_name || '＋' || h2.standard_name AS 第一名药对,
               ROUND(total_score, 2) AS 场景得分
        FROM pair_dataset_validation v
        JOIN herbs h1 ON h1.id = v.herb_a_id
        JOIN herbs h2 ON h2.id = v.herb_b_id
        WHERE v.baseline_run_id = (SELECT MAX(id) FROM analysis_runs)
          AND v.rank_position = 1
        ORDER BY CASE scenario_name
            WHEN 'GSE75214' THEN 1 WHEN 'GSE87466' THEN 2 ELSE 3 END
    """
    with connect() as connection:
        try:
            return pd.read_sql_query(query, connection)
        except (sqlite3.OperationalError, pd.errors.DatabaseError):
            return pd.DataFrame()


def load_docking_results() -> pd.DataFrame:
    query = """
        SELECT h.standard_name AS 药材,
               i.standard_name AS 活性成分,
               i.pubchem_cid AS PubChem_CID,
               t.gene_symbol AS 靶点,
               d.pdb_id AS PDB结构,
               ROUND(d.best_affinity_kcal_mol, 3) AS 最佳结合能_kcal_mol,
               CASE d.validation_status
                 WHEN 'preliminary_pending_redocking_validation' THEN '初步，待回对接验证'
                 WHEN 'validated_redocking_passed' THEN '已通过共晶配体回对接验证'
                 WHEN 'redocking_validation_failed' THEN '回对接未通过，不建议解读'
                 ELSE d.validation_status END AS 验证状态
        FROM molecular_docking_results d
        JOIN herbs h ON h.id = d.herb_id
        JOIN ingredients i ON i.id = d.ingredient_id
        JOIN targets t ON t.id = d.target_id
        ORDER BY d.best_affinity_kcal_mol ASC
    """
    with connect() as connection:
        try:
            return pd.read_sql_query(query, connection)
        except (sqlite3.OperationalError, pd.errors.DatabaseError):
            return pd.DataFrame()


def load_docking_validation() -> pd.DataFrame:
    query = """
        SELECT target_gene AS 靶点,
               pdb_id AS PDB结构,
               reference_ligand AS 共晶配体,
               ROUND(best_affinity_kcal_mol, 3) AS 回对接结合能_kcal_mol,
               ROUND(heavy_atom_rmsd_angstrom, 3) AS 重原子RMSD_埃,
               rmsd_threshold_angstrom AS 合格线_埃,
               CASE passed WHEN 1 THEN '通过' ELSE '未通过' END AS 结论,
               vina_version AS Vina版本,
               exhaustiveness AS 搜索强度,
               random_seed AS 随机种子
        FROM docking_validation
        ORDER BY created_at DESC
    """
    with connect() as connection:
        try:
            return pd.read_sql_query(query, connection)
        except (sqlite3.OperationalError, pd.errors.DatabaseError):
            return pd.DataFrame()

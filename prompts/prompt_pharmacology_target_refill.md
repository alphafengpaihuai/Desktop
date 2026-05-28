# 药理学靶典补全任务

## 背景
守一 CDSS 的药理学靶典（`data/disease_pharmacology_cache.json`）目前有 654 个疾病条目，但绝大多数疾病的 `key_targets=[]`、`evidence_based_herbs=[]`、`evidence_confidence=Insufficient`。

现有本地资源：
1. **SymMap v2.0 数据库**（`桌面/知识库备份/`）：
   - SMDE.xlsx：疾病表（14434条），含 `Disease_id`、`Disease_Name`、`UMLS_id`、`ICD10CM_id`
   - SMHB.xlsx：草药表（703条），含 `Herb_id`、`Chinese_name`、性味归经、分类
   - SMTT.xlsx：靶点/基因表（20965条），含 `Gene_id`、`Gene_symbol`、靶点信息

2. **口服方剂知识库**（`桌面/知识库备份/口服方剂知识库.xlsx`）：
   - 病名→证型→汤剂方案（含具体用药）
   - 共 625 个病名，10456 条含汤剂方案

## 任务
对 `pharmacology_cache.json` 中所有 `evidence_confidence != "Confirmed"` 的疾病条目，执行以下补全：

### 步骤1：查找 SymMap 关联
- 用 `disease_name` 去 SMDE.xlsx 的 `Disease_Name` 中搜索（中英文模糊匹配）
- 如果找到记录 `Disease_id`、`UMLS_id`、`ICD10CM_id`

### 步骤2：关联靶点
- 通过 `SMDE.Disease_id` 关联 SymMap 中疾病-靶点-草药三元组
- 提取该疾病已知的 `key_targets`（基因/蛋白靶点）

### 步骤3：关联证据基草药
- 从 SMHB 中查找对该疾病有药理证据的草药
- 从口服方剂知识库的汤剂方案中提取该病使用的具体草药

### 步骤4：分级标注
- `Confirmed`：有 2 条以上独立证据（靶点明确 + 药理学文献支持）
- `Moderate`：有 1 条独立证据（SymMap 关联或口服方剂支持）
- `Weak`：仅理论推测，无直接证据
- `Insufficient`：无任何可查到的证据

### 输出格式
保持 `pharmacology_cache.json` 的 JSON 结构不变，只更新以下字段：
- `key_targets`: `[{"gene_symbol": "...", "gene_name": "...", "evidence": "..."}]`
- `evidence_based_herbs`: `[{"herb_name": "...", "targets": ["..."], "evidence": "..."}]`
- `evidence_confidence`: `"Confirmed" | "Moderate" | "Weak" | "Insufficient"`
- `verification_status`: `"updated_by_symmap_refill"`

## 特别说明
- 所有疾病经过此流程后 `evidence_confidence` 不能为空
- 不从互联网抓取数据，只使用以上本地数据库文件
- 适用于 codex 批量执行
